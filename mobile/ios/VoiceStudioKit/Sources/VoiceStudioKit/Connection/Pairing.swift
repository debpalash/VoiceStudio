import Foundation
import Darwin

public struct ClientError: Error, Codable, Sendable, Equatable {
    public let key: String
    public let params: [String: String]
    public let status: Int?
    public init(_ key: String, params: [String: String] = [:], status: Int? = nil) {
        self.key = key; self.params = params; self.status = status
    }
    /// The host is slow, unreachable or temporarily overloaded; worth retrying.
    public var isTransient: Bool {
        key == "mobile.connect.error_unreachable" || key == "mobile.errors.timeout" || [429, 500, 502, 503, 504].contains(status ?? 0)
    }
    public static let invalidURL = ClientError("mobile.connect.error_invalid_url")
}

public enum CleartextPolicy {
    public static func permits(host raw: String) -> Bool {
        let host = raw.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        if host == "localhost" || host.hasSuffix(".localhost") || host.hasSuffix(".local") || host.hasSuffix(".ts.net") { return true }
        var ipv4 = in_addr()
        if inet_pton(AF_INET, host, &ipv4) == 1 {
            let n = UInt32(bigEndian: ipv4.s_addr)
            return (n >> 24 == 127) || (n >> 24 == 10) || (n >> 20 == 0xac1) || (n >> 16 == 0xc0a8)
                || (n >> 22 == 0x191) || (n >> 16 == 0xa9fe)
        }
        var ipv6 = in6_addr()
        if inet_pton(AF_INET6, host, &ipv6) == 1 {
            let b = withUnsafeBytes(of: &ipv6) { Array($0) }
            if b.dropLast().allSatisfy({ $0 == 0 }) && b.last == 1 { return true }
            if b[0] & 0xfe == 0xfc || (b[0] == 0xfe && b[1] & 0xc0 == 0x80) { return true }
            if b.prefix(10).allSatisfy({ $0 == 0 }) && b[10] == 255 && b[11] == 255 {
                return permits(host: b.suffix(4).map(String.init).joined(separator: "."))
            }
        }
        return false
    }
    public static func validate(_ url: URL) throws {
        guard let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme), let host = url.host, !host.isEmpty else { throw ClientError.invalidURL }
        guard scheme == "https" || permits(host: host) else { throw ClientError("mobile.connect.error_insecure") }
    }
}

public struct PairingAddress: Sendable, Equatable {
    public let baseURL: URL
    public let pin: String?
    public init(_ input: String, pin manualPIN: String? = nil, qr: Bool = false) throws {
        guard var c = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines)),
              c.user == nil, c.password == nil, c.fragment == nil,
              let url = c.url, c.port.map({ (1...65535).contains($0) }) ?? true else { throw ClientError.invalidURL }
        try CleartextPolicy.validate(url)
        let items = c.queryItems ?? []
        guard items.allSatisfy({ $0.name == "pin" }), items.count <= 1 else { throw ClientError.invalidURL }
        let p = manualPIN.flatMap { $0.isEmpty ? nil : $0 } ?? items.first?.value
        if let p, !(p.utf8.count == 6 && p.utf8.allSatisfy({ (48...57).contains($0) })) { throw ClientError("mobile.connect.error_pin") }
        if qr && (p == nil || !(c.path.isEmpty || c.path == "/")) { throw ClientError("mobile.connect.error_qr") }
        c.query = nil
        c.scheme = c.scheme?.lowercased()
        c.host = c.host?.lowercased()
        c.path = c.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        c.path = c.path.isEmpty ? "" : "/" + c.path
        guard let base = c.url else { throw ClientError.invalidURL }
        baseURL = base; pin = p
    }
    public func endpoint(_ path: String) throws -> URL {
        // Paths originate in the contract, never in arbitrary remote redirects.
        guard path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains(".."), !path.contains("\\"), !path.contains("#") else { throw ClientError.invalidURL }
        guard let url = URL(string: baseURL.absoluteString + path), Self.sameOrigin(url, baseURL) else { throw ClientError.invalidURL }
        return url
    }
    public static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.scheme == rhs.scheme && lhs.host == rhs.host && (lhs.port ?? (lhs.scheme == "https" ? 443 : 80)) == (rhs.port ?? (rhs.scheme == "https" ? 443 : 80))
    }
}
