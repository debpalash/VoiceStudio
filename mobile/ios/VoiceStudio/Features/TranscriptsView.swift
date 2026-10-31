import SwiftUI
import UniformTypeIdentifiers
import VoiceStudioKit

struct TranscriptsView: View {
    @Environment(AppModel.self) private var model
    @State private var records: [PhoneTranscript] = []
    @State private var query = ""
    @State private var importing = false
    @State private var preparing = false
    @State private var prepareProgress = 0.0
    @State private var preparation: Task<Void,Never>?
    @State private var selected: PhoneTranscript?
    @State private var deleting: PhoneTranscript?
    @State private var shared: SharedFile?
    private var asr: PhoneModel? { model.phone.selectedModel("file-transcribe",.asr) }
    private var ready: Bool { asr.map(model.phone.ready) ?? false }
    private var visible: [PhoneTranscript] { records.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) || $0.text.localizedCaseInsensitiveContains(query) } }
    private var jobChanges: String { PhoneJobs.shared.states.filter { $0.key.hasPrefix("transcript:") }.map { $0.key+String($0.value.running)+String($0.value.progress) }.sorted().joined() }
    var body: some View {
        Page {
            Text(model.t("mobile.transcripts.hint")).font(.subheadline)
            PhoneModelPicker(label: model.t("mobile.simple.asr"),selection: Binding(get: { asr?.id ?? "" },set: { model.phone.chooseModel("file-transcribe",$0) }), items: model.phone.candidates(.asr)).disabled(preparing)
            if !ready { LocalDownloadPrompt(items: asr.map { [$0] } ?? [],feature: "transcriptions.title") }
            Button(model.t("mobile.dub.pick_media"),systemImage: "doc.badge.plus") { importing = true }.studioButton(.primary).disabled(!ready || preparing)
            if preparing { ProgressView(value: prepareProgress); Button(model.t("common.cancel")) { preparation?.cancel() }.studioButton(.tertiary) }
            NavigationLink(model.t("transcriptions.capture")) { DictateView() }
            SearchField(prompt: model.t("transcriptions.search_placeholder"),text: $query)
            if !records.isEmpty { Button(model.t("transcriptions.export_title"),systemImage: "square.and.arrow.up") { export(records,format: "txt") }.studioButton(.tertiary) }
            if visible.isEmpty { EmptyState(key: query.isEmpty ? "transcriptions.empty_title" : "transcriptions.empty_search_title",icon: "text.bubble") }
            ForEach(visible) { record in
                Card {
                    Text(record.title).font(.headline)
                    Text(Date(timeIntervalSince1970: record.createdAt/1000),style: .date).font(.caption)
                    Text(record.text).lineLimit(5).textSelection(.enabled)
                    let id = "transcript:"+record.id
                    let state = PhoneJobs.shared.states[id]
                    if !record.complete {
                        ProgressView(value: state?.running == true ? state?.progress ?? record.progress : record.progress)
                        if state?.running == true { Button(model.t("common.cancel")) { PhoneJobs.shared.cancel(id: id) }.studioButton(.tertiary) }
                        else { Button(model.t("common.resume")) { start(record) }.studioButton(.primary) }
                        if let error = state?.error { Text(model.t(error.key,error.params)).font(.caption) }
                        PhoneJobStatusView(id: id,requiresGPU: false,cpuFallbackSupported: true)
                    }
                    HStack {
                        Button(model.t("transcriptions.copy"),systemImage: "doc.on.doc") { UIPasteboard.general.string = record.text }.labelStyle(.iconOnly).studioButton(.tertiary)
                        Button(model.t("clone.edit"),systemImage: "pencil") { selected = record }.labelStyle(.iconOnly).studioButton(.tertiary).disabled(!record.complete)
                        Menu {
                            Button("TXT") { export([record],format: "txt") }
                            Button("JSON") { export([record],format: "json") }
                            Button("SRT") { export([record],format: "srt") }.disabled(record.segments.isEmpty || record.editedText != nil)
                            Button("VTT") { export([record],format: "vtt") }.disabled(record.segments.isEmpty || record.editedText != nil)
                        } label: { Label(model.t("transcriptions.export"),systemImage: "square.and.arrow.up") }.labelStyle(.iconOnly).studioButton(.tertiary)
                        Button(model.t("transcriptions.delete"),systemImage: "trash",role: .destructive) { deleting = record }.labelStyle(.iconOnly).studioButton(.tertiary).disabled(state?.running == true)
                    }
                }
            }
        }
        .navigationTitle(model.t("transcriptions.title")).navigationBarTitleDisplayMode(.inline)
        .task { await model.phone.refresh(); await refresh() }
        .task(id: jobChanges) { await refresh() }
        .refreshable { await refresh() }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.audio,.movie]) { result in if case .success(let url) = result { prepare(url) } else if case .failure = result { model.error = ClientError("mobile.errors.file") } }
        .sheet(item: $selected) { record in TranscriptEditor(record: record) { await refresh() } }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .alert(model.t("transcriptions.delete"),isPresented: Binding(get: { deleting != nil },set: { if !$0 { deleting = nil } })) {
            Button(model.t("common.cancel"),role: .cancel) { deleting = nil }
            Button(model.t("common.delete"),role: .destructive) { if let record = deleting { Task { await model.performPhone { try await model.phone.transcriptStore?.delete(record.id); await refresh() } } }; deleting = nil }
        } message: { Text(deleting?.title ?? "") }
        .onDisappear { preparation?.cancel() }
    }
    private func refresh() async { await model.performPhone { records = try await model.phone.transcriptStore?.list() ?? [] } }
    private func prepare(_ source: URL) {
        guard let asr,ready,let store = model.phone.transcriptStore else { return }
        preparing = true; prepareProgress = 0
        preparation = Task {
            defer { preparing = false; preparation = nil }
            await model.performPhone {
                let record = try await store.prepare(source: source,title: source.lastPathComponent,model: asr,progress: { value in await MainActor.run { prepareProgress = value } })
                await refresh(); start(record)
            }
        }
    }
    private func start(_ record: PhoneTranscript) {
        guard let store = model.phone.transcriptStore,let engine = model.phone.engine,
              let asr = model.phone.models.first(where: { $0.id == record.modelID }),model.phone.ready(asr) else { model.error = ClientError("mobile.local.model_required"); return }
        PhoneJobs.shared.start(id: "transcript:"+record.id,title: record.title,requiresGPU: false,cpuFallbackSupported: true) { report in
            let result = try await store.run(record.id,model: asr,progress: { value in await report(EngineProgress(stage: .running,fraction: value)) }) { audio in
                try await engine.transcribeSegments(model: asr,audio: audio,progress: { _ in })
            }
            return PhoneJobResult(file: nil,text: result.text)
        }
    }
    private func export(_ records: [PhoneTranscript],format: String) {
        Task { await model.performPhone {
            let data: Data
            if format == "json" { let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted,.sortedKeys]; data = try encoder.encode(records) }
            else if format == "srt" || format == "vtt" { guard let record = records.first else { return }; data = Data(try PhoneTranscriptExport.subtitles(record,vtt: format == "vtt").utf8) }
            else { data = Data(PhoneTranscriptExport.text(records).utf8) }
            guard data.count <= 64*1024*1024 else { throw ClientError("mobile.errors.file") }
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("transcript-export-"+UUID().uuidString)
            try PrivateFiles.directory(directory); let file = directory.appendingPathComponent("transcripts."+format)
            try data.write(to: file,options: .atomic); shared = SharedFile(url: file)
        } }
    }
}
private struct TranscriptEditor: View {
    let record: PhoneTranscript
    let changed: @MainActor () async -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    var body: some View {
        NavigationStack {
            Page {
                Text(record.title).font(.headline)
                Text(model.t("mobile.transcripts.edited_hint")).font(.caption)
                TextEditor(text: $text).frame(minHeight: 240).accessibilityLabel(model.t("transcriptions.title"))
                DisclosureGroup(model.t("transcriptions.original_transcript")) { Text(record.originalText).textSelection(.enabled) }
                Button(model.t("transcriptions.original_transcript")) { text = record.originalText }.studioButton(.tertiary)
                Button(model.t("common.save")) { Task { await model.performPhone { try await model.phone.transcriptStore?.edit(record.id,text: text == record.originalText ? nil : text); await changed(); dismiss() } } }.studioButton(.primary).disabled(text.utf16.count > 1_000_000)
            }.navigationTitle(model.t("clone.edit"))
                .toolbar { Button(model.t("common.cancel")) { dismiss() } }
        }.onAppear { text = record.text }
    }
}
