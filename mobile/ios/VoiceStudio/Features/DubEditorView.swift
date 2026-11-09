import SwiftUI
import UniformTypeIdentifiers
import VoiceStudioKit

private struct DubEditorSnapshot {
    var segments: [PhoneDubSegment]
    var voices: [String:String]
}
struct DubEditorView: View {
    let record: PhoneDubRecord
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var segments: [PhoneDubSegment] = []
    @State private var target = ""
    @State private var voices: [String:String] = [:]
    @State private var undo: [DubEditorSnapshot] = []
    @State private var redo: [DubEditorSnapshot] = []
    @State private var mutationKey: String?
    @State private var splitPositions: [String:Double] = [:]
    @State private var saving = false
    @State private var importing = false
    @State private var pendingImport: DubEditorSnapshot?
    @State private var exported: SharedFile?
    @State private var player = AudioPlayer()
    @State private var previewLabel = ""
    @State private var previewSegmentID: String?
    @State private var previewing = false
    @State private var replaceCastOverrides = false
    var body: some View {
        Page {
            ChoicePicker(label: model.t("mobile.speak.language"),selection: $target,options: record.request.targets.map { SelectionOption(id: $0.code,title: $0.name) })
            HStack {
                Button(model.t("mobile.editing.undo"),systemImage: "arrow.uturn.backward") { restore(backward: true) }.disabled(undo.isEmpty)
                Button(model.t("mobile.editing.redo"),systemImage: "arrow.uturn.forward") { restore(backward: false) }.disabled(redo.isEmpty)
                Spacer(minLength: 0)
                Menu {
                    Button(model.t("mobile.editing.import_subtitles")) { importing = true }
                    Button(model.t("mobile.editing.export_srt")) { export(vtt: false) }
                    Button(model.t("mobile.editing.export_vtt")) { export(vtt: true) }
                } label: { Label(model.t("mobile.editing.subtitles"),systemImage: "captions.bubble") }
            }.buttonStyle(.borderless).disabled(saving)
            castPanel
            LazyVStack(spacing: VSTokens.spacingGap) {
                ForEach(Array(segments.enumerated()),id: \.element.id) { index,segment in
                    segmentCard(index,segment)
                }
            }.disabled(saving || previewing)
        }
        .navigationTitle(model.t("nav.dub")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("dub.generate_dub"),systemImage: "waveform") { save() }.studioButton(.primary).disabled(saving || segments.isEmpty)
                .padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .onAppear { if segments.isEmpty { segments = record.checkpoint.segments; target = record.request.targets.first?.code ?? "" } }
        .onDisappear { player.clear() }
        .onChange(of: target) { _,_ in player.clear(); mutationKey = nil }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.plainText,.data]) { result in
            switch result { case .success(let url): importSubtitles(url); case .failure: model.error = ClientError("mobile.editing.subtitle_invalid") }
        }
        .alert(model.t("mobile.editing.replace_subtitles"),isPresented: Binding(get: { pendingImport != nil },set: { if !$0 { pendingImport = nil } })) {
            Button(model.t("mobile.editing.replace"),role: .destructive) { if let draft = pendingImport { rememberEdit(); segments = draft.segments; voices = draft.voices; pendingImport = nil } }
            Button(model.t("common.cancel"),role: .cancel) { pendingImport = nil }
        } message: { Text(model.t("mobile.editing.replace_subtitles_detail")) }
        .sheet(item: $exported) { ShareSheet(items: [$0.url]) }
    }
    private func segmentCard(_ index: Int,_ segment: PhoneDubSegment) -> some View {
        let id = segment.id ?? "legacy-\(index)"
        return Card {
            TextField(model.t("mobile.dub_advanced.speaker"),text: Binding(get: { currentSegment(segment,index: index).speaker ?? "" },set: { value in
                guard let current = segmentIndex(segment,index: index) else { return }; rememberEdit("speaker-"+id)
                if segments[current].referenceOrigin == "cast" { voices[id] = ""; segments[current].referenceFile = nil; segments[current].referenceText = nil; segments[current].referenceOrigin = nil; segments[current].voiceID = nil }
                segments[current].speaker = value
            })).accessibilityLabel(model.t("mobile.dub_advanced.speaker"))
            TextField(model.t("mobile.editing.source_text"),text: Binding(get: { currentSegment(segment,index: index).speech.text },set: { value in
                guard let current = segmentIndex(segment,index: index) else { return }
                rememberEdit("source-"+id); let old = segments[current].speech
                segments[current].speech = SpeechSegment(start: old.start,end: old.end,text: value)
            }),axis: .vertical).lineLimit(1...6)
            HStack {
                TextField(model.t("dubWorkspace.start"),value: timing(segment,index: index,end: false),format: .number).keyboardType(.decimalPad).accessibilityLabel(model.t("dubWorkspace.start"))
                TextField(model.t("dubWorkspace.end"),value: timing(segment,index: index,end: true),format: .number).keyboardType(.decimalPad).accessibilityLabel(model.t("dubWorkspace.end"))
            }
            TextField(model.t("tools.translated_line"),text: Binding(get: { currentSegment(segment,index: index).translations[target] ?? "" },set: { value in
                guard let current = segmentIndex(segment,index: index) else { return }
                rememberEdit("translation-"+id+target); segments[current].translations[target] = value
            }),axis: .vertical).lineLimit(2...8)
            ChoicePicker(label: model.t("mobile.speak.voice"),selection: Binding(get: { selection(segment) },set: { value in
                rememberEdit(); voices[id] = value
                if value != "__keep__", let current = segmentIndex(segment,index: index) { segments[current].referenceOrigin = "segment"; segments[current].voiceID = value }
            }),options: (segment.referenceFile == nil ? [] : [SelectionOption(id: "__keep__",title: model.t("clone.keep_reference"))])+[SelectionOption(id: "",title: model.t("stories.defaultVoice"))]+model.phone.voices.map { SelectionOption(id: $0.id.uuidString,title: $0.name) })
            Text(model.t("mobile.processing.segment_gain")+" "+String(Int(((currentSegment(segment,index: index).gain ?? 1)*100).rounded()))+"%").font(.caption).monospacedDigit()
            Slider(value: Binding(get: { currentSegment(segment,index: index).gain ?? 1 },set: { value in
                guard let current = segmentIndex(segment,index: index) else { return }; rememberEdit("gain-"+id); segments[current].gain = value
            }),in: 0...2).accessibilityLabel(model.t("mobile.processing.segment_gain"))
            Toggle(model.t("mobile.processing.mute"),isOn: Binding(get: { currentSegment(segment,index: index).muted == true },set: { value in
                guard let current = segmentIndex(segment,index: index) else { return }; rememberEdit(); segments[current].muted = value
            })).frame(minHeight: 44)
            HStack {
                Button(model.t("mobile.editing.original"),systemImage: "play.circle") { preview(segment,generated: false) }
                Button(model.t("mobile.editing.generated"),systemImage: "waveform") { preview(segment,generated: true) }
                    .disabled(selection(segment) != (segment.referenceFile == nil ? "" : "__keep__"))
            }.buttonStyle(.borderless)
            if player.file != nil && previewSegmentID == segment.id {
                Text(model.t(previewLabel)).font(.caption)
                AudioPlayerView(player: player)
            }
            if segment.speech.start.isFinite && segment.speech.end.isFinite && segment.speech.end-segment.speech.start > 0.1 {
                let position = min(segment.speech.end-0.05,max(segment.speech.start+0.05,splitPositions[id] ?? (segment.speech.start+segment.speech.end)/2))
                Text(model.t("mobile.editing.split_at")+" "+String(format: "%.2f",position)).font(.caption).monospacedDigit()
                Slider(value: Binding(get: { position },set: { splitPositions[id] = $0 }),in: (segment.speech.start+0.05)...(segment.speech.end-0.05)).accessibilityLabel(model.t("mobile.editing.split_at"))
                HStack {
                    Button(model.t("mobile.editing.split"),systemImage: "scissors") { structuralEdit {
                        let result = try PhoneDubEditing.split(segments,index: index,at: position)
                        if let newID = result[index+1].id { voices[newID] = selection(segment) }; return result
                    } }
                    Button(model.t("mobile.editing.merge"),systemImage: "arrow.triangle.merge") { structuralEdit {
                        guard index+1 < segments.count, selection(segment) == selection(segments[index+1]) else { throw ClientError("mobile.editing.merge_invalid") }
                        return try PhoneDubEditing.merge(segments,index: index)
                    } }.disabled(index+1 >= segments.count)
                }.buttonStyle(.borderless)
            }
        }
    }
    private var castPanel: some View {
        Card {
            Text(model.t("mobile.dub_advanced.cast")).font(.headline)
            Text(model.t("mobile.dub_advanced.manual_cast_hint")).font(.caption)
            if !PhoneDubCast.speakers(segments).isEmpty {
                Toggle(model.t("mobile.dub_advanced.replace_overrides"),isOn: $replaceCastOverrides).frame(minHeight: 44)
                ForEach(PhoneDubCast.speakers(segments),id: \.self) { speaker in
                    ChoicePicker(label: speaker,selection: Binding(get: {
                        castSelection(speaker)
                    },set: { value in
                        guard value != "__keep__" else { return }; rememberEdit()
                        for cue in segments where cue.speaker?.trimmingCharacters(in: .whitespacesAndNewlines) == speaker && (replaceCastOverrides || !PhoneDubCast.isLineOverride(cue)) { if let id = cue.id { voices[id] = value } }
                        segments = PhoneDubCast.assigning(segments,speaker: speaker,voiceID: value,replaceOverrides: replaceCastOverrides)
                    }),options: [SelectionOption(id: "__keep__",title: model.t("mobile.dub_advanced.keep_cast")),SelectionOption(id: "",title: model.t("stories.defaultVoice"))]+model.phone.voices.map { SelectionOption(id: $0.id.uuidString,title: $0.name) })
                }
            }
        }.disabled(saving || previewing)
    }
    private func castSelection(_ speaker: String) -> String {
        let values = Set(segments.filter { $0.speaker?.trimmingCharacters(in: .whitespacesAndNewlines) == speaker && !PhoneDubCast.isLineOverride($0) }.compactMap(\.voiceID))
        guard values.count == 1, let selected = values.first, selected.isEmpty || model.phone.voices.contains(where: { $0.id.uuidString == selected }) else { return "__keep__" }
        return selected
    }
    private func selection(_ segment: PhoneDubSegment) -> String { voices[segment.id ?? ""] ?? (segment.referenceFile == nil ? "" : "__keep__") }
    private func rememberEdit(_ key: String? = nil) {
        if key == nil || mutationKey != key { undo.append(DubEditorSnapshot(segments: segments,voices: voices)); if undo.count > 50 { undo.removeFirst() } }
        mutationKey = key; redo = []; player.clear()
    }
    private func restore(backward: Bool) {
        let current = DubEditorSnapshot(segments: segments,voices: voices)
        let previous: DubEditorSnapshot?
        if backward { previous = undo.popLast(); if previous != nil { redo.append(current) } }
        else { previous = redo.popLast(); if previous != nil { undo.append(current) } }
        if let previous { segments = previous.segments; voices = previous.voices; mutationKey = nil; player.clear() }
    }
    private func structuralEdit(_ action: () throws -> [PhoneDubSegment]) {
        let before = DubEditorSnapshot(segments: segments,voices: voices)
        do { let result = try action(); let nextVoices = voices; voices = before.voices; rememberEdit(); segments = result; voices = nextVoices }
        catch { voices = before.voices; model.error = (error as? ClientError) ?? ClientError("mobile.editing.split_invalid") }
    }
    private func segmentIndex(_ segment: PhoneDubSegment,index: Int) -> Int? {
        if segments.indices.contains(index), segments[index].id == segment.id { return index }
        return segments.firstIndex { $0.id == segment.id }
    }
    private func currentSegment(_ segment: PhoneDubSegment,index: Int) -> PhoneDubSegment {
        segmentIndex(segment,index: index).map { segments[$0] } ?? segment
    }
    private func timing(_ segment: PhoneDubSegment,index: Int,end: Bool) -> Binding<Double> {
        Binding(get: { let speech = currentSegment(segment,index: index).speech; return end ? speech.end : speech.start },set: { value in
            guard let current = segmentIndex(segment,index: index) else { return }
            rememberEdit("timing-\(segment.id ?? "")-\(end)"); let old = segments[current].speech
            segments[current].speech = SpeechSegment(start: end ? old.start : value,end: end ? value : old.end,text: old.text)
        })
    }
    private func preview(_ segment: PhoneDubSegment,generated: Bool) {
        previewing = true
        Task {
            defer { previewing = false }
            await model.performPhone {
                guard let store = model.phone.dubStore else { throw ClientError("mobile.simple.media_failed") }
                let url = try await store.preview(record.id,segment: segment,target: target,generated: generated)
                defer { try? FileManager.default.removeItem(at: url) }
                try await player.load(file: url); previewSegmentID = segment.id; previewLabel = generated ? "mobile.editing.generated" : "mobile.editing.original"
            }
        }
    }
    private func importSubtitles(_ url: URL) {
        do {
            let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= 4_000_000, let text = String(data: try Data(contentsOf: url),encoding: .utf8) else { throw ClientError("mobile.editing.subtitle_invalid") }
            let cues = try PhoneDubSubtitles.parse(text)
            let imported = try PhoneDubEditing.importTranslation(segments,cues: cues,target: target,duration: Double(record.checkpoint.frames)/24000)
            var importedVoices: [String:String] = [:]
            for cue in imported {
                let overlapping = segments.filter { $0.speech.start < cue.speech.end && $0.speech.end > cue.speech.start }
                let choices = Set(overlapping.map { selection($0) })
                guard choices.count == 1 else { throw ClientError("mobile.editing.merge_invalid") }
                if let id = cue.id { importedVoices[id] = choices.first }
            }
            pendingImport = DubEditorSnapshot(segments: imported,voices: importedVoices)
        } catch { model.error = (error as? ClientError) ?? ClientError("mobile.editing.subtitle_invalid") }
    }
    private func export(vtt: Bool) {
        do {
            let content = try PhoneDubSubtitles.export(segments,target: target,vtt: vtt)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("subtitles-"+UUID().uuidString).appendingPathExtension(vtt ? "vtt" : "srt")
            try content.write(to: url,atomically: true,encoding: .utf8); exported = SharedFile(url: url)
        } catch { model.error = (error as? ClientError) ?? ClientError("mobile.editing.subtitle_invalid") }
    }
    private func save() {
        saving = true; player.clear()
        Task {
            defer { saving = false }
            await model.performPhone {
                guard let store = model.phone.dubStore, let voiceStore = model.phone.voiceStore else { throw ClientError("mobile.simple.media_failed") }
                var references: [Int:(PCMChunk,String)] = [:]; var edits = segments
                var referenceCache: [UUID:(PCMChunk,String)] = [:]
                for (index,segment) in edits.enumerated() {
                    let id = selection(segment)
                    if id == "__keep__" { continue }
                    if id.isEmpty { edits[index].referenceFile = nil; if segment.referenceFile != nil { edits[index].referenceText = nil }; continue }
                    guard let voice = model.phone.voices.first(where: { $0.id.uuidString == id }) else { throw ClientError("audiobook.blocked.voice") }
                    if let cached = referenceCache[voice.id] { references[index] = cached }
                    else { let reference = (try await voiceStore.reference(voice),voice.transcript); referenceCache[voice.id] = reference; references[index] = reference }
                }
                try await store.edit(record.id,segments: edits,references: references)
                await model.phone.refresh(); dismiss(); model.phone.startDub(record.id)
            }
        }
    }
}
