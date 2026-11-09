import Foundation

/// Disk-backed mono float32 audio. Long dubs never accumulate a complete track in RAM.
public struct PCMFile: Sendable {
    public let url: URL
    public let sampleRate: Int
    public init(_ url: URL, sampleRate: Int = 24000) { self.url = url; self.sampleRate = sampleRate }
    public var frames: Int { get throws {
        // URL resource values can retain an old size after an atomic decoder/cache replacement.
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard let bytes = attributes[.size] as? NSNumber, bytes.int64Value >= 0, bytes.int64Value.isMultiple(of: 4) else { throw ClientError("mobile.simple.media_failed") }
        return Int(bytes.int64Value / 4)
    } }
    public func read(start: Int, count: Int) throws -> PCMChunk {
        guard start >= 0, count > 0, count <= 720000 else { throw ClientError("mobile.simple.media_failed") }
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        try handle.seek(toOffset: UInt64(start) * 4)
        let bytes = try handle.read(upToCount: count * 4) ?? Data()
        guard bytes.count == count * 4 else { throw ClientError("mobile.simple.media_failed") }
        let decoded: [Float] = stride(from: 0, to: bytes.count, by: 4).map { offset in
            bytes.withUnsafeBytes { Float(bitPattern: UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: offset, as: UInt32.self))) }
        }
        guard decoded.allSatisfy(\.isFinite) else { throw ClientError("mobile.simple.media_failed") }
        return PCMChunk(samples: decoded, sampleRate: sampleRate)
    }
    public func clip(start: Double, end: Double) throws -> PCMChunk {
        guard start.isFinite, end.isFinite, start >= 0, end > start else { throw ClientError("mobile.simple.media_failed") }
        let first = Int(start * Double(sampleRate)), last = min(try frames, Int(end * Double(sampleRate)))
        return try read(start: first, count: last - first)
    }
    public static func write(_ audio: PCMChunk, to url: URL) throws {
        guard audio.sampleRate == 24000, !audio.samples.isEmpty, audio.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.errors.audio") }
        var bytes = Data(capacity: audio.samples.count * 4)
        for sample in audio.samples { var value = sample.bitPattern.littleEndian; withUnsafeBytes(of: &value) { bytes.append(contentsOf: $0) } }
        try bytes.write(to: url, options: .atomic)
    }
    public func markSynthetic(to output: URL) throws {
        let frames = try frames
        guard frames > 0, frames <= 345600000 else { throw ClientError("mobile.simple.audio_long") }
        let handle = try FileHandle(forWritingTo: PrivateFiles.empty(output)); defer { try? handle.close() }
        // Reuse the synthetic-audio chokepoint's header and INFO provenance, then stream PCM16.
        var header = try LocalAudio(samples: [], sampleRate: sampleRate).markSynthetic()
        var riff = UInt32(header.count - 8 + frames * 2).littleEndian
        withUnsafeBytes(of: &riff) { header.replaceSubrange(4..<8, with: $0) }
        var dataSize = UInt32(frames * 2).littleEndian
        withUnsafeBytes(of: &dataSize) { header.replaceSubrange((header.count-4)..<header.count, with: $0) }
        try handle.write(contentsOf: header)
        for start in stride(from: 0, to: frames, by: 8192) {
            try Task.checkCancellation()
            let audio = try read(start: start, count: min(8192, frames-start))
            var bytes = Data()
            for sample in audio.samples { var value = Int16((min(1,max(-1,sample))*32767).rounded()).littleEndian; withUnsafeBytes(of: &value) { bytes.append(contentsOf: $0) } }
            try handle.write(contentsOf: bytes)
        }
        try handle.synchronize()
    }
}

public enum PrivateFiles {
    public static func directory(_ root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var url = root; var values = URLResourceValues(); values.isExcludedFromBackup = true; try url.setResourceValues(values)
    }
    static func removeUnpublished(in root: URL,metadata: String) throws {
        for directory in try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil) where UUID(uuidString: directory.lastPathComponent) != nil {
            let safe = try child(root,directory.lastPathComponent)
            if !FileManager.default.fileExists(atPath: safe.appendingPathComponent(metadata).path) { try FileManager.default.removeItem(at: safe) }
        }
    }
    static func empty(_ url: URL) throws -> URL {
        guard FileManager.default.createFile(atPath: url.path, contents: Data()) else { throw ClientError("mobile.errors.file") }
        return url
    }
    static func write<T: Encodable>(_ value: T, to url: URL) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(value).write(to: url, options: .atomic)
    }
    static func read<T: Decodable>(_ type: T.Type, from url: URL) throws -> T { try JSONDecoder().decode(type, from: Data(contentsOf: url)) }
    static func child(_ root: URL, _ name: String) throws -> URL {
        guard !name.isEmpty, name != ".", name != "..", name.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45,46,95].contains($0) }) else { throw ClientError("mobile.errors.file") }
        let url = root.appendingPathComponent(name)
        guard !((try? url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) ?? false) else { throw ClientError("mobile.errors.file") }
        return url
    }
}

