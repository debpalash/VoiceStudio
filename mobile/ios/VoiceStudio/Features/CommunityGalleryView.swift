import SwiftUI
import VoiceStudioKit

struct CommunityGalleryView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let query: String
    @State private var catalogue: CommunityCatalogue?
    @State private var client: CommunityGallery?
    @State private var pack = ""
    @State private var language = ""
    @State private var loading = false
    @State private var failed = false
    @State private var active: String?
    @State private var player = AudioPlayer()
    @State private var playingID: String?
    @State private var target: GalleryEntry?
    @State private var imported: PhoneVoice?
    @State private var work: Task<Void,Never>?
    private var entries: [GalleryEntry] {
        let ids = catalogue?.packs.first { $0.id == pack }?.item_ids
        return (catalogue?.items ?? []).filter { item in
            (ids == nil || ids!.contains(item.id)) && (language.isEmpty || item.language == language) &&
            (query.isEmpty || [item.name,item.language,item.author ?? ""].contains { $0.localizedCaseInsensitiveContains(query.trimmingCharacters(in: .whitespacesAndNewlines)) })
        }
    }
    var body: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
        Text(model.t("gallery.community_explainer")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
        if let catalogue {
            ChoicePicker(label: model.t("gallery.community_packs"),selection: $pack,options: [SelectionOption(id: "",title: model.t("gallery.community_all_packs"))]+catalogue.packs.map { SelectionOption(id: $0.id,title: $0.name) })
            ChoicePicker(label: model.t("gallery.community_language"),selection: $language,options: [SelectionOption(id: "",title: model.t("gallery.community_all_languages"))]+Set(catalogue.items.map(\.language)).sorted().map { SelectionOption(id: $0,title: $0) })
        }
        if loading { ProgressView() }
        if failed { Button(model.t("common.retry"),systemImage: "arrow.clockwise") { Task { await refresh() } }.studioButton(.tertiary) }
        Text(model.t("gallery.all_voices",["count":String(entries.count)])).font(.headline)
        if catalogue != nil && entries.isEmpty { SearchEmptyState(query: query) }
        ForEach(entries) { item in
            Card {
                Text(item.name).font(.headline)
                Text([item.author,item.license,item.language].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(theme.color("textMuted"))
                if item.type == "preset" {
                    Text(model.t("gallery.no_preview")).font(.caption).foregroundStyle(theme.color("textMuted"))
                    NavigationLink { VoiceDesignView(communityRecipe: item) } label: { Label(model.t("gallery.use_voice"),systemImage: "waveform") }
                        .studioButton(.primary).accessibilityIdentifier("gallery.community.use.\(item.id)")
                } else {
                    Button(model.t("common.play"),systemImage: "play.fill") { play(item) }.studioButton(.tertiary).disabled(active != nil)
                        .accessibilityIdentifier("gallery.community.play.\(item.id)")
                    Button(model.t("gallery.use_voice"),systemImage: "plus") { target = item }.studioButton(.primary).disabled(active != nil)
                }
                if playingID == item.id && player.file != nil { AudioPlayerView(player: player) }
                if active == item.id { ProgressView(model.t("common.loading")); Button(model.t("common.cancel")) { work?.cancel() } }
            }
        }
        if let imported { NavigationLink(imported.name) { SpeakView(initialPhoneVoiceID: imported.id.uuidString) } }
        Link("debpalash/omnivoice-gallery",destination: URL(string: "https://github.com/debpalash/omnivoice-gallery")!).font(.caption)
        Button(model.t("common.refresh"),systemImage: "arrow.clockwise") { Task { await refresh() } }.studioButton(.tertiary).disabled(loading)
        }
        // Own work on the stable gallery container, not a footer that can leave the viewport.
        .task {
            do {
                let root = try FileManager.default.url(for: .cachesDirectory,in: .userDomainMask,appropriateFor: nil,create: true).appendingPathComponent("CommunityGallery")
                let service = try CommunityGallery(root: root); client = service
                guard let seed = Bundle.main.url(forResource: "community-gallery",withExtension: "json",subdirectory: "Catalogs") else { throw ClientError("mobile.local.catalogue_failed") }
                catalogue = try await service.cached(seed: Data(contentsOf: seed))
                await refresh()
            } catch is CancellationError {} catch { failed = true; model.error = ClientError("mobile.local.catalogue_failed") }
        }
        .onDisappear { work?.cancel(); player.clear() }
        .navigationDestination(isPresented: Binding(get: { imported != nil },set: { if !$0 { imported = nil } })) {
            if let imported { SpeakView(initialPhoneVoiceID: imported.id.uuidString) }
        }
        .alert(model.t("mobile.voices.consent_title"),isPresented: Binding(get: { target != nil },set: { if !$0 { target = nil } })) {
            if let target {
                Button(model.t("mobile.voices.consent_confirm")) { importVoice(target); self.target = nil }
                Button(model.t("common.cancel"),role: .cancel) { self.target = nil }
            }
        } message: { Text(model.t("mobile.voices.consent_body")+"\n"+[target?.author,target?.license].compactMap { $0 }.joined(separator: " · ")) }
    }
    private func refresh() async {
        guard let client, !loading else { return }; loading = true; defer { loading = false }
        do { catalogue = try await client.refresh(); failed = false }
        catch is CancellationError {} catch { failed = true } // Keep the cached/bundled catalogue visible offline.
    }
    private func play(_ item: GalleryEntry) {
        guard let client, active == nil else { return }; active = item.id
        work = Task { defer { active = nil; work = nil }; await model.performPhone {
            let file = try await client.recording(item); try Task.checkCancellation()
            try await player.load(file: file); playingID = item.id; player.play()
        } }
    }
    private func importVoice(_ item: GalleryEntry) {
        guard let client, let store = model.phone.voiceStore, active == nil else { return }; active = item.id
        work = Task { defer { active = nil; work = nil }; await model.performPhone {
            let file = try await client.recording(item); try Task.checkCancellation()
            let saved = try await store.importCommunity(item,source: file,consent: true)
            await model.phone.refresh()
            try Task.checkCancellation(); imported = saved
        } }
    }
}
