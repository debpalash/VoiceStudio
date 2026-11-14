import Foundation

private struct DiagnosticConsent: Codable, Sendable {
    let version: Int
    let enabled: Bool
    let epoch: String
    let profile_id: String
    let enabled_at_ms: Int64?
}
private struct DiagnosticRevocation {
    let consent: DiagnosticConsent?
    let failed: Bool
    let prompted: Bool
}
private func diagnosticRevocationPresent(_ storage: any DiagnosticStorage) -> Bool {
    do { return try storage.read("revoked.json", maxBytes: 4096) != nil }
    catch { return true }
}
// Runs only on the owned worker or detached bootstrap task, never capture.
private func diagnosticRevokeGrant(_ storage: any DiagnosticStorage, hadGrant hint: Bool? = nil, recordChoice: Bool = false) -> DiagnosticRevocation {
    let hadGrant: Bool
    if let hint { hadGrant = hint }
    else {
        do { hadGrant = try storage.read("consent.json", maxBytes: 4096) != nil }
        catch { hadGrant = true }
    }
    var failed = false
    let prompted = hadGrant || recordChoice || diagnosticRevocationPresent(storage)
    if prompted {
        // This independent anonymous file still revokes a locked old grant.
        do { try storage.write("revoked.json", data: Data("{\"version\":1}".utf8)) } catch { failed = true }
    }
    do { try storage.remove("outbox.json") } catch { failed = true }
    var revoked: DiagnosticConsent?
    if hadGrant {
        revoked = DiagnosticConsent(version: 1, enabled: false, epoch: diagnosticID(), profile_id: "install_" + diagnosticID(), enabled_at_ms: nil)
        try? storage.remove("consent.json")
        do { try storage.write("consent.json", data: diagnosticEncode(revoked!)) } catch { failed = true }
    }
    return DiagnosticRevocation(consent: revoked, failed: failed, prompted: prompted)
}
private struct DiagnosticRecord: Codable, Sendable, Equatable {
    let version: Int
    let id: String
    let timestamp_ms: Int64
    let epoch: String
    var props: [String: DiagnosticValue]
}
private struct DiagnosticPending: Sendable {
    let epoch: String
    let props: [String: DiagnosticValue]
    let id: String?
    let timestamp: Int64
}
/// This lock never encloses disk, network, actor waits or exception inspection.
private final class DiagnosticGate: @unchecked Sendable {
    let lock = NSLock()
    var state = DiagnosticStatus()
    var consent: DiagnosticConsent?
    var generation: UInt64 = 0
    var loaded = false
    var closed = false
    var wakeScheduled = false
    var pending: [DiagnosticPending] = []
    let disabled: @Sendable () -> Bool
    init(disabled: @escaping @Sendable () -> Bool) { self.disabled = disabled }
    func snapshot(expectedEpoch: String? = nil) -> (DiagnosticConsent, UInt64)? { lock.withLock {
        guard loaded, !closed, state.enabled, !disabled(), let consent, expectedEpoch == nil || expectedEpoch == consent.epoch else { return nil }
        return (consent, generation)
    } }
    func allowed(_ epoch: String, _ generation: UInt64) -> Bool { lock.withLock { loaded && !closed && state.enabled && !disabled() && consent?.epoch == epoch && self.generation == generation } }
    func status() -> DiagnosticStatus { lock.withLock {
        let killed = disabled()
        var value = state; value.enabled = value.enabled && !killed; value.optedIn = value.optedIn && !killed; value.available = !killed; return value
    } }
    func invalidate(closing: Bool = false) -> UInt64 { lock.withLock {
        generation &+= 1; state.enabled = false; state.optedIn = false; closed = closed || closing; return generation
    } }
    func controlCurrent(_ generation: UInt64) -> Bool { lock.withLock { !closed && self.generation == generation } }
    func revokeIfCurrent(_ epoch: String, _ generation: UInt64) -> UInt64? { lock.withLock {
        guard !closed, state.enabled, consent?.epoch == epoch, self.generation == generation else { return nil }
        self.generation &+= 1; state.enabled = false; state.optedIn = false
        return self.generation
    } }
    func count(_ records: Int) { lock.withLock { state.pendingCount = records + pending.count } }
    func drop() { lock.withLock { state.droppedCount += 1 } }
}

