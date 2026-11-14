import Foundation
import Observation
import MetricKit
import VoiceStudioKit

/// App-owned consent presentation, separate from the existing analytics choice.
@MainActor @Observable final class AppDiagnostics {
    private(set) var enabled = false
    private(set) var prompted = false
    private(set) var ready = false
    private(set) var available = false
    private(set) var saving = false
    private(set) var errorKey: String?
    private(set) var observationRevision: Int64 = 0
    private(set) var stallThresholdMs: Int64 = 600_000
    @ObservationIgnored private var reporter: DiagnosticReports?
    @ObservationIgnored private var subscriber: AppDiagnosticSubscriber?
    @ObservationIgnored private var started = false
    @ObservationIgnored private let disabled: Bool
    @ObservationIgnored private let injectedFixture: Bool
    @ObservationIgnored private var declinedThisRun = false
    @ObservationIgnored private var lastChoice = false
    @ObservationIgnored private var choiceGeneration: UInt64 = 0
    init(disabled: Bool = false, reporter: DiagnosticReports? = nil) {
        self.disabled = disabled; self.reporter = reporter; self.injectedFixture = reporter != nil
        if disabled { ready = true; prompted = true }
    }
    private var environmentKilled: Bool { !injectedFixture && Self.environmentDisabled() }
    var needsChoice: Bool { ready && available && !prompted }
    func load() async {
        guard !started, !disabled else { return }; started = true
        if environmentKilled { await revokeForHardKill(); return }
        if reporter == nil, let configuration = await Task.detached(priority: .utility, operation: { Self.configuredReporter() }).value {
            reporter = configuration.reporter; stallThresholdMs = configuration.stallMs
        }
        guard let reporter else { ready = true; prompted = true; return }
        await reporter.load(); refresh(); ready = true; observationRevision += 1; updateSubscription()
    }
    func setConsent(_ choice: Bool) async {
        guard let reporter, !disabled else { return }
        choiceGeneration &+= 1
        let generation = choiceGeneration
        saving = true; errorKey = nil; lastChoice = choice; observationRevision += 1
        if !choice {
            // Declining never holds the rest of the app behind a failed save.
            enabled = false; declinedThisRun = true; prompted = true; updateSubscription()
        }
        do { try await reporter.setConsent(choice) }
        catch { if generation == choiceGeneration { errorKey = "diagnostics.save_failed" } }
        guard generation == choiceGeneration else { return }
        refresh(); saving = false; updateSubscription()
    }
    var collecting: Bool { !disabled && enabled && reporter?.status.enabled == true }
    func captureStall() {
        guard collecting else { return }
        reporter?.capture(DiagnosticInput(category: "install_stalled", stage: "download", errorCode: "RUNTIME_DOWNLOAD_STALLED", reason: "no_progress"))
    }
    func retryConsent() async {
        if reporter == nil && environmentKilled { await revokeForHardKill() }
        else { await setConsent(lastChoice) }
    }
    private func revokeForHardKill() async {
        saving = true; defer { saving = false }
        enabled = false; available = false; prompted = true; ready = true
        lastChoice = false; observationRevision += 1; updateSubscription()
        // Cleanup precedes policy/destination loading: broken resources or a
        // blank destination must not preserve an earlier ON grant.
        if let directory = Self.ownedDirectory() {
            let success = await DiagnosticReports.revokeStoredConsent(directory: directory)
            errorKey = success ? nil : "diagnostics.save_failed"
        } else { errorKey = "diagnostics.save_failed" }
    }
    func flush() async { guard let reporter, !disabled else { return }; await reporter.flush(); refresh() }
    private func refresh() {
        guard let reporter else { return }
        let state = reporter.status
        if state.consentSaveFailed { errorKey = "diagnostics.save_failed" }
        available = state.available && !environmentKilled
        enabled = state.enabled && available
        prompted = state.prompted || declinedThisRun || !available
    }
    private func updateSubscription() {
        if !injectedFixture {
            if enabled, let reporter, let context=reporter.recoveryContext {
                BackgroundUpload.shared.setDiagnosticFailureHandler { [weak reporter] input, stamp in
                    _ = reporter?.captureRecovered(input, occurredAtMs:stamp, consentEpoch:context.epoch, recoveryKey:"ios:\(stamp):unknown", reportedAppVersion:context.appVersion)
                }
            } else { BackgroundUpload.shared.setDiagnosticFailureHandler(nil) }
        }
        // Explicit DI fixtures never subscribe to OS payloads; their core gate
        // and isolated fake transport own the test, independent of process env.
        if !injectedFixture, enabled, let reporter, subscriber == nil {
            let subscriber = AppDiagnosticSubscriber(reporter: reporter); self.subscriber = subscriber
            MXMetricManager.shared.add(subscriber)
        } else if !enabled, let subscriber {
            MXMetricManager.shared.remove(subscriber); self.subscriber = nil
        }
    }
    /// Keys/status are projected, never ClientError.params, localized messages or host URLs.
    func captureFailure(_ error: any Error, stage: String = "other", engineID: String? = nil, file: String = #filePath, line: Int = #line) {
        guard let reporter, collecting else { return }
        if error is CancellationError || (error as? URLError)?.code == .cancelled || (error as? CocoaError)?.code == .userCancelled { return }
        let failure = error as? ClientError
        guard let projected = AppDiagnosticFailure.project(key: failure?.key, status: failure?.status, stage: stage) else { return }
        var frames: [DiagnosticFrame] = []
        if let marker = file.range(of: "mobile/ios/VoiceStudio/", options: .backwards) {
            frames = [DiagnosticFrame(path: String(file[marker.lowerBound...]), line: line)]
        }
        reporter.capture(DiagnosticInput(category: projected.category, stage: stage, errorCode: projected.code, engineID: engineID, frames: frames), error: error)
    }
    nonisolated private static func environmentDisabled() -> Bool {
        ["OMNIVOICE_DIAGNOSTICS_DISABLED", "OMNIVOICE_ANALYTICS_DISABLED"].contains { key in
            guard let value = getenv(key) else { return false }
            return ["1", "true", "yes", "on"].contains(String(cString: value).trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        }
    }
    nonisolated private static func ownedDirectory() -> URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("Diagnostics", isDirectory: true)
    }
    nonisolated private static func configuredReporter() -> (reporter: DiagnosticReports, stallMs: Int64)? {
        do {
            let bundle = Bundle.main
            guard let policyURL = bundle.url(forResource: "diagnostics", withExtension: "json", subdirectory: "Catalogs"),
                  let destinationURL = bundle.url(forResource: "diagnostics-destination", withExtension: "json", subdirectory: "Catalogs"),
                  let version = AppVersion.full(info: bundle.infoDictionary),
                  let directory = ownedDirectory(),
                  let raw = try JSONSerialization.jsonObject(with: Data(contentsOf: destinationURL)) as? [String: String],
                  let site = raw["site"], let host = raw["host"], let appURL = raw["appUrl"] else { return nil }
            #if arch(arm64)
            let arch = "arm64"
            #elseif arch(x86_64)
            let arch = "x64"
            #else
            let arch = "unknown"
            #endif
            let policyData = try Data(contentsOf: policyURL)
            let policyObject = try JSONSerialization.jsonObject(with: policyData) as? [String: Any]
            let limits = policyObject?["limits"] as? [String: Int]
            let stallMs = Int64(max(600_000, limits?["stall_ms"] ?? 600_000))
            let configuredSite = getenv("SUPERMETRY_SITE_ID").map { String(cString: $0) } ?? site
            let configuredHost = getenv("SUPERMETRY_HOST").map { String(cString: $0) } ?? host
            let configuredApp = getenv("SUPERMETRY_APP_URL").map { String(cString: $0) } ?? appURL
            let reporter = try DiagnosticReports(directory: directory, policy: DiagnosticPolicy(data: policyData), destination: DiagnosticDestination(site: configuredSite, host: configuredHost, appUrl: configuredApp), appVersion: version, arch: arch)
            return (reporter, stallMs)
        } catch { return nil } // Corrupt/missing policy is unavailable, never implicit consent.
    }
}

