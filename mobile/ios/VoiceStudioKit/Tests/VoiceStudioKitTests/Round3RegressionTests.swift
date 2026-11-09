import Foundation
import Testing
@testable import VoiceStudioKit

@Test @MainActor func dubTranslationAlwaysNamesTheHostProvider() async throws {
    let host = MockHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api, pollInterval: .milliseconds(1))
    let requests = await host.requests
    let request = try #require(requests.first { $0.url?.path == "/dub/translate" })
    let bytes = try #require(request.httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
    #expect(body["provider"] as? String == "argos")
    #expect(requests.contains { $0.url?.path == "/engines/translation" })
}

private actor ScriptedFrames {
    var frames: [String]
    init(_ frames: [String]) { self.frames = frames }
    func next() throws -> URLSessionWebSocketTask.Message {
        guard !frames.isEmpty else { throw CancellationError() }
        return .string(frames.removeFirst())
    }
}
private final class ScriptedSocket: DictationSocket, Sendable {
    let frames: ScriptedFrames
    init(_ frames: [String]) { self.frames = ScriptedFrames(frames) }
    var closeCode: URLSessionWebSocketTask.CloseCode { .invalid }
    func resume() {}
    func receive() async throws -> URLSessionWebSocketTask.Message { try await frames.next() }
    func send(_ message: URLSessionWebSocketTask.Message) async throws {}
    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {}
}

@Test func sherpaUtteranceFinalsKeepTheActualStreamOpen() async throws {
    let socket = ScriptedSocket([
        #"{"type":"partial","text":"First"}"#,
        #"{"type":"final","final_kind":"utterance","text":"First phrase."}"#,
        #"{"type":"partial","text":"Second"}"#,
        #"{"type":"final","final_kind":"utterance","text":"Second phrase."}"#,
        #"{"type":"final","final_kind":"summary","text":"First phrase. Second phrase."}"#
    ])
    let stream = DictationStream(socketFactory: { _ in socket })
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: MockHost())
    let messages = try await stream.start(api: api)
    var received: [String] = []
    for try await message in messages { received.append(message.text ?? "") }
    #expect(received == ["First", "First phrase.", "Second", "Second phrase.", "First phrase. Second phrase."])
    await stream.close()
}

@Test func dubDefaultVoiceLabelDescribesEnginePreset() throws {
    let l10n = try L10n(catalogs: catalogs())
    #expect(l10n.t(DubVoiceChoice.defaultLabelKey) == "Default voice (engine preset)")
}

@Test @MainActor func dubRequiresAnExplicitVoiceChoiceBeforeStarting() throws {
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: MockHost())
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    #expect(throws: ClientError.self) {
        try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                           languages: [DubLanguage(code: "es", name: "Spanish")], profile: DubVoiceChoice.unselected)
    }
    #expect(workflow.checkpoint == nil)
}

private actor TranslationHost: HTTPTransport {
    let fallback = MockHost()
    var inventory: String
    private(set) var requests: [URLRequest] = []
    init(_ inventory: String) { self.inventory = inventory }
    func replace(_ inventory: String) { self.inventory = inventory }
    func send(_ request: URLRequest) async throws -> HTTPReply {
        requests.append(request)
        if request.url?.path == "/engines/translation" { return HTTPReply(data: data(inventory)) }
        return try await fallback.send(request)
    }
}
private let onlineInventory = #"{"active":"google","engines":[{"id":"google","display_name":"Google Translate","category":"online","installed":true,"ready":true}]}"#