/// Pad short speech; align overlapping waveforms when compressing long speech to preserve pitch.
public func fitTiming(_ audio: LocalAudio, frames: Int) throws -> PCMChunk {
    guard frames > 0, frames <= 720000, audio.sampleRate == 24000, !audio.samples.isEmpty, audio.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.errors.audio") }
    let source = audio.samples
    if source.count <= frames { return PCMChunk(samples: source + Array(repeating: 0, count: frames-source.count), sampleRate: 24000) }
    let window = 960, hop = 480, search = 240
    var output = [Float](repeating: 0, count: frames)
    if frames < window {
        for index in output.indices {
            let position = Double(index) * Double(source.count-1) / Double(max(1,frames-1))
            let a = Int(position), b = min(a+1,source.count-1)
            output[index] = source[a] + (source[b]-source[a])*Float(position-Double(a))
        }
    } else {
        output.replaceSubrange(0..<window, with: source[0..<window])
        var destination = hop, previous = 0
        while destination < frames {
            try Task.checkCancellation()
            let expected = min(source.count-window, Int(Double(destination)*Double(source.count-window)/Double(max(1,frames-window))))
            let overlap = min(hop,frames-destination)
            var best = expected, bestScore = -Double.infinity
            let lower = min(source.count-window,max(previous+1,expected-search)), upper = min(source.count-window,expected+search)
            for candidate in stride(from: lower, through: upper, by: 4) {
                var dot = 0.0, energy = 1e-12
                for index in stride(from: 0, to: overlap, by: 4) { let sample = Double(source[candidate+index]); dot += Double(output[destination+index])*sample; energy += sample*sample }
                let score = dot/sqrt(energy)
                if score > bestScore { bestScore = score; best = candidate }
            }
            let count = min(window,frames-destination,source.count-best)
            for index in 0..<count { let weight: Float = index < overlap ? Float(index)/Float(overlap) : 1; output[destination+index] = output[destination+index]*(1-weight)+source[best+index]*weight }
            previous = best; destination += hop
        }
    }
    return PCMChunk(samples: output, sampleRate: 24000)
}
public func mixTimeline(to url: URL, frames: Int, segments: [(Int, PCMFile)], gains: [Double] = []) throws -> PCMFile {
    guard frames > 0, frames <= 345600000 else { throw ClientError("mobile.simple.audio_long") }
    let handle = try FileHandle(forUpdating: PrivateFiles.empty(url)); defer { try? handle.close() }
    try handle.truncate(atOffset: UInt64(frames)*4)
    guard gains.isEmpty || gains.count == segments.count else { throw ClientError("mobile.simple.media_failed") }
    for (piece,(start, segment)) in segments.enumerated() {
        let gain = gains.isEmpty ? 1 : gains[piece]
        guard gain.isFinite, (0...2).contains(gain) else { throw ClientError("mobile.simple.media_failed") }
        if gain == 0 { continue }
        guard start >= 0, start < frames else { throw ClientError("mobile.simple.media_failed") }
        let count = min(try segment.frames,frames-start)
        for offset in stride(from: 0, to: count, by: 8192) {
            try Task.checkCancellation()
            let samples = try segment.read(start: offset, count: min(8192,count-offset)).samples
            try handle.seek(toOffset: UInt64(start+offset)*4)
            let bytes = try handle.read(upToCount: samples.count*4) ?? Data()
            guard bytes.count == samples.count*4 else { throw ClientError("mobile.simple.media_failed") }
            var mixed = Data()
            for (index,sample) in samples.enumerated() {
                let old = bytes.withUnsafeBytes { Float(bitPattern: UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: index*4, as: UInt32.self))) }
                var value = min(1,max(-1,old+sample*Float(gain))).bitPattern.littleEndian
                withUnsafeBytes(of: &value) { mixed.append(contentsOf: $0) }
            }
            try handle.seek(toOffset: UInt64(start+offset)*4); try handle.write(contentsOf: mixed)
        }
    }
    try handle.synchronize(); return PCMFile(url)
}
