import Foundation

/// The operator selects installed models before starting. Requests cannot download
/// models, select another private voice, open files, or supply network URLs.
public struct PhoneServerCapabilities: Sendable {
    public typealias Transcriber = @Sendable (PCMChunk) async throws -> String
    public typealias Translator = @Sendable (String,String) async throws -> String
    public var speech = true
    public var transcriptionModel: String?
    public var translationModel: String?
    public var transcribe: Transcriber?
    public var translate: Translator?
    public var translationLanguages: [String] = []
    public var encodeMP3: (@Sendable (Data) async throws -> Data)?
    public init() {}
}

public enum PhoneServerAudio {
    public static let maxRequestBytes = 2_097_152
    /// Strict, bounded PCM16 WAV input. No decoder plug-ins or filesystem paths.
    public static func decodeWave(_ data: Data) throws -> PCMChunk {
        guard data.count >= 44, data.count <= 1_500_000 else { throw ClientError("mobile.errors.audio") }
        let bytes = [UInt8](data)
        func text(_ start: Int,_ end: Int) -> String { String(decoding: bytes[start..<end],as: UTF8.self) }
        func u16(_ at: Int) -> Int { Int(bytes[at]) | Int(bytes[at+1]) << 8 }
        func u32(_ at: Int) -> Int { Int(bytes[at]) | Int(bytes[at+1]) << 8 | Int(bytes[at+2]) << 16 | Int(bytes[at+3]) << 24 }
        guard text(0,4) == "RIFF", text(8,12) == "WAVE", u32(4) == data.count-8 else { throw ClientError("mobile.errors.audio") }
        var position = 12, rate: Int?, payload: Range<Int>?
        while position+8 <= bytes.count {
            let size = u32(position+4), start = position+8
            guard size <= bytes.count-start else { throw ClientError("mobile.errors.audio") }
            switch text(position,position+4) {
            case "fmt ":
                guard rate == nil, (16...64).contains(size), u16(start) == 1, u16(start+2) == 1, u16(start+14) == 16, u16(start+12) == 2 else { throw ClientError("mobile.errors.audio") }
                let sampleRate = u32(start+4)
                guard (8000...24000).contains(sampleRate), u32(start+8) == sampleRate*2 else { throw ClientError("mobile.errors.audio") }
                rate = sampleRate
            case "data":
                guard payload == nil, size > 0, size.isMultiple(of: 2) else { throw ClientError("mobile.errors.audio") }
                payload = start..<start+size
            default: break
            }
            position = start+size+size%2
        }
        guard position == bytes.count, let rate, let payload, payload.count/2 <= rate*30 else { throw ClientError("mobile.errors.audio") }
        return try PCMChunk(pcm16: Data(bytes[payload]),sampleRate: rate)
    }
    public static func speechWave(_ audio: LocalAudio,speed: Float) throws -> Data {
        guard audio.sampleRate == 24000 else { throw ClientError("mobile.errors.audio") }
        return try LocalAudio(samples: NarrationAudio.speed(audio.samples,rate: speed),sampleRate: 24000).markSynthetic()
    }
    public static func mp3(_ wave: Data) async throws -> Data {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("phone-api-"+UUID().uuidString)
        try PrivateFiles.directory(root)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.wav"), output = root.appendingPathComponent("speech.mp3")
        try wave.write(to: source,options: .atomic)
        try await PhoneAudioExport.mp3(source: source,output: output)
        try Task.checkCancellation()
        return try Data(contentsOf: output)
    }
}
