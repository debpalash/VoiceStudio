import Foundation
import Testing
import VoiceStudioKit

// Only the scheduler's UI label is irrelevant to these owned, denied OS grants.
struct L10n { func t(_ key: String) -> String { key } }
private final class JobOwnerStore: DiagnosticStorage, @unchecked Sendable {
    private let lock = NSLock()
    private var files: [String: Data] = [:]
    private var unreadConsent = false
    func failConsentReads(_ value: Bool) { lock.withLock { unreadConsent = value } }
    func saved(_ name: String) -> Data? { lock.withLock { files[name] } }
    func read(_ name: String, maxBytes: Int) throws -> Data? { try lock.withLock {
        if name == "consent.json", unreadConsent { throw DiagnosticFailure.storageUnavailable }
        return files[name]
    } }
    func write(_ name: String, data: Data) { lock.withLock { files[name] = data } }
    func remove(_ name: String) { _ = lock.withLock { files.removeValue(forKey: name) } }
}
private final class JobOwnerTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var sent = 0
    var count: Int { lock.withLock { sent } }
    func cancelAll() {}
    func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int { lock.withLock { sent += 1 }; return 201 }
}
private actor JobOwnerExecution {
    private var count = 0
    private(set) var modes: [NativeComputeMode] = []
    let entered: AsyncStream<Void>
    private let signal: AsyncStream<Void>.Continuation
    init() { (entered, signal) = AsyncStream.makeStream(bufferingPolicy: .bufferingNewest(1)) }
    func run() async throws -> PhoneJobResult {
        count += 1; modes.append(NativeExecutionContext.compute)
        if count == 1 {
            signal.yield(())
            try await Task.sleep(for: .seconds(30))
            throw CancellationError()
        }
        throw ClientError("mobile.local.inference_failed")
    }
    func waitForFirstRun() async { for await _ in entered { return } }
}
@MainActor private func jobOwnerFinished(_ jobs: PhoneJobs, id: String) async -> Bool {
    let deadline = ContinuousClock.now.advanced(by: .seconds(5))
    while jobs.states[id]?.running == true, ContinuousClock.now < deadline { await Task.yield() }
    return jobs.states[id]?.running == false
}
@Suite(.serialized) struct PhoneJobDiagnosticOwnerTests {
    @MainActor @Test(.timeLimit(.minutes(1)), arguments: ["off-on", "regrant", "same-grant", "withdrawn", "manual-resume"])
    func automaticCPUHandoffRetainsTheUserStartedDiagnosticOwner(_ scenario: String) async throws {
        let policy = try DiagnosticPolicy(data: Data(contentsOf: URL(fileURLWithPath: ProcessInfo.processInfo.environment["VOICE_JOB_POLICY_PATH"]!)))
        let store = JobOwnerStore(), transport = JobOwnerTransport()
        let reporter = try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-owned-job-fixture"), policy: policy, destination: DiagnosticDestination(site: "fixture01", host: "http://127.0.0.1:1", appUrl: "https://app.test.invalid"), appVersion: "0.5.9-beta.3", transport: transport, storage: store, disabled: { false })
        let diagnostics = AppDiagnostics(reporter: reporter)
        await diagnostics.load()
        if scenario != "off-on" && scenario != "manual-resume" { await diagnostics.setConsent(true) }
        let jobs = PhoneJobs.shared
        let oldFallback = jobs.allowCPUFallback
        let oldObservation = jobs.diagnosticFailureObservation, oldFailure = jobs.onFailure
        let id = "generate:" + UUID().uuidString
        defer { jobs.cancel(id: id); jobs.allowCPUFallback = oldFallback; jobs.diagnosticFailureObservation = oldObservation; jobs.onFailure = oldFailure }
        jobs.allowCPUFallback = true
        jobs.diagnosticFailureObservation = { diagnostics.failureObservation }
        jobs.onFailure = { stage, error, observation in diagnostics.captureObservedFailure(error, observation: observation, stage: stage) }
        let operation = JobOwnerExecution()
        try #require(jobs.start(id: id, title: "Owned fixture", requiresGPU: true, cpuFallbackSupported: true) { _ in try await operation.run() })
        await operation.waitForFirstRun()
        if scenario == "regrant" || scenario == "withdrawn" { await diagnostics.setConsent(false) }
        if scenario == "off-on" || scenario == "regrant" || scenario == "manual-resume" { await diagnostics.setConsent(true) }
        if scenario == "manual-resume" {
            jobs.cancel(id: id)
            try #require(await jobOwnerFinished(jobs, id: id))
            try #require(jobs.resume(id: id))
        } else { jobs.enteredInactive() }
        try #require(await jobOwnerFinished(jobs, id: id))
        #expect(await operation.modes == [.automatic, scenario == "manual-resume" ? .automatic : .cpuOnly])
        await reporter.flush()
        let expected = scenario == "same-grant" || scenario == "manual-resume" ? 1 : 0
        #expect(transport.count == expected)
        if scenario == "withdrawn" { await diagnostics.setConsent(true) }
        let fresh = "generate:" + UUID().uuidString
        try #require(jobs.start(id: fresh, title: "Fresh fixture", requiresGPU: false) { _ in throw ClientError("mobile.local.inference_failed") })
        try #require(await jobOwnerFinished(jobs, id: fresh))
        await reporter.flush()
        #expect(transport.count == expected + 1)
        await reporter.close()
    }
}

