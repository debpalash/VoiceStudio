import Foundation
import Observation
import Network
import VoiceStudioKit

/// Network.framework observes interfaces locally; it never probes a remote service.
final class WifiState: @unchecked Sendable {
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var permitted = false
    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            lock.withLock { permitted = path.status == .satisfied && (path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet)) && !path.isExpensive }
        }
        monitor.start(queue: DispatchQueue(label: "com.voicestudio.model-network"))
    }
    var wifi: Bool { lock.withLock { permitted } }
    deinit { monitor.cancel() }
}
@MainActor @Observable final class PhoneModels {
    private(set) var models: [PhoneModel] = []
    private(set) var installed = Set<String>()
    private(set) var accepted = Set<String>()
    private(set) var progress: [String: Double] = [:]
    private(set) var used: Int64 = 0
    private(set) var modelBytes: [String: Int64] = [:]
    var error: ClientError?
    var allowMobileData: Bool { didSet { UserDefaults.standard.set(allowMobileData, forKey: "models.mobile-data") } }
    @ObservationIgnored private var downloads: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private let wifi = WifiState()
    private(set) var store: ModelStore?
    private(set) var dubs: [PhoneDubRecord] = []
    private(set) var dubStore: PhoneDubStore?
    @ObservationIgnored private var pendingDubs: [UUID] = []
    private(set) var activeDub: UUID?
    private(set) var dubProgress: PhoneDubProgress?
    @ObservationIgnored var onDubFinished: (@MainActor @Sendable (PhoneDubRequest) async -> Void)?
    @ObservationIgnored private var dubTask: Task<Void,Never>?
    private(set) var voices: [PhoneVoice] = []
    private(set) var recent: [PhoneGeneration] = []
    private(set) var voiceStore: PhoneVoiceStore?
    private(set) var generationStore: PhoneGenerationStore?
    private(set) var productionStore: ProductionStore?
    private(set) var workflowStore: PhoneWorkflowStore?
    private(set) var transcriptStore: PhoneTranscriptStore?
    private(set) var engine: (any LocalEngine)?
    init(bundle: Bundle = .main) {
        allowMobileData = UserDefaults.standard.bool(forKey: "models.mobile-data")
        pendingDubs = (UserDefaults.standard.stringArray(forKey: "dub.queue") ?? []).compactMap(UUID.init(uuidString:))
        do {
            let root: URL = try {
                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("--ui-smoke") {
                    return FileManager.default.temporaryDirectory.appendingPathComponent("PhoneModels-smoke-" + UUID().uuidString, isDirectory: true)
                }
                #endif
                return try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("PhoneModels", isDirectory: true)
            }()
            let store = try ModelStore(root: root)
            self.store = store; engine = NativeLocalEngine(store: store)
            guard let url = bundle.url(forResource: "models", withExtension: "json", subdirectory: "Catalogs") else { throw ClientError("mobile.local.catalogue_failed") }
            models = try ModelCatalogue.decode(Data(contentsOf: url)).models
            do { dubStore = try PhoneDubStore(root: root.appendingPathComponent("dubs")) }
            catch { self.error = ClientError("mobile.errors.file") }
            do { voiceStore = try PhoneVoiceStore(root: root.appendingPathComponent("voices")) }
            catch { self.error = ClientError("mobile.errors.file") }
            do { generationStore = try PhoneGenerationStore(root: root.appendingPathComponent("recent")) }
            catch { self.error = ClientError("mobile.errors.file") }
            do { productionStore = try ProductionStore(root: root.appendingPathComponent("production")) }
            catch { self.error = ClientError("mobile.errors.file") }
            do { workflowStore = try PhoneWorkflowStore(root: root.appendingPathComponent("workflows")) }
            catch { self.error = ClientError("mobile.errors.file") }
            do { transcriptStore = try PhoneTranscriptStore(root: root.appendingPathComponent("transcripts")) }
            catch { self.error = ClientError("mobile.errors.file") }
        } catch {
            // Initialization remains usable for the paired-host flows; the Models page explains the failure.

            self.error = error as? ClientError ?? ClientError("mobile.local.catalogue_failed")
        }
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-smoke"),
           ProcessInfo.processInfo.arguments.contains("--ui-phone-history") {
            Task {
                if let generationStore {
                    _ = try? await generationStore.save(text: "Saved audio for deletion review",
                        audio: LocalAudio(samples: Array(repeating: 0, count: 16000), sampleRate: 16000))
                    await refresh()
                }
            }
        }
        #endif
    }
    func refresh() async {
        guard let store else { return }
        var downloaded = Set<String>(); var permitted = Set<String>(); var bytes: [String: Int64] = [:]
        for model in models {
            if await store.installed(model) { downloaded.insert(model.id) }
            if await store.allowed(model) { permitted.insert(model.id) }
            bytes[model.id] = await store.modelStorage(model)
        }
        installed = downloaded; accepted = permitted; modelBytes = bytes; used = await store.storageUsed()
        do { voices = try await voiceStore?.list() ?? [] }
        catch { self.error = ClientError("mobile.errors.file") }
        do { recent = try await generationStore?.list() ?? [] }
        catch { self.error = ClientError("mobile.errors.file") }
        do { dubs = try await dubStore?.list() ?? [] }
        catch { self.error = ClientError("mobile.errors.file") }

    }
    func ready(_ model: PhoneModel) -> Bool { installed.contains(model.id) && accepted.contains(model.id) && model.fits() }
    func candidates(_ capability: ModelCapability) -> [PhoneModel] { models.filter { $0.capability == capability } }
    func preferred(_ capability: ModelCapability) -> PhoneModel? {
        candidates(capability).first(where: ready) ?? candidates(capability).first(where: { $0.fits() }) ?? candidates(capability).first
    }
    func download(_ model: PhoneModel) {
        guard downloads[model.id] == nil, let store else { return }
        progress[model.id] = 0
        let policy = DownloadPolicy(allowMobileData: allowMobileData); let wifi = wifi
        downloads[model.id] = Task {
            defer { downloads[model.id] = nil; progress[model.id] = nil }
            do {
                try await store.download(model, policy: policy, wifi: { wifi.wifi }, progress: { fraction in
                    await MainActor.run { self.progress[model.id] = fraction }
                })
            } catch is CancellationError {} catch { self.error = error as? ClientError ?? ClientError("mobile.local.download_failed") }
            await refresh()
        }
    }
    func cancel(_ model: PhoneModel) { downloads[model.id]?.cancel() }
    func delete(_ model: PhoneModel) async {
        guard let store else { return }
        downloads[model.id]?.cancel(); await downloads[model.id]?.value
        do { try await store.delete(model); await refresh() }
        catch { self.error = error as? ClientError ?? ClientError("mobile.errors.file") }
    }
    @discardableResult func accept(_ model: PhoneModel, accepted: Bool) async -> Bool {
        guard let store else { return false }
        if !accepted { downloads[model.id]?.cancel() }
        do {
            guard let version = AppVersion.full(info: Bundle.main.infoDictionary) else { throw ClientError("mobile.local.model_incompatible") }
            try await store.accept(model, fingerprint: model.licence.terms_fingerprint, appVersion: version, accepted: accepted)
            await refresh()
            return true
        } catch { self.error = error as? ClientError ?? ClientError("mobile.errors.file"); return false }
    }
    var dubModels: [PhoneModel] { [selectedModel("dub-asr",.asr), selectedModel("dub-translate",.translate), selectedModel("dub",.clone)].compactMap { $0 } }
    var dubReady: Bool { dubModels.count == 3 && dubModels.allSatisfy(ready) }
    func startDub(_ id: UUID) {
        if activeDub != id && !pendingDubs.contains(id) { pendingDubs.append(id) }
        UserDefaults.standard.set(pendingDubs.map(\.uuidString),forKey: "dub.queue")
        startNextDub()
    }
    private func startNextDub() {
        guard dubTask == nil, !pendingDubs.isEmpty, let dubStore, let engine, let store else { return }
        let id = pendingDubs.removeFirst()
        UserDefaults.standard.set(pendingDubs.map(\.uuidString),forKey: "dub.queue")
        activeDub = id
        let selected = models
        let job = "dub:"+id.uuidString
        PhoneJobs.shared.start(id: job,title: L10n().t("mobile.tabs.dub"),cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: selected)) { progress in
            let request = try await dubStore.request(id)
            let pinned = selected.filter { request.modelPins[$0.id] != nil }
            let pipeline = PhoneDubPipeline(store: dubStore,engine: engine,authorize: {
                for item in pinned { try await store.checkPermission(item) }
            })
            _ = try await pipeline.run(id: id,models: pinned,report: { value in
                await MainActor.run { self.dubProgress = value }
                await progress(EngineProgress(stage: .running,fraction: Double(value.percent)/100))
            })
            return PhoneJobResult(file: nil)
        }
        watchDub(id,store: dubStore)
    }
    /// Reattach the phone-library watcher when resuming the coordinator's original snapshot.
    func resumeDub(_ id: UUID) {
        guard activeDub == nil || activeDub == id,let dubStore else { return }
        guard PhoneJobs.shared.resume(id: "dub:"+id.uuidString) else { return }
        activeDub = id
        watchDub(id,store: dubStore)
    }
    private func watchDub(_ id: UUID,store dubStore: PhoneDubStore) {
        dubTask?.cancel()
        let job = "dub:"+id.uuidString
        dubTask = Task {
            while PhoneJobs.shared.states[job]?.running == true {
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            }
            guard !Task.isCancelled else { return }
            let state = PhoneJobs.shared.states[job]
            if let failure = state?.error { self.error = failure }
            if state?.result != nil,let request = try? await dubStore.request(id) {
                guard !Task.isCancelled else { return }
                await onDubFinished?(request)
            }
            guard !Task.isCancelled else { return }
            await refresh()
            // A Resume tap may have replaced this watcher during an await above.
            guard !Task.isCancelled else { return }
            activeDub = nil; dubProgress = nil; dubTask = nil
            if state?.paused != true { startNextDub() }
        }
    }
    func cancelDub() { if let activeDub { PhoneJobs.shared.cancel(id: "dub:"+activeDub.uuidString) } }
    private var modelChoices: [String:String] = [:]
    func chooseModel(_ task: String, _ id: String) { modelChoices[task] = id; UserDefaults.standard.set(id,forKey: "model."+task) }
    func selectedModel(_ task: String, _ capability: ModelCapability) -> PhoneModel? {
        let id = modelChoices[task] ?? UserDefaults.standard.string(forKey: "model."+task)
        let choices = task == "speak-tts" ? models.filter(\.supportsSpeech) : candidates(capability)
        return choices.first { $0.id == id } ?? preferred(capability)
    }
    func preference(_ task: String) -> TaskLocation? { UserDefaults.standard.string(forKey: "run-on." + task).flatMap(TaskLocation.init(rawValue:)) }
    func choose(_ task: String, _ location: TaskLocation) { UserDefaults.standard.set(location.rawValue, forKey: "run-on." + task) }
}
