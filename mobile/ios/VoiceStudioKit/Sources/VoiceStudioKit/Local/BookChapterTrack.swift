import Foundation

/// QuickTime text chapter track for Apple Books/AVFoundation, alongside Nero chapters for other players.
enum BookChapterTrack {
    static func add(to movie: Data,at offset: UInt64,chapters: [BookChapter]) throws -> (payload: Data,media: Data) {
        let boxes = try children(Data(movie.dropFirst(8)))
        guard let header = boxes.first(where: { $0.0 == "mvhd" })?.1, header.count >= 100 else { throw ClientError("mobile.books.export_failed") }
        let version = header[8], scale: UInt32, duration: UInt64
        if version == 0 { scale = header.u32(20); duration = UInt64(header.u32(24)) }
        else if version == 1, header.count >= 120 { scale = header.u32(28); duration = header.u64(32) }
        else { throw ClientError("mobile.books.export_failed") }
        guard scale > 0, duration > 0, chapters.first?.start == 0 else { throw ClientError("mobile.books.export_failed") }
        let seconds = Double(duration)/Double(scale), total = UInt32(min(Double(UInt32.max),(seconds*1000).rounded()))
        var maximum: UInt32 = 0
        for (_,track) in boxes where String(decoding: track[4..<8],as: UTF8.self) == "trak" {
            if let tkhd = try children(Data(track.dropFirst(8))).first(where: { $0.0 == "tkhd" })?.1 {
                maximum = max(maximum,tkhd.u32(tkhd[8] == 1 ? 28 : 20))
            }
        }
        guard maximum < UInt32.max else { throw ClientError("mobile.books.export_failed") }
        let identifier = max(maximum+1,header.u32(header.count-4))
        guard identifier < UInt32.max, duration <= UInt64(UInt32.max), total > 0 else { throw ClientError("mobile.books.export_failed") }
        var samples = Data(), sizes: [UInt32] = [], ticks: [UInt32] = []
        for (index,chapter) in chapters.enumerated() {
            let start = (chapter.start*1000).rounded(), end = index+1 < chapters.count ? (chapters[index+1].start*1000).rounded() : Double(total)
            guard start >= 0, start < end, end <= Double(total) else { throw ClientError("mobile.books.export_failed") }
            var title = Data()
            for scalar in chapter.title.unicodeScalars { let bytes = Data(String(scalar).utf8); if title.count+bytes.count > 255 { break }; title.append(bytes) }
            var sample = Data(); sample.put(UInt16(title.count)); sample.append(title)
            sample.append(atom("encd",Data([0,0,1,0])))
            samples.append(sample); sizes.append(UInt32(sample.count)); ticks.append(UInt32(end-start))
        }
        let media = atom("mdat",samples)
        var payload = Data()
        for (type,data) in boxes {
            if type == "mvhd" { var updated = data; updated.replaceSubrange(updated.count-4..<updated.count,with: number(identifier+1)); payload.append(updated) }
            else if type == "trak" {
                let members = try children(Data(data.dropFirst(8)))
                guard !members.contains(where: { $0.0 == "tref" }) else { throw ClientError("mobile.books.export_failed") }
                var body = Data(data.dropFirst(8)); body.append(atom("tref",atom("chap",number(identifier)))); payload.append(atom("trak",body))
            } else { payload.append(data) }
        }
        var tkhd = Data(); tkhd.put(UInt32(2)); tkhd.put(UInt32(0)); tkhd.put(UInt32(0)); tkhd.put(identifier); tkhd.put(UInt32(0)); tkhd.put(UInt32(duration)); tkhd.append(Data(count: 16)); tkhd.append(matrix); tkhd.append(Data(count: 8))
        var mdhd = Data(count: 12); mdhd.put(UInt32(1000)); mdhd.put(total); mdhd.put(UInt32(0))
        var hdlr = Data(count: 8); hdlr.append(Data("text".utf8)); hdlr.append(Data(count: 12)); hdlr.append(Data("SubtitleHandler\0".utf8))
        var generic = Data(count: 4); generic.put(UInt16(64)); for _ in 0..<3 { generic.put(UInt16(128)) }; generic.append(Data(count: 4))
        let gmhd = atom("gmhd",atom("gmin",generic)+atom("text",matrix))
        var dref = Data(count: 4); dref.put(UInt32(1)); dref.append(atom("url ",number(UInt32(1))))
        // QuickTime TextSampleEntry: reserved/data-reference, display flags, text box, default style/font table.
        let textEntry = Data(hex: "0000003b7465787400000000000000010000000100000000000000000000000000000000000000010000000000000000000d667461620001000100")
        var stsd = Data(count: 4); stsd.put(UInt32(1)); stsd.append(textEntry)
        var stts = Data(count: 4); stts.put(UInt32(ticks.count)); for tick in ticks { stts.put(UInt32(1)); stts.put(tick) }
        var stsc = Data(count: 4); [UInt32(1),1,UInt32(sizes.count),1].forEach { stsc.put($0) }
        var stsz = Data(count: 8); stsz.put(UInt32(sizes.count)); sizes.forEach { stsz.put($0) }
        var co64 = Data(count: 4); co64.put(UInt32(1)); co64.put(offset+8)
        let stbl = atom("stbl",atom("stsd",stsd)+atom("stts",stts)+atom("stsc",stsc)+atom("stsz",stsz)+atom("co64",co64))
        let minf = atom("minf",gmhd+atom("dinf",atom("dref",dref))+stbl)
        let mdia = atom("mdia",atom("mdhd",mdhd)+atom("hdlr",hdlr)+minf)
        payload.append(atom("trak",atom("tkhd",tkhd)+mdia))
        return (payload,media)
    }
    private static var matrix: Data { var result = Data(); [UInt32(0x10000),0,0,0,0x10000,0,0,0,0x40000000].forEach { result.put($0) }; return result }
    private static func number<T: FixedWidthInteger>(_ value: T) -> Data { var result = Data(); result.put(value); return result }
    private static func atom(_ type: String,_ payload: Data) -> Data { var result = number(UInt32(payload.count+8)); result.append(Data(type.utf8)); result.append(payload); return result }
    private static func children(_ data: Data) throws -> [(String,Data)] {
        var result: [(String,Data)] = [], cursor = 0
        while cursor < data.count {
            guard cursor+8 <= data.count else { throw ClientError("mobile.books.export_failed") }
            let size = Int(data.u32(cursor)); guard size >= 8, size <= data.count-cursor, result.count < 10000 else { throw ClientError("mobile.books.export_failed") }
            result.append((String(decoding: data[cursor+4..<cursor+8],as: UTF8.self),data.subdata(in: cursor..<cursor+size))); cursor += size
        }
        return result
    }
}
private extension Data {
    init(hex: String) { self.init(); var cursor = hex.startIndex; while cursor < hex.endIndex { let end = hex.index(cursor,offsetBy: 2); append(UInt8(hex[cursor..<end],radix: 16)!); cursor = end } }
    mutating func put<T: FixedWidthInteger>(_ value: T) { var v = value.bigEndian; Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) } }
    func u32(_ offset: Int) -> UInt32 { guard offset >= 0, offset+4 <= count else { return 0 }; return self[offset..<offset+4].reduce(0) { $0 << 8 | UInt32($1) } }
    func u64(_ offset: Int) -> UInt64 { guard offset >= 0, offset+8 <= count else { return 0 }; return self[offset..<offset+8].reduce(0) { $0 << 8 | UInt64($1) } }
}
