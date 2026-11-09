import SwiftUI
import VoiceStudioKit

struct ComputerDubAdvancedView: View {
    let jobID: String
    let title: String
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var tracks: [String:ComputerDubTrack] = [:]
    @State private var options = ComputerDubExport()
    @State private var analysis: ComputerDubAnalysis?
    @State private var cast: [String:String] = [:]
    @State private var speakers = 0
    @State private var reviewed = false
    @State private var timing = "concise"
    @State private var pendingTranslation: TranslationEngine?
    @State private var taskID: String?
    @State private var api: APIClient?
    @State private var busy = false
    @State private var work: Task<Void,Never>?
    @State private var shared: SharedFile?
    @State private var workspace = FileManager.default.temporaryDirectory.appendingPathComponent("computer-dub-"+UUID().uuidString)
    @State private var leaving = false
    private var receipt: String { "advancedDubTask:"+(api?.address.baseURL.absoluteString ?? "")+":"+jobID }
    private var groups: [String] { Array(Set(analysis?.segments.compactMap(\.speaker_id) ?? [])).sorted() }
    var body: some View {
        Page {
            Text(title).font(.headline)
            Text(model.t("mobile.dub_advanced.computer_hint")).font(.caption)
            if !tracks.isEmpty {
                exportControls
                Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { download() }.studioButton(.primary).disabled(busy || options.language.isEmpty)
            }
            Card {
                Text(model.t("dub.cast")).font(.headline)
                Text(model.t("mobile.dub_advanced.detect_hint")).font(.caption)
                Stepper(model.t("dub.num_speakers_label")+": "+(speakers == 0 ? model.t("dub.num_speakers_auto") : String(speakers)),value: $speakers,in: 0...20).disabled(busy || taskID != nil)
                Button(model.t("mobile.dub_advanced.detect"),systemImage: "person.2.wave.2") { detect() }.studioButton(.tertiary).disabled(busy || taskID != nil)
                if let analysis {
                    ForEach(Array(analysis.warnings.enumerated()),id: \.offset) { _,warning in Text(warning).font(.caption).textSelection(.enabled) }
                    Text(model.t("mobile.dub_advanced.review_cast")).font(.caption)
                    ForEach(groups,id: \.self) { speaker in
                        ChoicePicker(label: speaker,selection: Binding(get: { cast[speaker] ?? "" },set: { cast[speaker] = $0 }),options: [SelectionOption(id: "",title: model.t("stories.defaultVoice"))]+(analysis.sources[speaker] == nil ? [] : [SelectionOption(id: ComputerDubAnalysis.sourceVoiceID(speaker),title: model.t("mobile.dub_advanced.source_voice"))])+model.profiles.map { SelectionOption(id: $0.id,title: $0.name) }).disabled(busy || taskID != nil)
                    }
                    Toggle(model.t("mobile.dub_advanced.cast_checked"),isOn: $reviewed).frame(minHeight: 44).disabled(busy || taskID != nil)
                    ChoicePicker(label: model.t("mobile.dub_advanced.timing"),selection: $timing,options: [SelectionOption(id: "concise",title: model.t("dub.timing_concise")),SelectionOption(id: "smart_fit",title: model.t("dub.timing_smart_fit")),SelectionOption(id: "stretch_video",title: model.t("dub.timing_stretch_video"))]).disabled(busy || taskID != nil)
                    Button(model.t("dub.generate_dub"),systemImage: "waveform") { authorizeRender() }.studioButton(.primary).disabled(busy || !reviewed || options.language.isEmpty || taskID != nil)
                }
                if taskID != nil && !busy {
                    Button(model.t("mobile.simple.resume"),systemImage: "play") { resume() }.studioButton(.tertiary)
                }
                if let taskID { Button(model.t("mobile.dub.abort"),systemImage: "stop") { cancel(taskID) }.studioButton(.tertiary) }
            }
            if busy { ProgressView(); if taskID == nil { Button(model.t("common.cancel"),systemImage: "stop") { work?.cancel() }.studioButton(.tertiary) } }
        }
        .navigationTitle(model.t("mobile.dub_advanced.exports")).navigationBarTitleDisplayMode(.inline)
        .task {
            leaving = false
            do { let client = try model.api(); api = client; taskID = UserDefaults.standard.string(forKey: receipt); tracks = try await client.computerDubTracks(jobID).tracks
                let codes = tracks.keys.sorted(); options.language = codes.first ?? ""; options.tracks = codes
            } catch { model.error = (error as? ClientError) ?? ClientError("mobile.errors.dub_failed") }
        }
        .onChange(of: model.connection.active?.id) { _,_ in work?.cancel(); dismiss() }
        .onDisappear { leaving = true; if let work { work.cancel() } else { try? FileManager.default.removeItem(at: workspace) } }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .alert(model.t("mobile.dub.translation_online_title"),isPresented: Binding(get: { pendingTranslation != nil },set: { if !$0 { pendingTranslation = nil } })) {
            Button(model.t("mobile.dub.translation_allow")) { if let provider = pendingTranslation { pendingTranslation = nil; render(provider,confirmed: true) } }
            Button(model.t("mobile.dub.translation_decline"),role: .cancel) { pendingTranslation = nil }
        } message: { Text(model.t("mobile.dub.translation_online_body",["service":pendingTranslation?.display_name ?? ""])) }
    }
    private var exportControls: some View {
        Card {
            ChoicePicker(label: model.t("mobile.dub_advanced.exports"),selection: Binding(get: { options.kind.rawValue },set: { options.kind = ComputerDubExport.Kind(rawValue: $0) ?? .media }),options: ComputerDubExport.Kind.allCases.map { kind in SelectionOption(id: kind.rawValue,title: model.t(kindKey(kind))) })
            ChoicePicker(label: model.t("exportModal.default_audio_track"),selection: Binding(get: { options.language },set: { options.language = $0; if !options.tracks.contains($0) { options.tracks.append($0) } }),options: tracks.keys.sorted().map { SelectionOption(id: $0,title: tracks[$0]?.language ?? $0) })
            if options.kind == .media {
                ForEach(tracks.keys.sorted(),id: \.self) { code in
                    Toggle(tracks[code]?.language ?? code,isOn: Binding(get: { options.tracks.contains(code) },set: { include in
                        options.tracks.removeAll { $0 == code }; if include { options.tracks.append(code) }
                        if !options.tracks.contains(options.language) { options.language = options.tracks.sorted().first ?? "" }
                    })).frame(minHeight: 44)
                }
                Toggle(model.t("exportModal.original"),isOn: $options.original).frame(minHeight: 44)
                Toggle(model.t("batch.preserve_bg"),isOn: $options.preserveBackground).frame(minHeight: 44)
                Text(model.t("mobile.dub_advanced.background_hint")).font(.caption)
                Toggle(model.t("dub.burn_subs"),isOn: $options.burn).frame(minHeight: 44)
                if options.burn {
                    Toggle(model.t("exportModal.dual_subs"),isOn: Binding(get: { options.dual },set: { options.dual = $0; if $0 { options.karaoke = false } })).frame(minHeight: 44)
                    Toggle(model.t("exportModal.subs_style_karaoke"),isOn: Binding(get: { options.karaoke },set: { options.karaoke = $0; if $0 { options.dual = false } })).frame(minHeight: 44)
                }
                ChoicePicker(label: model.t("audiobook.format"),selection: $options.audioFormat,options: [SelectionOption(id: "m4a",title: "AAC / M4A"),SelectionOption(id: "wav",title: "WAV"),SelectionOption(id: "mp3",title: "MP3"),SelectionOption(id: "flac",title: "FLAC")])
            } else if options.kind == .srt || options.kind == .vtt { Toggle(model.t("exportModal.dual_subs"),isOn: $options.dual).frame(minHeight: 44) }
        }.disabled(busy)
    }
    private func kindKey(_ kind: ComputerDubExport.Kind) -> String {
        switch kind { case .media: "mobile.dub_advanced.multi_audio"; case .srt: "mobile.editing.export_srt"; case .vtt: "mobile.editing.export_vtt"; case .ass: "mobile.dub_advanced.ass"; case .clips: "mobile.dub_advanced.clips"; case .stems: "mobile.dub_advanced.stems" }
    }
    private func perform(_ action: @escaping @MainActor () async throws -> Void) {
        guard !busy else { return }; busy = true
        work = Task { defer { busy = false; work = nil; if leaving { try? FileManager.default.removeItem(at: workspace) } }; await model.perform { try await action() } }
    }
    private func detect() { guard let api else { return }; perform { analysis = try await api.computerDubAnalysis(jobID,speakers: speakers); reviewed = false; cast = [:] } }
    private func authorizeRender() {
        guard let api else { return }
        perform { let provider = try await api.translationEngineForJob(); if provider.requiresConfirmation { pendingTranslation = provider } else { try await startRender(api,provider: provider,confirmed: false) } }
    }
    private func render(_ provider: TranslationEngine,confirmed: Bool) {
        guard let api else { return }; perform { try await startRender(api,provider: provider,confirmed: confirmed) }
    }
    private func startRender(_ api: APIClient,provider: TranslationEngine,confirmed: Bool) async throws {
        guard let analysis else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        let target = options.language, voices = cast, strategy = timing
        let task = try await api.computerDubCast(jobID,analysis: analysis,language: target,name: model.languages.language(target)?.name ?? target,voices: voices,provider: provider,onlineConfirmed: confirmed,timing: strategy)
        taskID = task.task_id; UserDefaults.standard.set(task.task_id,forKey: receipt); try await monitor(api,task.task_id)
    }
    private func monitor(_ api: APIClient,_ task: String) async throws {
        var sequence = 0
        while !Task.isCancelled {
            let page = try await api.events(task,after: sequence)
            var failed = ["failed","cancelled"].contains(page.job.status)
            for event in page.events { sequence = max(sequence,event.seq); if let data = event.payload.replacingOccurrences(of: "data: ",with: "").trimmingCharacters(in: .whitespacesAndNewlines).data(using: .utf8), let object = (try? JSONSerialization.jsonObject(with: data)) as? [String:Any], ["error","failed","cancelled"].contains(object["type"] as? String ?? "") { failed = true } }
            if failed { taskID = nil; UserDefaults.standard.removeObject(forKey: receipt); throw ClientError("mobile.errors.dub_failed") }
            if page.job.status == "done" { taskID = nil; UserDefaults.standard.removeObject(forKey: receipt); tracks = try await api.computerDubTracks(jobID).tracks; return }
            try await Task.sleep(for: .seconds(2))
        }
        throw CancellationError()
    }
    private func resume() { guard let api, let taskID else { return }; perform { try await monitor(api,taskID) } }
    private func cancel(_ task: String) {
        guard let api else { return }; work?.cancel()
        Task { await model.perform { try await api.cancelComputerDubTask(task); taskID = nil; UserDefaults.standard.removeObject(forKey: receipt) } }
    }
    private func download() {
        guard let api else { return }; let snapshot = options
        perform {
            let reply = try await api.download(api.request(snapshot.path(job: jobID))); defer { try? FileManager.default.removeItem(at: reply.file) }
            let ext: String
            switch snapshot.kind { case .srt: ext = "srt"; case .vtt: ext = "vtt"; case .ass: ext = "ass"; case .clips,.stems: ext = "zip"; case .media:
                let type = reply.headers["content-type"] ?? ""
                ext = type.contains("matroska") ? "mkv" : type.contains("video/") ? "mp4" : type.contains("mpeg") ? "mp3" : type.contains("wav") ? "wav" : type.contains("flac") ? "flac" : "m4a"
            }
            try PrivateFiles.directory(workspace); let file = workspace.appendingPathComponent(UUID().uuidString).appendingPathExtension(ext)
            try FileManager.default.moveItem(at: reply.file,to: file); try Task.checkCancellation(); shared = SharedFile(url: file)
        }
    }
}
