import Foundation
import Testing
@testable import VoiceStudioKit

private let intakeRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../../../..").standardizedFileURL
private final class AppIntakeStore: DiagnosticStorage, @unchecked Sendable {
    let lock = NSLock(); var files: [String: Data] = [:]
    private var markerReads = 0
    var revocationReadCount: Int { lock.withLock { markerReads } }
    func read(_ name: String, maxBytes: Int) throws -> Data? { try lock.withLock { if name == "revoked.json" { markerReads += 1 }; guard let data = files[name] else { return nil }; guard data.count <= maxBytes else { throw DiagnosticFailure.corruptStore }; return data } }
    func write(_ name: String, data: Data) { lock.withLock { files[name] = data } }
    func remove(_ name: String) { _ = lock.withLock { files.removeValue(forKey: name) } }
}
private final class AppIntakeTransport: DiagnosticTransport, @unchecked Sendable {
    let lock = NSLock(); var requests: [Data] = []; var replies: [DiagnosticTransportReply] = []; var legacy: [Data] = []
    func cancelAll() {}
    func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int { lock.withLock { legacy.append(body) }; return 202 }
    func sendApp(destination: DiagnosticDestination, body: Data, userAgent: String, allowed: @escaping @Sendable () -> Bool) async throws -> DiagnosticTransportReply {
        guard allowed() else { throw DiagnosticFailure.cancelled }
        return lock.withLock {
            requests.append(body)
            if !replies.isEmpty { return replies.removeFirst() }
            let count = ((try? JSONSerialization.jsonObject(with: body) as? [String: Any])?["events"] as? [Any])?.count ?? 0
            return DiagnosticTransportReply(status: 202, body: Data("{\"accepted\":\(count),\"dropped\":[]}".utf8))
        }
    }
    func bodies() -> [Data] { lock.withLock { requests } }
    func setReplies(_ value: [DiagnosticTransportReply]) { lock.withLock { replies = value } }
}
private final class AppIntakeClock: DiagnosticClock, @unchecked Sendable {
    private let lock = NSLock(); private var value: Int64 = 1_794_640_000_000
    func nowMs() -> Int64 { lock.withLock { value } }
    func advance(_ ms: Int64) { lock.withLock { value += ms } }
    func sleep(milliseconds: Int) async throws {
        if milliseconds > 25 { try await Task.sleep(for: .seconds(3600)) }
        else { advance(Int64(milliseconds)); await Task.yield() }
    }
}
private final class AppIntakeResults: @unchecked Sendable {
    private let lock = NSLock(); private var values: [DiagnosticDeliveryResult] = []
    func append(_ result: DiagnosticDeliveryResult) { lock.withLock { values.append(result) } }
    func snapshot() -> [DiagnosticDeliveryResult] { lock.withLock { values } }
}

