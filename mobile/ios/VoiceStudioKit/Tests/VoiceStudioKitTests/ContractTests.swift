import Foundation
import Testing
@testable import VoiceStudioKit

let repository = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

func data(_ text: String) -> Data { Data(text.utf8) }
func catalogs() throws -> [String: Data] {
    let source = repository.appendingPathComponent("electron/src/renderer/src/i18n/locales")
    var catalogs: [String: Data] = [:]
    for path in try FileManager.default.contentsOfDirectory(at: source, includingPropertiesForKeys: nil) where path.pathExtension == "json" { catalogs[path.deletingPathExtension().lastPathComponent] = try Data(contentsOf: path) }
    var en = try JSONSerialization.jsonObject(with: #require(catalogs["en"])) as! [String: Any]
    func merge(_ lhs: inout [String: Any], _ rhs: [String: Any]) {
        for (k,v) in rhs { if var nested = lhs[k] as? [String: Any], let incoming = v as? [String: Any] { merge(&nested, incoming); lhs[k] = nested } else { lhs[k] = v } }
    }
    for name in ["strings.mobile.en.json", "strings.ios-additions.en.json"] {
        let incoming = try JSONSerialization.jsonObject(with: Data(contentsOf: repository.appendingPathComponent("mobile/contract/" + name))) as! [String: Any]
        merge(&en, incoming)
    }
    catalogs["en"] = try JSONSerialization.data(withJSONObject: en)
    return catalogs
}

@Test func tokenParityWithAuthoritativeJSON() throws {
    let json = try JSONSerialization.jsonObject(with: Data(contentsOf: repository.appendingPathComponent("mobile/contract/tokens.json"))) as! [String: Any]
    let palettes = json["palettes"] as! [[String: Any]]
    #expect(Set(VSTokens.palettes.map(\.id)) == Set(palettes.map { $0["id"] as! String }))
    for palette in palettes {
        let id = palette["id"] as! String
        let appearances = palette["colors"] as! [String: [String: String]]
        for appearance in Appearance.allCases {
            let colors = appearances[appearance.rawValue] ?? (palettes.first { $0["id"] as? String == "default" }!["colors"] as! [String: [String: String]])[appearance.rawValue]!
            let theme = Theme(palette: id, appearance: appearance)
            #expect(Set(theme.colors.keys) == Set(VSTokens.roles))
            for (role, hex) in colors {
                let n = UInt32(hex.dropFirst(), radix: 16)!
                let values = [Double((n >> 24) & 255), Double((n >> 16) & 255), Double((n >> 8) & 255), Double(n & 255)].map { $0 / 255 }
                let c = theme[role]
                for (actual, expected) in zip([c.r,c.g,c.b,c.a], values) { #expect(abs(actual - expected) < 0.00006) }
            }
        }
    }
    #expect(ThemePreferences().mode == .dark)
    #expect(ThemePreferences().lightPalette == "default")
    #expect(Theme(palette: "missing").colors == Theme().colors)
}

@Test func localizationInterpolationPluralFallback() throws {
    let l = try L10n(locale: "es-MX", catalogs: ["en": data(#"{"hello":"Hello {{name}}","item_one":"{{count}} item","item_other":"{{count}} items","fallback":"English"}"#), "es": data(#"{"hello":"Hola {{name}}"}"#), "es-MX": data(#"{"exact":"Exact"}"#)])
    #expect(l.t("hello", ["name":"Ada"]) == "Hola Ada")
    #expect(l.t("exact") == "Exact")
    #expect(l.t("item", count: 1) == "1 item")
    #expect(l.t("item", count: 2) == "2 items")
    #expect(l.t("fallback") == "English")
    #expect(l.t("missing") == "missing")
    let french = try L10n(locale: "fr-CA", catalogs: ["en": data("{}"), "fr": data(#"{"item_one":"one","item_other":"many"}"#)])
    #expect(french.t("item", count: 0) == "one")
}

@Test func allSourceLocalizationKeysExist() throws {
    let l10n = try L10n(catalogs: catalogs())
    let root = repository.appendingPathComponent("mobile/ios")
    let paths = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)!
    let pattern = #""((?:mobile|common|consent|privacy|player|themeAppearance|sidebarTools)\.[a-zA-Z0-9_.-]+)""#
    let regex = try NSRegularExpression(pattern: pattern)
    let dynamic: [String: [String]] = [
        "mobile.simple.": ["tts", "asr", "asr-stream", "clone", "translate"],
        "mobile.local.": [],
        "mobile.downloads.": ["waiting", "waiting_network", "connecting", "verifying", "paused"],
        "mobile.local.category.": ["commercial-ok", "conditional", "non-commercial", "unidentified"],
        "mobile.local.right_": ["commercial", "outputs", "redistribution", "voice", "unknown", "restricted"],
        "mobile.settings.mode_": ThemeMode.allCases.map(\.rawValue),
        "mobile.ios.dub_stages.": ["preparing", "transcribing", "translating", "rendering", "done", "failed", "cancelled"],
        "mobile.report.reasons.": ContentReportReason.allCases.map(\.rawValue)
    ]
    for case let file as URL in paths where file.pathExtension == "swift" && !file.path.contains("/.build/") && !file.path.contains("/.cache/") && !file.path.contains("/Tests/") && !file.path.contains("UITests/") {
        let source = try String(contentsOf: file, encoding: .utf8)
        for match in regex.matches(in: source, range: NSRange(source.startIndex..., in: source)) {
            let key = String(source[Range(match.range(at: 1), in: source)!])
            if let suffixes = dynamic[key] { for suffix in suffixes { #expect(l10n.contains(key + suffix), "Missing \(key + suffix)") } }
            else {
                let exists = l10n.contains(key) || (l10n.contains(key + "_one") && l10n.contains(key + "_other"))
                #expect(exists, "Missing \(key) in \(file.lastPathComponent)")
            }
        }
    }
    let mobile = L10n.flatten(try JSONSerialization.jsonObject(with: Data(contentsOf: repository.appendingPathComponent("mobile/contract/strings.mobile.en.json"))))
    for key in mobile.keys { #expect(l10n.contains(key)) }
}

@Test func iosAdditionsDoNotOverrideSharedStrings() throws {
    let contract = repository.appendingPathComponent("mobile/contract")
    let shared = L10n.flatten(try JSONSerialization.jsonObject(with: Data(contentsOf: contract.appendingPathComponent("strings.mobile.en.json"))))
    let additions = L10n.flatten(try JSONSerialization.jsonObject(with: Data(contentsOf: contract.appendingPathComponent("strings.ios-additions.en.json"))))
    #expect(Set(shared.keys).intersection(additions.keys).isEmpty, "Use the shared mobile wording instead of platform overrides")
}

@Test(arguments: ["http://192.168.1.20:3900/?pin=012345", "https://studio.example/", "http://[fd12::1]:3900/?pin=123456", "http://100.64.0.1:3900", "http://box.tail.ts.net/"])
func pairingAccepted(_ url: String) throws {
    let address = try PairingAddress(url)
    #expect(address.baseURL.query == nil)
    #expect(address.baseURL.user == nil)
    #expect(try address.endpoint("/health").host == address.baseURL.host)
}
@Test(arguments: ["http://example.com", "http://192.168.1.1/?pin=123", "http://192.168.1.1/?pin=123456&pin=123456", "https://user:secret@example.com", "file:///etc/passwd", "https://example.com/?api_key=secret", "https://example.com/#pin=123456", "http://100.128.0.1", "http://172.32.0.1", "http://192.168.1.1:0"])
func pairingRejected(_ url: String) { #expect(throws: (any Error).self) { try PairingAddress(url) } }
@Test func qrAndPrefixForms() throws {
    #expect(try PairingAddress("http://192.168.1.2/?pin=012345", qr: true).pin == "012345")
    #expect(throws: (any Error).self) { try PairingAddress("https://example.com", qr: true) }
    #expect(try PairingAddress("https://example.com/studio/").endpoint("/health").path == "/studio/health")
    let address = try PairingAddress("https://example.com")
    #expect(throws: (any Error).self) { try address.endpoint("//evil.example/path") }
    #expect(throws: (any Error).self) { try address.endpoint("/../secrets") }
}
@Test(arguments: ["127.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.255.255", "100.64.0.0", "100.127.255.255", "169.254.1.1", "::1", "fd00::1", "fc00::1", "fe80::1", "::ffff:192.168.1.1", "box.local", "box.ts.net", "localhost"])
func privateCleartext(_ host: String) { #expect(CleartextPolicy.permits(host: host)) }
@Test(arguments: ["8.8.8.8", "100.63.255.255", "100.128.0.0", "172.15.0.1", "172.32.0.1", "192.169.0.1", "2001:4860:4860::8888", "::ffff:8.8.8.8", "box.ts.net.evil.com", "box.local.evil", "192.168.1.1.evil", "0.0.0.0"])
func publicCleartext(_ host: String) { #expect(!CleartextPolicy.permits(host: host)) }

@Test func chineseScriptLocaleUsesDesktopCatalog() throws {
    let l10n = try L10n(locale: "zh-Hant-HK", catalogs: ["en": data(#"{"key":"English"}"#), "zh-TW": data(#"{"key":"Traditional"}"#), "zh-CN": data(#"{"key":"Simplified"}"#)])
    #expect(l10n.t("key") == "Traditional")
    var simplified = l10n; simplified.locale = "zh-Hans-CN"
    #expect(simplified.t("key") == "Simplified")
}

@Test func multipartStagingPreservesBytesAndEscapesFilename() throws {
    let directory = repository.appendingPathComponent("mobile/ios/.generated/test-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let source = directory.appendingPathComponent("clip.wav"), output = directory.appendingPathComponent("upload.multipart")
    let bytes = Data(repeating: 37, count: 2_100_000)
    try bytes.write(to: source)
    let contentType = try Multipart.stageDub(source: source, output: output, jobID: "job-1", audioOnly: true)
    let staged = try Data(contentsOf: output)
    #expect(contentType.hasPrefix("multipart/form-data; boundary="))
    #expect(staged.range(of: bytes) != nil)
    #expect(String(decoding: staged.prefix(512), as: UTF8.self).contains("name=\"video\""))
    var form = Multipart(); form.file("ref_audio", filename: "bad\r\nheader.wav", bytes: data("x"), mime: "audio/wav"); form.finish()
    #expect(!String(decoding: form.data, as: UTF8.self).contains("bad\r\nheader.wav"))
}