/// Portable reporter; the app owns OS crash/lifecycle adapters and supplies the shared policy.
public final class DiagnosticReports: @unchecked Sendable {
    private let gate: DiagnosticGate
    private let worker: DiagnosticWorker
    private let transport: any DiagnosticTransport
    private let projector: DiagnosticProjector
    private let clock: any DiagnosticClock
    public init(directory: URL, policy: DiagnosticPolicy, destination: DiagnosticDestination, appVersion: String, buildCommit: String? = nil, arch: String = "unknown", transport: any DiagnosticTransport = URLSessionDiagnosticTransport(), clock: any DiagnosticClock = SystemDiagnosticClock(), storage: (any DiagnosticStorage)? = nil, disabled: (@Sendable () -> Bool)? = nil) {
        self.transport = transport; self.clock = clock
        let gate = DiagnosticGate(disabled: disabled ?? { diagnosticKillSwitch(getenv("OMNIVOICE_DIAGNOSTICS_DISABLED").map { String(cString: $0) }) || diagnosticKillSwitch(getenv("OMNIVOICE_ANALYTICS_DISABLED").map { String(cString: $0) }) }); self.gate = gate
        let projector = DiagnosticProjector(policy: policy, appVersion: appVersion, buildCommit: buildCommit, arch: arch); self.projector = projector
        worker = DiagnosticWorker(gate: gate, projector: projector, destination: destination, transport: transport, clock: clock, storage: storage ?? FileDiagnosticStorage(directory: directory))
    }
    /// Bootstrap hard-kill cleanup does not require valid destination/policy
    /// resources. Live reporters must invalidate their gate before this helper.
    public static func revokeStoredConsent(directory: URL, storage: (any DiagnosticStorage)? = nil) async -> Bool {
        let owned = storage ?? FileDiagnosticStorage(directory: directory)
        return await Task.detached(priority: .utility) { !diagnosticRevokeGrant(owned).failed }.value
    }
    public var status: DiagnosticStatus { gate.status() }
    public var recoveryContext: DiagnosticRecoveryContext? {
        guard let (consent, _) = gate.snapshot(), let enabled = consent.enabled_at_ms else { return nil }
        return DiagnosticRecoveryContext(epoch: consent.epoch, enabledAtMs: enabled, appVersion: projector.appVersion)
    }
    /// No retained Error, stack, localizedDescription, model/user content or IO.
    @discardableResult public func capture(_ input: DiagnosticInput, error: (any Error)? = nil, expectedEpoch: String? = nil) -> Bool {
        guard let (consent, generation) = gate.snapshot(expectedEpoch: expectedEpoch) else { return false }
        var input = input
        if let error {
            if error is CancellationError || (error as? CocoaError)?.code == .userCancelled { return false }
            if let urlError = error as? URLError {
                if urlError.code == .cancelled { return false }
                input.errorType = urlError.code == .timedOut ? "TimeoutError" : "ConnectionError"
            } else if error is DecodingError || error is EncodingError { input.errorType = "ValueError" }
            else if error is CocoaError { input.errorType = "OSError" }
            else { input.errorType = "OtherError" }
        }
        let pending = DiagnosticPending(epoch: consent.epoch, props: projector.props(input, version: projector.appVersion, commit: projector.buildCommit), id: nil, timestamp: clock.nowMs())
        return enqueue(pending, generation: generation)
    }
    @discardableResult public func captureRecovered(_ input: DiagnosticInput, occurredAtMs: Int64, consentEpoch: String, recoveryKey: String, reportedAppVersion: String? = nil, occurrenceCount: Int = 1) -> Bool {
        guard (1...1_000_000).contains(occurrenceCount), let (consent, generation) = gate.snapshot(), let enabledAt = consent.enabled_at_ms else { return false }
        let parts = recoveryKey.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0] == "ios", Int64(parts[1]) == occurredAtMs, projector.policy.known("reasons", String(parts[2]), "") == parts[2], consentEpoch == consent.epoch,
            occurredAtMs >= enabledAt, occurredAtMs >= clock.nowMs() - Int64(projector.policy.limit("ttl_ms", 604800000)), occurredAtMs <= clock.nowMs() + 60000,
            reportedAppVersion == nil || diagnosticVersion(reportedAppVersion!) else { return false }
        var props = projector.props(input, version: reportedAppVersion, commit: nil)
        let fingerprint = props["fingerprint"]?.string ?? ""
        let identity = String(diagnosticHash(Data("\(consentEpoch):\(recoveryKey):\(fingerprint)".utf8)).prefix(32))
        props["occurrences"] = .integer(Int64(occurrenceCount))
        let pending = DiagnosticPending(epoch: consentEpoch, props: props, id: identity, timestamp: occurredAtMs)
        return enqueue(pending, generation: generation)
    }
    private func enqueue(_ pending: DiagnosticPending, generation: UInt64) -> Bool {
        let accepted = gate.lock.withLock {
            guard gate.state.enabled, !gate.closed, !gate.disabled(), gate.generation == generation, gate.consent?.epoch == pending.epoch else { return false }
            guard gate.pending.count < projector.policy.limit("queue_count", 64) else { gate.state.droppedCount += 1; return false }
            gate.pending.append(pending); return true
        }
        if accepted { wake() }; return accepted
    }
    private func wake() {
        let start = gate.lock.withLock {
            guard !gate.closed, gate.state.enabled, !gate.wakeScheduled else { return false }
            gate.wakeScheduled = true; return true
        }
        if start { Task { [weak self] in
            guard let self else { return }
            let generation = self.gate.lock.withLock { self.gate.generation }
            await self.worker.flush()
            let storageDeferred = await self.worker.storageDeferred
            let again = self.gate.lock.withLock { self.gate.wakeScheduled = false; return (!storageDeferred || generation != self.gate.generation) && !self.gate.pending.isEmpty && !self.gate.closed && self.gate.state.enabled }
            if again { self.wake() }
        } }
    }
    @discardableResult public func load() async -> DiagnosticStatus {
        if gate.disabled() { _ = gate.invalidate(); transport.cancelAll() }
        await worker.load(); wake(); return status
    }
    @discardableResult public func setConsent(_ enabled: Bool) async throws -> DiagnosticStatus {
        if enabled && gate.disabled() { _ = try await setConsent(false); throw DiagnosticFailure.disabled }
        if enabled && status.enabled { return status }
        let generation = gate.invalidate(); transport.cancelAll()
        do { try await worker.setConsent(enabled, generation: generation) }
        catch { gate.lock.withLock { if gate.generation == generation { gate.state.consentSaveFailed = true } }; throw error }
        return status
    }
    public func flush() async { await worker.flush() }
    public func close() async {
        _ = gate.invalidate(closing: true); transport.cancelAll()
        await worker.close()
    }
}

