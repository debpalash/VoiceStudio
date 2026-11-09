import SwiftUI
import VoiceStudioKit
import UniformTypeIdentifiers

struct VoiceToolsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.scenePhase) private var phase
    @State private var recorder = Recorder()
    @State private var recordingTask: Task<Void,Never>?
    @State private var recordingConsent = false
    @State private var mode = "convert"
    @State private var source: SelectedMedia?
    @State private var importing = false
    @State private var voice = ""
    @State private var voiceA = "preset:0"
    @State private var voiceB = "preset:1"
    @State private var text = ""
    @State private var transcript = ""
    @State private var matchDuration = true
    @State private var results: [ToolResult] = []
    @State private var shared: SharedFile?
    @State private var reporting: ContentReportSubject?
    @State private var running = false
    @State private var progress = 0.0
    @State private var work: Task<Void,Never>?
    @State private var player = AudioPlayer()
    private var tts: PhoneModel? { model.phone.selectedModel("production-tts",.tts) }
    private var clone: PhoneModel? { model.phone.selectedModel("production-clone",.clone) }
    private var asr: PhoneModel? { model.phone.selectedModel("convert-asr",.asr) }
    private var choices: [SelectionOption] {
        (0..<min(128,Int(tts?.config["num_speakers"]?.number ?? 1))).map { SelectionOption(id: "preset:\($0)",title: model.t("mobile.local.preset_voice",["number": String($0+1)])) }
            + model.phone.voices.map { SelectionOption(id: "phone:"+$0.id.uuidString,title: $0.name) }
    }
    private var needed: [PhoneModel] {
        if mode == "convert" { return [asr,clone].compactMap { $0 } }
        return [[voiceA,voiceB].contains(where: { $0.hasPrefix("preset:") }) ? tts : nil,
                [voiceA,voiceB].contains(where: { $0.hasPrefix("phone:") }) ? clone : nil].compactMap { $0 }
    }
    private var ready: Bool {
        guard !running, !recorder.recording, recordingTask == nil, needed.allSatisfy(model.phone.ready) else { return false }
        if mode == "convert" { return source != nil && model.phone.voices.contains { $0.id.uuidString == voice } && asr != nil && clone != nil }
        return !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.count <= 2000 && [voiceA,voiceB].allSatisfy { selection in choices.contains { $0.id == selection } }
    }
    var body: some View {
        Page {
            NavigationLink(model.t("mobile.processing.audio_editor")) { AudioEditorView() }.disabled(running || recorder.recording || recordingTask != nil)
            ChoicePicker(label: model.t("tools.title"),selection: $mode,options: [SelectionOption(id: "convert",title: model.t("convert.convert")),SelectionOption(id: "compare",title: model.t("compare.title"))]).disabled(running || recorder.recording || recordingTask != nil)
            Text(model.t("mobile.local.this_phone")).font(.caption).foregroundStyle(theme.color("textMuted"))
            VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
                if mode == "convert" {
                    Text(model.t("mobile.production.conversion_hint")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                    Button(source?.name ?? model.t("convert.source_kicker"),systemImage: "waveform") { importing = true }.studioButton(.tertiary).disabled(recorder.recording || recordingTask != nil)
                    Toggle(model.t("mobile.processing.record_consent"),isOn: $recordingConsent).frame(minHeight: 44).disabled(recorder.recording || recordingTask != nil)
                    Button(model.t(recorder.recording ? "common.stop" : "mobile.voices.record"),systemImage: recorder.recording ? "stop.fill" : "mic.fill") {
                        recordingTask = Task { defer { recordingTask = nil }; await model.performPhone {
                            if recorder.recording { recorder.stop(); source = recorder.file.map { SelectedMedia(url: $0) } }
                            else { if let old = recorder.file { try? FileManager.default.removeItem(at: old) }; source = nil; try await recorder.start() }
                        } }
                    }.studioButton(.tertiary).disabled((!recordingConsent && !recorder.recording) || recordingTask != nil)
                    if recorder.recording { Text(model.t("mobile.voices.recording",["seconds": String(recorder.seconds)])) }
                    ChoicePicker(label: model.t("convert.target_voice"),selection: $voice,options: model.phone.voices.map { SelectionOption(id: $0.id.uuidString,title: $0.name) })
                    if model.phone.voices.isEmpty { NavigationLink(model.t("mobile.voices.new_clone")) { VoicesView() } }
                    Toggle(model.t("convert.match_duration"),isOn: $matchDuration).frame(minHeight: 44)
                    modelPicker("convert-asr",.asr)
                } else {
                    TextField(model.t("compare.test_phrase"),text: $text,axis: .vertical).lineLimit(4...8).accessibilityIdentifier("tools.text")
                    ChoicePicker(label: model.t("compare.voice_a"),selection: $voiceA,options: choices)
                    ChoicePicker(label: model.t("compare.voice_b"),selection: $voiceB,options: choices)
                    modelPicker("production-tts",.tts)
                }
                modelPicker("production-clone",.clone)
            }.disabled(running)
            if !needed.allSatisfy(model.phone.ready) { LocalDownloadPrompt(items: needed,feature: "tools.title") }
            if running {
                ProgressView(value: progress)
                Button(model.t("common.cancel"),systemImage: "stop") { work?.cancel() }.studioButton(.tertiary)
            }
            if !transcript.isEmpty { Card { Text(model.t("convert.transcript")).font(.headline); Text(transcript) } }
            ForEach(results) { result in
                Card {
                    Text(result.title).font(.headline)
                    Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.performPhone { try await player.load(file: result.file); player.play() } } }.studioButton(.tertiary)
                    Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { shared = SharedFile(url: result.file) }.studioButton(.tertiary)
                    ReportButton(subject: result.subject,reporting: $reporting)
                }
            }
            if player.file != nil { AudioPlayerView(player: player) }
        }
        .navigationTitle(model.t("tools.title")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t(mode == "convert" ? "convert.convert" : "compare.compare_btn"),systemImage: "waveform") { start() }
                .studioButton(.primary).disabled(!ready).padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .task { await model.phone.refresh() }
        .onChange(of: mode) { _,_ in player.clear(); results = []; transcript = "" }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.audio]) { result in
            switch result { case .success(let url): source = SelectedMedia(url: url); case .failure: model.error = ClientError("mobile.errors.file") }
        }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .contentReportSheet($reporting)
        .onChange(of: recorder.seconds) { _,seconds in if seconds >= 29 && recorder.recording { recorder.stop(); source = recorder.file.map { SelectedMedia(url: $0) } } }
        .onChange(of: phase) { _,value in if value != .active {
            recordingTask?.cancel()
            if recorder.recording { recorder.stop(); if let file = recorder.file { try? FileManager.default.removeItem(at: file) }; source = nil }
        } }
        .onDisappear { work?.cancel(); recordingTask?.cancel(); recorder.stop(); if let file = recorder.file { try? FileManager.default.removeItem(at: file) }; player.clear() }
    }
    private func modelPicker(_ task: String,_ capability: ModelCapability) -> some View {
        PhoneModelPicker(label: model.t(capability == .asr ? "nav.transcribe" : capability == .clone ? "nav.clone" : "mobile.speak.engine"),selection: Binding(get: { model.phone.selectedModel(task,capability)?.id ?? "" },set: { model.phone.chooseModel(task,$0) }), items: model.phone.candidates(capability))
    }
    private func start() {
        guard let engine = model.phone.engine, let voices = model.phone.voiceStore else { return }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("voice-tools-"+UUID().uuidString)
        running = true; progress = 0; results = []; transcript = ""; player.clear()
        work = Task {
            defer { running = false; work = nil }
            await model.performPhone {
                try PrivateFiles.directory(directory)
                if mode == "convert", let source, let asr, let clone, let target = model.phone.voices.first(where: { $0.id.uuidString == voice }) {
                    let result = try await VoiceConversion.run(source: source.url,workspace: directory,voice: target,voices: voices,asr: asr,clone: clone,engine: engine,decoder: AVPhoneAudioDecoder(),matchDuration: matchDuration,progress: { value in await MainActor.run { progress = value.fraction } })
                    let file = directory.appendingPathComponent("converted.wav")
                    try result.audio.markSynthetic().write(to: file,options: .atomic)
                    transcript = result.transcript
                    results = [ToolResult(title: model.t("convert.result_kicker"),file: file,subject: .phone(.speak,id: file.lastPathComponent+directory.lastPathComponent,repo: clone.repo,revision: clone.revision))]
                } else {
                    let snapshot = text
                    let renderer = ProductionVoiceRenderer(engine: engine,store: voices,voices: model.phone.voices,tts: tts,clone: clone)
                    for (index,selection) in [voiceA,voiceB].enumerated() {
                        let audio = try await renderer.render(ProductionSegment(text: snapshot),selection)
                        try Task.checkCancellation()
                        let file = directory.appendingPathComponent("\(index).wav")
                        try audio.markSynthetic().write(to: file,options: .atomic)
                        let producer = selection.hasPrefix("phone:") ? clone : tts
                        results.append(ToolResult(title: model.t(index == 0 ? "compare.voice_a" : "compare.voice_b"),file: file,subject: .phone(.speak,id: directory.lastPathComponent+String(index),repo: producer?.repo,revision: producer?.revision,typedText: snapshot)))
                        progress = Double(index+1)/2
                    }
                }
            }
        }
    }
}

private struct ToolResult: Identifiable {
    var id: URL { file }
    let title: String
    let file: URL
    let subject: ContentReportSubject
}
