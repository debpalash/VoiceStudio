import Foundation
import Testing
@testable import VoiceStudioKit

private actor LLMHost: HTTPTransport {
    let fallback = MockHost()
    var baseURL: String
    let discoverable: Bool
    let legacy: Bool
    private(set) var requests: [URLRequest] = []
    init(baseURL: String, discoverable: Bool = true, legacy: Bool = false) {
        self.baseURL = baseURL; self.discoverable = discoverable; self.legacy = legacy
    }
    func changeEndpoint(_ value: String) { baseURL = value }
    func send(_ request: URLRequest) async throws -> HTTPReply {
        requests.append(request)
        switch request.url?.path {
        case "/engines/translation":
            return HTTPReply(data: data("""
            {"active":"openai","engines":[{"id":"openai","display_name":"LLM (OpenAI-compatible)","category":"llm","installed":true,"ready":true,"configured_via":"\(legacy ? "env" : "Test provider")"}]}
            """))
        case "/api/settings/llm-skills":
            if !discoverable { return HTTPReply(data: data("{}"), status: 403) }
            return HTTPReply(data: data("""
            {"skills":[{"id":"dub_translation","provider":"custom","provider_display_name":"Test provider","provider_local":true,"ready":\(!legacy)}]}
            """))
        case "/api/settings/llm-providers":
            let body = try JSONSerialization.data(withJSONObject: ["providers": [["id": "custom", "display_name": "Test provider", "local": true, "transport": "openai", "base_url": baseURL]]])
            return HTTPReply(data: body)
        case "/api/settings/llm-endpoint":
            return HTTPReply(data: try JSONSerialization.data(withJSONObject: ["base_url": baseURL]))
        default: return try await fallback.send(request)
        }
    }
}

@Test(arguments: [false, true]) @MainActor func remoteLLMRequiresExplicitPerJobConsent(_ allowed: Bool) async throws {
    let host = LLMHost(baseURL: "https://translator.example/v1")
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    let upload = DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "vs-dub-test.wav")
    let languages = [DubLanguage(code: "es", name: "Spanish")]
    try workflow.begin(upload, api: api, languages: languages, profile: "")
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation?.display_name == "Test provider")
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
    workflow.confirmTranslation(allowed)
    try await workflow.monitor(api: api)
    #expect(workflow.checkpoint?.stage == (allowed ? .done : .cancelled))
    #expect(await host.requests.filter { $0.url?.path == "/dub/translate" }.count == (allowed ? 1 : 0))
    try workflow.begin(upload, api: api, languages: languages, profile: "")
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation != nil)
}

@Test(arguments: [false, true]) @MainActor func provenLoopbackLLMNeedsNoCloudConsent(_ legacy: Bool) async throws {
    let host = LLMHost(baseURL: "http://127.0.0.1:11434/v1", legacy: legacy)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "vs-dub-test.wav"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation == nil)
    #expect(workflow.checkpoint?.stage == .done)
    #expect(await host.requests.contains { $0.url?.path == "/api/settings/llm-skills" })
    #expect(await host.requests.filter { $0.url?.path == "/dub/translate" }.count == 1)
}

@Test @MainActor func hiddenLLMSettingsRequireConsentEvenForKnownLocalProviderNames() async throws {
    let host = LLMHost(baseURL: "http://127.0.0.1:11434/v1", discoverable: false)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "vs-dub-test.wav"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation != nil)
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
}

@Test func failedDubStagingLeavesNoPartialFile() throws {
    let directory = repository.appendingPathComponent("mobile/ios/.generated/staging-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let output = directory.appendingPathComponent("upload.multipart")
    #expect(throws: (any Error).self) {
        try Multipart.stageDub(source: directory.appendingPathComponent("missing.wav"), output: output, jobID: "job", audioOnly: true)
    }
    #expect(!FileManager.default.fileExists(atPath: output.path))
}

@Test @MainActor func changingLLMEndpointInvalidatesJobApproval() async throws {
    let host = LLMHost(baseURL: "https://first.example/v1")
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let workflow = DubWorkflow(defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try workflow.begin(DubUpload(job_id: "dub-1", task_id: "prep_dub-1", filename: "clip.wav"), api: api,
                       languages: [DubLanguage(code: "es", name: "Spanish")], profile: "")
    try await workflow.monitor(api: api)
    workflow.confirmTranslation(true)
    await host.changeEndpoint("https://second.example/v1")
    try await workflow.monitor(api: api)
    #expect(workflow.pendingTranslation != nil)
    #expect(await host.requests.allSatisfy { $0.url?.path != "/dub/translate" })
}

@Test(arguments: ["http://127.0.0.1:11434/v1", "http://127.0.0.2/v1", "http://localhost:1234/v1", "http://[::1]:1234/v1"])
func loopbackLLMEndpoints(_ url: String) { #expect(TranslationEngine.isLoopbackEndpoint(url)) }

@Test(arguments: ["http://localhost.evil/v1", "http://127.0.0.1.evil/v1", "https://api.openai.com/v1", "http://192.168.1.2/v1", "http://100.64.0.1/v1", "http://user:secret@localhost/v1", "file://localhost/v1", "", "http://127.1/v1"])
func unprovenLLMEndpoints(_ url: String) { #expect(!TranslationEngine.isLoopbackEndpoint(url)) }

@Test func pickedNameIsIndependentOfStagingAndOlderUploadsRemainReadable() throws {
    let picked = SelectedMedia(url: URL(fileURLWithPath: "/chosen/vs-dub-test.wav"))
    #expect(picked.name == "vs-dub-test.wav")
    let legacy = data(#"{"host":"http://127.0.0.1/","jobID":"old","languages":[],"profile":"","file":"file:///old.multipart"}"#)
    #expect(try JSONDecoder().decode(PendingUpload.self, from: legacy).filename == nil)
}

@Test func dubHistoryMetadataUsesActualHostFieldsAndToleratesOlderRows() throws {
    let item = try JSONDecoder().decode(DubHistoryItem.self, from: data(#"{"id":"dub","filename":"vs-dub-test.wav","language":"Spanish","language_code":"es","duration":125,"created_at":1700000000}"#))
    #expect(item.title == "vs-dub-test.wav")
    let l10n = try L10n(locale: "en", catalogs: catalogs())
    let metadata = item.metadata(l10n: l10n)
    #expect(metadata == "Spanish dub, 2:05 long")
    let old = try JSONDecoder().decode(DubHistoryItem.self, from: data(#"{"id":"old"}"#))
    #expect(old.title == "old")
    #expect(old.metadata(l10n: l10n) == "Dub finished dub, 0:00 long")
}

@Test func searchEmptyAndRemoveCopyAreLocalized() throws {
    let l10n = try L10n(catalogs: catalogs())
    #expect(l10n.t("mobile.ios.no_matches", ["query": "missing voice"]) == "No matches for \"missing voice\".")
    #expect(l10n.t("mobile.ios.remove_file") == "Remove")
    #expect(l10n.t("common.remove", ["term": "vs-dub-test.wav"]) == "Remove vs-dub-test.wav")
}