private actor DiagnosticWorker {
    let gate: DiagnosticGate
    let projector: DiagnosticProjector
    let destination: DiagnosticDestination
    let transport: any DiagnosticTransport
    let clock: any DiagnosticClock
    let storage: any DiagnosticStorage
    var records: [DiagnosticRecord] = []
    var sending = false
    var retryAt: [String: Int64] = [:]
    var storageRetryAt: Int64?
    var outboxLoaded = false
    var storageDeferred: Bool { storageRetryAt != nil }
    var retryWake: (id: UUID, epoch: String, generation: UInt64, deadline: Int64, task: Task<Void, Never>)?
    var revocationPending = false
    var revocationWake: (id: UUID, generation: UInt64, deadline: Int64, task: Task<Void, Never>)?
    var waiters: [CheckedContinuation<Void, Never>] = []
    init(gate: DiagnosticGate, projector: DiagnosticProjector, destination: DiagnosticDestination, transport: any DiagnosticTransport, clock: any DiagnosticClock, storage: any DiagnosticStorage) {
        self.gate = gate; self.projector = projector; self.destination = destination; self.transport = transport; self.clock = clock; self.storage = storage
    }
    func load() {
        let state = gate.lock.withLock { (gate.closed, gate.loaded) }
        guard !state.0 else { return }
        let revoked = diagnosticRevocationPresent(storage)
        guard !state.1 || gate.disabled() || revoked else { return }
        cancelRetryWake()
        cancelRevocationWake()
        storageRetryAt = nil
        outboxLoaded = false
        let generation = gate.lock.withLock { gate.generation }
        var hadGrant = gate.lock.withLock { gate.consent != nil }
        var consent: DiagnosticConsent?
        do {
            if let data = try storage.read("consent.json", maxBytes: 4096) {
                hadGrant = true
                let value = try JSONDecoder().decode(DiagnosticConsent.self, from: data)
                if value.version == 1, diagnosticMatches(value.epoch, "[a-f0-9]{32}"), diagnosticMatches(value.profile_id, "install_[a-f0-9]{32}") {
                    let enabledAt = value.enabled_at_ms.flatMap { $0 > 0 && $0 <= clock.nowMs() + 60000 ? $0 : nil }
                    consent = DiagnosticConsent(version: 1, enabled: value.enabled, epoch: value.epoch, profile_id: value.profile_id, enabled_at_ms: enabledAt)
                }
            }
        } catch { consent = nil; hadGrant = true }
        if gate.disabled() || revoked {
            let current = gate.lock.withLock {
                guard gate.generation == generation, !gate.closed else { return false }
                gate.loaded = true; gate.state.enabled = false; gate.state.optedIn = false
                gate.pending.removeAll(); gate.state.pendingCount = 0; gate.state.prompted = hadGrant || revoked
                return true
            }
            guard current else { return }
            transport.cancelAll()
            _ = performRevocation(generation: generation, recordChoice: revoked, hadGrant: hadGrant)
            return
        }
        let applied = gate.lock.withLock {
            guard gate.generation == generation, !gate.closed else { return false }
            gate.consent = consent; gate.loaded = true; gate.state.prompted = consent != nil
            gate.state.enabled = consent?.enabled == true && !gate.disabled(); gate.state.optedIn = gate.state.enabled
            return true
        }
        guard applied else { return }
        records.removeAll()
        if let consent, consent.enabled, !gate.disabled() {
            do {
                try restoreOutbox(epoch: consent.epoch)
                try persist()
            } catch { deferStorageRetry() }
            scheduleRetryWake(epoch: consent.epoch, generation: generation)
        } else { outboxLoaded = true; try? storage.remove("outbox.json") }
        gate.count(records.count)
    }
    func setConsent(_ enabled: Bool, generation: UInt64) throws {
        guard gate.lock.withLock({ !gate.closed && gate.generation == generation }) else { return }
        cancelRetryWake()
        cancelRevocationWake()
        revocationPending = false
        storageRetryAt = nil; outboxLoaded = true
        gate.lock.withLock { gate.pending.removeAll() }; records.removeAll(); retryAt.removeAll()
        try? storage.remove("outbox.json")
        if !enabled {
            if performRevocation(generation: generation, recordChoice: true) { throw DiagnosticFailure.storageUnavailable }
            return
        }
        let consent = DiagnosticConsent(version: 1, enabled: true, epoch: diagnosticID(), profile_id: "install_" + diagnosticID(), enabled_at_ms: clock.nowMs())
        do {
            try storage.write("consent.json", data: diagnosticEncode(consent))
            guard gate.controlCurrent(generation) else { return }
            try storage.remove("revoked.json")
        } catch {
            _ = performRevocation(generation: generation, recordChoice: true)
            throw error
        }
        gate.lock.withLock {
            guard gate.generation == generation, !gate.closed else { return }
            gate.consent = consent; gate.loaded = true; gate.state.prompted = true; gate.state.pendingCount = 0; gate.state.consentSaveFailed = false
            gate.state.enabled = enabled && !gate.disabled(); gate.state.optedIn = gate.state.enabled
        }
    }
    @discardableResult private func performRevocation(generation: UInt64, recordChoice: Bool, hadGrant: Bool? = nil) -> Bool {
        guard gate.controlCurrent(generation) else { return false }
        cancelRetryWake(); storageRetryAt = nil; outboxLoaded = true
        records.removeAll(); retryAt.removeAll()
        gate.lock.withLock { gate.pending.removeAll() }
        let hint: Bool?
        if let hadGrant { hint = hadGrant }
        else { hint = gate.lock.withLock { gate.consent != nil } ? true : nil }
        let result = diagnosticRevokeGrant(storage, hadGrant: hint, recordChoice: recordChoice)
        revocationPending = result.failed
        gate.lock.withLock {
            guard gate.generation == generation, !gate.closed else { return }
            gate.consent = result.consent; gate.loaded = true
            gate.state.enabled = false; gate.state.optedIn = false
            gate.state.prompted = result.prompted; gate.state.pendingCount = 0
            gate.state.consentSaveFailed = result.failed
        }
        if result.failed { scheduleRevocationWake(generation: generation) }
        else { cancelRevocationWake() }
        return result.failed
    }
    private func cancelRevocationWake() {
        revocationWake?.task.cancel(); revocationWake = nil
    }
    private func scheduleRevocationWake(generation: UInt64) {
        guard revocationPending, gate.controlCurrent(generation) else { return }
        let deadline = clock.nowMs() + 60000
        cancelRevocationWake()
        let id = UUID(), clock = self.clock, gate = self.gate
        let task = Task { [weak self] in
            let delay = Int(max(0, deadline - clock.nowMs()))
            if delay > 0 {
                do { try await clock.sleep(milliseconds: delay) } catch { return }
            }
            guard !Task.isCancelled, gate.controlCurrent(generation) else { return }
            await self?.revocationWakeFired(id, generation: generation)
        }
        revocationWake = (id, generation, deadline, task)
    }
    private func revocationWakeFired(_ id: UUID, generation: UInt64) {
        guard revocationWake?.id == id, gate.controlCurrent(generation) else { return }
        revocationWake = nil
        _ = performRevocation(generation: generation, recordChoice: gate.lock.withLock { gate.state.prompted })
    }
    private func deliveryAllowed(_ consent: DiagnosticConsent, generation: UInt64) -> Bool {
        guard gate.allowed(consent.epoch, generation) else { return false }
        if diagnosticRevocationPresent(storage) {
            if let next = gate.revokeIfCurrent(consent.epoch, generation) {
                transport.cancelAll()
                _ = performRevocation(generation: next, recordChoice: true, hadGrant: true)
            }
            return false
        }
        return gate.allowed(consent.epoch, generation)
    }
    private func sanitized(_ record: DiagnosticRecord, epoch: String) -> DiagnosticRecord? {
        guard record.version == 1, record.epoch == epoch, diagnosticMatches(record.id, "[a-f0-9]{32}"), record.timestamp_ms >= clock.nowMs() - Int64(projector.policy.limit("ttl_ms", 604800000)), record.timestamp_ms <= clock.nowMs() + 60000 else { return nil }
        return DiagnosticRecord(version: 1, id: record.id, timestamp_ms: record.timestamp_ms, epoch: epoch, props: projector.sanitize(record.props))
    }
    private func restoreOutbox(epoch: String) throws {
        let data: Data?
        do { data = try storage.read("outbox.json", maxBytes: projector.policy.limit("queue_bytes", 524288)) }
        catch DiagnosticFailure.corruptStore {
            records.removeAll(); outboxLoaded = true; return
        }
        // An IO read failure above leaves the existing file untouched and keeps
        // intake bounded in memory until it can be merged with saved records.
        if let data {
            do {
                records = try JSONDecoder().decode([DiagnosticRecord].self, from: data).prefix(projector.policy.limit("queue_count", 64)).compactMap { sanitized($0, epoch: epoch) }
            } catch is DecodingError { records.removeAll() }
        } else { records.removeAll() }
        outboxLoaded = true
    }
    private func deferStorageRetry() {
        gate.count(records.count)
        storageRetryAt = clock.nowMs() + 60000
    }
    private func persist() throws {
        let data = try diagnosticEncode(records)
        guard data.count <= projector.policy.limit("queue_bytes", 524288) else { throw DiagnosticFailure.corruptStore }
        if records.isEmpty { try storage.remove("outbox.json") } else { try storage.write("outbox.json", data: data) }
        gate.count(records.count)
    }
    private func drain(_ epoch: String) throws {
        let intake = gate.lock.withLock { let result = gate.pending; gate.pending.removeAll(); return result }
        for pending in intake where pending.epoch == epoch {
            if let identity = pending.id, let index = records.firstIndex(where: { $0.id == identity }) {
                records[index].props["occurrences"] = .integer(max(records[index].props["occurrences"]?.integer ?? 1, pending.props["occurrences"]?.integer ?? 1))
                continue
            }
            var candidate = records
            if pending.id == nil, let index = records.firstIndex(where: { $0.props["fingerprint"] == pending.props["fingerprint"] && abs(pending.timestamp - $0.timestamp_ms) <= Int64(projector.policy.limit("dedupe_ms", 60000)) }) {
                candidate[index].props["occurrences"] = .integer(min((candidate[index].props["occurrences"]?.integer ?? 1) + 1, 1_000_000))
            } else if records.count < projector.policy.limit("queue_count", 64) {
                candidate.append(DiagnosticRecord(version: 1, id: pending.id ?? diagnosticID(), timestamp_ms: pending.timestamp, epoch: epoch, props: pending.props))
            } else { gate.drop(); continue }
            if try diagnosticEncode(candidate).count <= projector.policy.limit("queue_bytes", 524288) { records = candidate } else { gate.drop() }
        }
        gate.count(records.count)
    }
    private func cancelRetryWake() {
        retryWake?.task.cancel()
        retryWake = nil
    }
    private func scheduleRetryWake(epoch: String, generation: UInt64) {
        guard gate.allowed(epoch, generation) else { return }
        guard let deadline = storageRetryAt ?? records.compactMap({ retryAt[$0.id] }).min() else { cancelRetryWake(); return }
        if let wake = retryWake, wake.epoch == epoch, wake.generation == generation, wake.deadline == deadline { return }
        cancelRetryWake()
        let id = UUID(), clock = self.clock, gate = self.gate
        let task = Task { [weak self] in
            guard !Task.isCancelled, gate.allowed(epoch, generation) else { return }
            let delay = Int(max(0, deadline - clock.nowMs()))
            if delay > 0 {
                do { try await clock.sleep(milliseconds: delay) } catch { return }
            }
            guard !Task.isCancelled, gate.allowed(epoch, generation) else { return }
            await self?.retryWakeFired(id)
        }
        retryWake = (id, epoch, generation, deadline, task)
    }
    private func retryWakeFired(_ id: UUID) async {
        guard retryWake?.id == id else { return }
        retryWake = nil
        await flush()
    }
    func flush(expected: (DiagnosticConsent, UInt64)? = nil, followUpPending: Bool = true) async {
        guard let (consent, generation) = expected ?? gate.snapshot(), gate.allowed(consent.epoch, generation) else { return }
        if sending {
            let pendingAtJoin = gate.lock.withLock { gate.pending.contains { $0.epoch == consent.epoch } }
            await withCheckedContinuation { waiters.append($0) }
            // Joining a burst must also service intake accepted before this
            // call. One follow-up covers that snapshot without chasing later
            // captures, bypassing storage cooldown or borrowing another grant.
            if followUpPending, pendingAtJoin, gate.allowed(consent.epoch, generation), (storageRetryAt ?? 0) <= clock.nowMs() {
                await flush(expected: (consent, generation), followUpPending: false)
            }
            return
        }
        sending = true
        defer {
            sending = false
            scheduleRetryWake(epoch: consent.epoch, generation: generation)
            let finished = waiters; waiters.removeAll(); for waiter in finished { waiter.resume() }
        }
        do {
            guard deliveryAllowed(consent, generation: generation) else { return }
            if !outboxLoaded {
                if (storageRetryAt ?? 0) > clock.nowMs() { gate.count(records.count); return }
                try restoreOutbox(epoch: consent.epoch)
            }
            records = records.compactMap { sanitized($0, epoch: consent.epoch) }
            try drain(consent.epoch)
            if (storageRetryAt ?? 0) > clock.nowMs() { return }
            // Publication is required before delivery, including retries of
            // reports retained only in memory while the filesystem was full.
            try persist()
            storageRetryAt = nil
            let remainingIDs = Set(records.map(\.id))
            retryAt = retryAt.filter { remainingIDs.contains($0.key) }
            for record in records {
                if (retryAt[record.id] ?? 0) > clock.nowMs() { continue }
                guard deliveryAllowed(consent, generation: generation) else { break }
                struct Envelope: Encodable { let v: Int; let id: String; let ts: Int64; let site: String; let name: String; let url: String; let profile_id: String; let props: [String: DiagnosticValue] }
                let body = try diagnosticEncode(Envelope(v: 1, id: record.id, ts: record.timestamp_ms, site: destination.site, name: "diagnostic_report", url: destination.eventURL, profile_id: consent.profile_id, props: projector.sanitize(record.props)))
                guard body.count <= projector.policy.limit("body_bytes", 8192) else { records.removeAll { $0.id == record.id }; try persist(); continue }
                var terminal = false
                for attempt in 0..<projector.policy.limit("max_attempts", 3) {
                    guard deliveryAllowed(consent, generation: generation) else { break }
                    do {
                        let status = try await transport.send(destination: destination, body: body, allowed: { [gate] in gate.allowed(consent.epoch, generation) })
                        terminal = (200...499).contains(status) && status != 429
                    } catch { terminal = false }
                    if terminal { break }
                    if attempt + 1 < projector.policy.limit("max_attempts", 3) {
                        var remaining = 500 << attempt
                        while remaining > 0 && gate.allowed(consent.epoch, generation) {
                            try await clock.sleep(milliseconds: min(remaining, 25)); remaining -= 25
                        }
                    }
                }
                if gate.allowed(consent.epoch, generation) {
                    if terminal { records.removeAll { $0.id == record.id }; retryAt.removeValue(forKey: record.id); try persist() }
                    else { retryAt[record.id] = clock.nowMs() + 60000 }
                }
            }
        } catch {
            if gate.allowed(consent.epoch, generation) { deferStorageRetry() }
        }
    }
    func close() {
        cancelRetryWake()
        cancelRevocationWake()
        storageRetryAt = nil
        if revocationPending || gate.disabled() {
            _ = diagnosticRevokeGrant(storage, recordChoice: gate.lock.withLock { gate.state.prompted })
            return
        }
        if let consent = gate.lock.withLock({ gate.consent }) {
            do {
                if !outboxLoaded { try restoreOutbox(epoch: consent.epoch) }
                try drain(consent.epoch)
                try persist()
            } catch { /* Retain the last durable file when orderly persistence fails. */ }
        }
    }
}