struct DiagnosticAppIntakeTests {
    private func destination(pings: Bool = false) throws -> DiagnosticDestination {
        try DiagnosticDestination(site: "legacy01", host: "https://legacy.example", appUrl: "https://voicestudio.sh", app: DiagnosticAppConfiguration(appID: "native01", ingestKey: "smk_" + String(repeating: "a", count: 39), host: "https://intake.example", sessionPings: pings))
    }
    private func reporter(_ store: AppIntakeStore, _ transport: AppIntakeTransport, pings: Bool = false, clock: any DiagnosticClock = SystemDiagnosticClock(), results: AppIntakeResults = AppIntakeResults()) throws -> DiagnosticReports {
        try DiagnosticReports(directory: URL(fileURLWithPath: "/unused"), policy: DiagnosticPolicy(data: Data(contentsOf: intakeRoot.appendingPathComponent("config/diagnostics.json"))), destination: destination(pings: pings), appVersion: "0.5.9-beta.3", arch: "arm64", transport: transport, clock: clock, storage: store, disabled: { false }, onDeliveryResult: { results.append($0) })
    }
    @Test func commonGoldenMapping() throws {
        let raw = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: intakeRoot.appendingPathComponent("tests/fixtures/diagnostic-app-events.json"))) as? [String: Any])
        for item in try #require(raw["cases"] as? [[String: Any]]) {
            let props = try JSONDecoder().decode([String: DiagnosticValue].self, from: JSONSerialization.data(withJSONObject: item["props"]!))
            let data = try diagnosticAppEvent(props, id: "id", timestamp: 10, installID: "profile")
            var actual = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            for key in ["id", "ts", "install_id", "runtime"] { actual.removeValue(forKey: key) }
            let expected = try #require(item["expected"] as? [String: Any])
            #expect(NSDictionary(dictionary: actual).isEqual(to: expected), Comment(rawValue: item["name"] as? String ?? "case"))
            let wire = String(decoding: try diagnosticAppBody(site: "native01", events: [data]), as: UTF8.self)
            let order = ["category", "source", "stage", "error_code", "engine_id", "uptime_bucket", "elapsed_bucket", "memory_bucket", "disk_bucket", "schema_version"]
            var tail = wire[try #require(wire.range(of: "\"tags\":")).upperBound...]
            for key in order where (expected["tags"] as? [String: Any])?[key] != nil {
                let range = try #require(tail.range(of: "\"\(key)\":")); tail = tail[range.upperBound...]
            }
        }
    }
    @Test func configurationOverridesAreIndependentAndInvalidSelectedValuesDoNotFallBackToGeneratedCredentials() throws {
        let generated = try JSONSerialization.data(withJSONObject: ["app_id": "native01", "ingest_key": "smk_" + String(repeating: "a", count: 39), "host": "https://generated.example"])
        #expect(DiagnosticAppConfiguration.resolve(generated: generated, environment: ["SUPERMETRY_APP_ID": "invalid!"], fallbackHost: "https://fallback.example") == nil)
        let value = try #require(DiagnosticAppConfiguration.resolve(generated: generated, environment: ["SUPERMETRY_APP_HOST": "http://public.example"], fallbackHost: "https://fallback.example"))
        #expect(value.host == "https://fallback.example"); #expect(!value.sessionPings)
    }
    @Test(arguments: ["1", "0", "\"true\"", "true", "false", "null"]) func sessionPingsRequireLiteralJSONTrue(_ flag: String) throws {
        let key = "smk_" + String(repeating: "a", count: 39)
        let generated = Data("{\"app_id\":\"native01\",\"ingest_key\":\"\(key)\",\"diagnostics\":{\"sessionPings\":\(flag)}}".utf8)
        let config = try #require(DiagnosticAppConfiguration.resolve(generated: generated, environment: [:], fallbackHost: "https://fallback.example"))
        #expect(config.sessionPings == (flag == "true"))
    }
    @Test(arguments: ["site", "host", "app_url", "all"]) func validAppConfigurationIsIndependentOfLegacyOverrides(_ invalid: String) throws {
        let site = invalid == "site" || invalid == "all" ? "" : "legacy01"
        let host = invalid == "host" || invalid == "all" ? "http://public.example" : "https://legacy.example"
        let appURL = invalid == "app_url" || invalid == "all" ? "not-a-url" : "https://voicestudio.sh"
        #expect(throws: DiagnosticFailure.self) { try DiagnosticDestination(site: site, host: host, appUrl: appURL) }
        let app = try DiagnosticAppConfiguration(appID: "native01", ingestKey: "smk_" + String(repeating: "a", count: 39), host: "https://intake.example")
        do {
            let destination = try DiagnosticDestination(site: site, host: host, appUrl: appURL, app: app)
            #expect(destination.endpoint.absoluteString == "https://intake.example/api/public/v1/app-events")
            #expect(destination.app?.appID == "native01")
        } catch { Issue.record("Valid app-intake configuration was rejected by an unused legacy override") }
    }
    @Test func mixedAndMalformedAcknowledgements() {
        func completed(_ text: String) -> Set<Int> { diagnosticAppCompleted(DiagnosticTransportReply(status: 202, body: Data(text.utf8)), count: 4) }
        #expect(completed("{\"accepted\":1,\"dropped\":[{\"index\":1,\"reason\":\"invalid\"},{\"index\":2,\"reason\":\"duplicate\"},{\"index\":3,\"reason\":\"rate_limited\"}]}") == [0,1,2])
        #expect(completed("{\"accepted\":1,\"dropped\":[]}").isEmpty)
        #expect(completed("{\"accepted\":true,\"dropped\":[]}").isEmpty)
        #expect(completed("{\"accepted\":3,\"dropped\":[{\"index\":true,\"reason\":\"invalid\"}]}").isEmpty)
        #expect(completed("{\"accepted\":3,\"dropped\":[{\"index\":4,\"reason\":\"invalid\"}]}").isEmpty)
        #expect(diagnosticAppCompleted(DiagnosticTransportReply(status: 400), count: 4) == [0,1,2,3])
    }
    @Test func unauthorizedPausesTheWholeRunAndWithdrawalPurges() async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport()
        transport.setReplies([DiagnosticTransportReply(status: 401)])
        let reports = try reporter(store, transport)
        _ = try await reports.setConsent(true)
        #expect(reports.capture(DiagnosticInput(category: "engine_failed")))
        await reports.flush(); #expect(transport.bodies().count == 1); #expect(reports.status.pendingCount == 1)
        #expect(reports.capture(DiagnosticInput(category: "download_failed")))
        await reports.flush(); #expect(transport.bodies().count == 1)
        _ = try await reports.setConsent(false); #expect(reports.status.pendingCount == 0)
        _ = try await reports.setConsent(true)
        #expect(reports.capture(DiagnosticInput(category: "model_load_failed")))
        await reports.flush(); #expect(transport.bodies().count == 1)
        await reports.close()
    }
    @Test func realWorkerSplitsCountAndBytesWithoutLosingRecords() async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport(), reports = try reporter(store, transport)
        _ = try await reports.setConsent(true)
        for index in 0..<45 {
            #expect(reports.capture(DiagnosticInput(category: "engine_failed", frames: (0..<24).map { DiagnosticFrame(path: "backend/main.py", line: 24 * index + $0 + 1) })))
        }
        await reports.flush()
        var count = 0
        for data in transport.bodies() {
            #expect(data.count <= 16384)
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let events = try #require(body["events"] as? [[String: Any]])
            #expect((1...20).contains(events.count)); count += events.count
            #expect(events.allSatisfy { $0["session_id"] == nil && $0["runtime"] as? String == "swift" })
        }
        #expect(count == 45); #expect(reports.status.pendingCount == 0)
        await reports.close()
    }
    @Test func retriesKeepIDsAndMixedRepliesExposeOnlyLocalClosedResults() async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport(), results = AppIntakeResults(), clock = AppIntakeClock()
        let reports = try reporter(store, transport, clock: clock, results: results)
        _ = try await reports.setConsent(true)
        // Populate intake while publication is unavailable, then release all
        // three as one real worker batch rather than racing auto-flush captures.
        let savedConsent = try store.read("consent.json", maxBytes: 4096)
        let consent = try #require(savedConsent)
        let saved = try #require(JSONSerialization.jsonObject(with: consent) as? [String: Any])
        let epoch = try #require(saved["epoch"] as? String)
        let projector = DiagnosticProjector(policy: try DiagnosticPolicy(data: Data(contentsOf: intakeRoot.appendingPathComponent("config/diagnostics.json"))), appVersion: "0.5.9-beta.3", buildCommit: nil, arch: "arm64")
        let records = try ["engine_failed", "download_failed", "model_load_failed"].enumerated().map { index, category -> [String: Any] in
            let data = try diagnosticEncode(projector.props(DiagnosticInput(category: category), version: "0.5.9-beta.3", commit: nil))
            return ["version": 1, "id": String(repeating: String(index + 1), count: 32), "timestamp_ms": clock.nowMs(), "epoch": epoch, "props": try JSONSerialization.jsonObject(with: data)]
        }
        await reports.close()
        store.write("outbox.json", data: try JSONSerialization.data(withJSONObject: records))
        transport.setReplies([DiagnosticTransportReply(status: 429), DiagnosticTransportReply(status: 503), DiagnosticTransportReply(status: 202, body: Data("{\"accepted\":1,\"dropped\":[{\"index\":1,\"reason\":\"rate_limited\"},{\"index\":2,\"reason\":\"too_old\"}],\"warnings\":[{\"index\":0,\"reason\":\"tag_not_allowed\",\"tag\":\"stage\"}]}".utf8))])
        let retrying = try reporter(store, transport, clock: clock, results: results)
        await retrying.load(); await retrying.flush()
        let bodies = try transport.bodies().map { data -> [[String: Any]] in
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any]); return try #require(body["events"] as? [[String: Any]])
        }
        #expect(bodies.count == 3)
        #expect(bodies.map { $0.compactMap { $0["id"] as? String } }.allSatisfy { $0 == bodies.first?.compactMap { $0["id"] as? String } })
        #expect(retrying.status.pendingCount == 1)
        #expect(results.snapshot().contains { $0.reason == "too_old" })
        #expect(results.snapshot().contains { $0.reason == "tag_not_allowed" && $0.warningTag == "stage" })
        clock.advance(60000); await retrying.flush(); #expect(retrying.status.pendingCount == 0)
        await retrying.close()
    }
    @Test func restoredOldFailuresAreNotRelabelledWithCurrentSession() async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport()
        transport.setReplies([DiagnosticTransportReply(status: 401)])
        let first = try reporter(store, transport)
        _ = try await first.setConsent(true); first.capture(DiagnosticInput(category: "engine_failed")); await first.flush(); await first.close()
        let prior = transport.bodies().count, restarted = try reporter(store, transport, pings: true)
        await restarted.load(); await restarted.flush()
        let events = try transport.bodies().dropFirst(prior).flatMap { data -> [[String: Any]] in
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any]); return try #require(body["events"] as? [[String: Any]])
        }
        #expect(events.filter { $0["kind"] as? String == "session" }.count == 1)
        let failures = events.filter { $0["kind"] as? String != "session" }
        #expect(failures.count == 1); #expect(failures.allSatisfy { $0["session_id"] == nil })
        await restarted.close()
    }
    @Test func sessionOffByDefaultAndSingleRunCorrelationNeverEntersStorage() async throws {
        for pings in [false, true] {
            let store = AppIntakeStore(), transport = AppIntakeTransport(), reports = try reporter(store, transport, pings: pings)
            _ = try await reports.setConsent(true)
            #expect(reports.capture(DiagnosticInput(category: "engine_failed")))
            await reports.flush()
            let events = try transport.bodies().flatMap { data -> [[String: Any]] in
                let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any]); return try #require(body["events"] as? [[String: Any]])
            }
            #expect(events.filter { $0["kind"] as? String == "session" }.count == (pings ? 1 : 0))
            #expect(events.filter { $0["kind"] as? String != "session" }.allSatisfy { ($0["session_id"] != nil) == pings })
            let persisted = try store.read("outbox.json", maxBytes: 524288)
            #expect(persisted == nil || !String(decoding: persisted!, as: UTF8.self).contains("session_id"))
            _ = try await reports.setConsent(false); _ = try await reports.setConsent(true)
            #expect(reports.capture(DiagnosticInput(category: "model_load_failed")))
            await reports.flush()
            let lastData = try #require(transport.bodies().last)
            let last = try #require(JSONSerialization.jsonObject(with: lastData) as? [String: Any])
            #expect((last["events"] as? [[String: Any]])?.allSatisfy { $0["session_id"] == nil && $0["kind"] as? String != "session" } == true)
            await reports.close()
        }
    }
    @Test(arguments: [false, true]) func initialDeclineDoesNotRetireFirstSessionGrant(_ pings: Bool) async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport(), reports = try reporter(store, transport, pings: pings)
        await reports.load()
        _ = try await reports.setConsent(false)
        _ = try await reports.setConsent(false)
        #expect(transport.bodies().isEmpty)
        #expect(try store.read("consent.json", maxBytes: 4096) == nil)
        _ = try await reports.setConsent(true)
        #expect(reports.capture(DiagnosticInput(category: "engine_failed")))
        await reports.flush(); await reports.flush()
        let events = try transport.bodies().flatMap { data -> [[String: Any]] in
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            return try #require(body["events"] as? [[String: Any]])
        }
        #expect(events.filter { $0["kind"] as? String == "session" }.count == (pings ? 1 : 0))
        #expect(events.filter { $0["kind"] as? String != "session" }.allSatisfy { ($0["session_id"] != nil) == pings })
        await reports.close()
    }
    @Test func withdrawalBeforeFirstFlushStillRetiresRunSession() async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport(), reports = try reporter(store, transport, pings: true)
        _ = try await reports.setConsent(true)
        _ = try await reports.setConsent(false)
        _ = try await reports.setConsent(true)
        #expect(reports.capture(DiagnosticInput(category: "engine_failed")))
        await reports.flush()
        let events = try transport.bodies().flatMap { data -> [[String: Any]] in
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            return try #require(body["events"] as? [[String: Any]])
        }
        #expect(events.count == 1)
        #expect(events.allSatisfy { $0["kind"] as? String != "session" && $0["session_id"] == nil })
        await reports.close()
    }
    @Test func priorRunOffChoiceDoesNotRetireANewRunsFirstGrant() async throws {
        let store = AppIntakeStore(), firstTransport = AppIntakeTransport(), first = try reporter(store, firstTransport, pings: true)
        _ = try await first.setConsent(true); _ = try await first.setConsent(false); await first.close()
        let transport = AppIntakeTransport(), reports = try reporter(store, transport, pings: true)
        await reports.load(); _ = try await reports.setConsent(true)
        #expect(reports.capture(DiagnosticInput(category: "engine_failed")))
        await reports.flush()
        let events = try transport.bodies().flatMap { data -> [[String: Any]] in
            let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            return try #require(body["events"] as? [[String: Any]])
        }
        #expect(events.filter { $0["kind"] as? String == "session" }.count == 1)
        #expect(events.filter { $0["kind"] as? String != "session" }.allSatisfy { $0["session_id"] != nil })
        await reports.close()
    }

    @Test(arguments: [false, true]) func retiredSessionRetryCannotKeepTheWorkerFlushing(_ externalWithdrawal: Bool) async throws {
        let store = AppIntakeStore(), transport = AppIntakeTransport(), clock = AppIntakeClock()
        transport.setReplies(Array(repeating: DiagnosticTransportReply(status: 503), count: 3))
        let reports = try reporter(store, transport, pings: true, clock: clock)
        _ = try await reports.setConsent(true)
        await reports.flush()
        #expect(transport.bodies().count == 3)
        if externalWithdrawal {
            store.write("revoked.json", data: Data("{\"version\":1}".utf8))
            await reports.flush()
        } else { _ = try await reports.setConsent(false) }
        #expect(!reports.status.enabled)
        _ = try await reports.setConsent(true)
        clock.advance(60000)
        let before = store.revocationReadCount
        await reports.flush()
        // A retired ping has no eligible work. A stale retry deadline otherwise
        // rearms zero-delay flushes, each rereading the marker on the real worker.
        let deadline = ContinuousClock.now.advanced(by: .milliseconds(100))
        while store.revocationReadCount - before < 20 && ContinuousClock.now < deadline { await Task.yield() }
        let after = store.revocationReadCount
        await reports.close()
        #expect(after - before == 1)
        #expect(transport.bodies().count == 3)
        #expect(!reports.capture(DiagnosticInput(category: "engine_failed")))
    }

}