@Test(arguments: [false, true]) @MainActor func onlineTranslationRequiresPerJobConsent(_ allowed: Bool) async throws {
    let host = TranslationHost(onlineInventory)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    let upload = DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4")
    let languages = [DubLanguage(code: "es", name: "Spanish"), DubLanguage(code: "fr", name: "French")]
    try workflow.begin(upload, api: api, languages: languages, profile: "")
    try await workflow.monitor(api: api, pollInterval: .milliseconds(1))
    #expect(workflow.checkpoint?.stage == .translating)
    #expect(workflow.pendingTranslation?.display_name == "Google Translate")
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
    workflow.confirmTranslation(allowed)
    try await workflow.monitor(api: api, pollInterval: .milliseconds(1))
    #expect(workflow.checkpoint?.stage == (allowed ? .done : .cancelled))
    let translations = await host.requests.filter { $0.url?.path == "/dub/translate" }
    #expect(translations.count == (allowed ? 2 : 0))
    for request in translations {
        let bytes = try #require(request.httpBody)
        let json = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        #expect(json["provider"] as? String == "google")
    }
    // Consent cannot leak into a subsequent job (even with the same engine).
    try workflow.begin(upload, api: api, languages: languages, profile: "")
    try await workflow.monitor(api: api, pollInterval: .milliseconds(1))
    #expect(workflow.pendingTranslation?.id == "google")
    #expect(await host.requests.filter { $0.url?.path == "/dub/translate" }.count == translations.count)
}

@Test @MainActor func changedOnlineEngineRequiresAnotherConfirmation() async throws {
    let host = TranslationHost(onlineInventory)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api)
    workflow.confirmTranslation(true)
    await host.replace(onlineInventory.replacingOccurrences(of: "google", with: "deepl").replacingOccurrences(of: "Google Translate", with: "DeepL"))
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation?.display_name == "DeepL")
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
}

@Test @MainActor func translationApprovalResumesOnlyItsPersistedJob() async throws {
    let defaults = UserDefaults(suiteName: UUID().uuidString)!
    let host = TranslationHost(onlineInventory)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: defaults)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api)
    workflow.confirmTranslation(true)
    let restored = DubWorkflow(defaults: defaults)
    #expect(restored.checkpoint?.approvedOnlineProvider == "google")
    try await restored.monitor(api: api)
    #expect(restored.checkpoint?.stage == .done)
    #expect(restored.pendingTranslation == nil)
    #expect(await host.requests.filter { $0.url?.path == "/dub/translate" }.count == 1)
}

@Test(arguments: [
    #"{"active":"missing","engines":[]}"#,
    #"{"active":"argos","engines":[{"id":"argos","display_name":"Argos","category":"offline","installed":false,"ready":true}]}"#,
    #"{"active":"google","engines":[{"id":"google","display_name":"Google","category":"online","installed":true,"ready":false}]}"#,
    #"{"active":"argos","engines":[{"id":"argos","display_name":"Argos","category":"offline"}]}"#
]) @MainActor func unavailableTranslationNeverFallsBack(_ inventory: String) async throws {
    let host = TranslationHost(inventory)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    do { try await workflow.monitor(api: api); Issue.record("Expected engine setup error") }
    catch let error as ClientError { #expect(error.key == "mobile.dub.translation_setup") }
    #expect(workflow.pendingTranslation == nil)
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
}

@Test func emptyTranslationProviderCannotReachHost() async throws {
    let host = MockHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    await #expect(throws: ClientError.self) { try await api.translate("dub-1", segments: [], language: "es", provider: " ") }
    #expect(await host.requests.isEmpty)
}

@Test @MainActor func dubPresetAndClonedVoiceRequestsMatchTheirLabels() async throws {
    for profile in ["", "cloned-profile"] {
        let host = MockHost()
        let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
        let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
        try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                           languages: [DubLanguage(code: "es", name: "Spanish")], profile: profile)
        try await workflow.monitor(api: api)
        let request = try #require(await host.requests.first { $0.url?.path == "/dub/generate/dub-1" })
        let bytes = try #require(request.httpBody)
        let json = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        #expect((json["segments"] as? [[String: Any]])?.first?["profile_id"] as? String == profile)
    }
    let source = try String(contentsOf: repository.appendingPathComponent("mobile/ios/VoiceStudio/Features/SpeakView.swift"), encoding: .utf8)
    #expect(source.contains("DubVoiceChoice.defaultLabelKey"))
    #expect(!source.contains("mobile.ios.voice_default"))
}

