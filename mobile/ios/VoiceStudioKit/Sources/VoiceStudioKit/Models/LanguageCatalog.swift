import Foundation

public struct MobileLanguage: Identifiable, Hashable, Sendable {
    public let name: String
    public let code: String?
    public var id: String { name }
    public var value: String { name == "Auto" ? "" : name.lowercased() }
    public var automatic: Bool { name == "Auto" }
}

/// Names are the desktop JSON, never a platform-local alphabetical substitute.
public struct LanguageCatalog: Sendable {
    public let languages: [MobileLanguage]
    public init(names: [String], codes: [String: String] = [:]) {
        let english = Locale(identifier: "en")
        var iso: [String: String] = [:]
        for code in Locale.LanguageCode.isoLanguageCodes.sorted(by: { $0.identifier.count < $1.identifier.count }) {
            if let name = english.localizedString(forLanguageCode: code.identifier), iso[name.lowercased()] == nil { iso[name.lowercased()] = code.identifier }
        }
        languages = names.map { MobileLanguage(name: $0, code: codes[$0] ?? iso[$0.lowercased()]) }
    }
    public init(bundle: Bundle = .main) {
        let names = bundle.url(forResource: "languages", withExtension: "json", subdirectory: "Catalogs")
            .flatMap { try? Data(contentsOf: $0) }.flatMap { try? JSONDecoder().decode([String].self, from: $0) } ?? []
        let codes = bundle.url(forResource: "language-codes", withExtension: "json", subdirectory: "Catalogs")
            .flatMap { try? Data(contentsOf: $0) }.flatMap { try? JSONDecoder().decode([String: String].self, from: $0) } ?? [:]
        self.init(names: names, codes: codes)
    }
    public func language(_ value: String?) -> MobileLanguage? {
        guard let value, !value.isEmpty else { return languages.first { $0.automatic } }
        return languages.first { $0.name.caseInsensitiveCompare(value) == .orderedSame || $0.code?.caseInsensitiveCompare(value) == .orderedSame }
    }
    public func search(_ query: String, includeAuto: Bool = true) -> [MobileLanguage] {
        languages.filter { (includeAuto || !$0.automatic) && (query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || ($0.code?.localizedCaseInsensitiveContains(query) == true)) }
    }
    public var dubOptions: [DubLanguage] { languages.filter { !$0.automatic }.compactMap { language in language.code.map { DubLanguage(code: $0, name: language.name) } } }
}

public struct SpeechSelection: Sendable, Equatable {
    public var profileID = ""
    public var engineID = ""
    public var language = ""
    public init() {}
    public func profile(in profiles: [Profile]) -> Profile? { profiles.first { $0.id == profileID } }
    public func canGenerate(in inventory: EngineInventory?, profiles: [Profile]) -> Bool {
        inventory?.tts.backends.first { $0.id == engineID }?.selectable(for: profile(in: profiles)) == true
    }
    public mutating func reconcile(inventory: EngineInventory?, profiles: [Profile]) {
        guard let inventory else { return }
        if !profileID.isEmpty && profile(in: profiles) == nil { profileID = ""; language = "" }
        if engineID.isEmpty { engineID = inventory.tts.active }
        guard let selected = inventory.tts.backends.first(where: { $0.id == engineID }), selected.selectable(for: profile(in: profiles)) else {
            let choices = inventory.tts.backends.filter { $0.selectable(for: profile(in: profiles)) }
            engineID = choices.first { $0.id == inventory.tts.active }?.id ?? choices.first?.id ?? engineID
            return
        }
    }
    public mutating func chooseProfile(_ id: String, profiles: [Profile], inventory: EngineInventory?, catalog: LanguageCatalog) {
        profileID = id
        language = catalog.language(profile(in: profiles)?.language)?.value ?? ""
        reconcile(inventory: inventory, profiles: profiles)
    }
}

public enum PlaybackTime {
    public static func format(_ seconds: Double) -> String {
        let value = seconds.isFinite ? Int(max(0, min(seconds, Double(Int.max / 2)))) : 0
        return String(format: "%d:%02d", value / 60, value % 60)
    }
}
