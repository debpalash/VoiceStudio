import Foundation
import Observation
import UIKit
import VoiceStudioKit
@preconcurrency import BackgroundTasks

struct PhoneJobResult: Sendable {
    let file: URL?
    let text: String?
    let repo: String?
    let revision: String?
    init(file: URL?,text: String? = nil,repo: String? = nil,revision: String? = nil) { self.file = file; self.text = text; self.repo = repo; self.revision = revision }
}
enum PhoneJobPhase: Sendable { case foreground, requestingGPU, requestingCPU, continuedGPU, continuedCPU, switchingCPU, paused, completed }
struct PhoneJobState: Sendable {
    var progress = 0.0
    var running = true
    var result: PhoneJobResult?
    var error: ClientError?
    var paused = false
    var phase: PhoneJobPhase = .foreground
}
/// Owns user-started inference independently of screens. Saved steps, rather than an OS grant, provide durability.
@MainActor @Observable final class PhoneJobs {
    static let shared = PhoneJobs()
    typealias Operation = @Sendable (@escaping EngineProgressHandler) async throws -> PhoneJobResult
    private struct Recipe {
        let title: String
        let requiresGPU: Bool
        let cpuAvailable: Bool
        var allowCPU: Bool
        let identity: PhoneJobResumeIdentity
        let operation: Operation
    }
    private(set) var states: [String:PhoneJobState] = [:]
    var allowCPUFallback = UserDefaults.standard.bool(forKey: "jobs.cpu-fallback") {
        didSet { UserDefaults.standard.set(allowCPUFallback,forKey: "jobs.cpu-fallback") }
    }
    @ObservationIgnored private var tasks: [String:Task<Void,Never>] = [:]
    @ObservationIgnored private var recipes: [String:Recipe] = [:]
    @ObservationIgnored private var modes: [String:NativeComputeMode] = [:]
    @ObservationIgnored private var stops: [String:PhoneJobRestartGate] = [:]
    @ObservationIgnored private var grants: [String:UIBackgroundTaskIdentifier] = [:]
    @ObservationIgnored private var continued: [String:ContinuedTaskBox] = [:]
    @ObservationIgnored private var schedulerIDs: [String:String] = [:]
    @ObservationIgnored private var observer: NSObjectProtocol?
    private init() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        observer = NotificationCenter.default.addObserver(forName: ProcessInfo.thermalStateDidChangeNotification,object: nil,queue: .main) { _ in
            Task { @MainActor in if ProcessInfo.processInfo.thermalState == .critical { self.pauseAll() } }
        }
    }
    var supportsGPU: Bool {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) { return BGTaskScheduler.supportedResources.contains(.gpu) }
        #endif
        return false
    }
    private var safePower: Bool {
        ProcessInfo.processInfo.thermalState != .critical && !(UIDevice.current.batteryState == .unplugged && UIDevice.current.batteryLevel >= 0 && UIDevice.current.batteryLevel < 0.05)
    }
    @discardableResult func start(id: String,title: String,requiresGPU: Bool = true,cpuFallbackSupported: Bool = false,resumeIdentity: String? = nil,operation: @escaping Operation) -> Bool {
        guard tasks[id] == nil else { return false }
        recipes[id] = Recipe(title: title,requiresGPU: requiresGPU,cpuAvailable: cpuFallbackSupported,allowCPU: allowCPUFallback,identity: PhoneJobResumeIdentity(resumeIdentity),operation: operation)
        return begin(id: id)
    }
    @discardableResult func resume(id: String,identity: String? = nil) -> Bool {
        guard tasks[id] == nil, recipes[id]?.identity.matches(identity) == true else { return false }
        recipes[id]?.allowCPU = allowCPUFallback
        return begin(id: id)
    }
    func canResume(id: String,identity: String? = nil) -> Bool { states[id]?.paused == true && tasks[id] == nil && recipes[id]?.identity.matches(identity) == true }
    func discardStaleResume(id: String,identity: String?) {
        guard tasks[id] == nil,states[id]?.paused == true,let recipe = recipes[id],!recipe.identity.matches(identity) else { return }
        recipes[id] = nil; states[id] = nil
    }
    private func begin(id: String) -> Bool {
        guard let recipe = recipes[id] else { return false }
        guard safePower else {
            states[id] = PhoneJobState(running: false,error: ClientError("mobile.jobs.power_paused"),paused: true,phase: .paused); return false
        }
        guard UIApplication.shared.applicationState != .background else {
            states[id] = PhoneJobState(running: false,error: ClientError("mobile.jobs.background_paused"),paused: true,phase: .paused); return false
        }
        states[id] = PhoneJobState(); stops[id] = PhoneJobRestartGate(); modes[id] = .automatic
        grants[id] = UIApplication.shared.beginBackgroundTask(withName: id) { Task { @MainActor in self.expireGrant(id: id) } }
        // Foreground acceleration remains the default. A CPU grant can be held ready
        // for the explicit fallback without allowing foreground GPU work in background.
        launch(id: id,mode: .automatic)
        if recipe.requiresGPU && supportsGPU { requestContinuation(id: id,gpu: true) }
        else if !recipe.requiresGPU || (recipe.allowCPU && recipe.cpuAvailable) { requestContinuation(id: id,gpu: false) }
        return true
    }
    private func launch(id: String,mode: NativeComputeMode) {
        guard let recipe = recipes[id] else { return }
        modes[id] = mode
        tasks[id] = Task {
            var cancelled = false
            do {
                let result = try await NativeExecutionContext.$compute.withValue(mode) {
                    try await recipe.operation { value in await self.report(id: id,fraction: value.fraction) }
                }
                try Task.checkCancellation()
                states[id]?.result = result; states[id]?.progress = 1; states[id]?.phase = .completed
            } catch is CancellationError { cancelled = true }
            catch { states[id]?.error = error as? ClientError ?? ClientError("mobile.errors.file") }
            // NativeLocalEngine waits for the detached worker and its leases to be
            // released before returning cancellation. Only then can a fresh task run.
            let restart = stops[id]?.workerFinished() == true
            tasks[id] = nil
            if cancelled && restart && safePower && grant(id: id) != .none {
                states[id]?.paused = false; states[id]?.error = nil
                states[id]?.phase = continued[id] == nil ? .foreground : .continuedCPU
                launch(id: id,mode: .cpuOnly)
                return
            }
            if cancelled { states[id]?.paused = true; states[id]?.phase = .paused }
            states[id]?.running = false
            finish(id: id)
        }
    }
    private func requestContinuation(id: String,gpu: Bool) {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *), let recipe = recipes[id], UIApplication.shared.applicationState != .background {
            guard !gpu || supportsGPU else { return }
            closeContinuation(id: id,success: false)
            let identifier = "sh.voicestudio.mobile.processing."+UUID().uuidString
            schedulerIDs[id] = identifier
            states[id]?.phase = gpu ? .requestingGPU : .requestingCPU
            let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier,using: .main) { task in
                let boxed = ContinuedTaskBox(task,gpu: gpu)
                Task { @MainActor in
                    guard self.schedulerIDs[id] == identifier,self.tasks[id] != nil,let processing = boxed.task as? BGContinuedProcessingTask else { boxed.task.setTaskCompleted(success: false); return }
                    self.continued[id] = boxed
                    processing.progress.totalUnitCount = 1000
                    processing.progress.completedUnitCount = Int64((self.states[id]?.progress ?? 0)*1000)
                    processing.expirationHandler = { Task { @MainActor in self.pause(id: id,stop: .expired) } }
                    if self.states[id]?.phase != .switchingCPU { self.states[id]?.phase = gpu && self.modes[id] != .cpuOnly ? .continuedGPU : .continuedCPU }
                }
            }
            guard registered else { continuationDenied(id: id,gpu: gpu); return }
            let request = BGContinuedProcessingTaskRequest(identifier: identifier,title: recipe.title,subtitle: L10n().t("mobile.jobs.checkpointed"))
            request.strategy = .fail
            if gpu { request.requiredResources = .gpu }
            do { try BGTaskScheduler.shared.submit(request) }
            catch {
                // The scheduler validates the effective signed entitlement/provisioning.
                // Device support or a source .entitlements file alone is not authorization.
                continuationDenied(id: id,gpu: gpu)
            }
        }
        #endif
    }
    private func continuationDenied(id: String,gpu: Bool) {
        closeContinuation(id: id,success: false)
        states[id]?.phase = .foreground
        if gpu,let recipe = recipes[id],recipe.allowCPU,recipe.cpuAvailable { requestContinuation(id: id,gpu: false) }
    }
    private func report(id: String,fraction: Double) {
        guard fraction.isFinite else { return }
        states[id]?.progress = min(1,max(states[id]?.progress ?? 0,fraction))
        #if compiler(>=6.2)
        if #available(iOS 26.0,*),let task = continued[id]?.task as? BGContinuedProcessingTask {
            task.progress.completedUnitCount = Int64((states[id]?.progress ?? 0)*1000)
        }
        #endif
        if !safePower { pause(id: id,reason: "mobile.jobs.power_paused",stop: .power) }
    }
    func cancel(id: String) {
        stops[id]?.request(.user); states[id]?.paused = true; tasks[id]?.cancel()
    }
    /// Start a consented handoff while the app is still transitioning out of the foreground.
    func enteredInactive() {
        for id in tasks.keys {
            guard let recipe = recipes[id],recipe.allowCPU,recipe.cpuAvailable else { continue }
            if backgroundDecision(id: id) == .restartOnCPU { handoff(id: id) }
        }
    }
    func enteredBackground() {
        for id in tasks.keys {
            switch backgroundDecision(id: id) {
            case .continueCurrent: break
            case .restartOnCPU: handoff(id: id)
            case .checkpointAndPause: pause(id: id,stop: .expired)
            }
        }
    }
    private func backgroundDecision(id: String) -> PhoneBackgroundDecision {
        guard let recipe = recipes[id] else { return .checkpointAndPause }
        return PhoneBackgroundPolicy.decision(requiresGPU: recipe.requiresGPU,compute: modes[id] ?? .automatic,grant: grant(id: id),supportsGPU: supportsGPU,allowCPUFallback: recipe.allowCPU,cpuAvailable: recipe.cpuAvailable)
    }
    private func handoff(id: String) {
        guard states[id]?.phase != .switchingCPU else { return }
        states[id]?.phase = .switchingCPU; stops[id]?.request(.cpuHandoff); tasks[id]?.cancel()
    }
    private func grant(id: String) -> PhoneBackgroundGrant {
        if let task = continued[id] { return task.gpu ? .continuedGPU : .continuedCPU }
        if let finite = grants[id], finite != .invalid { return .finiteCPU }
        return .none
    }
    private func expireGrant(id: String) {
        if let finite = grants.removeValue(forKey: id),finite != .invalid { UIApplication.shared.endBackgroundTask(finite) }
        if continued[id] == nil { pause(id: id,stop: .expired) }
    }
    private func pause(id: String,reason: String = "mobile.jobs.background_paused",stop: PhoneJobRestartGate.Stop) {
        stops[id]?.request(stop); states[id]?.paused = true; states[id]?.error = ClientError(reason); tasks[id]?.cancel()
    }
    private func pauseAll() { for id in tasks.keys { pause(id: id,reason: "mobile.jobs.power_paused",stop: .power) } }
    private func closeContinuation(id: String,success: Bool) {
        continued.removeValue(forKey: id)?.task.setTaskCompleted(success: success)
        if let identifier = schedulerIDs.removeValue(forKey: id) { BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: identifier) }
    }
    private func finish(id: String) {
        closeContinuation(id: id,success: states[id]?.result != nil)
        if let finite = grants.removeValue(forKey: id),finite != .invalid { UIApplication.shared.endBackgroundTask(finite) }
        modes[id] = nil; stops[id] = nil
        if states[id]?.result != nil { recipes[id] = nil }
    }
    func statusKey(id: String) -> String {
        switch states[id]?.phase {
        case .requestingGPU: return "mobile.jobs.requesting_gpu"
        case .requestingCPU: return "mobile.jobs.requesting_cpu"
        case .continuedGPU: return "mobile.jobs.continued_gpu"
        case .continuedCPU: return "mobile.jobs.continued_cpu"
        case .switchingCPU: return "mobile.jobs.switching_cpu"
        case .paused: return "mobile.jobs.background_paused"
        default:
            if recipes[id]?.requiresGPU == true && modes[id] == .automatic { return "mobile.jobs.gpu_unavailable" }
            return "mobile.jobs.background_limited"
        }
    }
}
private final class ContinuedTaskBox: @unchecked Sendable {
    let task: BGTask
    let gpu: Bool
    init(_ task: BGTask,gpu: Bool) { self.task = task; self.gpu = gpu }
}
