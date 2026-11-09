import Foundation

public struct VoiceConversionResult: Sendable {
    public let transcript: String
    public let audio: LocalAudio
}

/// Conversion transcribes then re-synthesizes; it does not preserve the original performance.
public enum VoiceConversion {
    public static func run(source: URL, workspace: URL, voice: PhoneVoice, voices: PhoneVoiceStore,
                           asr: PhoneModel, clone: PhoneModel, engine: any LocalEngine,
                           decoder: any PhoneAudioDecoder, matchDuration: Bool,
                           progress: @escaping EngineProgressHandler = { _ in }) async throws -> VoiceConversionResult {
        try PrivateFiles.directory(workspace)
        let pcmURL = workspace.appendingPathComponent(UUID().uuidString+".pcm")
        defer { try? FileManager.default.removeItem(at: pcmURL) }
        let pcm = try await decoder.decode(source: source,output: pcmURL,maxSeconds: 30,progress: { value in await progress(EngineProgress(stage: .running,fraction: value*0.15)) })
        let audio = try pcm.read(start: 0,count: pcm.frames)
        let text = try await engine.transcribe(model: asr,audio: audio,progress: { value in await progress(EngineProgress(stage: value.stage,fraction: 0.15+value.fraction*0.25)) }).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw ClientError("mobile.simple.no_speech") }
        try Task.checkCancellation()
        let converted = try await engine.clone(model: clone,text: text,reference: voices.reference(voice),referenceText: voice.transcript,progress: { value in await progress(EngineProgress(stage: value.stage,fraction: 0.4+value.fraction*0.6)) })
        let result: LocalAudio
        if matchDuration {
            let fitted = try fitTiming(converted,frames: audio.samples.count)
            result = LocalAudio(samples: fitted.samples,sampleRate: fitted.sampleRate)
        } else { result = converted }
        try Task.checkCancellation()
        return VoiceConversionResult(transcript: text,audio: result)
    }
}
