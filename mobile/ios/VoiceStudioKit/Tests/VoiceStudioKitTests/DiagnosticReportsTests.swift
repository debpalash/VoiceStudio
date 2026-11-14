import Foundation
import Testing
@testable import VoiceStudioKit

private final class DiagnosticTestStore: DiagnosticStorage, @unchecked Sendable {
    let lock = NSLock()
    private var files: [String: Data] = [:]
    private var failWrite = false
    var blockOutbox = false
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    func waitForEntry() async -> Bool {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async { continuation.resume(returning: self.entered.wait(timeout: .now() + 2) == .success) }
        }
    }
    func read(_ name: String, maxBytes: Int) throws -> Data? {
        try lock.withLock { guard let value = files[name] else { return nil }; guard value.count <= maxBytes else { throw DiagnosticFailure.corruptStore }; return value }
    }
    func write(_ name: String, data: Data) throws {
        let blocked = try lock.withLock { if failWrite { throw DiagnosticFailure.storageUnavailable }; return blockOutbox && name == "outbox.json" }
        if blocked { entered.signal(); _ = release.wait(timeout: .now() + 5) }
        lock.withLock { files[name] = data }
    }
    func remove(_ name: String) throws { _ = lock.withLock { files.removeValue(forKey: name) } }
    func set(_ name: String, _ data: Data) { lock.withLock { files[name] = data } }
    func failing(_ value: Bool) { lock.withLock { failWrite = value } }
    func has(_ name: String) -> Bool { lock.withLock { files[name] != nil } }
    func data(_ name: String) -> Data? { lock.withLock { files[name] } }
}
private final class DiagnosticTestClock: DiagnosticClock, @unchecked Sendable {
    let lock = NSLock()
    private var time: Int64 = 1_794_640_000_000
    func nowMs() -> Int64 { lock.withLock { time } }
    func advance(_ ms: Int64) { lock.withLock { time += ms } }
    func sleep(milliseconds: Int) async throws { await Task.yield() }
}
private final class DiagnosticTestTransport: DiagnosticTransport, @unchecked Sendable {
    let lock = NSLock()
    private var sent: [Data] = []
    private var code = 503
    private var hold = false
    private var cancellations = 0
    var cancellationCount: Int { lock.withLock { cancellations } }
    private var continuation: CheckedContinuation<Int, any Error>?
    func set(code: Int) { lock.withLock { self.code = code } }
    func setHolding(_ hold: Bool) { lock.withLock { self.hold = hold } }
    func bodies() -> [Data] { lock.withLock { sent } }
    func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int {
        try await withCheckedThrowingContinuation { continuation in
            lock.withLock {
                guard allowed() else { continuation.resume(throwing: DiagnosticFailure.cancelled); return }
                sent.append(body)
                if hold { self.continuation = continuation } else { continuation.resume(returning: code) }
            }
        }
    }
    func cancelAll() {
        let pending = lock.withLock { cancellations += 1; let result = continuation; continuation = nil; return result }
        pending?.resume(throwing: DiagnosticFailure.cancelled)
    }
}
private func diagnosticTestPolicy() throws -> DiagnosticPolicy {
    var root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    for _ in 0..<10 {
        let file = root.appendingPathComponent("config/diagnostics.json")
        if FileManager.default.fileExists(atPath: file.path) { return try DiagnosticPolicy(data: Data(contentsOf: file)) }
        root.deleteLastPathComponent()
    }
    throw DiagnosticFailure.invalidPolicy
}
private final class DiagnosticTestKill: @unchecked Sendable {
    private let lock=NSLock()
    private var killed=false
    var value: Bool { lock.withLock { killed } }
    func set(_ value: Bool) { lock.withLock { killed=value } }
}
private func diagnosticTestReporter(_ store: any DiagnosticStorage, _ transport: any DiagnosticTransport, _ clock: DiagnosticTestClock = DiagnosticTestClock(), appVersion: String = "0.5.9-beta.2", buildCommit: String = String(repeating: "a", count: 40), disabled: @escaping @Sendable () -> Bool = { false }) throws -> DiagnosticReports {
    try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-test-diagnostics"), policy: diagnosticTestPolicy(), destination: DiagnosticDestination(site: "localtest123", host: "http://127.0.0.1:1", appUrl: "https://app.test.invalid"), appVersion: appVersion, buildCommit: buildCommit, transport: transport, clock: clock, storage: store, disabled: disabled)
}
private func diagnosticObject(_ data: Data) throws -> [String: Any] { try #require(JSONSerialization.jsonObject(with: data) as? [String: Any]) }
private func diagnosticRecords(_ store: DiagnosticTestStore) throws -> [[String: Any]] {
    guard let data = store.data("outbox.json") else { return [] }
    return try #require(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
}
private func diagnosticEvent() -> DiagnosticInput { DiagnosticInput(category: "generation_failed", stage: "generation", errorType: "TypeError") }

@Suite("DiagnosticReports") struct DiagnosticReportsTests {
    @Test func defaultOffIndependentAndNoPreConsentPersistence() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport)
        #expect(!reports.capture(diagnosticEvent(), error: URLError(.timedOut)))
        #expect(!store.has("consent.json")); #expect(!store.has("outbox.json"))
        await reports.load()
        #expect(!reports.status.enabled); #expect(transport.bodies().isEmpty)
        try await reports.setConsent(true)
        #expect(reports.capture(diagnosticEvent())); await reports.flush()
        #expect(!transport.bodies().isEmpty); await reports.close()
    }
    @Test func captureDoesNotWaitForBlockedPersistence() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        store.lock.withLock { store.blockOutbox = true }
        #expect(reports.capture(diagnosticEvent()))
        #expect(await store.waitForEntry())
        let start = ContinuousClock.now
        #expect(reports.capture(diagnosticEvent()))
        #expect(start.duration(to: .now) < .milliseconds(100))
        store.lock.withLock { store.blockOutbox = false }; store.release.signal()
        await reports.close()
    }
    @Test func projectsClosedMetadataAndReleasesErrorPayload() async throws {
        final class Payload {}
        final class SecretError: Error, @unchecked Sendable { let payload: Payload; init(_ payload: Payload) { self.payload = payload } }
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        weak var weakPayload: Payload?
        autoreleasepool {
            let payload = Payload(); weakPayload = payload
            #expect(reports.capture(DiagnosticInput(category: "hf_secret", stage: "/home/private", errorType: "private voice", engineID: "secret model", frames: [DiagnosticFrame(path: "/home/secret.swift", line: 1)], reason: "private"), error: SecretError(payload)))
        }
        #expect(weakPayload == nil)
        await reports.flush(); await reports.close()
        let saved = try #require(diagnosticRecords(store).first)
        let props = try #require(saved["props"] as? [String: Any])
        #expect(props["category"] as? String == "operation_failed"); #expect(props["error_type"] as? String == "OtherError")
        #expect(props["engine_id"] as? String == "unknown"); #expect(props["frames"] == nil)
        #expect(!String(decoding: try #require(store.data("outbox.json")), as: UTF8.self).contains("secret"))
    }
    @Test func durableReplayPreservesIdentityAndTimestamp() async throws {
        let store = DiagnosticTestStore(), first = DiagnosticTestTransport(), clock = DiagnosticTestClock()
        let reports = try diagnosticTestReporter(store, first, clock); try await reports.setConsent(true)
        reports.capture(diagnosticEvent()); await reports.flush(); await reports.close()
        let saved = try #require(diagnosticRecords(store).first)
        let delivery = DiagnosticTestTransport(); delivery.set(code: 201)
        let next = try diagnosticTestReporter(store, delivery, clock); await next.load(); await next.flush()
        let body = try diagnosticObject(#require(delivery.bodies().first))
        #expect(body["id"] as? String == saved["id"] as? String); #expect(body["ts"] as? Int64 == saved["timestamp_ms"] as? Int64)
        #expect(!store.has("outbox.json")); await next.close()
    }
    @Test func withdrawalCancelsInFlightPurgesAndMintsNewEpoch() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport(); transport.setHolding(true)
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        let old = try #require(reports.recoveryContext?.epoch)
        reports.capture(diagnosticEvent())
        for _ in 0..<100 where transport.bodies().isEmpty { await Task.yield() }
        #expect(transport.bodies().count == 1)
        try await reports.setConsent(false); await reports.flush()
        #expect(!reports.status.enabled); #expect(reports.recoveryContext == nil); #expect(!store.has("outbox.json"))
        #expect(!reports.capture(diagnosticEvent())); #expect(transport.bodies().count == 1)
        try await reports.setConsent(true); #expect(reports.recoveryContext?.epoch != old); await reports.close()
    }
    @Test func recoveredCrashRequiresEpochTimeAndCanonicalHistoricalVersion() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport(), clock = DiagnosticTestClock(); transport.set(code: 201)
        let reports = try diagnosticTestReporter(store, transport, clock); try await reports.setConsent(true)
        let context = try #require(reports.recoveryContext); clock.advance(1000)
        let occurred = clock.nowMs(), key = "ios:\(clock.nowMs()):crashed", input = DiagnosticInput(category: "app_crash", reason: "crashed")
        #expect(!reports.captureRecovered(input, occurredAtMs: context.enabledAtMs - 1, consentEpoch: context.epoch, recoveryKey: "ios:\(context.enabledAtMs - 1):crashed", reportedAppVersion: "0.5.8-beta.1"))
        #expect(!reports.captureRecovered(input, occurredAtMs: occurred, consentEpoch: String(repeating: "f", count: 32), recoveryKey: key, reportedAppVersion: "0.5.8-beta.1"))
        #expect(!reports.captureRecovered(input, occurredAtMs: occurred, consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.8-secret-token"))
        for _ in 0..<2 {
            #expect(reports.captureRecovered(input, occurredAtMs: occurred, consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.8-beta.1")); await reports.flush()
        }
        let bodies = try transport.bodies().map(diagnosticObject)
        #expect(Set(bodies.compactMap { $0["id"] as? String }).count == 1)
        let props = try #require(bodies.first?["props"] as? [String: Any])
        #expect(props["app_version"] as? String == "0.5.8-beta.1"); #expect(props["build_commit"] == nil)
        #expect(reports.captureRecovered(DiagnosticInput(category: "app_crash", reason: "killed"), occurredAtMs: occurred, consentEpoch: context.epoch, recoveryKey: "ios:\(occurred):killed", reportedAppVersion: "0.5.8b1")); await reports.flush()
        #expect(try diagnosticObject(#require(transport.bodies().last))["props"].flatMap { $0 as? [String: Any] }?["app_version"] as? String == "0.5.8b1")
        #expect(reports.captureRecovered(input, occurredAtMs: occurred, consentEpoch: context.epoch, recoveryKey: "ios:\(occurred):oom")); await reports.flush()
        #expect(try diagnosticObject(#require(transport.bodies().last))["props"].flatMap { $0 as? [String: Any] }?["app_version"] == nil)
        await reports.close()
    }
    @Test func legacyConsentCannotRecoverHistoryAndExpiredRecordsAreDropped() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport(), clock = DiagnosticTestClock()
        let reports = try diagnosticTestReporter(store, transport, clock); try await reports.setConsent(true)
        reports.capture(diagnosticEvent()); await reports.flush(); await reports.close()
        var consent = try diagnosticObject(#require(store.data("consent.json"))); consent.removeValue(forKey: "enabled_at_ms")
        store.set("consent.json", try JSONSerialization.data(withJSONObject: consent))
        let legacy = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); await legacy.load(); #expect(legacy.recoveryContext == nil); await legacy.close()
        clock.advance(604800001)
        let expired = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); await expired.load(); await expired.flush(); #expect(!store.has("outbox.json")); await expired.close()
        store.set("consent.json", Data("{broken".utf8)); store.set("outbox.json", Data("[]".utf8))
        let broken = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); await broken.load(); #expect(!broken.status.enabled); #expect(!store.has("outbox.json")); await broken.close()
    }
    @Test func boundedQueueExpectedCancellationAndConsentWriteFailure() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        #expect(!reports.capture(diagnosticEvent(), error: CancellationError()))
        for line in 1...100 { reports.capture(DiagnosticInput(category: "generation_failed", frames: [DiagnosticFrame(path: "backend/core/tasks.py", line: line)])) }
        await reports.close()
        #expect(try diagnosticRecords(store).count <= 64); #expect((store.data("outbox.json")?.count ?? 0) <= 524288)
        let next = try diagnosticTestReporter(store, DiagnosticTestTransport()); await next.load(); store.failing(true)
        do { try await next.setConsent(false); Issue.record("Expected unavailable consent storage") } catch {}
        #expect(!next.capture(diagnosticEvent())); store.failing(false); await next.close()
    }
    #if os(macOS)
    @Test func realLocalHTTPUsesPublicEndpointAndDoesNotFollowRedirect() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("diagnostic-http-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let output = directory.appendingPathComponent("requests.json")
        let script = """
import http.server,json,sys
requests=[]
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  requests.append({'path':self.path,'headers':dict(self.headers),'body':self.rfile.read(int(self.headers['Content-Length'])).decode()})
  with open(sys.argv[1],'w',encoding='utf-8') as f:json.dump(requests,f)
  self.send_response(201 if len(requests)==1 else 302)
  if len(requests)>1:self.send_header('Location','http://127.0.0.1:'+str(self.server.server_port)+'/private')
  self.end_headers()
 def log_message(self,*args):pass
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()
"""
        let process = try TestProcess(["python3", "-c", script, output.path]); defer { process.stop() }
        let port = try process.port(within: 5)
        let destination = try DiagnosticDestination(site: "localtest123", host: "http://127.0.0.1:\(port)", appUrl: "https://app.test.invalid")
        let transport = URLSessionDiagnosticTransport()
        #expect(try await transport.send(destination: destination, body: Data("{\"v\":1}".utf8), allowed: { true }) == 201)
        #expect(try await transport.send(destination: destination, body: Data("{}".utf8), allowed: { true }) == 302)
        let requests = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: output)) as? [[String: Any]])
        #expect(requests.count == 2); #expect(requests.first?["path"] as? String == "/api/public/v1/events")
        let headers = try #require(requests.first?["headers"] as? [String: String])
        #expect(headers["Origin"] == "https://app.test.invalid"); #expect(headers["Authorization"] == nil); #expect(headers["Cookie"] == nil); #expect(headers["Referer"] == nil)
        transport.cancelAll()
    }
    #endif
}

