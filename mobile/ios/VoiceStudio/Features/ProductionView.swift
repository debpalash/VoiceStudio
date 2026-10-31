import SwiftUI
import VoiceStudioKit
import UniformTypeIdentifiers

/// A voice selection is resolved from an immutable snapshot for the whole render.
struct ProductionVoiceRenderer: Sendable {
    let engine: any LocalEngine
    let store: PhoneVoiceStore
    let voices: [PhoneVoice]
    let tts: PhoneModel?
    let clone: PhoneModel?
    var configuration: String { [tts,clone].compactMap { $0 }.map { (try? phoneModelPin($0)) ?? "" }.joined(separator: "|") }
    func render(_ segment: ProductionSegment, _ selection: String) async throws -> LocalAudio {
        if selection.hasPrefix("phone:"), let voice = voices.first(where: { "phone:"+$0.id.uuidString == selection }), let clone {
            return try await engine.clone(model: clone,text: segment.text,reference: store.reference(voice),referenceText: voice.transcript,progress: { _ in })
        }
        guard selection.hasPrefix("preset:"), let speaker = Int(selection.dropFirst(7)), let tts else { throw ClientError("audiobook.blocked.voice") }
        return try await engine.synthesize(model: tts,text: segment.text,speaker: speaker,speed: 1,progress: { _ in })
    }
}

