import SwiftUI
import UniformTypeIdentifiers
import VoiceStudioKit

struct AudioEditorView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var document: PhoneAudioDocument?
    @State private var sourceName = ""
    @State private var options = PhoneAudioEditOptions()
    @State private var renderedOptions: PhoneAudioEditOptions?
    @State private var result: PhoneAudioEditResult?
    @State private var importing = false
    @State private var running = false
    @State private var leaving = false
    @State private var progress = 0.0
    @State private var work: Task<Void,Never>?
    @State private var format = "wav"
    @State private var shared: SharedFile?
    @State private var player = AudioPlayer()
    @State private var workspace = FileManager.default.temporaryDirectory.appendingPathComponent("audio-editor-"+UUID().uuidString)
    var body: some View {
        Page {
            Text(model.t("mobile.processing.hint")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
            Button(model.t("mobile.processing.select_audio"),systemImage: "doc.badge.plus") { importing = true }.studioButton(.tertiary).disabled(running)
            if let document {
                Text(sourceName).font(.headline)
                Canvas { context,size in
                    var path = Path()
                    for (index,value) in document.peaks.enumerated() {
                        let x = size.width*Double(index)/Double(max(1,document.peaks.count)), height = size.height*Double(min(1,value))/2
                        path.move(to: CGPoint(x: x,y: size.height/2-height)); path.addLine(to: CGPoint(x: x,y: size.height/2+height))
                    }
                    context.stroke(path,with: .color(theme.color("messageAction")))
                }.frame(height: VSTokens.textEditor.lineHeight*2).accessibilityLabel(model.t("mobile.editing.waveform"))
                Card {
                    HStack {
                        numberField("dubWorkspace.start",value: $options.start)
                        numberField("dubWorkspace.end",value: Binding(get: { options.end ?? document.duration },set: { options.end = $0 }))
                    }
                    HStack { numberField("mobile.processing.fade_in",value: $options.fadeIn); numberField("mobile.processing.fade_out",value: $options.fadeOut) }
                    Text(model.t("mobile.processing.gain_db")+" "+String(format: "%.1f",options.gainDB)).monospacedDigit().font(.caption)
                    Slider(value: $options.gainDB,in: -24...24).accessibilityLabel(model.t("mobile.processing.gain_db"))
                    Toggle(model.t("mobile.processing.normalize"),isOn: $options.normalize).frame(minHeight: 44)
                    if options.normalize {
                        Text(model.t("mobile.processing.target_peak")+" "+String(format: "%.1f",options.targetDB)).font(.caption).monospacedDigit()
                        Slider(value: $options.targetDB,in: -24...(-1)).accessibilityLabel(model.t("mobile.processing.target_peak"))
                    }
                    Toggle(model.t("mobile.processing.noise_gate"),isOn: $options.noiseGate).frame(minHeight: 44)
                    if options.noiseGate {
                        Text(model.t("mobile.processing.gate_threshold")+" "+String(format: "%.1f",options.gateDB)).font(.caption).monospacedDigit()
                        Slider(value: $options.gateDB,in: -80...(-10)).accessibilityLabel(model.t("mobile.processing.gate_threshold"))
                    }
                }.disabled(running)
                Button(model.t("mobile.editing.original"),systemImage: "play.circle") { originalPreview(document) }.studioButton(.tertiary).disabled(running)
                Text(model.t("mobile.processing.preview_hint")).font(.caption).foregroundStyle(theme.color("textMuted"))
            }
            if running { ProgressView(value: progress); Button(model.t("common.cancel"),systemImage: "stop") { work?.cancel() }.studioButton(.tertiary) }
            if let result {
                Card {
                    Text(model.t("mobile.processing.result")).font(.headline)
                    Text(model.t("mobile.processing.peak")+" "+String(format: "%.1f dBFS",20*log10(max(0.000001,result.peak)))).font(.caption).monospacedDigit()
                    if renderedOptions != options { Text(model.t("mobile.processing.changed_settings")).font(.caption).foregroundStyle(theme.color("textMuted")) }
                    Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.performPhone { try await player.load(file: result.file); player.play() } } }.studioButton(.tertiary).disabled(running)
                    ChoicePicker(label: model.t("audiobook.format"),selection: $format,options: [SelectionOption(id: "wav",title: "WAV"),SelectionOption(id: "m4a",title: "AAC / M4A"),SelectionOption(id: "mp3",title: "MP3")]).disabled(running)
                    Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { share(result) }.studioButton(.tertiary).disabled(running || renderedOptions != options)
                }
            }
            if player.file != nil { AudioPlayerView(player: player) }
        }
        .navigationTitle(model.t("mobile.processing.audio_editor")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("mobile.processing.process"),systemImage: "waveform") { process() }.studioButton(.primary).disabled(document == nil || running).padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.audio]) { reply in
            if case .success(let url) = reply { open(url) } else { model.error = ClientError("mobile.errors.file") }
        }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .onChange(of: options) { _,_ in player.clear() }
        .onAppear { leaving = false }
        .onDisappear { leaving = true; player.clear(); if let work { work.cancel() } else { try? FileManager.default.removeItem(at: workspace) } }
    }
    private func numberField(_ key: String,value: Binding<Double>) -> some View {
        VStack(alignment: .leading) { Text(model.t(key)).font(.caption); TextField(model.t(key),value: value,format: .number).keyboardType(.decimalPad).accessibilityLabel(model.t(key)) }
    }
    private func perform(_ action: @escaping @MainActor () async throws -> Void) {
        running = true; progress = 0
        work = Task {
            defer { running = false; work = nil; if leaving { try? FileManager.default.removeItem(at: workspace) } }
            await model.performPhone { try await action() }
        }
    }
    private func open(_ source: URL) {
        perform {
            let fresh = try await PhoneAudioProcessing.open(source: source,workspace: workspace,progress: { value in await MainActor.run { progress = value } })
            if let document { try? FileManager.default.removeItem(at: document.pcm.url) }
            if let result { try? FileManager.default.removeItem(at: result.file.deletingLastPathComponent()) }
            document = fresh; sourceName = source.lastPathComponent; result = nil; renderedOptions = nil; options = PhoneAudioEditOptions(); player.clear()
        }
    }
    private func process() {
        guard let document else { return }; let snapshot = options
        perform {
            let fresh = try await PhoneAudioProcessing.process(document,options: snapshot,workspace: workspace,progress: { value in await MainActor.run { progress = value } })
            player.clear(); if let result { try? FileManager.default.removeItem(at: result.file.deletingLastPathComponent()) }
            result = fresh; renderedOptions = snapshot
            try await player.load(file: fresh.file)
        }
    }
    private func originalPreview(_ document: PhoneAudioDocument) {
        var snapshot = PhoneAudioEditOptions(); snapshot.start = options.start; snapshot.end = min(options.end ?? document.duration,options.start+30)
        perform {
            let preview = try await PhoneAudioProcessing.process(document,options: snapshot,workspace: workspace)
            defer { try? FileManager.default.removeItem(at: preview.file.deletingLastPathComponent()) }
            try await player.load(file: preview.file); player.play()
        }
    }
    private func share(_ result: PhoneAudioEditResult) {
        let selected = format
        perform { let file = try await BookExport.encode(wave: result.file,format: selected,synthetic: result.synthetic); shared = SharedFile(url: file) }
    }
}
