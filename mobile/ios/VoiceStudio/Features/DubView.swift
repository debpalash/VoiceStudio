import SwiftUI
import VoiceStudioKit

struct DubView: View {
    @Environment(AppModel.self) private var model
    @State private var preference: TaskLocation?
    private var route: TaskRoute { LocalRouting.route(preference: preference,installed: model.phone.dubReady,paired: model.connection.client != nil) }
    var body: some View {
        VStack(spacing: 0) {
            RunOnPicker(preference: $preference,task: "dub",installed: model.phone.dubReady)
                .padding(.horizontal,Theme.compactInset).disabled(model.phone.activeDub != nil || model.busy)
            if route == .computer { ComputerDubView() }
            else { PhoneDubView(connect: route == .connect) }
        }.studioNavigationTitle(model.t("mobile.tabs.dub"))
            .task { preference = model.phone.preference("dub"); await model.phone.refresh() }
    }
}

import SwiftUI
import VoiceStudioKit
import UniformTypeIdentifiers

struct ComputerDubView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var upload = BackgroundUpload.shared
    @State private var importing = false
    @State private var selectedFile: SelectedMedia?
    @State private var sourceURL = ""
    @State private var selected = Set<String>()
    @State private var profile = DubVoiceChoice.unselected
    @State private var selectingLanguages = false
    @State private var shared: SharedFile?
    @State private var saved: SharedFile?
    @State private var reporting: ContentReportSubject?
    @State private var advanced: ComputerDubSelection?
    var languages: [DubLanguage] { model.languages.dubOptions }
    var body: some View {
        Page {
            if model.connection.client == nil { NavigationLink(model.t("mobile.settings.connection")) { ConnectView() } }
            if let selectedFile { DubFileCard(name: selectedFile.name) { self.selectedFile = nil } }
            else { DubSourceButton { importing = true } }
            HStack(spacing: VSTokens.spacingGap) {
                Image(systemName: "link").foregroundStyle(theme.color("iconMuted")).accessibilityHidden(true)
                TextField(model.t("mobile.dub.paste_url"), text: $sourceURL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("dub.source_url")
            }
            DubTiles {
                Button { selectingLanguages = true } label: {
                    TileLabel(icon: "globe", label: model.t("mobile.dub.target_languages"), value: {let names = languages.filter { selected.contains($0.code) }.map(\.name); return names.isEmpty ? model.t("mobile.android.choose") : ListFormatter.localizedString(byJoining: names)}())
                }.studioButton(.tertiary).accessibilityIdentifier("dub.languages")
                VoicePicker(selection: $profile, dubbing: true, tile: true)
            }
            if let pending = upload.pending {
                DubJobCard(title: pending.filename ?? model.t("mobile.tabs.dub"), stage: model.t("mobile.dub.uploading", ["percent": String(Int(upload.fraction * 100))]), fraction: upload.fraction) {
                    Button(model.t("mobile.ios.upload_cancel"), systemImage: "xmark", role: .destructive) { Task { await upload.cancel() } }
                }
            }
            if let engine = model.dub.pendingTranslation {
                Card {
                    Text(model.t("mobile.dub.translation_online_body", ["service": engine.display_name]))
                    // Both choices use the same native weight, size and style.
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: VSTokens.spacingGap) { translationChoice(true); translationChoice(false) }
                        VStack(spacing: VSTokens.spacingGap) { translationChoice(true); translationChoice(false) }
                    }
                }
            }
            if let cp = model.dub.checkpoint {
                DubJobCard(title: cp.title, stage: model.t("mobile.ios.dub_stages." + cp.stage.rawValue), fraction: model.dub.progress, running: model.dub.monitoring) {
                    if cp.host != model.connection.active?.id { NavigationLink(model.t("mobile.simple.reconnect")) { ConnectView() } }
                    else if cp.stage == .done {
                        Button(model.t("mobile.dub.download"), systemImage: "arrow.down.to.line") { Task { await download(cp.jobID, name: cp.title) } }
                        Button(model.t("mobile.dub_advanced.exports"),systemImage: "slider.horizontal.3") { advanced = ComputerDubSelection(id: cp.jobID,title: cp.title) }
                        ReportButton(subject: .computer(.dub, id: cp.jobID, engine: nil), reporting: $reporting, context: cp.title)
                    }
                    else if cp.stage != .cancelled {
                        if !model.dub.monitoring { Button(model.t("mobile.ios.upload_resume"), systemImage: "play.fill") { model.monitor() } }
                        Button(model.t("mobile.dub.abort"), systemImage: "xmark", role: .destructive) { Task { await model.perform { model.pauseMonitor(); try await model.dub.abort(api: model.api()) } } }
                    }
                }
            }
            if !model.dubHistory.isEmpty {
                Text(model.t("mobile.dub.history")).font(.headline)
                Card {
                    ForEach(model.dubHistory) { item in
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: VSTokens.spacingGap) {
                                Image(systemName: "film").foregroundStyle(theme.color("messageAction")).frame(width: 32).accessibilityHidden(true)
                                historyTitle(item)
                                historyActions(item)
                            }
                            VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
                                historyTitle(item)
                                historyActions(item)
                            }
                        }
                        if item.id != model.dubHistory.last?.id { Divider().overlay(theme.color("border")) }
                    }
                }
            }
        }
        .navigationTitle(model.t("mobile.tabs.dub")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            DubStartButton(busy: upload.pending != nil || model.dub.monitoring) { Task { await start() } }
                .disabled(model.connection.client == nil || profile == DubVoiceChoice.unselected || model.busy || upload.pending != nil || selected.isEmpty || (selectedFile == nil && sourceURL.isEmpty) || model.dub.monitoring)
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.movie, .audio]) { result in
            switch result { case .success(let url): selectedFile = SelectedMedia(url: url); sourceURL = ""; case .failure: model.error = ClientError("mobile.errors.file") }
        }
        .contentReportSheet($reporting)
        .sheet(item: $shared) { file in ShareSheet(items: [file.url]) }
        .sheet(item: $advanced) { job in NavigationStack { ComputerDubAdvancedView(jobID: job.id,title: job.title) } }
        .sheet(item: $saved) { file in SaveFileSheet(url: file.url) }
        .sheet(isPresented: $selectingLanguages) {
            LanguageSelectionSheet(selection: selected, includesUnavailable: true) { selected = $0 }
        }
        .onChange(of: sourceURL) { _, value in if !value.isEmpty { selectedFile = nil } }
        .refreshable { await model.perform { model.dubHistory = try await model.api().dubHistory() }; model.monitor() }
    }
    private func translationChoice(_ allowed: Bool) -> some View {
        Button {
            model.pauseMonitor()
            model.dub.confirmTranslation(allowed)
            if allowed { model.monitor() }
        } label: {
            Text(model.t(allowed ? "mobile.dub.translation_allow" : "mobile.dub.translation_decline"))
                .frame(maxWidth: .infinity)
        }.studioButton(.secondary)
            .accessibilityIdentifier(allowed ? "dub.translation.allow" : "dub.translation.decline")
    }
    private func start() async {
        await model.perform {
            try DubVoiceChoice.validate(profile)
            let api = try model.api()
            let targets = languages.filter { selected.contains($0.code) }
            if let file = selectedFile {
                let access = file.url.startAccessingSecurityScopedResource(); defer { if access { file.url.stopAccessingSecurityScopedResource() } }
                let type = UTType(filenameExtension: file.url.pathExtension)
                try await upload.start(source: file.url, api: api, languages: targets, profile: profile, audioOnly: type?.conforms(to: .audio) == true, filename: file.uploadName)
                selectedFile = nil
            } else {
                let result = try await api.ingest(url: sourceURL, jobID: UUID().uuidString)
                try model.dub.begin(result, api: api, languages: targets, profile: profile)
                sourceURL = ""; model.monitor()
            }
        }
    }
    private func historyTitle(_ item: DubHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
            Text(item.title).lineLimit(1).truncationMode(.middle)
            Text(item.metadata(l10n: model.l10n)).accessibilityIdentifier("dub.metadata." + item.id)
                .font(.caption)
                .foregroundStyle(theme.color("textMuted")).lineLimit(1).truncationMode(.tail)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func historyActions(_ item: DubHistoryItem) -> some View {
        HStack(spacing: VSTokens.spacingGap) {
            Button(model.t("mobile.dub.download"), systemImage: "arrow.down.to.line") {
                Task { await download(item.job_id ?? item.id, name: item.title) }
            }.accessibilityIdentifier("dub.download." + item.id)
                .accessibilityLabel(model.t("mobile.dub.download") + ": " + item.title)
            Button(model.t("mobile.speak.share"), systemImage: "square.and.arrow.up") {
                Task { await download(item.job_id ?? item.id, name: item.title, share: true) }
            }.accessibilityIdentifier("dub.share." + item.id)
                .accessibilityLabel(model.t("mobile.speak.share") + ": " + item.title)
            Button(model.t("mobile.dub_advanced.exports"),systemImage: "slider.horizontal.3") { advanced = ComputerDubSelection(id: item.job_id ?? item.id,title: item.title) }
            ReportButton(subject: .computer(.dub, id: item.job_id ?? item.id, engine: nil), reporting: $reporting, context: item.title)
        }.labelStyle(.iconOnly).studioButton(.tertiary).disabled(model.busy)
    }
    private func download(_ id: String, name: String, share: Bool = false) async {
        await model.perform {
            let api = try model.api()
            let reply = try await api.download(api.request("/dub/download/\(try APIClient.component(id))"))
            let disposition = reply.headers["content-disposition"] ?? ""
            // Derive only an extension, never a server-provided local path.
            let ext = ["mp4", "mkv", "wav", "mp3", "m4a"].first { disposition.contains("." + $0) } ?? "m4a"
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let candidate = URL(fileURLWithPath: name).deletingPathExtension().lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
            let basename = ["", ".", "..", "/"].contains(candidate) ? id : candidate
            let file = directory.appendingPathComponent(basename).appendingPathExtension(ext)
            try FileManager.default.moveItem(at: reply.file, to: file)
            if share { shared = SharedFile(url: file) } else { saved = SharedFile(url: file) }
        }
    }
}
struct SaveFileSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController { UIDocumentPickerViewController(forExporting: [url], asCopy: true) }
    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController, context: Context) {}
}
struct SharedFile: Identifiable { let id = UUID(); let url: URL }
struct ShareSheet: UIViewControllerRepresentable {
    let items: [URL]
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: items, applicationActivities: nil) }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

private struct ComputerDubSelection: Identifiable { let id: String; let title: String }
