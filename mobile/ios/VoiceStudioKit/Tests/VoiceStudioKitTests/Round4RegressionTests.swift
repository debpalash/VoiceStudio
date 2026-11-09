import Foundation
import Testing
@testable import VoiceStudioKit

private func preferences() -> UserDefaults { UserDefaults(suiteName: "VoiceStudioRound4-" + UUID().uuidString)! }
private func pendingUpload() -> PendingUpload {
    PendingUpload(host: "http://127.0.0.1:3900/", jobID: "upload-1", languages: [DubLanguage(code: "es", name: "Spanish")],
                  profile: "", file: URL(fileURLWithPath: "/upload.multipart"))
}

@Test(arguments: [URLError.backgroundSessionInUseByAnotherProcess, .backgroundSessionWasDisconnected, .backgroundSessionRequiresSharedContainer])
func backgroundUploadRefusalKeepsPendingFileAndCanFinishInForeground(_ code: URLError.Code) throws {
    let ledger = DubUploadLedger(defaults: preferences())
    let pending = pendingUpload()
    try ledger.begin(pending)
    let result = UploadCompletion.resolve(pending: pending, reply: HTTPReply(data: Data(), status: 0),
                                          error: URLError(code), authenticated: false, background: true)
    guard case .retryInForeground = result else { Issue.record("A background refusal must permit foreground upload"); return }
    #expect(ledger.pending()?.jobID == pending.jobID)
    #expect(ledger.receipt() == nil)
    let success = UploadCompletion.resolve(pending: pending,
        reply: HTTPReply(data: data(#"{"job_id":"upload-1","task_id":"prep-1","filename":"clip.mp4"}"#), status: 202),
        error: nil, authenticated: false, background: false)
    guard case .finished(let receipt) = success else { Issue.record("Foreground retry must accept host completion"); return }
    #expect(ledger.complete(receipt))
    #expect(ledger.receipt()?.upload?.task_id == "prep-1")
}

@Test func cancelledUploadsNeverProduceFailureReceipts() {
    for error: any Error in [URLError(.cancelled), CancellationError()] {
        let result = UploadCompletion.resolve(pending: pendingUpload(), reply: HTTPReply(data: Data(), status: 0),
                                              error: error, authenticated: false, background: true)
        guard case .cancelled = result else { Issue.record("Cancellation must remain silent"); continue }
    }
}

@Test func realUploadFailuresRemainActionable() {
    let result = UploadCompletion.resolve(pending: pendingUpload(), reply: HTTPReply(data: Data(), status: 401),
                                          error: nil, authenticated: true, background: true)
    guard case .finished(let receipt) = result else { Issue.record("Expected failure receipt"); return }
    #expect(receipt.failure?.status == 401)
    #expect(receipt.failure?.key == "mobile.connect.error_session_expired")
}

@Test func failedUploadReceiptIsConsumedOnceAcrossRelaunch() throws {
    let defaults = preferences()
    let ledger = DubUploadLedger(defaults: defaults)
    let pending = pendingUpload()
    try ledger.begin(pending)
    #expect(ledger.complete(UploadReceipt(pending: pending, upload: nil, failure: ClientError("mobile.ios.upload_failed"))))
    #expect(ledger.takeReceipt(host: "http://127.0.0.1:4900/") == nil)
    #expect(ledger.takeReceipt(host: pending.host)?.failure?.key == "mobile.ios.upload_failed")
    #expect(ledger.takeReceipt(host: pending.host) == nil)
    #expect(DubUploadLedger(defaults: defaults).receipt() == nil)
}

private actor UnreadableVault: CredentialVault {
    let cancelled: Bool
    let io: Bool
    var saved: Credentials?
    init(cancelled: Bool = false, io: Bool = false) { self.cancelled = cancelled; self.io = io }
    func read(host: String) throws -> Credentials? {
        if cancelled { throw CancellationError() }
        if io { throw CocoaError(.fileReadNoPermission) }
        throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "Invalid saved credentials"))
    }
    func write(_ credentials: Credentials?, host: String) { saved = credentials }
}

