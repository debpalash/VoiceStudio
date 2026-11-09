import Foundation

public struct L10n: Sendable {
    public static let languages = ["en", "zh-CN", "es", "fr", "de", "ja", "pt", "it", "ru", "ko", "hi", "tr", "pl", "nl", "sv", "th", "vi", "id", "uk", "ar", "zh-TW", "gl"]
    public var locale: String
    private let catalogs: [String: [String: String]]
    public init(locale: String = Locale.current.identifier, catalogs: [String: Data]) throws {
        self.locale = locale.replacingOccurrences(of: "_", with: "-")
        self.catalogs = try catalogs.mapValues { try Self.flatten(JSONSerialization.jsonObject(with: $0)) }
    }
    public init(bundle: Bundle = .main, locale: String = Locale.current.identifier) {
        self.locale = locale.replacingOccurrences(of: "_", with: "-")
        var loaded: [String: [String: String]] = [:]
        for code in Self.languages {
            if let url = bundle.url(forResource: code, withExtension: "json", subdirectory: "Catalogs"), let data = try? Data(contentsOf: url),
               let json = try? JSONSerialization.jsonObject(with: data) { loaded[code] = Self.flatten(json) }
        }
        // English mobile additions deliberately fall back until translations are merged upstream.
        for name in ["strings.mobile.en", "strings.ios-additions.en"] {
            if let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Catalogs"), let data = try? Data(contentsOf: url),
               let json = try? JSONSerialization.jsonObject(with: data) { loaded["en", default: [:]].merge(Self.flatten(json)) { _, new in new } }
        }
        catalogs = loaded
    }
    public static func flatten(_ object: Any, prefix: String = "") -> [String: String] {
        guard let object = object as? [String: Any] else { return [:] }
        var result: [String: String] = [:]
        for (k, v) in object {
            let key = prefix.isEmpty ? k : prefix + "." + k
            if let s = v as? String { result[key] = s }
            else { result.merge(flatten(v, prefix: key)) { _, new in new } }
        }
        return result
    }
    public func contains(_ key: String) -> Bool { catalogs["en"]?[key] != nil }
    public func t(_ key: String, _ params: [String: String] = [:], count: Int? = nil) -> String {
        let normalized: String
        if let exact = catalogs.keys.first(where: { $0.caseInsensitiveCompare(locale) == .orderedSame }) { normalized = exact }
        else if locale.lowercased().hasPrefix("zh") {
            let parts = locale.lowercased().split(separator: "-")
            // An explicit script takes priority over a region (e.g. zh-Hans-HK).
            normalized = parts.contains("hans") ? "zh-CN" :
                (parts.contains(where: { ["hant", "tw", "hk", "mo"].contains(String($0)) }) ? "zh-TW" : "zh-CN")
        } else { normalized = locale }
        let language = normalized.split(separator: "-").first.map(String.init) ?? "en"
        let chain = [normalized, language, "en"]
        var result: String?
        for code in chain {
            if let count {
                let suffix = Self.plural(count, language: code)
                if let value = catalogs[code]?[key + "_" + suffix] { result = value; break }
            }
            if let value = catalogs[code]?[key] { result = value; break }
        }
        let text = result ?? key
        var values = params
        if let count { values["count"] = String(count) }
        // A single literal scan: no ICU regex/replacement syntax and no recursive interpolation.
        var output = ""
        var cursor = text.startIndex
        while let open = text.range(of: "{{", range: cursor..<text.endIndex),
              let close = text.range(of: "}}", range: open.upperBound..<text.endIndex) {
            output += text[cursor..<open.lowerBound]
            let name = text[open.upperBound..<close.lowerBound].trimmingCharacters(in: .whitespacesAndNewlines)
            output += values[name] ?? String(text[open.lowerBound..<close.upperBound])
            cursor = close.upperBound
        }
        return output + text[cursor...]
    }
    private static func plural(_ count: Int, language: String) -> String {
        let lang = language.split(separator: "-").first.map(String.init) ?? language
        if ["ja", "zh", "ko", "th", "vi", "id"].contains(lang) { return "other" }
        if lang == "fr" || lang == "pt" { return count == 0 || count == 1 ? "one" : "other" }
        return count == 1 ? "one" : "other"
    }
}