extension DiagnosticReportsTests {
@Test func diagnosticKillSwitchMatchesExistingAnalyticsTruthiness() {
    for value in ["1", "true", "YES", " ON ", "True"] { #expect(diagnosticKillSwitch(value)) }
    for value in ["", "0", "false", "off", "private-value"] { #expect(!diagnosticKillSwitch(value)) }
    #expect(!diagnosticKillSwitch(nil))
}
}

extension DiagnosticReportsTests {
    @Test func quotedConsentScalarsNeverBecomePermission() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); try await first.setConsent(true); await first.close()
        let original = try diagnosticObject(#require(store.data("consent.json")))
        for (key, value) in [("enabled", "true"), ("version", "1"), ("enabled_at_ms", String(clock.nowMs()))] {
            var variant = original; variant[key] = value
            store.set("consent.json", try JSONSerialization.data(withJSONObject: variant))
            let transport = DiagnosticTestTransport()
            let next = try diagnosticTestReporter(store, transport, clock)
            await next.load(); #expect(!next.status.enabled); #expect(!next.capture(diagnosticEvent())); #expect(transport.bodies().isEmpty); await next.close()
        }
    }
    @Test func invalidRecoveryEnableTimeDoesNotInventHistory() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); try await first.setConsent(true); await first.close()
        let original = try diagnosticObject(#require(store.data("consent.json")))
        for stamp in [Int64(0), clock.nowMs() + 60001] {
            var variant = original; variant["enabled_at_ms"] = stamp
            store.set("consent.json", try JSONSerialization.data(withJSONObject: variant))
            let next = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); await next.load()
            #expect(next.status.enabled); #expect(next.recoveryContext == nil); await next.close()
        }
    }
    @Test func closeIsTerminalAndCannotBeReopenedByConsent() async throws {
        let store = DiagnosticTestStore(), reports = try diagnosticTestReporter(store, DiagnosticTestTransport())
        try await reports.setConsent(true); await reports.close(); try await reports.setConsent(true)
        #expect(!reports.status.enabled); #expect(!reports.capture(diagnosticEvent())); await reports.close()
    }
    @Test func failedRecordHasBoundedRetryEligibilityAcrossNewFailures() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        reports.capture(diagnosticEvent()); await reports.flush()
        let firstBody = try #require(transport.bodies().first)
        let firstObject = try diagnosticObject(firstBody)
        let firstID = try #require(firstObject["id"] as? String)
        #expect(try transport.bodies().map(diagnosticObject).filter { $0["id"] as? String == firstID }.count == 3)
        reports.capture(DiagnosticInput(category: "model_load_failed", stage: "model_load")); await reports.flush()
        #expect(try transport.bodies().map(diagnosticObject).filter { $0["id"] as? String == firstID }.count == 3)
        await reports.close()
    }
}