@Test(arguments: [false, true]) @MainActor func unreadableCredentialsAllowFreshPairing(_ io: Bool) async throws {
    let vault = UnreadableVault(io: io)
    let host = MockHost()
    let store = ConnectionStore(defaults: preferences(), vault: vault, transport: host)
    _ = try await store.connect(address: PairingAddress("http://127.0.0.1/?pin=123456"), key: "new-key")
    #expect(store.active != nil)
    #expect(await vault.saved == Credentials(pin: "123456", session: "short-session"))
    #expect(await host.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer new-key")
}

@Test @MainActor func credentialReadCancellationIsNotSwallowed() async throws {
    let host = MockHost()
    let store = ConnectionStore(defaults: preferences(), vault: UnreadableVault(cancelled: true), transport: host)
    await #expect(throws: CancellationError.self) { try await store.connect(address: PairingAddress("http://127.0.0.1")) }
    #expect(await host.requests.isEmpty)
}

@Test @MainActor func corruptPreferencesUseDefaults() {
    let defaults = preferences()
    for key in ["hosts", "dubCheckpoint", "pendingUpload", "uploadReceipt"] { defaults.set(data("{broken"), forKey: key) }
    let store = ConnectionStore(defaults: defaults, vault: MemoryVault(), transport: MockHost())
    #expect(store.hosts.isEmpty)
    #expect(DubWorkflow(defaults: defaults).checkpoint == nil)
    let ledger = DubUploadLedger(defaults: defaults)
    #expect(ledger.pending() == nil && ledger.receipt() == nil)
}

@Test @MainActor func reconnectKeepsRenamedHostAndRefreshesLastSeen() async throws {
    let defaults = preferences()
    let address = try PairingAddress("http://127.0.0.1:3900")
    defaults.set(try JSONEncoder().encode([SavedHost(url: address.baseURL, label: "Studio desk", lastSeen: .distantPast)]), forKey: "hosts")
    let store = ConnectionStore(defaults: defaults, vault: MemoryVault(), transport: MockHost())
    _ = try await store.connect(address: address)
    #expect(store.active?.label == "Studio desk")
    #expect(store.active?.lastSeen != .distantPast)
    #expect(store.hosts.count == 1)
    #expect(ConnectionStore(defaults: defaults).hosts.first?.label == "Studio desk")
}