@Suite struct IncomingPrivacyGateTests {
    @MainActor private func reporter(_ store: JobOwnerStore, _ transport: JobOwnerTransport) throws -> DiagnosticReports {
        let policy = try DiagnosticPolicy(data: Data(contentsOf: URL(fileURLWithPath: ProcessInfo.processInfo.environment["VOICE_JOB_POLICY_PATH"]!)))
        return try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-owned-gate-fixture"), policy: policy, destination: DiagnosticDestination(site: "fixture01", host: "http://127.0.0.1:1", appUrl: "https://app.test.invalid"), appVersion: "0.5.9-beta.3", transport: transport, storage: store, disabled: { false })
    }
    @MainActor @Test(arguments: [false, true])
    func unreadSavedConsentDoesNotBlockIncomingContentOrOpenReporting(_ savedOn: Bool) async throws {
        let store = JobOwnerStore(), transport = JobOwnerTransport()
        let previous = try reporter(store, transport)
        _ = try await previous.setConsent(true)
        await previous.close()
        if !savedOn {
            let bytes = try #require(store.saved("consent.json"))
            var saved = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
            saved["enabled"] = false
            store.write("consent.json", data: try JSONSerialization.data(withJSONObject: saved))
        }
        let original = try #require(store.saved("consent.json"))
        store.failConsentReads(true)
        let reports = try reporter(store, transport), diagnostics = AppDiagnostics(reporter: reports)
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: true), "Initial privacy check has not run")
        await diagnostics.load()
        #expect(diagnostics.allowsIncomingContent(analyticsAnswered: true), "An optional diagnostics read failure must not block shared content or shortcuts")
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: false))
        #expect(!diagnostics.ready); #expect(!diagnostics.needsChoice); #expect(!diagnostics.collecting)
        diagnostics.failureHandler(stage: "generation")(ClientError("mobile.local.inference_failed"))
        #expect(!reports.capture(DiagnosticInput(category: "generation_failed")))
        await reports.flush()
        #expect(transport.count == 0)
        await diagnostics.setConsent(true)
        #expect(store.saved("consent.json") == original)
        #expect(!diagnostics.ready)
        store.failConsentReads(false)
        await diagnostics.retryConsent()
        #expect(diagnostics.ready); #expect(diagnostics.enabled == savedOn)
        #expect(diagnostics.allowsIncomingContent(analyticsAnswered: true))
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: false))
        #expect(store.saved("consent.json") == original); #expect(transport.count == 0)
        await reports.close()
    }
    @MainActor @Test(arguments: [false, true])
    func startupAndTheRealFirstChoiceStillGateIncomingContent(_ allow: Bool) async throws {
        let store = JobOwnerStore(), transport = JobOwnerTransport(), reports = try reporter(store, transport)
        let diagnostics = AppDiagnostics(reporter: reports)
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: true))
        await diagnostics.load()
        #expect(diagnostics.needsChoice)
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: true))
        await diagnostics.setConsent(allow)
        #expect(!diagnostics.needsChoice)
        #expect(diagnostics.allowsIncomingContent(analyticsAnswered: true))
        #expect(!diagnostics.allowsIncomingContent(analyticsAnswered: false))
        #expect(diagnostics.collecting == allow)
        #expect(transport.count == 0)
        await reports.close()
    }
    @MainActor @Test func disabledAndUnavailableReportingDoNotBlockIncomingContent() async {
        let disabled = AppDiagnostics(disabled: true)
        #expect(disabled.allowsIncomingContent(analyticsAnswered: true))
        #expect(!disabled.allowsIncomingContent(analyticsAnswered: false))
        #expect(!disabled.collecting)
        let unavailable = AppDiagnostics()
        #expect(!unavailable.allowsIncomingContent(analyticsAnswered: true))
        // This test bundle has no app Catalogs. No destination or owned store is
        // opened, and the explicit fixture environment rules out ambient kills.
        await unavailable.load()
        #expect(!unavailable.available); #expect(!unavailable.collecting)
        #expect(unavailable.allowsIncomingContent(analyticsAnswered: true))
        #expect(!unavailable.allowsIncomingContent(analyticsAnswered: false))
    }
}
