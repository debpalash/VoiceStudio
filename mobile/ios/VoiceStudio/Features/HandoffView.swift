import SwiftUI
import UniformTypeIdentifiers
import VoiceStudioKit

private struct HandoffImport: Identifiable { let id = UUID(); let file: URL; let preview: HandoffPreview; let owned: Bool }
struct HandoffView: View {
    @Environment(AppModel.self) private var model
    @State private var projects: [ProductionProject] = []
    @State private var remote: [HandoffRemote] = []
    @State private var importing = false
    @State private var pending: HandoffImport?
    @State private var permission = false
    @State private var busy = false
    @State private var work: Task<Void,Never>?
    @State private var shared: SharedFile?
    @State private var notice: String?
    private var configuration: String { [model.phone.selectedModel("production-tts",.tts),model.phone.selectedModel("production-clone",.clone)].compactMap { $0 }.map { (try? phoneModelPin($0)) ?? "" }.joined(separator: "|") }
    var body: some View {
        Page {
            Text(model.t("mobile.handoff.hint"))
            Text(model.t("mobile.handoff.compatibility")).font(.caption)
            Button(model.t("mobile.handoff.import"),systemImage: "square.and.arrow.down") { importing = true }.studioButton(.primary).disabled(busy)
            Toggle(model.t("mobile.handoff.permission"),isOn: $permission).disabled(busy)
            if busy { ProgressView(); Button(model.t("common.cancel")) { work?.cancel() }.studioButton(.tertiary) }
            ForEach(projects) { project in
                Card {
                    Text(project.title).font(.headline)
                    Button(model.t("mobile.handoff.export"),systemImage: "square.and.arrow.up") { export(project: project,send: false) }.studioButton(.tertiary).disabled(busy)
                    if model.connection.client != nil { Button(model.t("mobile.handoff.send"),systemImage: "desktopcomputer") { export(project: project,send: true) }.studioButton(.tertiary).disabled(busy) }
                }
            }
            ForEach(model.phone.dubs) { record in
                Card {
                    Text(record.request.title).font(.headline)
                    Button(model.t("mobile.handoff.export"),systemImage: "square.and.arrow.up") { export(dub: record.request.id,send: false) }.studioButton(.tertiary).disabled(busy)
                    if model.connection.client != nil { Button(model.t("mobile.handoff.send"),systemImage: "desktopcomputer") { export(dub: record.request.id,send: true) }.studioButton(.tertiary).disabled(busy || !permission) }
                }
            }
            if model.connection.client != nil {
                HStack { Text(model.t("mobile.handoff.inbox")).font(.headline); Spacer(); Button(model.t("common.refresh"),systemImage: "arrow.clockwise") { run { remote = try await model.api().handoffInbox() } }.labelStyle(.iconOnly).disabled(busy) }
                ForEach(remote) { item in
                    HStack { Text(item.title).frame(maxWidth: .infinity,alignment: .leading); Button(model.t("mobile.handoff.receive")) { receive(item) }.disabled(busy) }.frame(minHeight: 44)
                }
            }
        }
        .navigationTitle(model.t("mobile.handoff.title")).navigationBarTitleDisplayMode(.inline)
        .task { await refresh(); if model.connection.client != nil { do { remote = try await model.api().handoffInbox() } catch { model.error = ClientError("mobile.handoff.failed") } } }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.data]) { result in
            guard case .success(let file) = result else { return }
            run {
                let access = file.startAccessingSecurityScopedResource(); defer { if access { file.stopAccessingSecurityScopedResource() } }
                pending = HandoffImport(file: file,preview: try PhoneProjectHandoff.preview(file),owned: false)
            }
        }
        .sheet(item: $pending) { item in
            VStack(alignment: .leading,spacing: 20) {
                Text(item.preview.title).font(.title2)
                Text(model.t("mobile.handoff.compatibility"))
                if item.preview.voices > 0 { Toggle(model.t("mobile.handoff.permission"),isOn: $permission) }
                Button(model.t("mobile.handoff.import")) { pending = nil; restore(item) }.studioButton(.primary).disabled(item.preview.voices > 0 && !permission)
                Button(model.t("common.cancel")) { pending = nil; if item.owned { try? FileManager.default.removeItem(at: item.file) } }.studioButton(.tertiary)
            }.padding(24).presentationDetents([.medium,.large])
        }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .alert(model.t("mobile.handoff.imported"),isPresented: Binding(get: { notice != nil },set: { if !$0 { notice = nil } })) { Button(model.t("common.confirm")) { notice = nil } } message: { Text(notice ?? "") }
        .onDisappear { work?.cancel() }
    }
    private func run(_ action: @escaping @MainActor () async throws -> Void) {
        guard !busy else { return }; busy = true
        work = Task {
            defer { busy = false; work = nil }
            do { try await action() }
            catch is CancellationError {} catch let error as ClientError { model.error = error }
            catch { model.error = ClientError("mobile.handoff.failed") }
        }
    }
    private func refresh() async { await model.phone.refresh(); projects = (try? await model.phone.productionStore?.list()) ?? [] }
    private func export(project: ProductionProject? = nil,dub: UUID? = nil,send: Bool) {
        let allow = permission, config = configuration
        run {
            guard let production = model.phone.productionStore, let voices = model.phone.voiceStore, let dubs = model.phone.dubStore else { throw ClientError("mobile.errors.file") }
            let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".vsproject")
            if let project { _ = try await PhoneProjectHandoff.exportProduction(project,store: production,voices: voices,configuration: config,to: file) }
            else if let dub { _ = try await PhoneProjectHandoff.exportDub(dub,store: dubs,models: model.phone.models,voices: voices,to: file) }
            if send {
                defer { try? FileManager.default.removeItem(at: file) }
                if try PhoneProjectHandoff.preview(file).voices > 0 && !allow { throw ClientError("mobile.handoff.voices_required") }
                try await model.api().sendHandoff(file,allowVoices: allow); remote = try await model.api().handoffInbox()
            } else { shared = SharedFile(url: file) }
        }
    }
    private func receive(_ item: HandoffRemote) { run { let file = try await model.api().receiveHandoff(item.id); pending = HandoffImport(file: file,preview: try PhoneProjectHandoff.preview(file),owned: true) } }
    private func restore(_ item: HandoffImport) {
        let allow = permission, config = configuration
        run {
            defer { if item.owned { try? FileManager.default.removeItem(at: item.file) } }
            let access = item.file.startAccessingSecurityScopedResource(); defer { if access { item.file.stopAccessingSecurityScopedResource() } }
            guard let production = model.phone.productionStore, let voices = model.phone.voiceStore, let dubs = model.phone.dubStore else { throw ClientError("mobile.errors.file") }
            let result = try await PhoneProjectHandoff.restore(item.file,production: production,dubs: dubs,voices: voices,models: model.phone.models,configuration: config,allowVoices: allow)
            notice = result.title; await refresh()
        }
    }
}
