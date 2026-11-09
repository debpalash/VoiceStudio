import Foundation

/// Native implementations for every supported graph operation. No host, downloads or UI state.
public struct WorkflowGraphExecutor: Sendable {
    private let engine: any LocalEngine
    private let store: PhoneVoiceStore
    private let voices: [PhoneVoice]
    private let tts: PhoneModel?
    private let clone: PhoneModel?
    private let asr: PhoneModel?
    private let translator: PhoneModel?
    public init(engine: any LocalEngine,voices: PhoneVoiceStore,library: [PhoneVoice],tts: PhoneModel?,clone: PhoneModel?,asr: PhoneModel?,translator: PhoneModel?) { self.engine = engine; store = voices; self.voices = library; self.tts = tts; self.clone = clone; self.asr = asr; self.translator = translator }
    public func execute(_ step: WorkflowGraphStep,_ value: WorkflowGraphValue,_ workspace: URL) async throws -> WorkflowGraphOutput {
        switch step.kind {
        case "transcribe":
            guard let source = value.audio,let asr else { throw ClientError("mobile.local.model_required") }
            return WorkflowGraphOutput(text: try await WorkflowTranscription.run(source: source,model: asr,engine: engine,decoder: AVPhoneAudioDecoder()))
        case "translate":
            guard let text = value.text,let translator,let language = step.language else { throw ClientError("mobile.local.model_required") }
            let target = Locale(identifier: "en").localizedString(forLanguageCode: language) ?? language
            let source = step.sourceLanguage == "Auto" ? "auto" : step.sourceLanguage ?? "auto"
            return WorkflowGraphOutput(text: try await engine.translate(model: translator,text: text,sourceLanguage: source,targetLanguage: target,progress: { _ in }))
        case "speak":
            guard let text = value.text,let selection = step.voiceId else { throw ClientError("mobile.workflowParity.bind_voices") }
            if selection.hasPrefix("phone:") {
                guard let clone,let voice = voices.first(where: { "phone:"+$0.id.uuidString == selection }) else { throw ClientError("mobile.workflowParity.bind_voices") }
                let result = try await engine.clone(model: clone,text: text,reference: store.reference(voice),referenceText: voice.transcript,progress: { _ in })
                let adjusted = try NarrationAudio.speed(result.samples,rate: Float(step.speed ?? 1))
                return WorkflowGraphOutput(audio: LocalAudio(samples: adjusted,sampleRate: result.sampleRate))
            }
            guard let tts,selection.hasPrefix("preset:"),let speaker = Int(selection.dropFirst(7)) else { throw ClientError("mobile.workflowParity.bind_voices") }
            let chosen: PhoneModel
            if let language = step.language,language != "Auto",language != "auto" {
                let code = tts.languages.first { $0 == language || Locale(identifier: "en").localizedString(forLanguageCode: $0) == language }
                guard let code else { throw ClientError("mobile.local.model_incompatible") }; chosen = try tts.selectingLanguage(code)
            } else { chosen = tts }
            return WorkflowGraphOutput(audio: try await engine.synthesize(model: chosen,text: text,speaker: speaker,speed: Float(step.speed ?? 1),progress: { _ in }))
        case "convert":
            guard let source = value.audio,let asr,let clone,let voice = voices.first(where: { "phone:"+$0.id.uuidString == step.voiceId }) else { throw ClientError("mobile.workflowParity.bind_voices") }
            let result = try await VoiceConversion.run(source: source,workspace: workspace,voice: voice,voices: store,asr: asr,clone: clone,engine: engine,decoder: AVPhoneAudioDecoder(),matchDuration: true)
            return WorkflowGraphOutput(audio: result.audio)
        case "normalize":
            guard value.synthetic,let source = value.audio else { throw ClientError("mobile.workflowParity.incompatible_merge") }
            let document = try await PhoneAudioProcessing.open(source: source,workspace: workspace)
            defer { try? FileManager.default.removeItem(at: document.pcm.url) }
            var options = PhoneAudioEditOptions(); options.normalize = true; options.targetDB = step.targetDb ?? -2
            return WorkflowGraphOutput(file: try await PhoneAudioProcessing.process(document,options: options,workspace: workspace).file)
        default: throw ClientError("mobile.workflowParity.unsupported",params: ["kind":step.kind])
        }
    }
}