@Test(arguments: ["zh-Hans", "zh-Hans-CN", "zh-Hans-SG", "zh-Hans-TW", "zh-Hans-HK", "zh-Hant", "zh-Hant-TW", "zh-Hant-HK", "zh-Hant-CN", "zh-HK", "zh-MO", "zh_TW"])
func chineseSystemLocalesSelectBundledCatalogs(_ tag: String) throws {
    let l10n = try L10n(locale: tag, catalogs: ["en": data(#"{"key":"English"}"#),
        "zh-CN": data(#"{"key":"Simplified"}"#), "zh-TW": data(#"{"key":"Traditional"}"#)])
    #expect(l10n.t("key") == (tag.contains("Hans") ? "Simplified" : "Traditional"))
}

@Test @MainActor func rejectedDifferentHostKeepsActiveSession() async throws {
    let host = MockHost(), vault = MemoryVault()
    let store = ConnectionStore(defaults: preferences(), vault: vault, transport: host)
    let a = try PairingAddress("http://127.0.0.1:3900")
    let b = try PairingAddress("http://127.0.0.1:4900")
    _ = try await store.connect(address: a, key: "key-a")
    await host.setResponse(HTTPReply(data: Data(), status: 401))
    do {
        _ = try await store.connect(address: b, key: "bad-key")
        Issue.record("Expected rejected candidate host")
    } catch let failure as ClientError {
        // The exact error-routing path used by AppModel.perform.
        await store.handle(failure, connectionChange: true)
    }
    #expect(store.active?.id == a.baseURL.absoluteString)
    #expect(store.client?.address == a)
    #expect(await vault.read(host: a.baseURL.absoluteString)?.session == "short-session")
    #expect(await vault.read(host: b.baseURL.absoluteString) == nil)
    #expect(await host.requests.allSatisfy { $0.httpMethod != "DELETE" })
}

@Test(arguments: [false, true]) @MainActor func disconnectRevokesOnlyExistingSession(_ hasSession: Bool) async throws {
    let host = MockHost(), vault = MemoryVault()
    let store = ConnectionStore(defaults: preferences(), vault: vault, transport: host)
    let address = try PairingAddress("http://127.0.0.1/?pin=123456")
    _ = try await store.connect(address: address, key: hasSession ? "key" : "")
    try await store.disconnect()
    let revokes = await host.requests.filter { $0.httpMethod == "DELETE" }
    #expect(revokes.count == (hasSession ? 1 : 0))
    #expect(store.active == nil && store.client == nil)
    #expect(await vault.read(host: address.baseURL.absoluteString) == nil)
}

private actor DeferredFrame {
    private var continuation: CheckedContinuation<URLSessionWebSocketTask.Message, Never>?
    private var waiter: CheckedContinuation<Void, Never>?
    private var started = false
    func receive() async -> URLSessionWebSocketTask.Message {
        await withCheckedContinuation {
            continuation = $0; started = true
            waiter?.resume(); waiter = nil
        }
    }
    func waitUntilStarted() async {
        if started { return }
        await withCheckedContinuation { waiter = $0 }
    }
    func release() {
        continuation?.resume(returning: .string(#"{"type":"final","final_kind":"summary","text":"Done"}"#))
        continuation = nil
    }
}

private final class PausingSocket: DictationSocket, @unchecked Sendable {
    let frame = DeferredFrame()
    private let lock = NSLock()
    private var cancelled = false
    var wasCancelled: Bool { lock.withLock { cancelled } }
    var closeCode: URLSessionWebSocketTask.CloseCode { .invalid }
    func resume() {}
    func receive() async throws -> URLSessionWebSocketTask.Message { await frame.receive() }
    func send(_ message: URLSessionWebSocketTask.Message) async throws {
        if wasCancelled { throw CancellationError() }
    }
    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) { lock.withLock { cancelled = true } }
}

private final class SocketSequence: @unchecked Sendable {
    private let lock = NSLock()
    private var sockets: [PausingSocket]
    init(_ sockets: [PausingSocket]) { self.sockets = sockets }
    func next() -> PausingSocket { lock.withLock { sockets.removeFirst() } }
}

@Test func staleDictationTerminationCannotCloseReplacementSocket() async throws {
    let old = PausingSocket(), replacement = PausingSocket()
    let sockets = SocketSequence([old, replacement])
    let stream = DictationStream(socketFactory: { _ in sockets.next() })
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: MockHost())
    let previousMessages = try await stream.start(api: api)
    await old.frame.waitUntilStarted()
    let currentMessages = try await stream.start(api: api)
    await replacement.frame.waitUntilStarted()
    #expect(old.wasCancelled)
    // Delay the old receive/termination until the new socket has been assigned.
    await old.frame.release()
    for try await _ in previousMessages {}
    // Allow its independently queued onTermination actor task to run.
    for _ in 0..<20 { await Task.yield() }
    #expect(!replacement.wasCancelled)
    try await stream.send(data("pcm"))
    await stream.close()
    await replacement.frame.release()
    for try await _ in currentMessages {}
}

@Test func uploadNameAddsTheFileTypeExtensionWhenMissing() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("staged.wav")
    try Data([0]).write(to: file)
    #expect(SelectedMedia(url: file, name: "Interview").uploadName == "Interview.wav")
    #expect(SelectedMedia(url: file, name: "Clip.WAV").uploadName == "Clip.WAV")
    #expect(SelectedMedia(url: file, name: "Interview.final").uploadName == "Interview.final.wav")
    #expect(SelectedMedia(url: file).uploadName == "staged.wav")
}
