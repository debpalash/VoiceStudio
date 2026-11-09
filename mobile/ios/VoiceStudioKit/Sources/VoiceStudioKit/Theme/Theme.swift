import Foundation

public enum Appearance: String, CaseIterable, Codable, Sendable { case light, dark }
public enum ThemeMode: String, CaseIterable, Codable, Sendable { case light, dark, system }
public struct ThemePreferences: Codable, Sendable, Equatable {
    public var mode: ThemeMode = .dark
    public var lightPalette = "default"
    public var darkPalette = "default"
    public init() {}
    // Tolerant decoding: a missing key or an unknown mode from another app
    // version keeps the default instead of discarding every saved choice.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = (try? c.decodeIfPresent(ThemeMode.self, forKey: .mode)) ?? .dark
        lightPalette = (try? c.decodeIfPresent(String.self, forKey: .lightPalette)) ?? "default"
        darkPalette = (try? c.decodeIfPresent(String.self, forKey: .darkPalette)) ?? "default"
    }
    public func appearance(system: Appearance) -> Appearance {
        mode == .system ? system : (mode == .light ? .light : .dark)
    }
    public func palette(system: Appearance) -> String { appearance(system: system) == .light ? lightPalette : darkPalette }
}
public struct Theme: Sendable {
    public let colors: [String: VSTokens.RGBA]
    public let appearance: Appearance
    public init(palette: String = "default", appearance: Appearance = .dark) {
        self.appearance = appearance
        let fallback = VSTokens.palettes.first { $0.id == "default" }!
        let selected = VSTokens.palettes.first { $0.id == palette } ?? fallback
        colors = (appearance == .dark ? selected.dark : selected.light) ?? (appearance == .dark ? fallback.dark : fallback.light)!
    }
    public subscript(role: String) -> VSTokens.RGBA { colors[role] ?? colors["text"]! }
    public static func paletteLabel(_ palette: VSTokens.Palette, l10n: L10n) -> String {
        palette.id == "default" ? l10n.t("themeAppearance.default") : palette.label
    }
    public static var titleTypeSize: Double { VSTokens.textEditor.lineHeight }
    public static var compactInset: Double { min(16, VSTokens.spacingInset) }
    public static var controlTypeSize: Double { max(17, VSTokens.textUi.size) }
}
