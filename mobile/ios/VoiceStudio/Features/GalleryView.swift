import SwiftUI
import VoiceStudioKit

struct GalleryView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var location = "community"
    @State private var query = ""
    @State private var items: [CommunityItem] = []
    @State private var total = 0
    @State private var player = AudioPlayer()
    @State private var chosen: CommunityVoice?
    @State private var loading = false
    @State private var imported: [String:String] = [:]
    @State private var copying = false
    @State private var copyTarget: CommunityItem?
    @State private var copied: PhoneVoice?
    @State private var copyTask: Task<Void,Never>?
    private var phoneModels: [PhoneModel] { model.phone.models.filter { $0.capability == .tts } }
    private func speakers(_ item: PhoneModel) -> [Int] {
        let all = Array(0..<max(1,min(128,Int(item.config["num_speakers"]?.number ?? 1))))
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if search.isEmpty || item.name.localizedCaseInsensitiveContains(search) { return all }
        return all.filter { model.t("mobile.local.preset_voice",["number": String($0+1)]).localizedCaseInsensitiveContains(search) }
    }
    var body: some View {
        Page {
            ChoicePicker(label: model.t("nav.gallery"),selection: $location,options: [SelectionOption(id: "community",title: model.t("gallery.zone_community")),SelectionOption(id: "phone",title: model.t("mobile.local.this_phone")),SelectionOption(id: "computer",title: model.t("mobile.local.my_computer"))])
            SearchField(prompt: model.t("mobile.voices.search"),text: $query)
            if location == "community" {
                CommunityGalleryView(query: query)
            } else if location == "phone" {
                Text(model.t("gallery.all_voices",["count": String(phoneModels.reduce(0) { $0 + speakers($1).count })])).font(.headline)
                if phoneModels.allSatisfy({ speakers($0).isEmpty }) { SearchEmptyState(query: query) }
                ForEach(phoneModels) { item in
                    if !speakers(item).isEmpty {
                        Card {
                            Text(item.name).font(.headline)
                            if model.phone.ready(item) {
                                Label(model.t("mobile.ios.status_ready"),systemImage: "checkmark.circle").font(.caption).foregroundStyle(theme.color("textMuted"))
                            } else {
                                LocalDownloadPrompt(items: [item],feature: "nav.gallery")
                            }
                            ForEach(speakers(item),id: \.self) { speaker in
                                NavigationLink { SpeakView(initialPhonePreset: speaker,initialPhoneModelID: item.id) } label: { Label(model.t("mobile.local.preset_voice",["number": String(speaker+1)]),systemImage: "waveform").frame(maxWidth: .infinity,minHeight: 44,alignment: .leading) }
                                    .accessibilityIdentifier("gallery.preset.\(item.id).\(speaker)")
                            }
                        }
                    }
                }
                NavigationLink(model.t("mobile.local.models")) { ModelsView() }
                NavigationLink(model.t("mobile.tabs.voices")) { VoicesView() }
            } else if model.connection.client == nil {
                Text(model.t("gallery.community_explainer")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                Text(model.t("mobile.local.pair_settings")).foregroundStyle(theme.color("textMuted"))
                NavigationLink(model.t("mobile.simple.reconnect")) { ConnectView() }
            } else {
                Text(model.t("gallery.community_explainer")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                if items.isEmpty && !loading { EmptyState(key: "gallery.community_empty",icon: "person.2") }
                ForEach(items.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { item in
                    Card {
                        Text(item.name).font(.headline)
                        Text([item.author,item.license,item.language].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(theme.color("textMuted"))
                        Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.perform { let api = try model.api(), scope = model.hostState.scopeID; let data = try await api.communityAudio(item.id); guard scope == model.hostState.scopeID else { throw CancellationError() }; try await player.load(data,extension: "wav"); player.play() } } }.studioButton(.tertiary)
                        Button(model.t("gallery.use_voice"),systemImage: "plus") { Task { await model.perform { let api = try model.api(), scope = model.hostState.scopeID; let saved = try await api.useCommunity(item.id); let profiles = try await api.profiles(); guard scope == model.hostState.scopeID else { throw CancellationError() }; chosen = saved; imported[item.id] = saved.profile_id; model.profiles = profiles } } }.studioButton(.primary).disabled(model.busy)
                        if imported[item.id] != nil { Button(model.t("mobile.galleryCopy.action"),systemImage: "iphone.and.arrow.forward") { copyTarget = item }.studioButton(.tertiary).disabled(copying) }
                    }
                }
                if copying { ProgressView(model.t("common.loading")); Button(model.t("common.cancel")) { copyTask?.cancel() }.studioButton(.tertiary) }
                if let copied { NavigationLink(model.t("mobile.galleryCopy.ready")+" · "+copied.name) { SpeakView(initialPhoneVoiceID: copied.id.uuidString) } }
                Text(model.t("mobile.galleryCopy.requires_host")).font(.caption)
                if items.count < total { Button(model.t("common.more_info")) { Task { await load() } }.studioButton(.tertiary).disabled(loading) }
                if loading { ProgressView() }
                if let chosen { NavigationLink(chosen.name) { SpeakView(initialComputerVoiceID: chosen.profile_id) } }
                if player.file != nil { AudioPlayerView(player: player) }
            }
        }
        .navigationTitle(model.t("nav.gallery")).navigationBarTitleDisplayMode(.inline)
        .task { await model.phone.refresh() }
        .task(id: location) { if location == "computer", items.isEmpty, model.connection.client != nil { await load() } }
        .refreshable { if location == "computer" { items = []; await load() } else { await model.phone.refresh() } }
        .onChange(of: model.hostState.scopeID) { _,_ in copyTask?.cancel(); items = []; total = 0; chosen = nil; imported = [:]; copyTarget = nil; copied = nil; player.clear() }
        .onDisappear { copyTask?.cancel(); player.clear() }
        .confirmationDialog(model.t("mobile.galleryCopy.action"),isPresented: Binding(get: { copyTarget != nil },set: { if !$0 { copyTarget = nil } }),titleVisibility: .visible) {
            if let item = copyTarget {
                Button(model.t("mobile.galleryCopy.action")) { copy(item); copyTarget = nil }
                Button(model.t("common.cancel"),role: .cancel) { copyTarget = nil }
            }
        } message: {
            Text(model.t("mobile.galleryCopy.confirm")+"\n"+[copyTarget?.author,copyTarget?.license].compactMap { $0 }.joined(separator: " · "))
        }
    }
    private func copy(_ item: CommunityItem) {
        guard !copying,let profile = imported[item.id],let store = model.phone.voiceStore else { return }
        copying = true
        copyTask = Task { defer { copying = false; copyTask = nil }; await model.perform {
            let api = try model.api(),scope = model.hostState.scopeID
            let file = try await api.downloadPhoneVoiceBundle(profile); defer { try? FileManager.default.removeItem(at: file) }
            guard scope == model.hostState.scopeID else { throw CancellationError() }
            var attribution = ["source":"community","id":item.id,"name":item.name]
            attribution["author"] = item.author; attribution["license"] = item.license; attribution["language"] = item.language
            copied = try await PhoneVoiceBundle.install(file,store: store,attribution: attribution,consent: true)
            await model.phone.refresh()
        } }
    }
    private func load() async {
        guard !loading, model.connection.client != nil else { return }; loading = true; defer { loading = false }
        await model.perform { let api = try model.api(), scope = model.hostState.scopeID; let page = try await api.community(offset: items.count); guard scope == model.hostState.scopeID else { throw CancellationError() }; items += page.items; total = page.total }
    }
}
