import Foundation
import Testing
@testable import VoiceStudioKit

actor MockHost: HTTPTransport {
    private(set) var requests: [URLRequest] = []
    var response: HTTPReply?
    func setResponse(_ response: HTTPReply?) { self.response = response }
    func send(_ request: URLRequest) throws -> HTTPReply {
        requests.append(request)
        if let response { return response }
        let path = request.url!.path
        switch path {
        case "/health": return HTTPReply(data: data(#"{"status":"ok","device":"cpu","version":"1"}"#), headers: ["x-omnivoice-backend":"1"])
        case "/api/auth/session":
            if request.httpMethod == "DELETE" { return HTTPReply(data: Data(), status: 204) }
            return HTTPReply(data: data(#"{"token":"short-session","expires_at":9999999999,"expires_in":3600}"#), status: 201)
        case "/api/auth/ws-ticket": return HTTPReply(data: data(#"{"ticket":"single-use","expires_in":30}"#))
        case "/generate": return HTTPReply(data: data("RIFF-test-wave"), headers: ["content-type":"audio/wav", "x-audio-id":"take-1"])
        case "/dub/upload": return HTTPReply(data: data(#"{"job_id":"dub-1","task_id":"prep_dub-1","filename":"clip.mp4"}"#), status: 202)
        case "/jobs/prep_dub-1/events": return HTTPReply(data: data(#"{"job":{"id":"prep_dub-1","status":"done"},"events":[{"seq":1,"payload":"data: {\"type\":\"ready\"}\n\n"}]}"#))
        case "/dub/transcribe/dub-1": return HTTPReply(data: data(#"{"segments":[{"id":0,"start":0,"end":2,"text":"Source","gain":0,"speed":0.85,"direction":"Whisper urgently","effect_preset":"raw","instruct":"Calm delivery"}],"source_lang":"en"}"#))
        case "/engines/translation": return HTTPReply(data: data(#"{"active":"argos","engines":[{"id":"argos","display_name":"Argos","category":"offline","installed":true,"ready":true}]}"#))
        case "/dub/translate": return HTTPReply(data: data(#"{"translated":[{"id":"0","text":"Translated"}]}"#))
        case "/dub/generate/dub-1": return HTTPReply(data: data(#"{"task_id":"render-1"}"#))
        case "/jobs/render-1/events": return HTTPReply(data: data(#"{"job":{"id":"render-1","status":"done"},"events":[{"seq":2,"payload":"data: {\"type\":\"done\"}\n\n"}]}"#))
        default: return HTTPReply(data: data("[]"))
        }
    }
}
actor MemoryVault: CredentialVault {
    var values: [String: Credentials] = [:]
    func read(host: String) -> Credentials? { values[host] }
    func write(_ credentials: Credentials?, host: String) { values[host] = credentials }
}

@Test func healthPINSessionRevokeAndExpiredSession() async throws {
    let host = MockHost()
    let address = try PairingAddress("http://127.0.0.1:3900/?pin=123456")
    let client = APIClient(address: address, transport: host)
    #expect(try await client.health().version == "1")
    let session = try await client.exchange(key: "master-never-store")
    #expect(session.token == "short-session")
    let authenticated = APIClient(address: address, credentials: Credentials(pin: address.pin, session: session.token), transport: host)
    try await authenticated.revoke()
    let requests = await host.requests
    #expect(requests.allSatisfy { $0.value(forHTTPHeaderField: "X-OmniVoice-Pin") == "123456" })
    #expect(requests[1].value(forHTTPHeaderField: "Authorization") == "Bearer master-never-store")
    #expect(requests[1].httpBody == data(#"{"transport":"bearer"}"#))
    #expect(requests[2].httpMethod == "DELETE")
    #expect(requests[2].value(forHTTPHeaderField: "Authorization") == "Bearer short-session")
    await host.setResponse(HTTPReply(data: data(#"{"detail":"API key required"}"#), status: 401))
    do { _ = try await authenticated.health(); Issue.record("Expected expired session") } catch let e as ClientError { #expect(e.key == "mobile.connect.error_session_expired"); #expect(e.status == 401) }
}
@Test func rejectsHealthLookalikesAndRedirects() async throws {
    let host = MockHost()
    let client = APIClient(address: try PairingAddress("https://example.com"), transport: host)
    await host.setResponse(HTTPReply(data: data(#"{"status":"ok","device":"cpu","version":"1"}"#)))
    await #expect(throws: ClientError.self) { try await client.health() }
    await host.setResponse(HTTPReply(data: Data(), status: 302, headers: ["location":"https://evil.example"]))
    do { _ = try await client.health(); Issue.record("Expected rejected redirect") } catch let e as ClientError { #expect(e.key == "mobile.errors.redirect") }
}
@Test func generateUsesContractMultipartAndReceivesWave() async throws {
    let host = MockHost()
    let client = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let result = try await client.generate(text: "Sample", profile: "voice-1", engine: "engine-1", language: "English")
    #expect(result.id == "take-1")
    #expect(result.audio == data("RIFF-test-wave"))
    let request = try #require(await host.requests.first)
    let body = String(decoding: request.httpBody!, as: UTF8.self)
    for field in ["text", "profile_id", "engine", "language", "stream"] { #expect(body.contains("name=\"\(field)\"")) }
    #expect(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
}
@Test func dubUploadProgressAndTicketShape() async throws {
    let host = MockHost()
    let client = APIClient(address: try PairingAddress("https://example.com"), credentials: Credentials(session: "session"), transport: host)
    var form = Multipart(); form.file("video", filename: "clip.mp4", bytes: data("clip"), mime: "video/mp4"); form.finish()
    let upload = try await client.uploadDub(form)
    let page = try await client.events(upload.task_id, after: 0)
    #expect(page.job.status == "done")
    #expect(DubProgress.parse(page.events[0])?.fraction == 1)
    let ws = try await client.dictationRequest()
    #expect(ws.url?.scheme == "wss")
    #expect(URLComponents(url: ws.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "ws_ticket" })?.value == "single-use")
    #expect(ws.url?.query?.contains("pcm=1&sr=16000") == true)
    #expect(ws.value(forHTTPHeaderField: "Authorization") == nil)
    let requests = await host.requests
    let ticket = try #require(requests.first { $0.url?.path == "/api/auth/ws-ticket" })
    #expect(ticket.httpBody == data(#"{"path":"\/ws\/transcribe"}"#) || ticket.httpBody == data(#"{"path":"/ws/transcribe"}"#))
}
@Test @MainActor func connectionPersistsOnlySessionAndHandles401() async throws {
    let defaults = UserDefaults(suiteName: "VoiceStudioTests-" + UUID().uuidString)!
    let vault = MemoryVault(), host = MockHost()
    let store = ConnectionStore(defaults: defaults, vault: vault, transport: host)
    let address = try PairingAddress("http://127.0.0.1/?pin=123456")
    _ = try await store.connect(address: address, key: "master-never-store", label: "Computer")
    #expect(store.active?.label == "Computer")
    #expect(await vault.read(host: address.baseURL.absoluteString)?.session == "short-session")
    #expect(!String(describing: defaults.dictionaryRepresentation()).contains("master-never-store"))
    await store.handle(ClientError("mobile.connect.error_session_expired", status: 401))
    #expect(store.client == nil)
    #expect(await vault.read(host: address.baseURL.absoluteString) == nil)
}
@Test @MainActor func dubPipelinePersistsAndResumes() async throws {
    let defaults = UserDefaults(suiteName: "VoiceStudioDubTests-" + UUID().uuidString)!
    let host = MockHost(), client = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: MockHost())
    let api = APIClient(address: client.address, transport: host)
    let workflow = DubWorkflow(defaults: defaults)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.mp4"), api: api, languages: [DubLanguage(code: "es", name: "Spanish"), DubLanguage(code: "fr", name: "French")], profile: "voice-1")
    let restored = DubWorkflow(defaults: defaults)
    #expect(restored.checkpoint?.stage == .preparing)
    try await restored.monitor(api: api, pollInterval: .milliseconds(1))
    #expect(restored.checkpoint?.stage == .done)
    #expect(restored.checkpoint?.languageIndex == 2)
    let requests = await host.requests
    #expect(requests.filter { $0.url?.path == "/dub/generate/dub-1" }.count == 2)
    #expect(requests.filter { $0.url?.path == "/dub/transcribe/dub-1" }.count == 1)
    let body = try JSONSerialization.jsonObject(with: #require(requests.first { $0.url?.path == "/dub/generate/dub-1" }?.httpBody)) as! [String: Any]
    #expect(body["language_code"] as? String == "es")
    let cue = try #require((body["segments"] as? [[String: Any]])?.first)
    #expect(cue["profile_id"] as? String == "voice-1")
    #expect(cue["gain"] as? Double == 0); #expect(cue["speed"] as? Double == 0.85)
    #expect(cue["direction"] as? String == "Whisper urgently"); #expect(cue["effect_preset"] as? String == "raw")
    #expect(cue["instruct"] as? String == "Calm delivery")
}

@Test func forgedOffHostRequestsNeverReachTransport() async throws {
    let host = MockHost()
    let client = APIClient(address: try PairingAddress("https://paired.example"), credentials: Credentials(session: "private-session"), transport: host)
    let forged = URLRequest(url: URL(string: "https://other.example/health")!)
    await #expect(throws: ClientError.self) { try await client.send(forged) }
    await #expect(throws: ClientError.self) { try await client.download(forged) }
    await #expect(throws: ClientError.self) { try await client.download(forged,maxBytes: 65536) }
    #expect(await host.requests.isEmpty)
}

@Test @MainActor func offlineDisconnectStillClearsLocalSession() async throws {
    let defaults = UserDefaults(suiteName: "VoiceStudioOffline-" + UUID().uuidString)!
    let vault = MemoryVault(), host = MockHost()
    let store = ConnectionStore(defaults: defaults, vault: vault, transport: host)
    let address = try PairingAddress("http://127.0.0.1")
    _ = try await store.connect(address: address, key: "master")
    await host.setResponse(HTTPReply(data: Data(), status: 503))
    await #expect(throws: ClientError.self) { try await store.disconnect() }
    #expect(store.active == nil)
    #expect(store.client == nil)
    #expect(await vault.read(host: address.baseURL.absoluteString) == nil)
    #expect(await host.requests.last?.httpMethod == "DELETE")
}
