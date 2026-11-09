import Foundation

public struct Credentials: Codable, Sendable, Equatable {
    public var pin: String?
    public var session: String?
    public init(pin: String? = nil, session: String? = nil) { self.pin = pin; self.session = session }
}
public struct APIClient: Sendable {
    public let address: PairingAddress
    public let credentials: Credentials
    public let transport: any HTTPTransport
    public init(address: PairingAddress, credentials: Credentials = .init(), transport: any HTTPTransport = URLSessionTransport()) {
        self.address = address; self.credentials = credentials; self.transport = transport
    }
    public func request(_ path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil) throws -> URLRequest {
        var r = URLRequest(url: try address.endpoint(path))
        r.httpMethod = method; r.httpBody = body
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        if let pin = credentials.pin ?? address.pin { r.setValue(pin, forHTTPHeaderField: "X-OmniVoice-Pin") }
        if let session = credentials.session { r.setValue("Bearer " + session, forHTTPHeaderField: "Authorization") }
        if let contentType { r.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        return r
    }
    public func send(_ request: URLRequest) async throws -> HTTPReply {
        try Task.checkCancellation()
        guard let url = request.url, PairingAddress.sameOrigin(url, address.baseURL) else { throw ClientError.invalidURL }
        do {
            let reply = try await transport.send(request)
            try Task.checkCancellation()
            return try Self.validate(reply, authenticated: credentials.session != nil)
        }
        catch is CancellationError { throw CancellationError() }
        catch let e as ClientError { throw e }
        catch let e as URLError {
            if e.code == .cancelled { throw CancellationError() }
            throw ClientError(e.code == .timedOut ? "mobile.errors.timeout" : "mobile.connect.error_unreachable", params: ["host": address.baseURL.host ?? ""])
        }
        catch { throw ClientError("mobile.errors.invalid_response") }
    }
    public func download(_ request: URLRequest, maxBytes: Int? = nil) async throws -> DownloadReply {
        try Task.checkCancellation()
        guard let url = request.url, PairingAddress.sameOrigin(url, address.baseURL) else { throw ClientError.invalidURL }
        do {
            let reply: DownloadReply
            if let maxBytes { reply = try await transport.download(request, maxBytes: maxBytes) }
            else { reply = try await transport.download(request) }
            do {
                try Task.checkCancellation()
                let body: Data
                if !(200...299).contains(reply.status) {
                    let reader = try FileHandle(forReadingFrom: reply.file)
                    defer { try? reader.close() }
                    body = try reader.read(upToCount: 65_536) ?? Data()
                } else { body = Data() }
                _ = try Self.validate(HTTPReply(data: body, status: reply.status, headers: reply.headers), authenticated: credentials.session != nil)
                return reply
            } catch { try? FileManager.default.removeItem(at: reply.file); throw error }
        } catch is CancellationError { throw CancellationError() }
        catch let error as ClientError { throw error }
        catch let error as URLError {
            if error.code == .cancelled { throw CancellationError() }
            throw ClientError(error.code == .timedOut ? "mobile.errors.timeout" : "mobile.connect.error_unreachable", params: ["host": address.baseURL.host ?? ""])
        } catch { throw ClientError("mobile.errors.file") }
    }
    public static func validate(_ reply: HTTPReply, authenticated: Bool) throws -> HTTPReply {
        if (200...299).contains(reply.status) { return reply }
        if reply.status == 401 {
            let body = String(data: reply.data, encoding: .utf8) ?? ""
            throw ClientError(authenticated ? "mobile.connect.error_session_expired" : (body.contains("PIN") ? "mobile.connect.error_pin" : "mobile.connect.error_api_key"), status: 401)
        }
        if let error = ServerFailure.parse(reply.data, status: reply.status) { throw error }
        let key: String
        switch reply.status {
        case 403: key = "mobile.errors.forbidden"
        case 429: key = "mobile.errors.rate_limited"
        case 300...399: key = "mobile.errors.redirect"
        default: key = "mobile.errors.backend"
        }
        throw ClientError(key, params: ["status": String(reply.status), "seconds": reply.headers["retry-after"] ?? "30"], status: reply.status)
    }
    public func decode<T: Decodable & Sendable>(_ type: T.Type, _ path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil) async throws -> T {
        let reply = try await send(request(path, method: method, body: body, contentType: contentType))
        do { return try JSONDecoder().decode(type, from: reply.data) }
        catch { throw ClientError("mobile.errors.invalid_response") }
    }
    public func json<T: Encodable & Sendable, R: Decodable & Sendable>(_ path: String, method: String = "POST", value: T, response: R.Type) async throws -> R {
        try await decode(response, path, method: method, body: JSONEncoder().encode(value), contentType: "application/json")
    }
    public func health() async throws -> Health {
        let reply = try await send(request("/health"))
        guard let health = try? JSONDecoder().decode(Health.self, from: reply.data), health.status == "ok", !health.version.isEmpty,
              reply.headers["x-omnivoice-backend"] == health.version else {
            throw ClientError("mobile.connect.error_not_voicestudio", params: ["host": address.baseURL.host ?? ""])
        }
        return health
    }
    public func exchange(key: String) async throws -> Session {
        var r = try request("/api/auth/session", method: "POST", body: Data(#"{"transport":"bearer"}"#.utf8), contentType: "application/json")
        r.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        let reply = try await send(r)
        guard let session = try? JSONDecoder().decode(Session.self, from: reply.data), !session.token.isEmpty else { throw ClientError("mobile.errors.invalid_response") }
        return session
    }
    public func revoke() async throws {
        guard let session = credentials.session, !session.isEmpty else { return }
        _ = try await send(request("/api/auth/session", method: "DELETE"))
    }
    public func profiles() async throws -> [Profile] { try await decode([Profile].self, "/profiles") }
    public func engines() async throws -> EngineInventory { try await decode(EngineInventory.self, "/engines") }
    public func history() async throws -> [HistoryItem] { try await decode([HistoryItem].self, "/history") }
    public func generate(text: String, profile: String, engine: String, language: String) async throws -> Generation {
        var form = Multipart()
        for (k,v) in [("text", text), ("profile_id", profile), ("engine", engine), ("language", language), ("stream", "false")] where !v.isEmpty { form.field(k, v) }
        form.finish()
        var r = try request("/generate", method: "POST", body: form.data, contentType: form.contentType)
        r.setValue("audio/wav", forHTTPHeaderField: "Accept")
        let reply = try await send(r)
        guard reply.headers["content-type"]?.contains("audio/") == true, !reply.data.isEmpty else { throw ClientError("mobile.errors.invalid_response") }
        return Generation(id: reply.headers["x-audio-id"] ?? UUID().uuidString, audio: reply.data)
    }
    public func star(_ item: HistoryItem) async throws {
        struct Star: Encodable, Sendable { let starred: Bool }
        _ = try await send(request("/history/\(try Self.component(item.id))/starred", method: "PUT", body: JSONEncoder().encode(Star(starred: !item.starred)), contentType: "application/json"))
    }
    public func deleteHistory(_ id: String) async throws { _ = try await send(request("/history/\(try Self.component(id))", method: "DELETE")) }
    public func clone(name: String, filename: String, audio: Data) async throws -> Profile {
        var form = Multipart(); form.field("name", name); form.field("kind", "clone"); form.file("ref_audio", filename: filename, bytes: audio, mime: "application/octet-stream"); form.finish()
        return try await decode(Profile.self, "/profiles", method: "POST", body: form.data, contentType: form.contentType)
    }
    public func rename(_ id: String, name: String) async throws {
        struct Name: Encodable, Sendable { let name: String }
        _ = try await send(request("/profiles/\(try Self.component(id))", method: "PUT", body: JSONEncoder().encode(Name(name: name)), contentType: "application/json"))
    }
    public func deleteProfile(_ id: String) async throws { _ = try await send(request("/profiles/\(try Self.component(id))", method: "DELETE")) }
    public func profileAudio(_ id: String) async throws -> Data { try await send(request("/profiles/\(try Self.component(id))/audio")).data }
    // Static WAV mount registered in backend/main.py (StaticFiles is absent from FastAPI OpenAPI).
    public func historyAudioRequest(_ id: String) throws -> URLRequest { try request("/audio/\(try Self.component(id)).wav") }
    public func historyAudio(_ id: String) async throws -> Data { try await send(historyAudioRequest(id)).data }
    public func dubHistory() async throws -> [DubHistoryItem] { try await decode([DubHistoryItem].self, "/dub/history") }
    public func ingest(url: String, jobID: String) async throws -> DubUpload {
        struct Ingest: Encodable, Sendable { let url: String; let job_id: String }
        return try await json("/dub/ingest-url", value: Ingest(url: url, job_id: jobID), response: DubUpload.self)
    }
    public func uploadDub(_ form: Multipart) async throws -> DubUpload {
        try await decode(DubUpload.self, "/dub/upload", method: "POST", body: form.data, contentType: form.contentType)
    }
    public func events(_ task: String, after: Int) async throws -> JobEvents { try await decode(JobEvents.self, "/jobs/\(try Self.component(task))/events?after_seq=\(after)") }
    public func transcribe(_ job: String) async throws -> Transcript { try await decode(Transcript.self, "/dub/transcribe/\(try Self.component(job))", method: "POST") }
    public func translationEngines() async throws -> TranslationEngines { try await decode(TranslationEngines.self, "/engines/translation") }
    public func translationEngineForJob() async throws -> TranslationEngine {
        let engine = try await translationEngines().selected()
        guard engine.category == "llm" else { return engine }
        do {
            let skills = try await decode(LLMSkills.self, "/api/settings/llm-skills")
            if let skill = skills.skills.first(where: { $0.id == "dub_translation" }), skill.ready == true,
               let id = skill.provider {
                let providers = try await decode(LLMProviders.self, "/api/settings/llm-providers")
                let provider = providers.providers.first { $0.id == id }
                return engine.resolved(provider: id, name: skill.provider_display_name ?? provider?.display_name,
                                       baseURL: provider?.base_url, transport: provider?.transport)
            }
            // dub_translate.py falls back to TRANSLATE_* only when no ready
            // skill client resolves. The inventory explicitly identifies this.
            if engine.configured_via == "env" {
                let endpoint = try await decode(LLMEndpoint.self, "/api/settings/llm-endpoint")
                return engine.resolved(provider: nil, name: nil, baseURL: endpoint.base_url)
            }
        } catch is CancellationError { throw CancellationError() }
        catch let error as ClientError where error.status == 401 || error.isTransient { throw error }
        // Settings discovery is admin-only on desktop: forbidden, missing or unreadable
        // means unknown, which requires consent. Transient failures above are retried.
        catch {}
        return engine.resolved(provider: nil, name: nil, baseURL: nil)
    }
    public func translate(_ job: String, segments: [Segment], language: String, provider: String) async throws -> Translation {
        guard !provider.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ClientError("mobile.dub.translation_setup") }
        struct Input: Encodable, Sendable { let job_id: String; let segments: [Segment]; let target_lang: String; let provider: String }
        return try await json("/dub/translate", value: Input(job_id: job, segments: segments, target_lang: language, provider: provider), response: Translation.self)
    }
    public func render(_ job: String, segments: [Segment], language: String, languageName: String) async throws -> TaskReference {
        struct Input: Encodable, Sendable { let segments: [Segment]; let language_code: String; let language: String }
        return try await json("/dub/generate/\(try Self.component(job))", value: Input(segments: segments, language_code: language, language: languageName), response: TaskReference.self)
    }
    public func abort(_ job: String, task: String?) async throws {
        if let task { _ = try await send(request("/tasks/cancel/\(try Self.component(task))", method: "POST")) }
        _ = try await send(request("/dub/abort/\(try Self.component(job))", method: "POST"))
    }
    public func downloadDub(_ job: String) async throws -> Data { try await send(request("/dub/download/\(try Self.component(job))")).data }
    public func readiness() async throws -> Readiness { try await decode(Readiness.self, "/dictation/readiness") }
    public func dictationRequest() async throws -> URLRequest {
        var query = "?pcm=1&sr=16000"
        if credentials.session != nil {
            struct Path: Encodable, Sendable { let path = "/ws/transcribe" }
            let ticket = try await json("/api/auth/ws-ticket", value: Path(), response: Ticket.self)
            query += "&ws_ticket=" + (ticket.ticket.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "")
        }
        var r = try request("/ws/transcribe" + query)
        var c = URLComponents(url: r.url!, resolvingAgainstBaseURL: false)!
        c.scheme = c.scheme == "https" ? "wss" : "ws"; r.url = c.url
        r.setValue(nil, forHTTPHeaderField: "Authorization")
        return r
    }
    public static func component(_ value: String) throws -> String {
        guard !value.isEmpty, value.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }) else { throw ClientError("mobile.errors.invalid_response") }
        return value
    }
}


public struct CommunityItem: Decodable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let author: String?
    public let license: String?
    public let language: String?
}
public struct CommunityPage: Decodable, Sendable { public let total: Int; public let items: [CommunityItem] }
public struct CommunityVoice: Decodable, Sendable { public let profile_id: String; public let name: String }
public extension APIClient {
    func community(offset: Int = 0) async throws -> CommunityPage {
        guard offset >= 0 else { throw ClientError("mobile.errors.invalid_response") }
        return try await decode(CommunityPage.self,"/community/items?limit=100&offset=\(offset)")
    }
    func useCommunity(_ id: String) async throws -> CommunityVoice { try await decode(CommunityVoice.self,"/community/items/\(try Self.component(id))/use",method: "POST") }
    func communityAudio(_ id: String) async throws -> Data { try await send(request("/community/items/\(try Self.component(id))/preview")).data }
}
