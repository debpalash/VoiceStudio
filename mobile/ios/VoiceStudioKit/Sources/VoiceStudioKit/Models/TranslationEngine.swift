import Foundation
import CryptoKit

/// GET /engines/translation: loose OpenAPI response, defined by routers/engines.py.
public struct TranslationEngines: Decodable, Sendable {
    public let active: String
    public let engines: [TranslationEngine]
    public func selected() throws -> TranslationEngine {
        guard !active.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let engine = engines.first(where: { $0.id == active }),
              engine.installed == true, engine.ready == true,
              ["offline", "online", "llm"].contains(engine.category) else {
            throw ClientError("mobile.dub.translation_setup")
        }
        return engine
    }
}

public struct TranslationEngine: Decodable, Sendable, Identifiable {
    public let id: String
    public private(set) var display_name: String
    public let category: String
    public let installed: Bool?
    public let ready: Bool?
    public let configured_via: String?
    private var verifiedLoopback = false
    private var routeIdentity: String?
    private enum CodingKeys: CodingKey { case id, display_name, category, installed, ready, configured_via }
    public var requiresConfirmation: Bool { category == "online" || (category == "llm" && !verifiedLoopback) }
    /// An LLM engine ID names a router, not its destination. Approval must also
    /// cover the effective provider/endpoint, including changes during a job.
    public var consentIdentity: String {
        category == "llm" ? id + ":" + (routeIdentity ?? configured_via ?? "unknown") : id
    }
    func resolved(provider: String?, name: String?, baseURL: String?, transport: String? = "openai") -> Self {
        var value = self
        if let name = name?.nilIfBlank { value.display_name = name }
        else if let name = configured_via?.nilIfBlank, name != "env" { value.display_name = name }
        if let baseURL {
            value.routeIdentity = SHA256.hash(data: Data(((provider ?? "env") + "|" + baseURL).utf8))
                .map { String(format: "%02x", $0) }.joined()
            value.verifiedLoopback = transport == "openai" && Self.isLoopbackEndpoint(baseURL)
        }
        return value
    }
    static func isLoopbackEndpoint(_ value: String) -> Bool {
        guard let url = URLComponents(string: value), ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.user == nil, url.password == nil, let host = url.host?.lowercased() else { return false }
        if host == "localhost" || host.hasSuffix(".localhost") || host == "[::1]" || host == "::1" { return true }
        let octets = host.split(separator: ".", omittingEmptySubsequences: false)
        return octets.count == 4 && octets[0] == "127" && octets.allSatisfy { part in
            !part.isEmpty && part.allSatisfy(\.isNumber) && UInt8(part) != nil
        }
    }
}

// Loose schemas: backend/api/routers/settings.py and services/llm_skills.py.
struct LLMSkills: Decodable, Sendable {
    let skills: [Skill]
    struct Skill: Decodable, Sendable {
        let id: String
        let provider: String?
        let provider_display_name: String?
        let ready: Bool?
    }
}
struct LLMProviders: Decodable, Sendable {
    let providers: [Provider]
    struct Provider: Decodable, Sendable {
        let id: String
        let display_name: String?
        let base_url: String?
        let transport: String?
    }
}
struct LLMEndpoint: Decodable, Sendable { let base_url: String? }
