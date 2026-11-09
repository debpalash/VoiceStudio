import Foundation
#if canImport(FoundationXML)
import FoundationXML
#endif
import PDFKit
import VSNative

public enum BookImport {
    public static let textLimit = 1_000_000
    public static func read(_ url: URL) async throws -> String {
        let work = Task.detached(priority: .userInitiated) {
            let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
            guard url.isFileURL, (try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) <= 67_108_864 else { throw ClientError("mobile.books.import_failed") }
            let text: String
            switch url.pathExtension.lowercased() {
            case "epub": text = try epub(Data(contentsOf: url))
            case "pdf":
                guard let document = PDFDocument(url: url), !document.isLocked, !document.isEncrypted, document.pageCount <= 5000 else { throw ClientError("mobile.books.import_failed") }
                var pages: [String] = [], count = 0
                for index in 0..<document.pageCount {
                    try Task.checkCancellation()
                    let page = document.page(at: index)?.string ?? ""
                    count += page.utf16.count; guard count <= textLimit else { throw ClientError("mobile.books.import_failed") }
                    pages.append(page)
                }
                text = pages.joined(separator: "\n\n")
            default:
                guard let value = String(data: try Data(contentsOf: url),encoding: .utf8) else { throw ClientError("mobile.books.import_failed") }; text = value
            }
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.utf16.count <= textLimit else { throw ClientError("mobile.books.no_text") }
            return text
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    public static func epub(_ bytes: Data) throws -> String {
        let zip = try BookZip(bytes)
        let container = try BookXML.parse(zip.read("META-INF/container.xml"))
        guard let package = container.rootfile else { throw ClientError("mobile.books.import_failed") }
        let packagePath = try BookZip.path(package)
        let opf = try BookXML.parse(zip.read(packagePath))
        guard !opf.spine.isEmpty, opf.spine.count <= 5000 else { throw ClientError("mobile.books.import_failed") }
        let base = (packagePath as NSString).deletingLastPathComponent
        var chapters: [String] = [], count = 0
        for id in opf.spine {
            try Task.checkCancellation()
            guard let href = opf.manifest[id], let decoded = href.removingPercentEncoding, !decoded.contains(":"), !decoded.contains("?") else { throw ClientError("mobile.books.import_failed") }
            let fragmentless = String(decoded.split(separator: "#",maxSplits: 1,omittingEmptySubsequences: false)[0])
            let path = try BookZip.path(base.isEmpty ? fragmentless : base+"/"+fragmentless)
            let document = try BookXML.parse(zip.read(path))
            let body = document.text.trimmingCharacters(in: .whitespacesAndNewlines)
            count += body.utf16.count; guard count <= textLimit else { throw ClientError("mobile.books.import_failed") }
            if !body.isEmpty { chapters.append("# "+(document.heading?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty ?? String(chapters.count+1))+"\n"+body) }
        }
        let text = chapters.joined(separator: "\n\n")
        guard !text.isEmpty, text.utf16.count <= textLimit else { throw ClientError("mobile.books.no_text") }
        return text
    }
}

private final class BookXML: NSObject, XMLParserDelegate {
    var rootfile: String?, manifest: [String:String] = [:], spine: [String] = [], text = "", heading: String?
    private var headingDepth = 0, depth = 0, headingText = ""
    private var ignored = 0
    static func parse(_ data: Data) throws -> BookXML {
        guard data.count <= 16_777_216, !String(decoding: data,as: UTF8.self).uppercased().contains("<!ENTITY") else { throw ClientError("mobile.books.import_failed") }
        let delegate = BookXML(), parser = XMLParser(data: data)
        parser.shouldResolveExternalEntities = false; parser.delegate = delegate
        guard parser.parse(), delegate.text.utf16.count <= BookImport.textLimit else { throw ClientError("mobile.books.import_failed") }
        return delegate
    }
    func parser(_ parser: XMLParser,didStartElement elementName: String,namespaceURI: String?,qualifiedName: String?,attributes: [String:String]) {
        depth += 1
        let name = elementName.split(separator: ":").last.map(String.init)?.lowercased() ?? elementName
        if name == "rootfile" { rootfile = attributes["full-path"] }
        if name == "item", let id = attributes["id"], let href = attributes["href"] { manifest[id] = href }
        if name == "itemref", let id = attributes["idref"], attributes["linear"] != "no" { spine.append(id) }
        if ignored > 0 || ["script","style","head"].contains(name) { ignored += 1 }
        if ignored == 0, heading == nil, headingDepth == 0, ["h1","h2"].contains(name) { headingDepth = depth; headingText = "" }
        if ignored == 0, ["p","div","br","li","h1","h2","h3","section"].contains(name) { text += "\n" }
    }
    func parser(_ parser: XMLParser,didEndElement elementName: String,namespaceURI: String?,qualifiedName: String?) {
        defer { depth -= 1 }
        if ignored > 0 { ignored -= 1; return }
        if headingDepth == depth { heading = headingText; headingDepth = 0 }
        let name = elementName.split(separator: ":").last.map(String.init)?.lowercased() ?? elementName
        if ["p","div","li","h1","h2","h3","section"].contains(name) { text += "\n" }
    }
    func parser(_ parser: XMLParser,foundCharacters string: String) {
        if ignored == 0 { if headingDepth > 0 { headingText += string }; text += string; if text.utf16.count > BookImport.textLimit { parser.abortParsing() } }
    }
    func parser(_ parser: XMLParser,resolveExternalEntityName name: String,systemID: String?) -> Data? { nil }
}

/// Reads only named EPUB members in memory; no archive path is ever extracted to the filesystem.
private struct BookZip {
    private struct Entry { let offset: Int; let compressed: Int; let size: Int; let method: Int; let crc: UInt32 }
    private let bytes: Data
    private var entries: [String:Entry] = [:]
    init(_ bytes: Data) throws {
        guard bytes.count >= 22, bytes.count <= 67_108_864 else { throw ClientError("mobile.books.import_failed") }
        self.bytes = bytes
        let end = stride(from: bytes.count-22,through: max(0,bytes.count-65557),by: -1).first { BookZip.u32(bytes,$0) == 0x06054b50 }
        guard let end, BookZip.u16(bytes,end+4) == 0, BookZip.u16(bytes,end+6) == 0, BookZip.u16(bytes,end+8) == BookZip.u16(bytes,end+10), end+22+BookZip.u16(bytes,end+20) == bytes.count else { throw ClientError("mobile.books.import_failed") }
        let count = BookZip.u16(bytes,end+10), centralSize = Int(BookZip.u32(bytes,end+12)), start = Int(BookZip.u32(bytes,end+16))
        guard count <= 4000, start >= 0, centralSize <= bytes.count, start <= end-centralSize else { throw ClientError("mobile.books.import_failed") }
        var cursor = start, total = 0
        for _ in 0..<count {
            guard cursor >= 0, cursor <= bytes.count-46, BookZip.u32(bytes,cursor) == 0x02014b50 else { throw ClientError("mobile.books.import_failed") }
            let flags = BookZip.u16(bytes,cursor+8), method = BookZip.u16(bytes,cursor+10)
            let size = Int(BookZip.u32(bytes,cursor+24)), compressed = Int(BookZip.u32(bytes,cursor+20)), offset = Int(BookZip.u32(bytes,cursor+42))
            let nameSize = BookZip.u16(bytes,cursor+28), extra = BookZip.u16(bytes,cursor+30), comment = BookZip.u16(bytes,cursor+32)
            guard flags & 1 == 0, [0,8].contains(method), size <= 16_777_216, compressed <= 67_108_864, cursor+46+nameSize+extra+comment <= start+centralSize, let name = String(data: bytes.subdata(in: cursor+46..<cursor+46+nameSize),encoding: .utf8) else { throw ClientError("mobile.books.import_failed") }
            total += size; guard total <= 134_217_728 else { throw ClientError("mobile.books.import_failed") }
            let safe = try Self.path(name.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
            guard !name.hasPrefix("/"), entries[safe] == nil else { throw ClientError("mobile.books.import_failed") }
            if !name.hasSuffix("/") { entries[safe] = Entry(offset: offset,compressed: compressed,size: size,method: method,crc: BookZip.u32(bytes,cursor+16)) }
            cursor += 46+nameSize+extra+comment
        }
        guard cursor == start+centralSize else { throw ClientError("mobile.books.import_failed") }
    }
    static func path(_ value: String) throws -> String {
        guard !value.isEmpty, !value.hasPrefix("/"), !value.contains("\\"), !value.contains(":"), !value.utf8.contains(0) else { throw ClientError("mobile.books.import_failed") }
        var parts: [String] = []
        for component in value.split(separator: "/",omittingEmptySubsequences: false) {
            if component == "." { continue }
            if component == ".." { guard !parts.isEmpty else { throw ClientError("mobile.books.import_failed") }; parts.removeLast() }
            else { guard !component.isEmpty else { throw ClientError("mobile.books.import_failed") }; parts.append(String(component)) }
        }
        guard !parts.isEmpty else { throw ClientError("mobile.books.import_failed") }; return parts.joined(separator: "/")
    }
    func read(_ name: String) throws -> Data {
        guard let entry = entries[try Self.path(name)], entry.offset >= 0, entry.offset <= bytes.count-30, Self.u32(bytes,entry.offset) == 0x04034b50 else { throw ClientError("mobile.books.import_failed") }
        let start = entry.offset+30+Self.u16(bytes,entry.offset+26)+Self.u16(bytes,entry.offset+28)
        guard start <= bytes.count, entry.compressed <= bytes.count-start else { throw ClientError("mobile.books.import_failed") }
        let compressed = bytes.subdata(in: start..<start+entry.compressed)
        if entry.method == 0 {
            guard compressed.count == entry.size, compressed.withUnsafeBytes({ vs_book_crc32($0.bindMemory(to: UInt8.self).baseAddress,$0.count) }) == entry.crc else { throw ClientError("mobile.books.import_failed") }; return compressed
        }
        var output = Data(count: max(1,entry.size))
        let result = output.withUnsafeMutableBytes { destination in compressed.withUnsafeBytes { source in vs_book_inflate(source.bindMemory(to: UInt8.self).baseAddress,source.count,destination.bindMemory(to: UInt8.self).baseAddress,entry.size,entry.crc) } }
        guard result == 0 else { throw ClientError("mobile.books.import_failed") }; output.count = entry.size; return output
    }
    private static func u16(_ d: Data,_ i: Int) -> Int { guard i >= 0,i+2 <= d.count else { return -1 }; return Int(d[i]) | Int(d[i+1]) << 8 }
    private static func u32(_ d: Data,_ i: Int) -> UInt32 { guard i >= 0,i+4 <= d.count else { return UInt32.max }; return UInt32(d[i]) | UInt32(d[i+1]) << 8 | UInt32(d[i+2]) << 16 | UInt32(d[i+3]) << 24 }
}

private extension String { var nilIfEmpty: String? { isEmpty ? nil : self } }
