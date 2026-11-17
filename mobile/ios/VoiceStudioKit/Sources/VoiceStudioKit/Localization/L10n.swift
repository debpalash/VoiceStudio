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
        // Legacy English additions only fill gaps in the renderer catalog.
        var englishFallback: [String: String] = [:]
        for name in ["strings.mobile.en", "strings.ios-additions.en"] {
            if let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Catalogs"), let data = try? Data(contentsOf: url),
               let json = try? JSONSerialization.jsonObject(with: data) { englishFallback.merge(Self.flatten(json)) { _, new in new } }
        }
        loaded["en"] = englishFallback.merging(loaded["en"] ?? [:]) { _, renderer in renderer }
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
                // Same precedence as the Android reader: the exact category, then
                // `_other`, then the unsuffixed key.
                let suffix = Self.plural(count, language: code)
                if let value = catalogs[code]?[key + "_" + suffix] ?? catalogs[code]?[key + "_other"] {
                    result = value
                    break
                }
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
    /// CLDR cardinal category for `count`, matching `mobile/android/core/l10n`.
    ///
    /// Both apps read the same generated catalogs, so a count that lands in `few`
    /// on Android has to land in `few` here too; before this the iOS reader only
    /// ever produced `one` or `other`, and a Polish or Russian catalog entry for
    /// 2–4 items was never selected.
    private static func plural(_ count: Int, language: String) -> String {
        let lang = language.split(separator: "-").first.map(String.init) ?? language
        let mod10 = count % 10
        let mod100 = count % 100
        switch lang {
        case "ar":
            if count == 0 { return "zero" }
            if count == 1 { return "one" }
            if count == 2 { return "two" }
            if (3...10).contains(mod100) { return "few" }
            if (11...99).contains(mod100) { return "many" }
            return "other"
        case "ru", "uk":
            if mod10 == 1 && mod100 != 11 { return "one" }
            if (2...4).contains(mod10) && !(12...14).contains(mod100) { return "few" }
            if mod10 == 0 || (5...9).contains(mod10) || (11...14).contains(mod100) { return "many" }
            return "other"
        case "pl":
            if count == 1 { return "one" }
            if (2...4).contains(mod10) && !(12...14).contains(mod100) { return "few" }
            return "many"
        case "fr", "pt":
            return (0...1).contains(count) ? "one" : "other"
        case "hi":
            return (count == 0 || count == 1) ? "one" : "other"
        case "zh", "ja", "ko", "th", "vi", "id":
            return "other"
        default:
            return count == 1 ? "one" : "other"
        }
    }
}
