import SwiftUI
import VoiceStudioKit
import UniformTypeIdentifiers
import AVKit

struct PhoneDubView: View {
    var projectID: UUID? = nil
    var libraryOnly = false
    var batchMode = false
    @State private var batchSources: [SelectedMedia] = []
    private var records: [PhoneDubRecord] { model.phone.dubs.filter { projectID == nil || $0.id == projectID } }
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var connect = false
    @State private var source: SelectedMedia?
    @State private var importing = false
    @State private var targets = Set<String>()
    @State private var languages = false
    @State private var voice = ""
    @State private var consent = false
    @State private var audioOnly = false
    @State private var editing: PhoneDubRecord?
    private struct ExportSelection: Identifiable {
        let record: PhoneDubRecord
        let language: String
        var id: String { record.id.uuidString + ":" + language }
    }
    @State private var exporting: ExportSelection?
    @State private var shared: SharedFile?
    @State private var saved: SharedFile?
    @State private var playing: SharedFile?
    @State private var creating: Task<Void,Never>?
    @State private var copying = false
    @State private var reporting: ContentReportSubject?
    private var choices: [DubLanguage] { model.languages.dubOptions }
    private var ready: Bool { model.phone.dubReady && !connect }
    var body: some View {
        Page {
            if projectID == nil && !libraryOnly {
                if connect { NavigationLink(model.t("mobile.simple.reconnect")) { ConnectView() } }
                Group {
                    if batchMode {
                        DubSourceButton { importing = true }
                        ForEach(batchSources,id: \.url) { item in DubFileCard(name: item.name) { batchSources.removeAll { $0.url == item.url } } }
                    }
                    else if let source { DubFileCard(name: source.name) { self.source = nil } }
                    else { DubSourceButton { importing = true } }
                }.disabled(copying)
                DubTiles {
                    Button { languages = true } label: {
                        TileLabel(icon: "globe", label: model.t("mobile.dub.target_languages"), value: {let names = choices.filter { targets.contains($0.code) }.map(\.name); return names.isEmpty ? model.t("mobile.android.choose") : ListFormatter.localizedString(byJoining: names)}())
                    }.studioButton(.tertiary).accessibilityIdentifier("dub.languages")
                    ChoicePicker(label: model.t("mobile.speak.voice"), selection: $voice, options: [SelectionOption(id: "", title: model.t("mobile.simple.reference"))] + model.phone.voices.map { SelectionOption(id: $0.id.uuidString, title: $0.name) }, icon: "person.wave.2", tile: true)
                }
                DubConsentRow(isOn: $consent)
                ModelRamWarning(items: model.phone.dubModels)
                DisclosureGroup {
                    Toggle(model.t("mobile.simple.audio_only"), isOn: $audioOnly).frame(minHeight: 44)
                    phoneModel("dub-asr", .asr)
                    phoneModel("dub-translate", .translate)
                    phoneModel("dub", .clone)
                } label: { Label(model.t("mobile.simple.more"), systemImage: "slider.horizontal.3") }
                    .padding(.horizontal, Theme.compactInset)
                    .disabled(copying || model.phone.activeDub != nil)
                if !ready { LocalDownloadPrompt(items: model.phone.dubModels, feature: "mobile.tabs.dub", compact: true) }
                if copying { ProgressView().accessibilityLabel(model.t("mobile.ios.progress")) }
            }
            if !records.isEmpty || libraryOnly || projectID != nil {
                Text(model.t("mobile.dub.history")).font(.headline)
                Group {
                    ForEach(records.filter { !$0.checkpoint.done }) { record in
                        let progress = model.phone.activeDub == record.id ? model.phone.dubProgress : nil
                        let percent = progress?.percent ?? record.checkpoint.percent
                        DubJobCard(title: record.request.title, stage: model.t(progress?.stage ?? record.checkpoint.stage), fraction: Double(percent) / 100, running: model.phone.activeDub == record.id) {
                            if model.phone.activeDub == record.id {
                                Button(model.t("common.cancel"), systemImage: "xmark") { model.phone.cancelDub() }
                            } else {
                                Button(model.t("mobile.simple.resume"), systemImage: "play.fill") { model.phone.startDub(record.id) }.disabled(model.phone.activeDub != nil || model.busy)
                                Button(model.t("mobile.simple.remove_job"), systemImage: "trash", role: .destructive) { remove(record) }.labelStyle(.iconOnly)
                            }
                        }
                        PhoneJobStatusView(id: "dub:"+record.id.uuidString,requiresGPU: true,cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: model.phone.dubModels),onResume: { model.phone.resumeDub(record.id) })
                    }
                    if records.isEmpty { EmptyState(key: "mobile.simple.empty_dubs",icon: "film") }
                    ForEach(records) { record in
                        ForEach(record.checkpoint.outputs) { output in
                            HStack {
                                Image(systemName: "film").foregroundStyle(theme.color("messageAction")).frame(width: 32).accessibilityHidden(true)
                                VStack(alignment: .leading) { Text(record.request.title).lineLimit(1); Text(output.name).font(.caption).foregroundStyle(theme.color("textMuted")) }.frame(maxWidth: .infinity,alignment: .leading)
                                Button(model.t("common.play"),systemImage: "play.fill") { open(record,output,action: .play) }
                                Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { open(record,output,action: .share) }
                                Button(model.t("common.save"),systemImage: "arrow.down.to.line") { open(record,output,action: .save) }
                                ReportButton(subject: reportSubject(record,output), reporting: $reporting, context: record.request.title + " " + output.name)
                                if record.checkpoint.done { Button(model.t("mobile.dub_advanced.exports"),systemImage: "square.and.arrow.up.on.square") { exporting = ExportSelection(record: record,language: output.language) } }
                                if record.checkpoint.done { Button(model.t("clone.edit"),systemImage: "pencil") { editing = record } }
                                if record.checkpoint.done { Button(model.t("mobile.simple.remove_job"),systemImage: "trash",role: .destructive) { remove(record) } }
                            }.labelStyle(.iconOnly).studioButton(.tertiary)
                        }
                    }
                }
            }
        }
        .navigationTitle(projectID != nil ? (records.first?.request.title ?? model.t("projects.title")) : model.t(libraryOnly ? "projects.title" : batchMode ? "batch.title" : "mobile.tabs.dub")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if projectID == nil && !libraryOnly {
                DubStartButton(busy: copying || model.phone.activeDub != nil, action: start)
                    .disabled(!ready || !consent || (batchMode ? batchSources.isEmpty : source == nil) || targets.isEmpty || model.busy || copying || model.phone.activeDub != nil)
            }
        }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.movie,.audio],allowsMultipleSelection: batchMode) { result in
            switch result {
            case .success(let urls):
                if batchMode { batchSources += urls.filter { url in !batchSources.contains { $0.url == url } }.map { SelectedMedia(url: $0) } }
                else { source = urls.first.map { SelectedMedia(url: $0) } }
            case .failure: model.error = ClientError("mobile.simple.media_failed")
            }
        }
        .sheet(isPresented: $languages) {
            LanguageSelectionSheet(selection: targets, includesUnavailable: false) { targets = $0 }
        }
        .contentReportSheet($reporting)
        .sheet(item: $editing) { record in NavigationStack { DubEditorView(record: record) } }
        .sheet(item: $exporting) { selection in NavigationStack { PhoneDubExportView(record: selection.record,initialTarget: selection.language) } }
        .sheet(item: $shared) { file in ShareSheet(items: [file.url]) }
        .sheet(item: $saved) { file in SaveFileSheet(url: file.url) }
        .sheet(item: $playing) { file in PhoneDubPlayback(url: file.url) }
        .onChange(of: model.phone.voices) { _, voices in if !voices.contains(where: { $0.id.uuidString == voice }) { voice = "" } }
        .refreshable { await model.phone.refresh() }
        .onDisappear { creating?.cancel() }
    }
    private func phoneModel(_ task: String,_ capability: ModelCapability) -> some View {
        PhoneModelPicker(label: model.t("mobile.simple."+capability.rawValue),selection: Binding(get: { model.phone.selectedModel(task,capability)?.id ?? "" },set: { model.phone.chooseModel(task,$0) }), items: model.phone.candidates(capability), showCapability: true, showRamWarning: false)
    }
    private func start() {
        let sources = batchMode ? batchSources : source.map { [$0] } ?? []
        guard !sources.isEmpty else { return }
        let selectedModels = model.phone.dubModels, selectedTargets = choices.filter { targets.contains($0.code) }.map { PhoneDubTarget(code: $0.code,name: $0.name) }
        let chosenVoice = model.phone.voices.first { $0.id.uuidString == voice }, exportAudio = audioOnly
        copying = true
        creating = Task {
            defer { copying = false }
            await model.performPhone {
                guard let store = model.phone.dubStore else { throw ClientError("mobile.simple.checkpoint_failed") }
                let reference: PCMChunk?
                if let chosenVoice { reference = try await model.phone.voiceStore?.reference(chosenVoice) } else { reference = nil }
                for source in sources {
                    try Task.checkCancellation()
                    let request = try await store.create(source: source.url,title: source.name,targets: selectedTargets,models: selectedModels,consent: consent,reference: reference,referenceText: chosenVoice?.transcript,audioOnly: exportAudio)
                    if batchMode { batchSources.removeAll { $0.url == source.url } } else { self.source = nil }
                    await model.phone.refresh(); model.phone.startDub(request.id)
                }
                consent = false
            }
        }
    }
    private enum OutputAction { case play,share,save }
    private func open(_ record: PhoneDubRecord,_ output: PhoneDubOutput,action: OutputAction) {
        Task { await model.performPhone {
            guard let store = model.phone.dubStore else { throw ClientError("mobile.simple.media_failed") }
            let url = try await store.verifiedOutput(record.id,output)
            let file = SharedFile(url: url)
            switch action { case .play: playing = file; case .share: shared = file; case .save: saved = file }
        } }
    }
    /// The dub's speech comes from its pinned voice (clone) model.
    private func reportSubject(_ record: PhoneDubRecord,_ output: PhoneDubOutput) -> ContentReportSubject {
        let voice = phoneDubVoiceModel(pins: record.request.modelPins, in: model.phone.models)
        return .phone(.dub, id: record.id.uuidString + ":" + output.language, repo: voice?.repo, revision: voice?.revision)
    }
    private func remove(_ record: PhoneDubRecord) {
        Task { await model.performPhone { try await model.phone.dubStore?.delete(record.id); await model.phone.refresh() } }
    }
}
struct PhoneDubPlayback: View {
    @Environment(AppModel.self) private var model
    let url: URL
    @State private var player: AVPlayer?
    @MainActor static func startPlayback(url: URL) throws -> AVPlayer {
        try activatePlaybackSession()
        let player = AVPlayer(url: url)
        player.play()
        return player
    }
    var body: some View {
        VideoPlayer(player: player).onAppear {
            do { player = try Self.startPlayback(url: url) }
            catch { model.error = ClientError("mobile.errors.audio") }
        }.onDisappear { player?.pause(); player = nil }
    }
}
