import Foundation
import Testing
import VoiceStudioKit

// Only the scheduler's UI label is irrelevant to these owned, denied OS grants.
struct L10n { func t(_ key: String) -> String { key } }
private final class JobOwnerStore: DiagnosticStorage, @unchecked Sendable {
    private let lock = NSLock()
    private var files: [String: Data] = [:]
    func read(_ name: String, maxBytes: Int) -> Data? { lock.withLock { files[name] } }
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
