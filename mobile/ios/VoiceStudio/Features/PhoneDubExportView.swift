import SwiftUI
import VoiceStudioKit

struct PhoneDubExportView: View {
    let record: PhoneDubRecord
    var initialTarget = ""
    @Environment(AppModel.self) private var model
    @State private var target = ""
    @State private var kind = "ass"
    @State private var style = PhoneDubSubtitleStyle()
    @State private var busy = false
    @State private var work: Task<Void,Never>?
    @State private var shared: SharedFile?
    @State private var leaving = false
    @State private var workspace = FileManager.default.temporaryDirectory.appendingPathComponent("dub-exports-"+UUID().uuidString)
    var body: some View {
        Page {
            Text(record.request.title).font(.headline)
            ChoicePicker(label: model.t("mobile.speak.language"),selection: $target,options: record.request.targets.map { SelectionOption(id: $0.code,title: $0.name) })
            ChoicePicker(label: model.t("mobile.dub_advanced.exports"),selection: $kind,options: [SelectionOption(id: "ass",title: model.t("mobile.dub_advanced.ass")),SelectionOption(id: "clips",title: model.t("mobile.dub_advanced.clips")),SelectionOption(id: "stems",title: model.t("mobile.dub_advanced.stems"))]+(record.checkpoint.outputs.contains { $0.file.hasSuffix(".mp4") } ? [SelectionOption(id: "video",title: model.t("mobile.dub_advanced.multi_audio"))] : []))
            if kind == "ass" {
                Text(model.t("mobile.dub_advanced.font_size")+" "+String(style.fontSize)).monospacedDigit()
                Slider(value: Binding(get: { Double(style.fontSize) },set: { style.fontSize = Int($0.rounded()) }),in: 18...72,step: 1).accessibilityLabel(model.t("mobile.dub_advanced.font_size"))
                ChoicePicker(label: model.t("mobile.dub_advanced.color"),selection: $style.color,options: [SelectionOption(id: "FFFFFF",title: model.t("mobile.dub_advanced.white")),SelectionOption(id: "FFFF00",title: model.t("mobile.dub_advanced.yellow")),SelectionOption(id: "00FFFF",title: model.t("mobile.dub_advanced.cyan"))])
                Toggle(model.t("mobile.dub_advanced.top"),isOn: $style.top).frame(minHeight: 44)
                Toggle(model.t("mobile.dub_advanced.dual"),isOn: $style.dual).frame(minHeight: 44)
            } else if kind == "stems" { Text(model.t("mobile.dub_advanced.stems_hint")).font(.caption) }
            if kind == "video" { Text(model.t("mobile.dub_advanced.multi_audio_hint")).font(.caption) }
            else if kind != "ass" { Text(model.t("mobile.dub_advanced.export_limit")).font(.caption) }
            if busy { ProgressView(); Button(model.t("common.cancel"),systemImage: "stop") { work?.cancel() }.studioButton(.tertiary) }
        }
        .navigationTitle(model.t("mobile.dub_advanced.exports")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { export() }.studioButton(.primary).disabled(busy || target.isEmpty).padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .onAppear { leaving = false; if target.isEmpty { target = initialTarget.isEmpty ? record.request.targets.first?.code ?? "" : initialTarget } }
        .onDisappear { leaving = true; if let work { work.cancel() } else { try? FileManager.default.removeItem(at: workspace) } }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
    }
    private func export() {
        let selected = target, mode = kind, subtitleStyle = style
        busy = true
        work = Task {
            defer { busy = false; work = nil; if leaving { try? FileManager.default.removeItem(at: workspace) } }
            await model.performPhone {
                guard let store = model.phone.dubStore else { throw ClientError("mobile.simple.media_failed") }
                try PrivateFiles.directory(workspace)
                let file: URL
                if mode == "ass" {
                    let cp = try await store.checkpoint(record.id)
                    file = workspace.appendingPathComponent("subtitles-"+UUID().uuidString+".ass")
                    try PhoneDubASS.export(cp.segments,target: selected,style: subtitleStyle).write(to: file,atomically: true,encoding: .utf8)
                } else if mode == "video" {
                    file = try await store.exportMultiAudio(record.id,defaultLanguage: selected,workspace: workspace)
                } else { file = try await store.exportPackage(record.id,target: selected,kind: mode == "clips" ? .clips : .stems,workspace: workspace) }
                try Task.checkCancellation(); shared = SharedFile(url: file)
            }
        }
    }
}