extension DiagnosticReportsTests {
    @Test func failedWithdrawalCannotReloadAnOldEnabledGrant() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock)
        try await first.setConsent(true)
        #expect(first.status.enabled)
        store.failing(true)
        do { try await first.setConsent(false); Issue.record("Expected consent save failure") } catch {}
        #expect(!first.status.enabled)
        await first.close()
        store.failing(false)
        let next = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock)
        await next.load()
        #expect(!next.status.enabled)
        #expect(!next.capture(diagnosticEvent()))
        await next.close()
    }
}

extension DiagnosticReportsTests {
    @Test func recoveryGroupsPreserveIdentityCountsAndVersionAcrossUpgrade() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); try await first.setConsent(true)
        let context = try #require(first.recoveryContext); clock.advance(1000)
        let key = "ios:\(clock.nowMs()):crashed", input = DiagnosticInput(category: "app_crash", reason: "crashed", signal: "SIGSEGV")
        #expect(first.captureRecovered(input, occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.9-beta.2", occurrenceCount: 3))
        await first.flush(); await first.close()
        let saved = try #require(diagnosticRecords(store).first)
        let next = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, appVersion: "0.5.10-beta.1", buildCommit: String(repeating: "b", count: 40))
        await next.load(); await next.flush()
        #expect(next.captureRecovered(input, occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.9-beta.2", occurrenceCount: 3))
        await next.flush(); await next.close()
        let replayed = try #require(diagnosticRecords(store).first)
        #expect(saved["id"] as? String == replayed["id"] as? String)
        #expect(saved["timestamp_ms"] as? Int64 == replayed["timestamp_ms"] as? Int64)
        let props = try #require(replayed["props"] as? [String: Any])
        #expect(props["occurrences"] as? Int == 3); #expect(props["build_commit"] == nil)
    }
    @Test func recoverySignalsAndVersionsWithinSameWindowDoNotCollide() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock()
        let reports = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock); try await reports.setConsent(true)
        let context = try #require(reports.recoveryContext); clock.advance(1000)
        let key = "ios:\(clock.nowMs()):crashed", input = DiagnosticInput(category: "app_crash", reason: "crashed", signal: "SIGSEGV")
        #expect(!reports.captureRecovered(input, occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.8-beta.1", occurrenceCount: 0))
        #expect(reports.captureRecovered(input, occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.8-beta.1", occurrenceCount: 2))
        #expect(reports.captureRecovered(DiagnosticInput(category: "app_crash", reason: "crashed", signal: "SIGILL"), occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.8-beta.1"))
        #expect(reports.captureRecovered(input, occurredAtMs: clock.nowMs(), consentEpoch: context.epoch, recoveryKey: key, reportedAppVersion: "0.5.9-beta.2"))
        await reports.close()
        let records = try diagnosticRecords(store)
        #expect(Set(records.compactMap { $0["id"] as? String }).count == 3)
        #expect(records.compactMap { ($0["props"] as? [String: Any])?["occurrences"] as? Int }.sorted() == [1, 1, 2])
    }
}