struct ProductionView: View {
    let kind: String
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var project = ProductionProject(kind: "stories",title: "")
    @State private var projects: [ProductionProject] = []
    @State private var exportFormat = "wav"
    @State private var exporting = false
    @State private var exportTask: Task<Void,Never>?
    @State private var word = ""
    @State private var spoken = ""
    private var jobID: String { "production:"+project.id.uuidString }
    private var resumeIdentity: String? { try? PhoneJobResumeIdentity.fingerprint(project,configuration: renderer?.configuration ?? "") }
    private var running: Bool { PhoneJobs.shared.states[jobID]?.running == true }
    private var progress: Double { PhoneJobs.shared.states[jobID]?.progress ?? 0 }
    @State private var output: URL?
    @State private var chapters: [URL] = []
    @State private var player = AudioPlayer()
    @State private var shared: SharedFile?
    @State private var reporting: ContentReportSubject?
    @State private var importing = false
    @State private var deleting: ProductionProject?
    private var titleKey: String { kind == "stories" ? "nav.stories" : "audiobook.title" }
    private var plan: ProductionPlan? { try? project.plan() }
    private var tts: PhoneModel? { model.phone.selectedModel("production-tts",.tts) }
    private var clone: PhoneModel? { model.phone.selectedModel("production-clone",.clone) }
    private var renderer: ProductionVoiceRenderer? {
        guard let engine = model.phone.engine, let store = model.phone.voiceStore else { return nil }
        return ProductionVoiceRenderer(engine: engine,store: store,voices: model.phone.voices,tts: tts,clone: clone)
    }
    private var needed: [PhoneModel] {
        let selections = plan?.segments.map { project.voice(for: $0.role) } ?? [project.defaultVoice]
        return [selections.contains(where: { $0.hasPrefix("preset:") }) ? tts : nil,
                selections.contains(where: { $0.hasPrefix("phone:") }) ? clone : nil].compactMap { $0 }
    }
    private var voiceOptions: [SelectionOption] {
        let count = min(128,Int(tts?.config["num_speakers"]?.number ?? 1))
        return (0..<count).map { SelectionOption(id: "preset:\($0)",title: model.t("mobile.local.preset_voice",["number": String($0+1)])) }
            + model.phone.voices.map { SelectionOption(id: "phone:"+$0.id.uuidString,title: $0.name) }
    }
    private var validVoices: Bool {
        plan?.segments.allSatisfy { segment in voiceOptions.contains { $0.id == project.voice(for: segment.role) } } == true
    }
    var body: some View {
        Page {
            Text(model.t("mobile.local.this_phone")).font(.caption).foregroundStyle(theme.color("textMuted"))
            editor.disabled(running)
            PhoneJobStatusView(id: jobID,requiresGPU: needed.contains { $0.runtime != .sherpa },cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: needed),resumeIdentity: resumeIdentity)
            if !needed.allSatisfy(model.phone.ready) { LocalDownloadPrompt(items: needed,feature: titleKey) }
            if running {
                ProgressView(value: progress)
                Button(model.t("audiobook.stop"),systemImage: "stop.fill") { PhoneJobs.shared.cancel(id: jobID) }.studioButton(.tertiary)
            }
            if let output {
                Card {
                    Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.performPhone { try await player.load(file: output); player.play() } } }.studioButton(.tertiary)
                    if player.file != nil { AudioPlayerView(player: player) }
                    ChoicePicker(label: model.t("audiobook.format"),selection: $exportFormat,options: [SelectionOption(id: "wav",title: "WAV"),SelectionOption(id: "m4a",title: "AAC / M4A"),SelectionOption(id: "mp3",title: "MP3"),SelectionOption(id: "m4b",title: model.t("audiobook.format_m4b"))])
                    Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { export(output) }.studioButton(.tertiary).disabled(exporting)
                    if exporting { ProgressView(); Button(model.t("common.cancel")) { exportTask?.cancel() }.studioButton(.tertiary) }
                    ReportButton(subject: reportSubject,reporting: $reporting)
                    ForEach(Array(chapters.enumerated()),id: \.offset) { index,url in
                        Button(model.t("audiobook.chapter_n",["n": String(index+1)]),systemImage: "square.and.arrow.up") { shared = SharedFile(url: url) }.studioButton(.tertiary)
                    }
                }
            }
            library
        }
        .navigationTitle(model.t(titleKey)).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("stories.generateAll"),systemImage: "waveform") { generate() }
                .studioButton(.primary).disabled(running || !validVoices || needed.isEmpty || !needed.allSatisfy(model.phone.ready) || project.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .task {
            await model.phone.refresh()
            if project.title.isEmpty { project = freshProject() }
            await reload()
        }
        .task(id: ProductionIdentity(project: project,configuration: renderer?.configuration ?? "")) {
            output = nil; chapters = []; player.clear()
            guard let renderer, !project.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            do {
                try await Task.sleep(for: .milliseconds(350))
                try await model.phone.productionStore?.save(project)
                output = try await model.phone.productionStore?.output(project,configuration: renderer.configuration)
                chapters = try await model.phone.productionStore?.chapterOutputs(project,configuration: renderer.configuration) ?? []
                await reload()
            } catch is CancellationError {} catch { model.error = ClientError("mobile.errors.file") }
        }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.plainText,.pdf,UTType(filenameExtension: "epub") ?? .data]) { result in
            Task { await model.performPhone {
                project.script = try await BookImport.read(result.get())
            } }
        }
        .task(id: PhoneJobs.shared.states[jobID]?.running) {
            guard let state = PhoneJobs.shared.states[jobID], !state.running else { return }
            if let error = state.error { model.error = error }
            guard let renderer, let file = state.result?.file else { return }
            output = file
            chapters = (try? await model.phone.productionStore?.chapterOutputs(project,configuration: renderer.configuration)) ?? []
            await reload()
        }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .contentReportSheet($reporting)
        .sheet(item: $deleting) { item in DeleteConfirmation(title: model.t("stories.deleteProjectConfirm")) {
            Task { await model.performPhone {
                try await model.phone.productionStore?.delete(item)
                if project.id == item.id { project = freshProject() }
                await reload()
            } }
        } }
        .onDisappear {
            exportTask?.cancel(); player.clear()
            let snapshot = project
            if !snapshot.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Task { await model.performPhone { try await model.phone.productionStore?.save(snapshot) } }
            }
        }
    }
    private var editor: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
            TextField(model.t("stories.projectName"),text: $project.title).accessibilityIdentifier("production.title")
            Text(model.t("mobile.production.script_hint")).font(.caption).foregroundStyle(theme.color("textMuted"))
            TextEditor(text: $project.script).frame(minHeight: 240).accessibilityLabel(model.t("audiobook.script")).accessibilityIdentifier("production.script")
            Button(model.t("audiobook.import"),systemImage: "doc") { importing = true }.studioButton(.tertiary)
            Text(model.t("mobile.books.controls_hint")).font(.caption).foregroundStyle(theme.color("textMuted"))
            HStack {
                Button(model.t("stories.insertPause")) { project.script += "\n[pause:0.5]\n" }
                Button(model.t("mobile.books.line_speed")) { project.script += "\n[speed:1.25] " }
            }
            ChoicePicker(label: model.t("stories.global_speed"),selection: Binding(get: { String(project.speed ?? 1) },set: { project.speed = Float($0) ?? 1 }),options: [Float(0.5),0.75,1,1.25,1.5,2].map { SelectionOption(id: String($0),title: String($0)+"×") })
            DisclosureGroup(model.t("pronunciation.title")) {
                TextField(model.t("audiobook.lex_word"),text: $word)
                TextField(model.t("audiobook.lex_say"),text: $spoken)
                Button(model.t("audiobook.lex_add")) {
                    var values = project.pronunciations ?? [:]; values[word.trimmingCharacters(in: .whitespacesAndNewlines)] = spoken.trimmingCharacters(in: .whitespacesAndNewlines); project.pronunciations = values; word = ""; spoken = ""
                }.disabled(word.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || spoken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || word.count > 100 || spoken.count > 500 || (project.pronunciations?.count ?? 0) >= 100)
                ForEach((project.pronunciations ?? [:]).keys.sorted(),id: \.self) { key in
                    HStack { Text(key+" → "+(project.pronunciations?[key] ?? "")); Spacer(); Button(model.t("common.delete"),systemImage: "trash",role: .destructive) { project.pronunciations?[key] = nil }.labelStyle(.iconOnly) }.frame(minHeight: 44)
                }
            }
            productionModel("production-tts",.tts)
            productionModel("production-clone",.clone)
            ChoicePicker(label: model.t("audiobook.default_voice"),selection: $project.defaultVoice,options: voiceOptions)
            ForEach(plan?.roles ?? [],id: \.self) { role in
                ChoicePicker(label: role,selection: Binding(get: { project.cast[role] ?? "" },set: { project.cast[role] = $0 }),options: [SelectionOption(id: "",title: model.t("stories.defaultVoice"))]+voiceOptions)
            }
        }
    }
    private var library: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
            HStack {
                Text(model.t("stories.projects")).font(.headline)
                Spacer()
                Button(model.t("mobile.production.new"),systemImage: "plus") { switchProject(freshProject()) }.disabled(running)
            }
            ForEach(projects.filter { $0.kind == kind }) { item in
                HStack {
                    Button(item.title) { switchProject(item) }.frame(maxWidth: .infinity,alignment: .leading).disabled(running)
                    Button(model.t("common.delete"),systemImage: "trash",role: .destructive) { deleting = item }.labelStyle(.iconOnly).disabled(running)
                }.frame(minHeight: 44)
            }
        }
    }
    private func productionModel(_ task: String, _ capability: ModelCapability) -> some View {
        PhoneModelPicker(label: model.t(capability == .tts ? "mobile.speak.engine" : "nav.clone"),selection: Binding(get: { model.phone.selectedModel(task,capability)?.id ?? "" },set: { model.phone.chooseModel(task,$0) }), items: model.phone.candidates(capability))
    }
    private func switchProject(_ next: ProductionProject) {
        let previous = project
        Task { await model.performPhone {
            if !previous.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { try await model.phone.productionStore?.save(previous) }
            project = next
        } }
    }
    private func freshProject() -> ProductionProject { ProductionProject(kind: kind,title: model.t(kind == "stories" ? "stories.untitled" : "audiobook.untitled")) }
    private func reload() async { do { projects = try await model.phone.productionStore?.list() ?? [] } catch { model.error = ClientError("mobile.errors.file") } }
    private struct ProductionIdentity: Equatable { let project: ProductionProject; let configuration: String }
    private var reportSubject: ContentReportSubject {
        let engine = needed.count == 1 ? needed.first : nil
        return .phone(.speak,id: output?.path ?? project.id.uuidString,repo: engine?.repo,revision: engine?.revision,typedText: project.script)
    }
    private func generate() {
        guard let renderer, let store = model.phone.productionStore,let resumeIdentity else { return }
        let snapshot = project
        output = nil; player.clear()
        PhoneJobs.shared.start(id: jobID,title: snapshot.title,requiresGPU: needed.contains { $0.runtime != .sherpa },cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: needed),resumeIdentity: resumeIdentity) { progress in
            let file = try await store.render(snapshot,configuration: renderer.configuration,progress: { value in await progress(EngineProgress(stage: .running,fraction: value)) },synthesize: renderer.render)
            return PhoneJobResult(file: file)
        }
    }
    private func export(_ wave: URL) {
        let format = exportFormat, snapshot = project, configuration = renderer?.configuration ?? ""
        exporting = true
        exportTask = Task {
            defer { exporting = false; exportTask = nil }
            await model.performPhone {
                if format == "wav" { shared = SharedFile(url: wave) }
                else {
                    let cues = try await model.phone.productionStore?.chapterCues(snapshot,configuration: configuration) ?? []
                    shared = SharedFile(url: try await BookExport.encode(wave: wave,format: format,chapters: cues))
                }
            }
        }
    }
}
