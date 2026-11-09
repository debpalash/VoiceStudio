import Foundation

/// Compact headings keep the precise localized recovery instruction visible below them.
public struct ErrorPresentation: Sendable {
    public let title: String
    public let detail: String?
    public init(_ error: ClientError, l10n: L10n, titleKey: String? = nil) {
        let key = titleKey ?? Self.shortTitle(error.key)
        title = l10n.t(key)
        let specific = l10n.t(error.key,error.params)
        detail = specific == title ? nil : specific
    }
    private static func shortTitle(_ key: String) -> String {
        if key.hasPrefix("mobile.simple.") { return key }
        if key.hasPrefix("mobile.local.") { return "mobile.simple.models_error" }
        if key.contains("offline") || key.contains("connection") { return "mobile.simple.connection_error" }
        if key.contains("file") || key.contains("media") { return "mobile.simple.media_failed" }
        if key.contains("clone") { return "mobile.simple.clone_failed" }
        if key.contains("dub") { return "mobile.simple.dub_failed" }
        return "mobile.simple.error"
    }
}