extension DiagnosticReportsTests {
    @Test func cocoaUserCancellationNeverQueuesOrSends() async throws {
        let store = DiagnosticTestStore(), transport = DiagnosticTestTransport()
        let reports = try diagnosticTestReporter(store, transport); try await reports.setConsent(true)
        #expect(!reports.capture(diagnosticEvent(), error: CocoaError(.userCancelled)))
        await reports.flush(); await reports.close()
        #expect(transport.bodies().isEmpty); #expect(!store.has("outbox.json"))
    }
}

extension DiagnosticReportsTests {
    @Test func hardKillRevokesOldGrantAndPreventsUnkillHistoryReplay() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock(), kill = DiagnosticTestKill()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, disabled: { kill.value })
        try await first.setConsent(true)
        let oldEpoch = try #require(first.recoveryContext?.epoch)
        first.capture(diagnosticEvent()); await first.flush(); await first.close()
        kill.set(true)
        let killed = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, disabled: { kill.value }); await killed.load()
        #expect(!killed.status.available)
        _ = try? await killed.setConsent(true)
        await killed.close(); kill.set(false)
        let transport = DiagnosticTestTransport(), next = try diagnosticTestReporter(store, transport, clock, disabled: { kill.value })
        await next.load(); await next.flush()
        #expect(!next.status.enabled); #expect(!next.capture(diagnosticEvent())); #expect(next.recoveryContext == nil); #expect(transport.bodies().isEmpty)
        try await next.setConsent(true); #expect(next.recoveryContext?.epoch != oldEpoch); await next.close()
    }
}

