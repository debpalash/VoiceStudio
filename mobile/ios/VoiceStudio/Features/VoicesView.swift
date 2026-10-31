import SwiftUI
import VoiceStudioKit
import UniformTypeIdentifiers

private struct VoiceEntry: Identifiable {
    let id: String
    let name: String
    let phone: PhoneVoice?
    init(_ voice: PhoneVoice) { id = "phone:"+voice.id.uuidString; name = voice.name; phone = voice }
    init(_ voice: Profile) { id = voice.id; name = voice.name; phone = nil }
    /// A phone voice reports the catalogue model that made it; the computer reports no engine for a clone.
    var reportSubject: ContentReportSubject {
        if let phone { return .phone(.clone, id: phone.id.uuidString, repo: phone.modelRepo, revision: phone.modelRevision) }
        return .computer(.clone, id: id, engine: nil)
    }
}
struct VoicesView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var query = ""
    @State private var creating = false
    @State private var player = AudioPlayer()
    @State private var renamed: VoiceEntry?
    @State private var deleting: VoiceEntry?
    @State private var name = ""
    @State private var hostScope: UUID?
    @State private var reporting: ContentReportSubject?
    private var entries: [VoiceEntry] { model.phone.voices.map(VoiceEntry.init) + model.profiles.map(VoiceEntry.init) }
    private var filtered: [VoiceEntry] { entries.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) } }
    var body: some View {
        Page(fill: entries.isEmpty && !model.loading) {
            if entries.isEmpty && !model.loading {
                Spacer(minLength: 0)
                ContentUnavailableView {
                    Label(model.t("mobile.simple.empty_voices"), systemImage: "person.wave.2")
                } description: {
                    Text(model.t("clone.reference_hint"))
                }
                Spacer(minLength: 0)
            } else {
                SearchField(prompt: model.t("mobile.voices.search"), text: $query)
                Card {
                    if filtered.isEmpty { SearchEmptyState(query: query) }
                    ForEach(filtered) { entry in
                        HStack(spacing: VSTokens.spacingGap) {
                            Text(String(entry.name.prefix(1)).uppercased()).frame(width: 44,height: 44).background(theme.color("input"),in: Circle()).accessibilityHidden(true)
                            VStack(alignment: .leading,spacing: 4) {
                                Text(entry.name).lineLimit(1)
                                if let attribution = entry.phone?.sourceAttribution { Text([attribution["author"],attribution["license"],attribution["name"]].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(theme.color("textMuted")).textSelection(.enabled) }
                                Text(model.t(entry.phone == nil ? "mobile.simple.computer" : "mobile.simple.phone")).font(.caption).foregroundStyle(theme.color("textMuted"))
                            }.frame(maxWidth: .infinity,alignment: .leading)
                            Button(model.t("common.play"), systemImage: "play.fill") {
                                Task { await model.perform(onPhone: entry.phone != nil) {
                                    let bytes: Data
                                    if let voice = entry.phone, let store = model.phone.voiceStore { bytes = try Data(contentsOf: await store.preview(voice)) }
                                    else { bytes = try await model.api().profileAudio(entry.id) }
                                    try await player.load(bytes,extension: "wav"); player.play()
                                } }
                            }.labelStyle(.iconOnly).studioButton(.tertiary)
                            Menu {
                                Button(model.t("mobile.voices.rename"),systemImage: "pencil") { name = entry.name; renamed = entry }
                                ReportButton(subject: entry.reportSubject, reporting: $reporting, context: entry.name)
                                Button(model.t("common.delete"),systemImage: "trash",role: .destructive) { deleting = entry }
                            } label: { Label(model.t("mobile.ios.more_actions"),systemImage: "ellipsis") }
                                .labelStyle(.iconOnly).studioButton(.tertiary).frame(minWidth: 44,minHeight: 44).accessibilityIdentifier("voices.actions."+entry.id)
                        }
                        if entry.id != filtered.last?.id { Divider().overlay(theme.color("border")) }
                    }
                }
            }
            if player.file != nil { AudioPlayerView(player: player) }
        }.studioNavigationTitle(model.t("mobile.tabs.voices"))
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if entries.isEmpty && !model.loading {
                Button { creating = true } label: {
                    Label(model.t("mobile.voices.new_clone"), systemImage: "plus").frame(maxWidth: .infinity)
                }.studioButton(.primary).accessibilityIdentifier("voices.create")
                    .padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
            }
        }
        .toolbar {
            if !entries.isEmpty || model.loading {
                Button(model.t("mobile.voices.new_clone"),systemImage: "plus") { creating = true }.accessibilityIdentifier("voices.create")
            }
        }
        .task { await model.phone.refresh() }
        .refreshable { await model.phone.refresh(); if model.connection.client != nil { await model.perform { model.profiles = try await model.api().profiles() } } }
        .contentReportSheet($reporting)
        .fullScreenCover(isPresented: $creating) { NavigationStack { CloneView() }.environment(model) }
        .sheet(item: $renamed) { entry in
            NavigationStack {
                Page(backgroundRole: "surfaceOverlay") {
                    TextField(model.t("mobile.voices.name_label"),text: $name).accessibilityIdentifier("voices.rename.name")
                    Button(model.t("common.save"),systemImage: "checkmark") { Task { await model.perform(onPhone: entry.phone != nil) {
                        if let voice = entry.phone { try await model.phone.voiceStore?.rename(voice,name: name); await model.phone.refresh() }
                        else { try await model.api().rename(entry.id,name: name); model.profiles = try await model.api().profiles() }
                        renamed = nil
                    } } }.studioButton(.primary).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.busy)
                }.navigationTitle(model.t("mobile.voices.rename"))
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.cancel")) { renamed = nil } } }
            }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        }
        .sheet(item: $deleting) { entry in
            DeleteConfirmation(title: model.t("mobile.voices.delete_confirm",["name": entry.name])) {
                Task { await model.perform(onPhone: entry.phone != nil) {
                    player.clear()
                    if let voice = entry.phone { try await model.phone.voiceStore?.delete(voice); await model.phone.refresh() }
                    else { try await model.api().deleteProfile(entry.id); model.profiles = try await model.api().profiles() }
                } }
            }
        }
        .onAppear { hostScope = model.hostState.scopeID }
        .onDisappear { if hostScope != model.hostState.scopeID { player.clear() } else { player.pause() } }
    }
}
struct CloneView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @State private var preference: TaskLocation?
    @State private var name = ""
    @State private var consent = false
    @State private var importing = false
    @State private var audio: SelectedMedia?
    @State private var recorder = Recorder()
    @State private var work: Task<Void,Never>?
    @State private var recordingTask: Task<Void,Never>?
    @State private var running = false
    @State private var progress = 0.0
    private var asr: PhoneModel? { model.phone.selectedModel("clone-asr",.asr) }
    private var cloneModel: PhoneModel? { model.phone.selectedModel("clone",.clone) }
    private var ready: Bool { (asr.map(model.phone.ready) ?? false) && (cloneModel.map(model.phone.ready) ?? false) }
    private var route: TaskRoute { LocalRouting.route(preference: preference,installed: ready,paired: model.connection.client != nil) }
    var body: some View {
        Form {
            if model.connection.client != nil {
                Section { RunOnPicker(preference: $preference, task: "clone", installed: ready) }
            }
            if route == .connect { NavigationLink(model.t("mobile.simple.reconnect")) { ConnectView() } }
            Section {
                TextField(model.t("mobile.voices.name_label"),text: $name).textFieldStyle(.plain).accessibilityIdentifier("voices.clone.name")
            }.listRowBackground(theme.color("surface"))
            Section {
                HStack {
                    Toggle(model.t("mobile.simple.voice_consent"),isOn: $consent).frame(minHeight: 44)
                    InfoButton(title: model.t("mobile.voices.consent_title"),detail: model.t("mobile.voices.consent_body"))
                }
                Button(model.t(recorder.recording ? "common.stop" : "mobile.voices.record"),systemImage: recorder.recording ? "stop.fill" : "mic.fill") {
                    recordingTask = Task { await model.performPhone {
                        if recorder.recording { recorder.stop(); audio = recorder.file.map { SelectedMedia(url: $0) } }
                        else { audio = nil; try await recorder.start() }
                    } }
                }.disabled(!consent || running)
                Button(model.t("mobile.voices.pick_file"),systemImage: "doc") { importing = true }.disabled(recorder.recording || running)
                if recorder.recording { Text(model.t("mobile.voices.recording",["seconds": String(recorder.seconds)])) }
                if let audio { SelectedFileRow(name: audio.name) { self.audio = nil } }
            } footer: { Text(model.t("clone.reference_hint")) }
            .listRowBackground(theme.color("surface"))
            if route == .download {
                Section { LocalDownloadPrompt(items: [asr,cloneModel].compactMap { $0 }, feature: "mobile.tabs.voices") }
                    .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
            }
            if route != .computer {
                Section {
                    Text(model.t("mobile.calls.clone_hint")).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                    DisclosureGroup(model.t("mobile.simple.more")) {
                        ChoicePicker(label: model.t("mobile.simple.clone"), selection: Binding(get: { cloneModel?.id ?? "" }, set: { model.phone.chooseModel("clone",$0) }), options: model.phone.candidates(.clone).map { SelectionOption(id: $0.id,title: $0.name,enabled: $0.fits()) })
                        ChoicePicker(label: model.t("mobile.simple.asr"), selection: Binding(get: { asr?.id ?? "" }, set: { model.phone.chooseModel("clone-asr",$0) }), options: model.phone.candidates(.asr).map { SelectionOption(id: $0.id, title: $0.name, enabled: $0.fits()) })
                    }
                }.listRowBackground(theme.color("surface"))
            }
        }.scrollContentBackground(.hidden).background(theme.color("canvas").ignoresSafeArea()).keyboardAccessible()
        .navigationTitle(model.t("mobile.voices.new_clone")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: VSTokens.spacingGap) {
            Button { create() } label: {
                Label(model.t("mobile.ios.clone_create"), systemImage: "person.badge.plus").frame(maxWidth: .infinity)
            }
                .studioButton(.primary)
                .disabled(!consent || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 200 || audio == nil || recorder.recording || model.busy || model.phone.activeDub != nil || running || (route != .phone && route != .computer))
            if running {
                ProgressView(value: progress).accessibilityLabel(model.t("mobile.ios.progress"))
                Button(model.t("common.cancel")) { work?.cancel() }
            }
            }.padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
        }
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.cancel")) { work?.cancel(); recorder.stop(); dismiss() } } }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.audio]) { result in
            switch result { case .success(let url): audio = SelectedMedia(url: url); case .failure: model.error = ClientError("mobile.errors.file") }
        }
        .task { preference = model.phone.preference("clone") }
        .onChange(of: recorder.seconds) { _, seconds in if seconds >= 29 && recorder.recording { recorder.stop(); audio = recorder.file.map { SelectedMedia(url: $0) } } }
        .onChange(of: consent) { _, allowed in if !allowed { work?.cancel() } }
        .onChange(of: phase) { _, value in if value != .active { work?.cancel(); if value == .background { recordingTask?.cancel() }; if recorder.recording { recorder.stop(); audio = nil } } }
        .onDisappear { work?.cancel(); recordingTask?.cancel(); recorder.stop(); if let file = recorder.file { try? FileManager.default.removeItem(at: file) } }
    }
    private func create() {
        guard let audio, consent else { return }
        let route = route
        running = true
        work = Task {
            defer { running = false }
            await model.perform(onPhone: route == .phone) {
                guard consent else { throw CancellationError() }
                if route == .phone {
                    guard let asr, let cloneModel, let engine = model.phone.engine, let store = model.phone.voiceStore else { throw ClientError("mobile.local.model_required") }
                    _ = try await store.create(name: name,source: audio.url,consent: consent,asr: asr,cloneModel: cloneModel,engine: engine,decoder: AVPhoneAudioDecoder(),progress: { value in await MainActor.run { progress = value.fraction } })
                    await model.phone.refresh()
                } else {
                    let access = audio.url.startAccessingSecurityScopedResource(); defer { if access { audio.url.stopAccessingSecurityScopedResource() } }
                    _ = try await model.api().clone(name: name,filename: audio.uploadName,audio: Data(contentsOf: audio.url))
                    try Task.checkCancellation()
                    guard consent else { throw CancellationError() }
                    let profiles = try await model.api().profiles()
                    try Task.checkCancellation()
                    guard consent else { throw CancellationError() }
                    model.profiles = profiles
                }
                try Task.checkCancellation(); dismiss()
            }
        }
    }
}