/// MetricKit objects never cross into a Task or the durable queue. Only closed
/// observations and the OS-provided per-diagnostic app version are projected.
private final class AppDiagnosticSubscriber: NSObject, MXMetricManagerSubscriber, @unchecked Sendable {
    private let reporter: DiagnosticReports
    init(reporter: DiagnosticReports) { self.reporter = reporter }
    private struct Group: Hashable {
        let version: String
        let category: String
        let stage: String
        let reason: String
        let code: String
        let signal: String
    }
    func didReceive(_ payloads: [MXDiagnosticPayload]) {
        guard let context = reporter.recoveryContext else { return }
        for payload in payloads.prefix(8) {
            guard let begin = AppDiagnosticRecoveryWindow.milliseconds(payload.timeStampBegin.timeIntervalSince1970),
                  let end = AppDiagnosticRecoveryWindow.milliseconds(payload.timeStampEnd.timeIntervalSince1970) else { continue }
            // A payload can straddle an app update or consent choice. Its whole
            // window must be eligible; never attribute an earlier portion to ON.
            guard AppDiagnosticRecoveryWindow.permits(begin: begin, end: end, enabledAt: context.enabledAtMs) else { continue }
            var groups: [Group: Int] = [:]
            func add(_ diagnostic: MXDiagnostic, category: String, stage: String, reason: String, code: String = "UNKNOWN", signal: String = "unknown") {
                let version = diagnostic.applicationVersion
                guard version.utf8.count <= 64 else { return }
                let key = Group(version: version, category: category, stage: stage, reason: reason, code: code, signal: signal)
                guard groups[key] != nil || groups.count < 64 else { return }
                groups[key] = min((groups[key] ?? 0) + 1, 1_000_000)
            }
            for diagnostic in (payload.crashDiagnostics ?? []).prefix(1024) {
                let number = diagnostic.signal?.intValue
                if number == 15 { continue } // Deliberate termination is not a crash.
                let signal = [4: "SIGILL", 6: "SIGABRT", 8: "SIGFPE", 9: "SIGKILL", 10: "SIGBUS", 11: "SIGSEGV"][number ?? 0] ?? "unknown"
                add(diagnostic, category: "app_crash", stage: "other", reason: "crashed", code: number == 4 ? "NATIVE_ILLEGAL_INSTRUCTION" : "NATIVE_CRASH", signal: signal)
            }
            for diagnostic in (payload.hangDiagnostics ?? []).prefix(1024) { add(diagnostic, category: "operation_failed", stage: "other", reason: "no_progress") }
            // These are OS-reported resource exceptions, not guesses about an
            // engine or OOM. No stack, sizes, termination text or JSON is read.
            for diagnostic in (payload.cpuExceptionDiagnostics ?? []).prefix(1024) { add(diagnostic, category: "operation_failed", stage: "other", reason: "unknown") }
            for diagnostic in (payload.diskWriteExceptionDiagnostics ?? []).prefix(1024) { add(diagnostic, category: "operation_failed", stage: "other", reason: "unknown") }
            for (group, count) in groups {
                reporter.captureRecovered(DiagnosticInput(category: group.category, stage: group.stage, errorCode: group.code, reason: group.reason, signal: group.signal), occurredAtMs: end, consentEpoch: context.epoch, recoveryKey: "ios:\(end):\(group.reason)", reportedAppVersion: group.version, occurrenceCount: count)
            }
        }
    }
}
