import Foundation
@preconcurrency import AVFoundation

public struct BookChapter: Sendable, Equatable {
    public let title: String
    public let start: Double
    public init(title: String,start: Double) { self.title = title; self.start = start }
}

/// Native AAC-LC plus bounded MP4 chapter/provenance metadata. WAV remains the lossless master.
public enum BookExport {
    public static func cues(files: [URL],titles: [String]) throws -> [BookChapter] {
        var start = 0.0
        return try files.enumerated().map { index,url in
            let cue = BookChapter(title: index < titles.count ? titles[index] : String(index+1),start: start)
            let audio = try AVAudioFile(forReading: url)
            start += Double(audio.length)/audio.processingFormat.sampleRate+0.25
            return cue
        }
    }
    public static func encode(wave: URL,format: String,chapters: [BookChapter] = [],synthetic: Bool = true) async throws -> URL {
        if format == "wav" { return wave }
        if format == "mp3" { return try await PhoneAudioExport.mp3(source: wave,output: wave.deletingPathExtension().appendingPathExtension("mp3"),synthetic: synthetic) }
        guard ["m4a","m4b"].contains(format), (format != "m4b" || chapters.count <= 255) else { throw ClientError("mobile.books.export_failed") }
        let work = Task.detached(priority: .userInitiated) {
            let output = wave.deletingPathExtension().appendingPathExtension(format)
            let pending = wave.deletingLastPathComponent().appendingPathComponent(UUID().uuidString+".m4a")
            defer { try? FileManager.default.removeItem(at: pending) }
            do {
                try encodeAAC(wave: wave,output: pending)
                try Task.checkCancellation()
                try addMetadata(pending,chapters: format == "m4b" ? chapters : [],synthetic: synthetic)
                try Task.checkCancellation()
                try? FileManager.default.removeItem(at: output)
                try FileManager.default.moveItem(at: pending,to: output)
                return output
            } catch is CancellationError { throw CancellationError() }
            catch { throw ClientError("mobile.books.export_failed") }
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    private static func encodeAAC(wave: URL,output: URL) throws {
        let input = try AVAudioFile(forReading: wave,commonFormat: .pcmFormatFloat32,interleaved: false)
        let duration = Double(input.length)/input.processingFormat.sampleRate
        guard duration >= 0.1, duration <= 14400,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32,sampleRate: 24000,channels: 1,interleaved: false),
              let converter = AVAudioConverter(from: input.processingFormat,to: format),
              let buffer = AVAudioPCMBuffer(pcmFormat: format,frameCapacity: 8192) else { throw ClientError("mobile.books.export_failed") }
        converter.downmix = true
        let settings: [String:Any] = [AVFormatIDKey: kAudioFormatMPEG4AAC,AVSampleRateKey: 24000,AVNumberOfChannelsKey: 1,AVEncoderBitRateKey: 64000]
        let writer = try AVAudioFile(forWriting: output,settings: settings,commonFormat: .pcmFormatFloat32,interleaved: false)
        let failure = ExportReadFailure()
        var finished = false
        while !finished {
            try Task.checkCancellation()
            var error: NSError?
            let status = converter.convert(to: buffer,error: &error) { count,status in
                if input.framePosition >= input.length { status.pointee = .endOfStream; return nil }
                guard let source = AVAudioPCMBuffer(pcmFormat: input.processingFormat,frameCapacity: count) else { status.pointee = .endOfStream; return nil }
                do { try input.read(into: source,frameCount: count); status.pointee = .haveData; return source }
                catch { failure.set(error); status.pointee = .endOfStream; return nil }
            }
            guard status != .error, error == nil, !failure.failed else { throw ClientError("mobile.books.export_failed") }
            if buffer.frameLength > 0 { try writer.write(from: buffer) }
            finished = status == .endOfStream
        }
    }

    private static func atom(_ type: String,_ payload: Data) -> Data {
        var value = Data(); value.be(UInt32(payload.count+8)); value.append(contentsOf: type.data(using: .isoLatin1)!); value.append(payload); return value
    }
    /// Keep all existing media offsets unchanged: retire the old moov and append the augmented moov.
    static func addMetadata(_ url: URL,chapters: [BookChapter],synthetic: Bool = true) throws {
        guard chapters.count <= 255, chapters.allSatisfy({ $0.start.isFinite && $0.start >= 0 }), zip(chapters,chapters.dropFirst()).allSatisfy({ $0.start <= $1.start }) else { throw ClientError("mobile.books.export_failed") }
        let file = try FileHandle(forUpdating: url); defer { try? file.close() }
        let length = try file.seekToEnd(); var position: UInt64 = 0, moovPosition: UInt64?, original = Data()
        while position+8 <= length {
            try file.seek(toOffset: position); let header = try file.read(upToCount: 16) ?? Data()
            guard header.count >= 8 else { throw ClientError("mobile.books.export_failed") }
            let small = UInt64(header.big32(0)); let size = small == 1 ? header.big64(8) : small == 0 ? length-position : small
            guard size >= (small == 1 ? 16 : 8), size <= length-position else { throw ClientError("mobile.books.export_failed") }
            if String(decoding: header[4..<8],as: UTF8.self) == "moov" {
                guard small != 1, moovPosition == nil, size <= 33_554_432 else { throw ClientError("mobile.books.export_failed") }
                moovPosition = position; try file.seek(toOffset: position); original = try file.read(upToCount: Int(size)) ?? Data(); guard original.count == Int(size) else { throw ClientError("mobile.books.export_failed") }
            }
            position += size
        }
        guard position == length, let moovPosition else { throw ClientError("mobile.books.export_failed") }
        let marker = Data((synthetic ? "VoiceStudio synthetic audio" : "VoiceStudio audio").utf8)
        var comment = Data(); comment.be(UInt16(marker.count)); comment.be(UInt16(0)); comment.append(marker)
        var metadata = atom("©cmt",comment)
        if !chapters.isEmpty {
            var cues = Data([1,0,0,0,0,0,0,0,UInt8(chapters.count)])
            for chapter in chapters {
                cues.be(UInt64((chapter.start*10_000_000).rounded()))
                // Truncate only at Unicode scalar boundaries to keep chapter titles valid UTF-8.
                var title = Data()
                for scalar in chapter.title.unicodeScalars { let bytes = Data(String(scalar).utf8); if title.count+bytes.count > 255 { break }; title.append(bytes) }
                cues.append(UInt8(title.count)); cues.append(title)
            }
            metadata.append(atom("chpl",cues))
        }
        let track = chapters.isEmpty ? nil : try BookChapterTrack.add(to: original,at: length,chapters: chapters)
        var payload = track?.payload ?? Data(original.dropFirst(8)); payload.append(atom("udta",metadata))
        let replacement = atom("moov",payload)
        try file.seekToEnd(); if let track { try file.write(contentsOf: track.media) }; try file.write(contentsOf: replacement); try file.synchronize()
        try file.seek(toOffset: moovPosition+4); try file.write(contentsOf: Data("free".utf8)); try file.synchronize()
    }
}

private extension Data {
    mutating func be<T: FixedWidthInteger>(_ value: T) { var number = value.bigEndian; Swift.withUnsafeBytes(of: &number) { append(contentsOf: $0) } }
    func big32(_ offset: Int) -> UInt32 { guard offset+4 <= count else { return 0 }; return self[offset..<offset+4].reduce(0) { $0 << 8 | UInt32($1) } }
    func big64(_ offset: Int) -> UInt64 { guard offset+8 <= count else { return 0 }; return self[offset..<offset+8].reduce(0) { $0 << 8 | UInt64($1) } }
}

private final class ExportReadFailure: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var failed: Bool { lock.withLock { value } }
    func set(_ error: any Error) { lock.withLock { value = true } }
}