extension DiagnosticReportsTests {
    @Test func hardKillWithoutPriorGrantCreatesNoIdentityOrFiles() async throws {
        let store = DiagnosticTestStore(), reports = try diagnosticTestReporter(store, DiagnosticTestTransport(), disabled: { true })
        await reports.load()
        #expect(!reports.status.available); #expect(!reports.capture(diagnosticEvent()))
        #expect(!store.has("consent.json")); #expect(!store.has("outbox.json")); await reports.close()
    }
    @Test func hardKillSaveFailureIsVisibleAndRemovedGrantStaysOffOnRestart() async throws {
        let store = DiagnosticTestStore(), clock = DiagnosticTestClock(), kill = DiagnosticTestKill()
        let first = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, disabled: { kill.value })
        try await first.setConsent(true); await first.close()
        kill.set(true); store.failing(true)
        let killed = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, disabled: { kill.value }); await killed.load()
        #expect(killed.status.consentSaveFailed); #expect(!killed.status.enabled); await killed.close()
        store.failing(false); kill.set(false)
        let next = try diagnosticTestReporter(store, DiagnosticTestTransport(), clock, disabled: { kill.value }); await next.load()
        #expect(!next.status.enabled); await next.close()
    }
}

extension DiagnosticReportsTests {
    @Test func bootstrapRevocationNeedsNoPolicyOrDestination() async throws {
        let store = DiagnosticTestStore(), first = try diagnosticTestReporter(store, DiagnosticTestTransport())
        try await first.setConsent(true); first.capture(diagnosticEvent()); await first.flush(); await first.close()
        let success = await DiagnosticReports.revokeStoredConsent(directory: URL(fileURLWithPath: "/unused-test-store"), storage: store)
        #expect(success)
        let consent = try diagnosticObject(#require(store.data("consent.json")))
        #expect(consent["enabled"] as? Bool == false); #expect(!store.has("outbox.json"))
        let next = try diagnosticTestReporter(store, DiagnosticTestTransport()); await next.load()
        #expect(!next.status.enabled); #expect(next.recoveryContext == nil); await next.close()
        let empty = DiagnosticTestStore()
        #expect(await DiagnosticReports.revokeStoredConsent(directory: URL(fileURLWithPath: "/unused-test-store"), storage: empty))
        #expect(!empty.has("consent.json")); #expect(!empty.has("outbox.json"))
    }
}

private func diagnosticWaitForEvent(_ event: DispatchSemaphore) async -> Bool {
    await withCheckedContinuation { continuation in
        DispatchQueue.global().async { continuation.resume(returning: event.wait(timeout: .now() + 5) == .success) }
    }
}
private final class DiagnosticStaleWriteStore: DiagnosticStorage, @unchecked Sendable {
    let firstEntered=DispatchSemaphore(value: 0), releaseFirst=DispatchSemaphore(value: 0)
    let secondEntered=DispatchSemaphore(value: 0), releaseSecond=DispatchSemaphore(value: 0)
    private let lock=NSLock()
    private var files: [String:Data]=[:]
    private var writes=0
    func read(_ name: String,maxBytes: Int) throws -> Data? { lock.withLock { files[name] } }
    func remove(_ name: String) throws { _ = lock.withLock { files.removeValue(forKey: name) } }
    func write(_ name: String,data: Data) throws {
        if name=="consent.json" {
            let count=lock.withLock { writes += 1; return writes }
            if count==1 { firstEntered.signal(); _ = releaseFirst.wait(timeout: .now()+5); throw DiagnosticFailure.storageUnavailable }
            if count==2 { secondEntered.signal(); _ = releaseSecond.wait(timeout: .now()+5) }
        }
        lock.withLock { files[name]=data }
    }
}
extension DiagnosticReportsTests {
    @Test func staleWriteFailureDoesNotMarkNewerPendingChoiceAsFailed() async throws {
        let store=DiagnosticStaleWriteStore(), transport=DiagnosticTestTransport()
        let reports=try diagnosticTestReporter(store,transport)
        let stale=Task { _ = try? await reports.setConsent(true) }
        #expect(await diagnosticWaitForEvent(store.firstEntered))
        let latest=Task { _ = try? await reports.setConsent(false) }
        let deadline=ContinuousClock.now.advanced(by: .seconds(5))
        while transport.cancellationCount<2 && ContinuousClock.now<deadline { await Task.yield() }
        #expect(transport.cancellationCount>=2)
        store.releaseFirst.signal()
        #expect(await diagnosticWaitForEvent(store.secondEntered))
        await stale.value
        // The latest write is deliberately still pending, so its state cannot
        // be cleared by its own success before observing the stale failure.
        #expect(!reports.status.consentSaveFailed)
        store.releaseSecond.signal(); await latest.value; await reports.close()
    }
}

extension DiagnosticReportsTests {
    @Test @MainActor func persistedPreConsentUploadReceiptDoesNotReportAfterOptIn() async throws {
        let suite="diagnostic-upload-"+UUID().uuidString
        let defaults=try #require(UserDefaults(suiteName:suite));defer{defaults.removePersistentDomain(forName:suite)}
        let pending=PendingUpload(host:"http://127.0.0.1:3900/",jobID:"old-upload",languages:[DubLanguage(code:"es",name:"Spanish")],profile:"",file:URL(fileURLWithPath:"/private/old-clip.multipart"))
        let ledger=DubUploadLedger(defaults:defaults);try ledger.begin(pending)
        #expect(ledger.complete(UploadReceipt(pending:pending,upload:nil,failure:ClientError("mobile.errors.server",params:["message":"PRIVATE_OLD_CONTENT"],status:500))))
        let uploader=BackgroundUpload(configuration:.ephemeral,defaults:defaults)
        let store=DiagnosticTestStore(),transport=DiagnosticTestTransport();transport.set(code:201)
        let reports=try diagnosticTestReporter(store,transport);try await reports.setConsent(true)
        uploader.setDiagnosticFailureHandler { input, _ in _ = reports.capture(input) }
        #expect(uploader.takeReceipt(host:pending.host)?.failure != nil)
        await reports.flush()
        #expect(transport.bodies().isEmpty)
        uploader.setDiagnosticFailureHandler(nil);await reports.close()
    }
    #if os(macOS)
    @Test @MainActor func liveUploadFailureReportsOnlyWhileOptedInAndExcludesAuth() async throws {
        for (consent,status,expected) in [(true,500,1),(false,500,0),(true,401,0),(true,422,0)] {
            let script="""
import http.server,socket,sys
socket.getfqdn=lambda name='':name
class Host(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  body=b'{"detail":"PRIVATE_LIVE_CONTENT"}'
  self.send_response(int(sys.argv[1]));self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
host=http.server.HTTPServer(('127.0.0.1',0),Host);print(host.server_port,flush=True);host.serve_forever()
"""
            let host=try TestProcess(["python3","-u","-c",script,String(status)]);defer{host.stop()}
            let port=try host.port(within:5)
            let directory=FileManager.default.temporaryDirectory.appendingPathComponent("live-diagnostic-upload-"+UUID().uuidString)
            try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true);defer{try? FileManager.default.removeItem(at:directory)}
            let suite="live-diagnostic-upload-"+UUID().uuidString
            let defaults=try #require(UserDefaults(suiteName:suite));defer{defaults.removePersistentDomain(forName:suite)}
            let uploader=BackgroundUpload(configuration:.ephemeral,defaults:defaults,stagingDirectory:directory)
            let store=DiagnosticTestStore(),transport=DiagnosticTestTransport();transport.set(code:201)
            let reports=try diagnosticTestReporter(store,transport);await reports.load()
            if consent {try await reports.setConsent(true)}
            uploader.setDiagnosticFailureHandler { input, _ in _ = reports.capture(input) }
            let source=directory.appendingPathComponent("test.wav");try Data("RIFF-test-upload".utf8).write(to:source)
            let api=APIClient(address:try PairingAddress("http://127.0.0.1:\(port)/"))
            try await uploader.start(source:source,api:api,languages:[DubLanguage(code:"es",name:"Spanish")],profile:"",audioOnly:true)
            for _ in 0..<250 where uploader.receipt==nil {try await Task.sleep(for:.milliseconds(20))}
            #expect(uploader.receipt?.failure != nil)
            await reports.flush()
            #expect(transport.bodies().count==expected)
            #expect(!transport.bodies().contains{String(decoding:$0,as:UTF8.self).contains("PRIVATE_LIVE_CONTENT")})
            uploader.setDiagnosticFailureHandler(nil);await reports.close()
        }
    }
    #endif
}

