import SwiftUI
import VoiceStudioKit

struct VoicePicker: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: String
    var dubbing = false
    var compact = false
    var tile = false
    var body: some View {
        ChoicePicker(label: model.t("mobile.speak.voice"), selection: $selection, options:
            (dubbing ? [SelectionOption(id: DubVoiceChoice.unselected, title: model.t("mobile.dub.choose_voice"), enabled: false)] : []) +
            [SelectionOption(id: "", title: model.t(DubVoiceChoice.defaultLabelKey))] +
            model.profiles.map { SelectionOption(id: $0.id, title: $0.name, subtitle: model.languages.language($0.language)?.name) }, compact: compact, icon: tile ? "person.wave.2" : nil, tile: tile)
    }
}
struct SpeakView: View {
    var initialText: String? = nil
    var initialPhoneVoiceID: String? = nil
    var initialPhonePreset: Int? = nil
    var initialPhoneModelID: String? = nil
    var initialComputerVoiceID: String? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var preference: TaskLocation?
    @State private var phoneModelID = ""
    @State private var speakerID = "0"
    @State private var localProgress: EngineProgress?
    @State private var generation: Task<Void, Never>?
    @State private var phoneLanguage = "en"
    @State private var phoneExportSource: URL?
    @State private var exportFormat = "wav"
    @State private var exporting = false
    @State private var exportTask: Task<Void,Never>?
    @State private var shared: SharedFile?
    private var phoneVoice: PhoneVoice? { model.phone.voices.first { "phone:"+$0.id.uuidString == speakerID } }
    private var capability: ModelCapability { phoneVoice == nil ? .tts : .clone }
    private var phoneModel: PhoneModel? { model.phone.models.filter(\.supportsSpeech).first { $0.id == phoneModelID } ?? model.phone.selectedModel("speak-"+capability.rawValue,capability) }
    private var localReady: Bool { phoneModel.map(model.phone.ready) ?? false }
    private var route: TaskRoute { LocalRouting.route(preference: preference, installed: localReady, paired: model.connection.client != nil) }
    @State private var text = ""
    @State private var selection = SpeechSelection()
    @State private var player = AudioPlayer()
    @State private var generating = false
    @State private var deleting: HistoryItem?
    @State private var hostScope: UUID?
    /// The take loaded in the player, for its Report action.
    @State private var result: ContentReportSubject?
    @State private var reporting: ContentReportSubject?
    private var engines: [SelectionOption] {
        (model.inventory?.tts.backends ?? []).map {
            SelectionOption(id: $0.id, title: $0.display_name,
                            subtitle: $0.explanation(for: selection.profile(in: model.profiles), l10n: model.l10n),
                            enabled: $0.selectable(for: selection.profile(in: model.profiles)))
        }
    }
    var body: some View {
        Page {
            RunOnPicker(preference: $preference, task: "speak", installed: localReady).disabled(generating || exporting)
            if route == .connect { NavigationLink(model.t("mobile.settings.connection")) { ConnectView() } }
            speechEditor
            speechOptions
            if player.file != nil || player.playing {
                AudioPlayerView(player: player)
                if let result { ReportButton(subject: result, reporting: $reporting).studioButton(.tertiary) }
                if route != .computer, let source = phoneExportSource {
                    ChoicePicker(label: model.t("audiobook.format"),selection: $exportFormat,options: [SelectionOption(id: "wav",title: "WAV"),SelectionOption(id: "m4a",title: "AAC / M4A"),SelectionOption(id: "mp3",title: "MP3")]).disabled(exporting)
                    Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { exportPhone(source) }.studioButton(.tertiary).disabled(exporting)
                    if exporting { ProgressView(); Button(model.t("common.cancel")) { exportTask?.cancel() }.studioButton(.tertiary) }
                }
            }
            recentGenerations.disabled(exporting)
        }.navigationTitle(model.t("mobile.tabs.speak")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            Group {
                if route == .download {
                    LocalDownloadPrompt(items: phoneModel.map { [$0] } ?? [], feature: "mobile.tabs.speak", prominent: true)
                } else { generationControls }
            }.padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
        }
        .onChange(of: model.inventory?.tts.active, initial: true) { _, _ in selection.reconcile(inventory: model.inventory, profiles: model.profiles) }
        .onChange(of: model.profiles.map(\.id)) { _, _ in selection.reconcile(inventory: model.inventory, profiles: model.profiles) }
        .refreshable { if model.connection.client != nil { await model.perform { try await model.refresh() } }; await model.phone.refresh(); selection.reconcile(inventory: model.inventory, profiles: model.profiles) }
        .contentReportSheet($reporting)
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .sheet(item: $deleting) { item in
            DeleteConfirmation(title: model.t("mobile.ios.delete_generation")) {
                Task { await model.perform { try await model.api().deleteHistory(item.id); model.history = try await model.api().history() } }
            }
        }
        .onAppear { if let initialText,text.isEmpty { text = initialText }; hostScope = model.hostState.scopeID; preference = initialPhoneVoiceID == nil && initialPhonePreset == nil ? model.phone.preference("speak") : .phone; if let id = initialPhoneVoiceID { speakerID = "phone:" + id }; if let preset = initialPhonePreset { speakerID = String(preset); if let id = initialPhoneModelID { phoneModelID = id; model.phone.chooseModel("speak-tts",id) } }; if let id = initialComputerVoiceID { preference = .computer; selection.chooseProfile(id, profiles: model.profiles, inventory: model.inventory, catalog: model.languages) } }
        .task { await model.phone.refresh(); reconcilePhone() }
        .onChange(of: speakerID) { _, _ in
            if phoneVoice != nil, phoneModel?.supportsCloning != true { phoneModelID = model.phone.selectedModel("speak-clone",.clone)?.id ?? "" }
            reconcilePhone()
        }
        .onChange(of: phoneModel?.id) { _, _ in reconcilePhone() }
        .onChange(of: model.phone.voices) { _, _ in if speakerID.hasPrefix("phone:") && phoneVoice == nil { speakerID = "0" } }
        .onDisappear { exportTask?.cancel(); generation?.cancel(); if hostScope != model.hostState.scopeID { player.clear() } else { player.pause() } }
    }
    private var speechEditor: some View {
        ZStack(alignment: .topLeading) {
            TextEditor(text: $text).frame(minHeight: 200)
                .scrollContentBackground(.hidden).padding(Theme.compactInset)
                .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
                .accessibilityLabel(model.t("mobile.speak.placeholder")).accessibilityIdentifier("speak.text")
            if text.isEmpty {
                Text(model.t("mobile.speak.placeholder")).foregroundStyle(theme.color("textMuted"))
                    .padding(Theme.compactInset).allowsHitTesting(false).accessibilityHidden(true)
            }
        }
    }
    private var speakerOptions: [SelectionOption] {
        let count = min(128, Int(phoneModel?.config["num_speakers"]?.number ?? 1))
        let presets: [SelectionOption] = (0..<count).map { index in
            SelectionOption(id: String(index), title: phoneModel?.supportsDesign == true ? model.t("mobile.simple.default_voice") : model.t("mobile.local.preset_voice", ["number": String(index + 1)]))
        }
        let saved: [SelectionOption] = model.phone.voices.map { SelectionOption(id: "phone:" + $0.id.uuidString, title: $0.name) }
        return (phoneModel?.requiresReference == true ? [SelectionOption(id: "", title: model.t("mobile.dub.choose_voice"), enabled: false)] : presets) + saved
    }
    private var phoneModelBinding: Binding<String> {
        Binding(get: { phoneModel?.id ?? "" }, set: { id in
            guard let chosen = model.phone.models.first(where: { $0.id == id && $0.supportsSpeech }) else { return }
            phoneModelID = id
            if phoneVoice != nil && !chosen.supportsCloning { speakerID = "0" }
            model.phone.chooseModel("speak-" + capability.rawValue, id)
            if chosen.supportsCloning { model.phone.chooseModel("speak-clone", id) }
            reconcilePhone()
        })
    }
    private var modelOptions: [SelectionOption] {
        model.phone.models.filter(\.supportsSpeech).map { item in
            SelectionOption(id: item.id, title: item.name, subtitle: model.t("mobile.simple." + item.capability.rawValue), enabled: item.fits())
        }
    }
    private var languageOptions: [SelectionOption] {
        if phoneVoice != nil || phoneModel?.capability == .clone { return [SelectionOption(id: "auto", title: model.t("mobile.ios.language_auto"))] }
        let locale = Locale(identifier: model.l10n.locale)
        return (phoneModel?.languages ?? []).map { code in
            SelectionOption(id: code, title: locale.localizedString(forLanguageCode: code) ?? code)
        }
    }
    private var speechOptions: some View {
        Card {
            if route != .computer {
                VStack(alignment: .leading, spacing: 0) {
                    ChoicePicker(label: model.t("mobile.speak.voice"), selection: $speakerID, options: speakerOptions, compact: false)
                Divider().overlay(theme.color("border"))
                    ChoicePicker(label: model.t("mobile.local.models"), selection: phoneModelBinding, options: modelOptions, compact: false)
                Divider().overlay(theme.color("border"))
                    ChoicePicker(label: model.t("mobile.speak.language"), selection: $phoneLanguage, options: languageOptions, compact: false)

                }.disabled(generating)
                if phoneModel?.requiresReference == true && phoneVoice == nil {
                    Text(model.t("mobile.speak.reference_required")).font(.callout).foregroundStyle(theme.color("textMuted"))
                    NavigationLink(model.t("nav.gallery")) { GalleryView() }.disabled(generating)
                }
            } else {
            VStack(alignment: .leading, spacing: 0) {
                VoicePicker(selection: Binding(get: { selection.profileID }, set: {
                    selection.chooseProfile($0, profiles: model.profiles, inventory: model.inventory, catalog: model.languages)
                }), compact: false)
                Divider().overlay(theme.color("border"))
                ChoicePicker(label: model.t("mobile.speak.engine"), selection: $selection.engineID, options: engines, compact: false).accessibilityIdentifier("speak.engine")
                Divider().overlay(theme.color("border"))
                ChoicePicker(label: model.t("mobile.speak.language"), selection: $selection.language,
                             options: model.languages.languages.map { SelectionOption(id: $0.value, title: $0.name) }, compact: false).accessibilityIdentifier("speak.language")
            }
            }
        }
    }
    @ViewBuilder private var recentGenerations: some View {
        Text(model.t("mobile.speak.recent")).font(.headline)
        if route != .computer {
            if model.phone.recent.isEmpty { EmptyState(key: "mobile.simple.empty_recent") }
            ForEach(model.phone.recent) { item in
                HStack {
                    Text(item.text).lineLimit(1).frame(maxWidth: .infinity,alignment: .leading)
                    Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.performPhone {
                        guard let store = model.phone.generationStore else { throw ClientError("mobile.errors.file") }
                        try await player.load(Data(contentsOf: await store.file(item)),extension: "wav"); player.play()
                        phoneExportSource = try await store.file(item)
                        result = phoneSubject(item)
                    } } }
                    ReportButton(subject: phoneSubject(item), reporting: $reporting, context: item.text)
                    Button(model.t("common.delete"),systemImage: "trash",role: .destructive) { Task { await model.performPhone { try await model.phone.generationStore?.delete(item); await model.phone.refresh() } } }
                }.labelStyle(.iconOnly).studioButton(.tertiary)
            }
        } else if model.history.isEmpty && !model.loading { EmptyState(key: "mobile.simple.empty_recent") }
        else {
            Card {
                ForEach(model.history) { item in
                    HStack(spacing: VSTokens.spacingGap) {
                        Text(item.text).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                        Button(model.t("common.play"), systemImage: "play.fill") { Task { await model.perform { let api = try model.api(); try await player.stream(request: api.historyAudioRequest(item.id), api: api); result = computerSubject(item.id, text: item.text) } } }
                            .labelStyle(.iconOnly).studioButton(.tertiary)
                        Menu {
                            Button(model.t(item.starred ? "mobile.speak.unstar" : "mobile.speak.star"), systemImage: item.starred ? "star.fill" : "star") { Task { await model.perform { try await model.api().star(item); model.history = try await model.api().history() } } }
                            ReportButton(subject: computerSubject(item.id, text: item.text), reporting: $reporting, context: item.text)
                            Button(model.t("common.delete"), systemImage: "trash", role: .destructive) { deleting = item }
                        } label: { Label(model.t("mobile.ios.more_actions"), systemImage: "ellipsis") }
                            .labelStyle(.iconOnly).studioButton(.tertiary).frame(minWidth: 44, minHeight: 44)
                    }
                    if item.id != model.history.last?.id { Divider().overlay(theme.color("border")) }
                }
            }
        }
    }
    private var generationControls: some View {
        VStack(spacing: VSTokens.spacingGap) {
            Button {
                let submittedText = text
                generation = Task {
                    generating = true; defer { generating = false; localProgress = nil }
                    await model.perform(onPhone: route == .phone) {
                        if route == .phone {
                            guard let phoneModel, let engine = model.phone.engine else { throw ClientError("mobile.local.model_required") }
                            let audio: LocalAudio
                            let report: EngineProgressHandler = { value in await MainActor.run { localProgress = value } }
                            if let voice = phoneVoice, let store = model.phone.voiceStore {
                                let reference = try await store.reference(voice)
                                audio = try await engine.clone(model: phoneModel,text: submittedText,reference: reference,referenceText: voice.transcript,progress: report)
                            } else {
                                audio = try await engine.synthesize(model: phoneModel.capability == .clone ? phoneModel : phoneModel.selectingLanguage(phoneLanguage),text: submittedText,speaker: Int(speakerID) ?? 0,speed: 1,progress: report)
                            }
                            let saved = try await model.phone.generationStore?.save(text: submittedText,audio: audio,model: phoneModel)
                            await model.phone.refresh()
                            try await player.load(audio.markSynthetic(), extension: "wav")
                            phoneExportSource = player.file
                            self.result = saved.map(phoneSubject) ?? .phone(.speak, id: UUID().uuidString, repo: phoneModel.repo, revision: phoneModel.revision, typedText: submittedText)
                            return
                        }
                        phoneExportSource = nil
                        let hostEngine = selection.engineID.isEmpty ? model.inventory?.tts.active : selection.engineID
                        let result = try await model.hostState.generate(api: model.api(), text: submittedText, profile: selection.profileID, engine: selection.engineID, language: selection.language)
                        if let hostEngine { model.computerEngines[result.id] = hostEngine }
                        try await player.load(result.audio, extension: "wav")
                        self.result = computerSubject(result.id, text: submittedText)
                        model.history = try await model.api().history()
                    }
                }
            } label: {
                Label(model.t(generating ? "mobile.speak.generating" : "mobile.speak.generate"), systemImage: "waveform")
                    .frame(maxWidth: .infinity)
            }.studioButton(.primary)
                .disabled((route == .phone && phoneModel?.requiresReference == true && phoneVoice == nil) || exporting || model.busy || model.phone.activeDub != nil || generating || (route != .phone && route != .computer) || (route == .computer && !selection.canGenerate(in: model.inventory, profiles: model.profiles)) || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("speak.generate")
            if generating {
                if let value = localProgress { ProgressView(value: value.fraction).accessibilityLabel(model.t("mobile.ios.progress")) }
                Button(model.t("common.cancel")) { generation?.cancel() }
            }
        }
    }
    private func exportPhone(_ source: URL) {
        let format = exportFormat
        exporting = true
        exportTask = Task {
            defer { exporting = false; exportTask = nil }
            await model.performPhone {
                shared = SharedFile(url: try await BookExport.encode(wave: source,format: format))
            }
        }
    }
    private func phoneSubject(_ item: PhoneGeneration) -> ContentReportSubject {
        .phone(.speak, id: item.id.uuidString, repo: item.modelRepo, revision: item.modelRevision, typedText: item.text)
    }
    private func computerSubject(_ id: String, text: String) -> ContentReportSubject {
        .computer(.speak, id: id, engine: model.computerEngines[id], typedText: text)
    }
    private func reconcilePhone() {
        if phoneVoice != nil || phoneModel?.capability == .clone { phoneLanguage = "auto" }
        else if !(phoneModel?.languages.contains(phoneLanguage) ?? false) { phoneLanguage = phoneModel?.languages.first ?? "en" }
        if phoneVoice == nil {
            let count = min(128,Int(phoneModel?.config["num_speakers"]?.number ?? 1))
            if phoneModel?.requiresReference == true { speakerID = "" }
            else if !(0..<max(1,count)).contains(Int(speakerID) ?? -1) { speakerID = "0" }
        }
    }

}
