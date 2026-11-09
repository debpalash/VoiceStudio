import Foundation
import Observation
import Security

public protocol CredentialVault: Sendable {
    func read(host: String) async throws -> Credentials?
    func write(_ credentials: Credentials?, host: String) async throws
}
public actor KeychainVault: CredentialVault {
    private let service = "com.voicestudio.mobile.sessions"
    public init() {}
    public func read(host: String) throws -> Credentials? {
        var query = base(host)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw ClientError("mobile.errors.keychain") }
        return try? JSONDecoder().decode(Credentials.self, from: data)
    }
    public func write(_ credentials: Credentials?, host: String) throws {
        let query = base(host)
        guard let credentials else {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw ClientError("mobile.errors.keychain") }
            return
        }
        let data = try JSONEncoder().encode(credentials)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw ClientError("mobile.errors.keychain") }
        } else if status != errSecSuccess { throw ClientError("mobile.errors.keychain") }
    }
    private func base(_ host: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: host]
    }
}

public struct SavedHost: Codable, Identifiable, Sendable, Equatable {
    public let url: URL
    public var label: String
    public var lastSeen: Date
    public var id: String { url.absoluteString }
    public init(url: URL, label: String, lastSeen: Date = .now) { self.url = url; self.label = label; self.lastSeen = lastSeen }
    // Only the URL is essential; other fields fall back so an app update that
    // changes them never forgets the paired servers.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        url = try c.decode(URL.self, forKey: .url)
        label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? url.host() ?? url.absoluteString
        lastSeen = (try? c.decodeIfPresent(Date.self, forKey: .lastSeen)) ?? .distantPast
    }
}

@MainActor @Observable public final class ConnectionStore {
    public private(set) var hosts: [SavedHost]
    public private(set) var active: SavedHost?
    public private(set) var client: APIClient?
    public private(set) var connecting = false
    @ObservationIgnored public var onHostChange: (@MainActor (String?) -> Void)?
    public private(set) var error: ClientError?
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let vault: any CredentialVault
    @ObservationIgnored private let transport: any HTTPTransport
    @ObservationIgnored private var attempt = UUID()
    public init(defaults: UserDefaults = .standard, vault: any CredentialVault = KeychainVault(), transport: any HTTPTransport = URLSessionTransport()) {
        self.defaults = defaults; self.vault = vault; self.transport = transport
        hosts = defaults.data(forKey: "hosts").flatMap { try? JSONDecoder().decode([SavedHost].self, from: $0) } ?? []
    }
    @discardableResult public func connect(address: PairingAddress, key: String = "", label: String = "") async throws -> APIClient {
        let current = UUID(); attempt = current
        connecting = true; error = nil
        defer { if current == attempt { connecting = false } }
        do {
            let old: Credentials?
            do { old = try await vault.read(host: address.baseURL.absoluteString) }
            catch is CancellationError { throw CancellationError() }
            catch { old = nil } // Unreadable credentials must not prevent fresh pairing.
            var credentials = Credentials(pin: address.pin ?? old?.pin, session: old?.session)
            var candidate = APIClient(address: address, credentials: credentials, transport: transport)
            if !key.isEmpty {
                credentials.session = try await candidate.exchange(key: key).token
                candidate = APIClient(address: address, credentials: credentials, transport: transport)
            }
            _ = try await candidate.health()
            try Task.checkCancellation()
            guard attempt == current else { throw CancellationError() }
            try await vault.write(credentials, host: address.baseURL.absoluteString)
            guard attempt == current else { throw CancellationError() }
            let savedLabel = hosts.first { $0.url == address.baseURL }?.label
            let host = SavedHost(url: address.baseURL, label: label.isEmpty ? (savedLabel ?? address.baseURL.host ?? address.baseURL.absoluteString) : label)
            hosts.removeAll { $0.id == host.id }; hosts.insert(host, at: 0)
            let changed = active?.id != host.id
            active = host; client = candidate
            if changed { onHostChange?(host.id) }
            persist(); defaults.set(host.id, forKey: "activeHost")
            return candidate
        } catch let failure as ClientError {
            if failure.status == 401 { try? await vault.write(nil, host: address.baseURL.absoluteString) }
            if attempt == current { error = failure }
            throw failure
        }
    }
    public func restore() async {
        guard let id = defaults.string(forKey: "activeHost"), let host = hosts.first(where: { $0.id == id }) else { return }
        do { _ = try await connect(address: PairingAddress(host.url.absoluteString), label: host.label) }
        catch { /* Connect remains reachable, with the actionable error retained. */ }
    }
    public func handle(_ failure: ClientError, connectionChange: Bool = false) async {
        // connect owns candidate-host failures. They cannot expire the active host.
        guard !connectionChange else { return }
        error = failure
        if failure.status == 401, let host = active {
            let current = attempt
            try? await vault.write(nil, host: host.id)
            guard attempt == current, active?.id == host.id else { return }
            attempt = UUID(); client = nil; active = nil; onHostChange?(nil); defaults.removeObject(forKey: "activeHost")
        }
    }
    public func disconnect() async throws {
        attempt = UUID()
        let oldClient = client, oldHost = active?.id
        client = nil; active = nil; error = nil; onHostChange?(nil)
        defaults.removeObject(forKey: "activeHost")
        var failure: (any Error)?
        if let oldHost { do { try await vault.write(nil, host: oldHost) } catch { failure = error } }
        if let oldClient { do { try await oldClient.revoke() } catch { if failure == nil { failure = error } } }
        if let failure { throw failure }
    }
    public func forget(_ host: SavedHost) async throws {
        if active?.id == host.id { try await disconnect() }
        try await vault.write(nil, host: host.id)
        hosts.removeAll { $0.id == host.id }; persist()
    }
    public func rename(_ host: SavedHost, label: String) {
        guard let i = hosts.firstIndex(where: { $0.id == host.id }), !label.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        hosts[i].label = label; if active?.id == host.id { active = hosts[i] }; persist()
    }
    private func persist() { defaults.set(try? JSONEncoder().encode(hosts), forKey: "hosts") }
}
