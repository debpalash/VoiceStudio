import SwiftUI
import VoiceStudioKit

struct LocalAPIView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var lan = false
    @State private var speech = true
    @State private var transcription = false
    @State private var translation = false
    @State private var source = "preset"
    @State private var speaker = "0"
    @State private var voiceID = ""
    @State private var server: PhoneSpeechServer?
    @State private var starting = false
    @State private var copied = false
    @State private var work: Task<Void,Never>?
    private var speechModel: PhoneModel? { model.phone.selectedModel(source == "saved" ? "phone-api-clone" : "phone-api",source == "saved" ? .clone : .tts) }
    private var recognizer: PhoneModel? { model.phone.selectedModel("phone-api-asr",.asr) }
    private var translator: PhoneModel? { model.phone.selectedModel("phone-api-translate",.translate) }
    private var savedVoice: PhoneVoice? { model.phone.voices.first { $0.id.uuidString == voiceID } ?? model.phone.voices.first }
    private var needed: [PhoneModel] { [speech ? speechModel : nil,transcription ? recognizer : nil,translation ? translator : nil].compactMap { $0 } }
    private var ready: Bool {
        (speech || transcription || translation) && (!speech || (speechModel.map(model.phone.ready) == true && (source == "preset" || savedVoice != nil))) && (!transcription || recognizer.map(model.phone.ready) == true) && (!translation || translator.map(model.phone.ready) == true)
    }
    var body: some View {
        Page {
            Text(model.t("mobile.host.capabilities_hint"))
            if let server {
                Card {
                    Text(model.t("mobile.host.running")).font(.headline)
                    ForEach(server.addresses,id: \.self) { Text($0).font(.system(.footnote,design: .monospaced)).textSelection(.enabled) }
                    Text("/v1/models · /mcp").font(.footnote)
                    Button(model.t(copied ? "sharing.copied" : "mobile.host.copy"),systemImage: "doc.on.doc") {
                        UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: server.token]],options: [.localOnly:true,.expirationDate:Date().addingTimeInterval(120)])
                        copied = true
                    }.studioButton(.tertiary)
                    Button(model.t("common.stop"),systemImage: "stop.fill") { stop() }.studioButton(.primary)
                }
            } else {
                Toggle(model.t("mobile.host.lan"),isOn: $lan).disabled(starting)
                Text(model.t("mobile.host.lan_hint")).font(.footnote)
                VStack(spacing: VSTokens.spacingGap) {
                    Toggle(model.t("mobile.host.allow_speech"),isOn: $speech)
                    if speech {
                        ChoicePicker(label: model.t("mobile.host.voice_source"),selection: $source,options: [SelectionOption(id: "preset",title: model.t("mobile.host.preset")),SelectionOption(id: "saved",title: model.t("mobile.host.saved"))])
                        if source == "saved" {
                            if model.phone.voices.isEmpty { NavigationLink(model.t("mobile.voices.new_clone")) { VoicesView() } }
                            else { ChoicePicker(label: model.t("mobile.speak.voice"),selection: Binding(get: { savedVoice?.id.uuidString ?? "" },set: { voiceID = $0 }),options: model.phone.voices.map { SelectionOption(id: $0.id.uuidString,title: $0.name) }) }
                        } else {
                            ChoicePicker(label: model.t("mobile.speak.voice"),selection: $speaker,options: (0..<max(1,min(128,Int(speechModel?.config["num_speakers"]?.number ?? 1)))).map { SelectionOption(id: String($0),title: model.t("mobile.local.preset_voice",["number":String($0+1)])) })
                        }
                        modelPicker(source == "saved" ? "phone-api-clone" : "phone-api",source == "saved" ? .clone : .tts)
                    }
                    Toggle(model.t("mobile.host.allow_transcription"),isOn: $transcription)
                    if transcription { modelPicker("phone-api-asr",.asr); Text(model.t("mobile.host.audio_hint")).font(.footnote) }
                    Toggle(model.t("mobile.host.allow_translation"),isOn: $translation)
                    if translation { modelPicker("phone-api-translate",.translate) }
                }.disabled(starting)
                let missing = needed.filter { !model.phone.ready($0) }
                if !missing.isEmpty { LocalDownloadPrompt(items: missing,feature: "mobile.host.title") }
                Button(model.t("mobile.host.start"),systemImage: "network") { start() }.studioButton(.primary).disabled(starting || !ready)
            }
        }
        .navigationTitle(model.t("mobile.host.title")).navigationBarTitleDisplayMode(.inline)
        .task { await model.phone.refresh() }
        .onChange(of: speechModel?.id) { _,_ in speaker = "0" }
        .onChange(of: phase) { _,value in if value != .active { stop() } }
        .onDisappear { stop() }
    }
    private func modelPicker(_ task: String,_ capability: ModelCapability) -> some View {
        PhoneModelPicker(label: model.t("mobile.local.model"),selection: Binding(get: { model.phone.selectedModel(task,capability)?.id ?? "" },set: { model.phone.chooseModel(task,$0) }), items: model.phone.candidates(capability))
    }
    private func start() {
        guard ready, let engine = model.phone.engine, let selected = Int(speaker) else { return }
        let localNetwork = lan, speechEnabled = speech, useSaved = source == "saved"
        let selectedModel = speechModel, saved = savedVoice, voices = model.phone.voiceStore
        let asr = recognizer, translationModel = translator, languageCatalog = model.languages
        var capabilities = PhoneServerCapabilities()
        capabilities.speech = speechEnabled
        capabilities.encodeMP3 = { try await PhoneServerAudio.mp3($0) }
        if transcription, let asr {
            capabilities.transcriptionModel = asr.id
            capabilities.transcribe = { try await engine.transcribe(model: asr,audio: $0,progress: {_ in}) }
        }
        if translation, let translationModel {
            capabilities.translationModel = translationModel.id; capabilities.translationLanguages = translationModel.languages
            capabilities.translate = { text,code in
                try await engine.translate(model: translationModel,text: text,sourceLanguage: "auto",targetLanguage: languageCatalog.language(code)?.name ?? code,progress: {_ in})
            }
        }
        let features = capabilities
        starting = true; copied = false
        work = Task {
            defer { starting = false; work = nil }
            await model.performPhone {
                let candidate = try PhoneSpeechServer(model: selectedModel?.id ?? "phone",capabilities: features) { text,speed in
                    guard let item = selectedModel else { throw ClientError("mobile.local.model_required") }
                    if useSaved {
                        guard let saved, let voices else { throw ClientError("mobile.local.model_required") }
                        let audio = try await engine.clone(model: item,text: text,reference: voices.reference(saved),referenceText: saved.transcript,progress: {_ in})
                        return try PhoneServerAudio.speechWave(audio,speed: speed)
                    }
                    return try await engine.synthesize(model: item,text: text,speaker: selected,speed: speed,progress: {_ in}).markSynthetic()
                }
                do { try await candidate.start(lan: localNetwork); try Task.checkCancellation(); server = candidate }
                catch { candidate.stop(); throw error }
            }
        }
    }
    private func stop() { work?.cancel(); server?.stop(); server = nil; copied = false }
}
