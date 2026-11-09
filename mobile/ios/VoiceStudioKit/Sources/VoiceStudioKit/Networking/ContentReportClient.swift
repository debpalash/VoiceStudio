import Foundation

/// The root `package.json` version (`X.Y.Z` or `X.Y.Z-beta.N`). App Store Connect only
/// accepts `X.Y.Z` in CFBundleShortVersionString, so the full one ships under its own key.
public enum AppVersion {
    public static func full(info: [String: Any]?) -> String? {
        (info?[ContentReportClient.appVersionInfoKey] as? String)?.nilIfBlank ?? (info?["CFBundleShortVersionString"] as? String)?.nilIfBlank
    }
}

/// `POST /v1/content-reports` on VoiceStudio's app API. Deliberately separate from
/// `APIClient`: it never carries the paired host's PIN, session or any `Authorization`.
public struct ContentReportClient: Sendable {
    public static let productionBaseURL = URL(string: "https://appapi.voicestudio.sh")!
    /// Info.plist key holding the root `package.json` version (written by prepare_resources.py).
    public static let appVersionInfoKey = "VoiceStudioAppVersion"
    /// Info.plist key holding a build's `VOICESTUDIO_CONTENT_REPORTS_URL` override (written by prepare_resources.py).
    public static let baseURLInfoKey = "VoiceStudioContentReportsURL"
    /// Production for every build unless the build was pointed elsewhere, e.g. at the development API.
    public static func baseURL(info: [String: Any]?) -> URL {
        guard let raw = (info?[baseURLInfoKey] as? String)?.nilIfBlank, raw.hasPrefix("https://"), let url = URL(string: raw) else { return productionBaseURL }
        return url
    }
    /// The full app version (`X.Y.Z` or `X.Y.Z-beta.N`), limited to the server's `^[0-9A-Za-z.+_-]{1,32}$`.
    public static func appVersion(info: [String: Any]?) -> String {
        let raw = AppVersion.full(info: info) ?? ""
        let kept = String(String.UnicodeScalarView(raw.unicodeScalars.filter { ContentReportSubject.allowed($0, extra: ".+_-") }).prefix(32))
        return kept.isEmpty ? "unknown" : kept
    }

    public let baseURL: URL
    public let transport: any HTTPTransport
    public let makeKey: @Sendable () -> String
    public init(baseURL: URL, transport: any HTTPTransport = URLSessionTransport(), makeKey: @escaping @Sendable () -> String = IdempotencyKey.make) {
        self.baseURL = baseURL; self.transport = transport; self.makeKey = makeKey
    }
    public func request(_ body: ContentReportBody, key: String) throws -> URLRequest {
        var r = URLRequest(url: baseURL.appendingPathComponent("v1/content-reports"), cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        r.httpMethod = "POST"
        r.httpShouldHandleCookies = false
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        r.setValue(key, forHTTPHeaderField: "Idempotency-Key")
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        r.httpBody = try encoder.encode(body)
        return r
    }
    /// Sends once; on 409 makes a new key and sends once more. Returns the outcome
    /// and the key to reuse on Retry.
    public func submit(_ body: ContentReportBody, key: String) async -> (ContentReportOutcome, String) {
        var key = key
        for attempt in 0..<2 {
            let status: Int
            do { status = try await transport.send(request(body, key: key)).status }
            catch { return (.failed, key) }
            switch status {
            case 201: return (.sent, key)
            case 409:
                // The key was used with a different body; it can never succeed again.
                key = makeKey()
                if attempt == 1 { return (.failed, key) }
            case 429: return (.rateLimited, key)
            case 400: return (.invalid, key)
            default: return (.failed, key)
            }
        }
        return (.failed, key)
    }
}