extension DiagnosticReportsTests {
    @Test func uploadCompletionTreatsCocoaUserCancellationAsExpected() {
        let pending=PendingUpload(host:"http://127.0.0.1:3900/",jobID:"cancelled",languages:[],profile:"",file:URL(fileURLWithPath:"/unused-test-upload"))
        let result=UploadCompletion.resolve(pending:pending,reply:HTTPReply(data:Data(),status:0),error:CocoaError(.userCancelled),authenticated:false,background:false)
        if case .cancelled=result {} else { Issue.record("User cancellation must not become a failure observation") }
    }
}

#if os(macOS)
private final class DiagnosticBlockedUploadDefaults: UserDefaults, @unchecked Sendable {
    let receiptEntered=DispatchSemaphore(value:0),releaseReceipt=DispatchSemaphore(value:0)
    override func set(_ value: Any?,forKey key: String) {
        if key=="uploadReceipt",value != nil { receiptEntered.signal();_ = releaseReceipt.wait(timeout:.now()+5) }
        super.set(value,forKey:key)
    }
}
extension DiagnosticReportsTests {
    @Test @MainActor func blockedUploadLedgerCannotRetroactivelyUseLaterConsent() async throws {
        for initiallyEnabled in [false,true] {
            let script="""
import http.server,socket
socket.getfqdn=lambda name='':name
class Host(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')));body=b'{"detail":"PRIVATE_BLOCKED_UPLOAD"}'
  self.send_response(500);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
host=http.server.HTTPServer(('127.0.0.1',0),Host);print(host.server_port,flush=True);host.serve_forever()
"""
            let host=try TestProcess(["python3","-u","-c",script]);defer{host.stop()}
            let port=try host.port(within:5)
            let directory=FileManager.default.temporaryDirectory.appendingPathComponent("blocked-diagnostic-upload-"+UUID().uuidString)
            try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true);defer{try? FileManager.default.removeItem(at:directory)}
            let suite="blocked-diagnostic-upload-"+UUID().uuidString
            let defaults=try #require(DiagnosticBlockedUploadDefaults(suiteName:suite));defer{defaults.removePersistentDomain(forName:suite)}
            let uploader=BackgroundUpload(configuration:.ephemeral,defaults:defaults,stagingDirectory:directory)
            let store=DiagnosticTestStore(),transport=DiagnosticTestTransport(),clock=DiagnosticTestClock();transport.set(code:201)
            clock.advance(SystemDiagnosticClock().nowMs()-clock.nowMs())
            let reports=try diagnosticTestReporter(store,transport,clock);await reports.load()
            if initiallyEnabled {
                try await reports.setConsent(true)
                let context=try #require(reports.recoveryContext)
                uploader.setDiagnosticFailureHandler{ input,stamp in _ = reports.captureRecovered(input,occurredAtMs:stamp,consentEpoch:context.epoch,recoveryKey:"ios:\(stamp):unknown",reportedAppVersion:context.appVersion) }
            }
            let source=directory.appendingPathComponent("test.wav");try Data("RIFF-test-upload".utf8).write(to:source)
            let api=APIClient(address:try PairingAddress("http://127.0.0.1:\(port)/"))
            try await uploader.start(source:source,api:api,languages:[DubLanguage(code:"es",name:"Spanish")],profile:"",audioOnly:true)
            #expect(await diagnosticWaitForEvent(defaults.receiptEntered))
            if initiallyEnabled {uploader.setDiagnosticFailureHandler(nil);try await reports.setConsent(false)}
            clock.advance(SystemDiagnosticClock().nowMs()-clock.nowMs());try await reports.setConsent(true)
            let current=try #require(reports.recoveryContext)
            uploader.setDiagnosticFailureHandler{ input,stamp in _ = reports.captureRecovered(input,occurredAtMs:stamp,consentEpoch:current.epoch,recoveryKey:"ios:\(stamp):unknown",reportedAppVersion:current.appVersion) }
            defaults.releaseReceipt.signal()
            for _ in 0..<250 where uploader.receipt==nil {try await Task.sleep(for:.milliseconds(20))}
            #expect(uploader.receipt?.failure != nil)
            await reports.flush()
            #expect(transport.bodies().isEmpty)
            uploader.setDiagnosticFailureHandler(nil);await reports.close()
        }
    }
}
#endif