@Test func dictationSummaryReplacesCommittedPhrasesAndUsesRefinement() throws {
    var transcript = DictationTranscript()
    func frame(_ value: String) throws -> DictationMessage { try JSONDecoder().decode(DictationMessage.self, from: data(value)) }
    transcript.receive(try frame(#"{"type":"final","final_kind":"utterance","text":"First phrase."}"#))
    #expect(!transcript.finished)
    #expect(transcript.text == "First phrase.")
    transcript.receive(try frame(#"{"type":"partial","text":"Second"}"#))
    #expect(transcript.text == "First phrase. Second")
    transcript.receive(try frame(#"{"type":"final","final_kind":"utterance","text":"Second phrase.","refined_text":"Second refined phrase."}"#))
    #expect(!transcript.finished)
    #expect(transcript.text == "First phrase. Second refined phrase.")
    transcript.receive(try frame(#"{"type":"final","final_kind":"utterance","text":" "}"#))
    transcript.receive(try frame(#"{"type":"final","final_kind":"unknown","text":"Ignored"}"#))
    #expect(!transcript.finished)
    transcript.receive(try frame(#"{"type":"final","final_kind":"summary","text":"First phrase. Second phrase.","refined_text":"Complete refined summary."}"#))
    #expect(transcript.finished)
    #expect(transcript.text == "Complete refined summary.")
}

@Test(arguments: [#"{"type":"final","text":"Legacy EOF"}"#, #"{"type":"final","final_kind":"summary","text":""}"#])
func dictationLegacyEOFAndEmptySummaryTerminate(_ json: String) throws {
    var transcript = DictationTranscript()
    let utterance = try JSONDecoder().decode(DictationMessage.self, from: data(#"{"type":"final","final_kind":"utterance","text":"Committed"}"#))
    transcript.receive(utterance)
    let message = try JSONDecoder().decode(DictationMessage.self, from: data(json))
    transcript.receive(message)
    #expect(message.isSessionFinal)
    #expect(transcript.finished)
    #expect(transcript.text == (message.text?.isEmpty == false ? "Legacy EOF" : "Committed"))
}

private actor ReplyGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var waiter: CheckedContinuation<Void, Never>?
    private var started = false
    func suspend() async {
        await withCheckedContinuation { continuation in
            self.continuation = continuation; started = true
            waiter?.resume(); waiter = nil
        }
    }
    func waitUntilStarted() async {
        if started { return }
        await withCheckedContinuation { waiter = $0 }
    }
    func release() { continuation?.resume(); continuation = nil }
}

@Test(arguments: ["switch", "disconnect", "unauthorized"]) @MainActor
func hostTransitionClearsAllStateAndPreventsOldAudioOnNewHost(_ action: String) async throws {
    let defaults = UserDefaults(suiteName: UUID().uuidString)!
    let transport = MockHost()
    let connection = ConnectionStore(defaults: defaults, vault: MemoryVault(), transport: transport)
    let state = HostState(dub: DubWorkflow(defaults: defaults))
    connection.onHostChange = { state.transition(to: $0) }
    let a = try await connection.connect(address: PairingAddress("http://127.0.0.1:3900"))
    _ = try await state.generate(api: a, text: "Host A", profile: "", engine: "", language: "")
    state.profiles = try JSONDecoder().decode([Profile].self, from: data(#"[{"id":"voice-a","name":"A","kind":"clone"}]"#))
    state.history = try JSONDecoder().decode([HistoryItem].self, from: data(#"[{"id":"take-1","text":"Host A"}]"#))
    state.dubHistory = try JSONDecoder().decode([DubHistoryItem].self, from: data(#"[{"id":"dub-1"}]"#))
    state.inventory = try JSONDecoder().decode(EngineInventory.self, from: data(#"{"tts":{"active":"a","backends":[]}}"#))
    try state.dub.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: a,
                        languages: [DubLanguage(code: "es", name: "Spanish")], profile: "voice-a")
    let oldScope = state.scopeID
    // Re-pairing the same computer preserves its session data.
    _ = try await connection.connect(address: a.address)
    #expect(state.scopeID == oldScope)
    #expect(state.generation?.id == "take-1")
    let gate = ReplyGate()
    let stalePlayback = Task { @MainActor in
        try await state.run {
            await gate.suspend()
            // This is the dangerous UI pattern: looking up the current client
            // after an await, while still holding host A's history identifier.
            guard let api = connection.client else { throw CancellationError() }
            _ = try await api.historyAudio("take-1")
        }
    }
    await gate.waitUntilStarted()
    switch action {
    case "switch": _ = try await connection.connect(address: PairingAddress("http://127.0.0.1:4900"))
    case "disconnect": try await connection.disconnect()
    default: await connection.handle(ClientError("mobile.connect.error_session_expired", status: 401))
    }
    await gate.release()
    await #expect(throws: CancellationError.self) { try await stalePlayback.value }
    #expect(state.scopeID != oldScope)
    #expect(state.generation == nil)
    #expect(state.profiles.isEmpty && state.history.isEmpty && state.dubHistory.isEmpty)
    #expect(state.inventory == nil)
    #expect(state.dub.checkpoint == nil)
    #expect(DubWorkflow(defaults: defaults).checkpoint == nil)
    #expect(await transport.requests.allSatisfy { $0.url?.path != "/audio/take-1.wav" })
}

private actor DelayedHost: HTTPTransport {
    let gate: ReplyGate
    let path: String
    let fallback = MockHost()
    private(set) var requests: [URLRequest] = []
    init(gate: ReplyGate, path: String) { self.gate = gate; self.path = path }
    func send(_ request: URLRequest) async throws -> HTTPReply {
        requests.append(request)
        if request.url?.path == path { await gate.suspend() }
        return try await fallback.send(request)
    }
}

@Test @MainActor func lateGenerationCannotRepopulateAnotherHostPlayer() async throws {
    let gate = ReplyGate()
    let delayed = DelayedHost(gate: gate, path: "/generate")
    let state = HostState(dub: DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!))
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:3900"), transport: delayed)
    state.transition(to: api.address.baseURL.absoluteString)
    let work = Task { @MainActor in try await state.generate(api: api, text: "A", profile: "", engine: "", language: "") }
    await gate.waitUntilStarted()
    state.transition(to: "http://127.0.0.1:4900/")
    await gate.release()
    await #expect(throws: CancellationError.self) { try await work.value }
    #expect(state.generation == nil)
}

@Test @MainActor func hostSwitchStopsDubMonitoringAndDiscardsLateEvents() async throws {
    let gate = ReplyGate()
    let host = DelayedHost(gate: gate, path: "/jobs/prep_dub-1/events")
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:3900"), transport: host)
    let state = HostState(dub: DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!))
    state.transition(to: api.address.baseURL.absoluteString)
    try state.dub.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api,
                        languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    let monitor = Task { @MainActor in try await state.dub.monitor(api: api) }
    await gate.waitUntilStarted()
    state.transition(to: "http://127.0.0.1:4900/")
    await gate.release()
    await #expect(throws: CancellationError.self) { try await monitor.value }
    #expect(state.dub.checkpoint == nil)
    #expect(!state.dub.monitoring)
    #expect(state.dub.error == nil)
    #expect(await host.requests.count == 1)
}

@Test func oldUploadCompletionCannotOverwriteAnotherHost() throws {
    let ledger = DubUploadLedger(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    let a = PendingUpload(host: "http://127.0.0.1:3900/", jobID: "a", languages: [], profile: "voice-a", file: URL(fileURLWithPath: "/upload-a"))
    let b = PendingUpload(host: "http://127.0.0.1:4900/", jobID: "b", languages: [], profile: "voice-b", file: URL(fileURLWithPath: "/upload-b"))
    try ledger.begin(a)
    ledger.discard(a)
    ledger.clearReceipt()
    try ledger.begin(b)
    #expect(!ledger.complete(UploadReceipt(pending: a, upload: nil, failure: ClientError("mobile.ios.upload_failed"))))
    #expect(ledger.pending()?.host == b.host)
    #expect(ledger.receipt() == nil)
    // A late cancel from A likewise cannot remove B's upload.
    ledger.discard(a)
    #expect(ledger.pending()?.jobID == "b")
    #expect(ledger.complete(UploadReceipt(pending: b, upload: DubUpload(job_id: "b", task_id: "prep-b", filename: "b.mp4"), failure: nil)))
    #expect(ledger.pending() == nil)
    #expect(ledger.receipt()?.pending.host == b.host)
    ledger.clearReceipt()
    #expect(ledger.receipt() == nil)
}
