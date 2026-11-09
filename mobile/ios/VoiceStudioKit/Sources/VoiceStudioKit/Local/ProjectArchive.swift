import Foundation
import VSNative

public struct ProjectArchiveFile: Sendable {
    public let path: String
    public let file: URL
    public init(path: String,file: URL) { self.path = path; self.file = file }
}
public struct ProjectArchiveEntry: Sendable {
    public let path: String
    public let size: Int
    let offset: UInt64
    let crc: UInt32
}
/// Portable uncompressed ZIP. No archive-controlled name is ever resolved outside a fresh staging directory.
public enum ProjectArchive {
    public static let maxBytes = 1_073_741_824, maxMember = 536_870_912, maxFiles = 20000
    public static func path(_ value: String) throws -> String {
        guard !value.isEmpty, value.utf8.count <= 240, value.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45,46,47,95].contains($0) }), value.split(separator: "/",omittingEmptySubsequences: false).allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && !$0.hasSuffix(".") }) else { throw ClientError("mobile.handoff.invalid") }
        let reserved = Set(["CON","PRN","AUX","NUL"]+(1...9).flatMap { ["COM\($0)","LPT\($0)"] })
        guard value.split(separator: "/").allSatisfy({ !reserved.contains($0.split(separator: ".")[0].uppercased()) }) else { throw ClientError("mobile.handoff.invalid") }
        return value
    }
    @discardableResult public static func write(entries: [ProjectArchiveFile],to output: URL) async throws -> URL {
        let work = Task.detached {
            guard entries.count <= maxFiles, !FileManager.default.fileExists(atPath: output.path), Set(entries.map { $0.path.lowercased() }).count == entries.count else { throw ClientError("mobile.handoff.invalid") }
            let pending = output.deletingLastPathComponent().appendingPathComponent(".zip-"+UUID().uuidString)
            guard FileManager.default.createFile(atPath: pending.path,contents: nil) else { throw ClientError("mobile.errors.file") }
            defer { try? FileManager.default.removeItem(at: pending) }
            let writer = try FileHandle(forWritingTo: pending); defer { try? writer.close() }
            var central = Data(), position: UInt32 = 0, total = 0
            for entry in entries {
                _ = try path(entry.path)
                var source = entry.file; source.removeAllCachedResourceValues()
                let properties = try source.resourceValues(forKeys: [.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey])
                guard properties.isRegularFile == true, properties.isSymbolicLink != true, let size = properties.fileSize, size <= maxMember else { throw ClientError("mobile.handoff.invalid") }
                total += size; guard total <= maxBytes else { throw ClientError("mobile.handoff.too_large") }
                let crc = try checksum(entry.file,expected: size), name = Data(entry.path.utf8)
                var header = Data(); header.le(UInt32(0x04034b50)); header.le(UInt16(20)); header.le(UInt16(0x800)); header.le(UInt16(0)); header.le(UInt16(0)); header.le(UInt16(0)); header.le(crc); header.le(UInt32(size)); header.le(UInt32(size)); header.le(UInt16(name.count)); header.le(UInt16(0)); header.append(name)
                try writer.write(contentsOf: header)
                let reader = try FileHandle(forReadingFrom: entry.file)
                do {
                    var copied = 0, actual: UInt32 = 0
                    while let bytes = try reader.read(upToCount: 262144), !bytes.isEmpty {
                        try Task.checkCancellation(); copied += bytes.count; guard copied <= size else { throw ClientError("mobile.handoff.invalid") }
                        actual = update(actual,bytes); try writer.write(contentsOf: bytes)
                    }
                    try reader.close(); guard copied == size, actual == crc else { throw ClientError("mobile.handoff.invalid") }
                } catch { try? reader.close(); throw error }
                central.le(UInt32(0x02014b50)); central.le(UInt16(0x0314)); central.le(UInt16(20)); central.le(UInt16(0x800)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(crc); central.le(UInt32(size)); central.le(UInt32(size)); central.le(UInt16(name.count)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt32(0o100600)<<16); central.le(position); central.append(name)
                position += UInt32(header.count+size)
            }
            try writer.write(contentsOf: central)
            var end = Data(); end.le(UInt32(0x06054b50)); end.le(UInt16(0)); end.le(UInt16(0)); end.le(UInt16(entries.count)); end.le(UInt16(entries.count)); end.le(UInt32(central.count)); end.le(position); end.le(UInt16(0))
            try writer.write(contentsOf: end); try writer.synchronize(); try writer.close(); try Task.checkCancellation()
            try FileManager.default.moveItem(at: pending,to: output); return output
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    public static func entries(_ source: URL) throws -> [ProjectArchiveEntry] {
        let reader = try FileHandle(forReadingFrom: source); defer { try? reader.close() }
        let size = try reader.seekToEnd(); guard size >= 22, size <= UInt64(maxBytes+8_388_608) else { throw ClientError("mobile.handoff.too_large") }
        let tailStart = size > 65557 ? size-65557 : 0; try reader.seek(toOffset: tailStart)
        let tail = try reader.read(upToCount: Int(size-tailStart)) ?? Data()
        guard let end = stride(from: tail.count-22,through: 0,by: -1).first(where: { tail.u32($0) == 0x06054b50 }), end+22+tail.u16(end+20) == tail.count, tail.u16(end+4) == 0, tail.u16(end+6) == 0, tail.u16(end+8) == tail.u16(end+10) else { throw ClientError("mobile.handoff.invalid") }
        let count = tail.u16(end+10), centralSize = Int(tail.u32(end+12)), centralOffset = UInt64(tail.u32(end+16))
        guard count <= maxFiles, centralSize <= 8_388_608, centralOffset+UInt64(centralSize) == tailStart+UInt64(end) else { throw ClientError("mobile.handoff.invalid") }
        try reader.seek(toOffset: centralOffset); let central = try reader.read(upToCount: centralSize) ?? Data(); guard central.count == centralSize else { throw ClientError("mobile.handoff.invalid") }
        var result: [ProjectArchiveEntry] = [], cursor = 0, total = 0, names = Set<String>()
        for _ in 0..<count {
            try Task.checkCancellation()
            guard cursor+46 <= central.count, central.u32(cursor) == 0x02014b50 else { throw ClientError("mobile.handoff.invalid") }
            let nameSize = central.u16(cursor+28), extra = central.u16(cursor+30), comment = central.u16(cursor+32), length = Int(central.u32(cursor+24)), compressed = Int(central.u32(cursor+20)), offset = UInt64(central.u32(cursor+42)), mode = central.u32(cursor+38)>>16
            guard cursor+46+nameSize+extra+comment <= central.count, length <= maxMember, compressed == length, central.u16(cursor+10) == 0, central.u16(cursor+8)&(~0x800) == 0, mode&0o170000 == 0 || mode&0o170000 == 0o100000, let name = String(data: central.subdata(in: cursor+46..<cursor+46+nameSize),encoding: .utf8) else { throw ClientError("mobile.handoff.invalid") }
            _ = try path(name); guard names.insert(name.lowercased()).inserted else { throw ClientError("mobile.handoff.invalid") }
            total += length; guard total <= maxBytes, offset+30 <= centralOffset else { throw ClientError("mobile.handoff.too_large") }
            try reader.seek(toOffset: offset); let local = try reader.read(upToCount: 30) ?? Data()
            guard local.count == 30, local.u32(0) == 0x04034b50, local.u16(6) == central.u16(cursor+8), local.u16(8) == 0, local.u32(14) == central.u32(cursor+16), local.u32(18) == UInt32(length), local.u32(22) == UInt32(length), local.u16(26) == nameSize else { throw ClientError("mobile.handoff.invalid") }
            let localName = try reader.read(upToCount: nameSize) ?? Data()
            guard localName == Data(name.utf8) else { throw ClientError("mobile.handoff.invalid") }
            let content = offset+30+UInt64(nameSize+local.u16(28))
            guard content+UInt64(length) <= centralOffset else { throw ClientError("mobile.handoff.invalid") }
            result.append(ProjectArchiveEntry(path: name,size: length,offset: content,crc: central.u32(cursor+16))); cursor += 46+nameSize+extra+comment
        }
        guard cursor == central.count else { throw ClientError("mobile.handoff.invalid") }; return result
    }
    public static func extract(_ source: URL,to directory: URL) async throws -> [String:URL] {
        let work = Task.detached {
            guard !FileManager.default.fileExists(atPath: directory.path) else { throw ClientError("mobile.handoff.invalid") }
            let entries = try entries(source)
            try FileManager.default.createDirectory(at: directory,withIntermediateDirectories: true)
            var completed = false; defer { if !completed { try? FileManager.default.removeItem(at: directory) } }
            let reader = try FileHandle(forReadingFrom: source); defer { try? reader.close() }
            var result: [String:URL] = [:]
            for entry in entries {
                let target = directory.appendingPathComponent(entry.path)
                try FileManager.default.createDirectory(at: target.deletingLastPathComponent(),withIntermediateDirectories: true)
                guard FileManager.default.createFile(atPath: target.path,contents: nil) else { throw ClientError("mobile.errors.file") }
                let writer = try FileHandle(forWritingTo: target)
                do {
                    try reader.seek(toOffset: entry.offset); var remaining = entry.size, crc: UInt32 = 0
                    while remaining > 0 {
                        try Task.checkCancellation(); let bytes = try reader.read(upToCount: min(262144,remaining)) ?? Data()
                        guard !bytes.isEmpty else { throw ClientError("mobile.handoff.invalid") }; remaining -= bytes.count; crc = update(crc,bytes); try writer.write(contentsOf: bytes)
                    }
                    try writer.close(); guard crc == entry.crc else { throw ClientError("mobile.handoff.invalid") }
                } catch { try? writer.close(); throw error }
                result[entry.path] = target
            }
            completed = true; return result
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    private static func checksum(_ file: URL,expected: Int) throws -> UInt32 {
        let reader = try FileHandle(forReadingFrom: file); defer { try? reader.close() }; var total = 0, crc: UInt32 = 0
        while let bytes = try reader.read(upToCount: 262144), !bytes.isEmpty { try Task.checkCancellation(); total += bytes.count; guard total <= expected else { throw ClientError("mobile.handoff.invalid") }; crc = update(crc,bytes) }
        guard total == expected else { throw ClientError("mobile.handoff.invalid") }; return crc
    }
    private static func update(_ crc: UInt32,_ bytes: Data) -> UInt32 { bytes.withUnsafeBytes { vs_book_crc32_update(crc,$0.bindMemory(to: UInt8.self).baseAddress,$0.count) } }
}
private extension Data {
    mutating func le<T: FixedWidthInteger>(_ value: T) { var v = value.littleEndian; Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) } }
    func u16(_ offset: Int) -> Int { guard offset >= 0,offset+2 <= count else { return -1 }; return Int(self[offset]) | Int(self[offset+1])<<8 }
    func u32(_ offset: Int) -> UInt32 { guard offset >= 0,offset+4 <= count else { return .max }; return UInt32(self[offset]) | UInt32(self[offset+1])<<8 | UInt32(self[offset+2])<<16 | UInt32(self[offset+3])<<24 }
}
