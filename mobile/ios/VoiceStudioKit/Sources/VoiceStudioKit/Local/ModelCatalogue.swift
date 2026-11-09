import Foundation
import CryptoKit

public enum ModelCapability: String, Codable, Sendable { case tts, asr, stream = "asr-stream", clone, translate }
public enum ModelRuntime: String, Codable, Sendable { case sherpa = "sherpa-onnx", omni = "omnivoice.cpp", llama = "llama.cpp" }
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String), number(Double), bool(Bool), array([JSONValue]), object([String: JSONValue]), null
    public init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }
    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    public var string: String? { if case .string(let v) = self { v } else { nil } }
    public var number: Double? { if case .number(let v) = self { v } else { nil } }
}
public enum ModelAssetSource {
    public static func validURL(_ text: String) -> Bool {
        guard let url = URLComponents(string: text),url.scheme == "https",url.user == nil,url.password == nil,url.port == nil,url.query == nil,url.fragment == nil else { return false }
        let parts = url.path.split(separator: "/").map(String.init)
        guard parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && $0.range(of: "^[A-Za-z0-9_.-]+$",options: .regularExpression) != nil }) else { return false }
        if url.host == "github.com" { return parts.count == 6 && Array(parts.prefix(4)) == ["k2-fsa","sherpa-onnx","releases","download"] && parts.last?.hasSuffix(".onnx") == true }
        return url.host == "huggingface.co" && parts.count >= 5 && parts[2] == "resolve" && parts[3].range(of: "^[a-f0-9]{40}$",options: .regularExpression) != nil
    }
    public static func validBundled(_ path: String) -> Bool {
        !path.hasPrefix("/") && path.split(separator: "/",omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." && $0.range(of: "^[A-Za-z0-9_.-]+$",options: .regularExpression) != nil }
    }
}
public struct PhoneModel: Codable, Identifiable, Sendable {
    public struct File: Codable, Sendable {
        public let path: String
        public let size: Int64
        public let sha256: String
        public let role: String
        public var url: String? = nil
        public var bundled: String? = nil
    }
    public struct Licence: Codable, Sendable {
        public let id: String
        public let category: String
        public let requires_acceptance: Bool
        public let credit: String
        public let rights_holder: String
        public let terms_fingerprint: String
        public let terms: [String: JSONValue]?
        public var disclosure: String? {
            guard let terms else { return nil }
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes]
            return (try? encoder.encode(terms)).flatMap { String(data: $0, encoding: .utf8) }
        }
        public var gated: Bool { category != "commercial-ok" || requires_acceptance }
        public var review: LicenceReview { LicenceReview(terms: terms ?? [:]) }
        public var termLines: [TermLine] { TermLine.lines(terms ?? [:]) }
    }
    public let id: String
    public let name: String
    public let capability: ModelCapability
    public let runtime: ModelRuntime
    public let repo: String
    public let revision: String
    public let files: [File]
    public let total_bytes: Int64
    public let languages: [String]
    public let min_ram_gb: Double
    public let licence: Licence
    public let config: [String: JSONValue]
    public func fits(ram: UInt64 = ProcessInfo.processInfo.physicalMemory) -> Bool { Double(ram) >= min_ram_gb * 1_000_000_000 }
    public func selectingLanguage(_ language: String) throws -> PhoneModel {
        guard supportsSpeech, languages.contains(language) else { throw ClientError("mobile.local.model_incompatible") }
        var options = config; options["lang"] = .string(language)
        return PhoneModel(id: id,name: name,capability: capability,runtime: runtime,repo: repo,revision: revision,files: files,total_bytes: total_bytes,languages: languages,min_ram_gb: min_ram_gb,licence: licence,config: options)
    }
    public var supportsSpeech: Bool { capability == .tts || capability == .clone }
    public var supportsCloning: Bool { capability == .clone }
    public var requiresReference: Bool { capability == .clone }
    public var supportsDesign: Bool { runtime == .omni }
    public func supports(_ value: ModelCapability) -> Bool {
        value == .tts ? supportsSpeech : value == .clone ? supportsCloning : capability == value
    }
    public func path(role: String, root: URL) -> String { files.first { $0.role == role }.map { root.appendingPathComponent($0.path).path } ?? "" }
    public func option(_ key: String, fallback: String = "") -> String { config[key]?.string ?? fallback }
}
public struct ModelCatalogue: Codable, Sendable {
    public let schema_version: Int
    public let models: [PhoneModel]
    public static func decode(_ data: Data) throws -> Self {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.schema_version == 1, Set(value.models.map(\.id)).count == value.models.count else { throw ClientError("mobile.local.catalogue_failed") }
        for m in value.models {
            guard safe(m.id), !m.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  m.repo.split(separator: "/").count == 2, m.repo.split(separator: "/").allSatisfy({ safe(String($0)) }),
                  hex(m.revision, count: 40), !m.files.isEmpty, m.min_ram_gb.isFinite, m.min_ram_gb > 0,
                  hex(m.licence.terms_fingerprint, count: 64), ["commercial-ok", "non-commercial", "conditional", "unidentified"].contains(m.licence.category),
                  Set(m.files.map(\.path)).count == m.files.count,
                  m.files.allSatisfy({ ($0.url == nil || ModelAssetSource.validURL($0.url!)) && ($0.bundled == nil || ($0.url == nil && ModelAssetSource.validBundled($0.bundled!))) && $0.size > 0 && hex($0.sha256, count: 64) && !$0.path.hasPrefix("/") && $0.path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy({ safe(String($0)) }) }),
                  m.files.reduce(0.0, { $0 + Double($1.size) }) == Double(m.total_bytes),
                  (m.capability == .clone ? (m.runtime == .omni || (m.runtime == .sherpa && ["pocket","zipvoice"].contains(m.option("model_type")))) : m.runtime == (m.capability == .translate ? .llama : .sherpa))
            else { throw ClientError("mobile.local.catalogue_failed") }
        }
        return value
    }
    private static func hex(_ value: String, count: Int) -> Bool { value.count == count && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) } }
    private static func safe(_ value: String) -> Bool { !value.isEmpty && value != "." && value != ".." && value.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45,46,95].contains($0) } }
}
public enum ModelHash {
    public static func file(_ url: URL) throws -> String {
        let reader = try FileHandle(forReadingFrom: url); defer { try? reader.close() }
        var hash = SHA256()
        while let bytes = try reader.read(upToCount: 1_048_576), !bytes.isEmpty { try Task.checkCancellation(); hash.update(data: bytes) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
public enum TaskLocation: String, Codable, Sendable { case phone, computer }
public enum TaskRoute: Equatable, Sendable { case phone, computer, download, connect }
public enum LocalRouting {
    public static func route(preference: TaskLocation?, installed: Bool, paired: Bool) -> TaskRoute {
        if preference == .computer && paired { return .computer }
        if preference == .phone { return installed ? .phone : .download }
        return installed ? .phone : paired ? .computer : .download
    }
}

/// Plain-language reading of a model's declared terms. The raw terms and their
/// fingerprint stay the record of what was accepted; this is only a summary.
public struct LicenceReview: Sendable, Equatable {
    public struct Right: Sendable, Equatable {
        /// `commercial`, `outputs`, `redistribution` or `voice`.
        public let key: String
        /// `restricted`, or `unknown` for anything not stated.
        public let status: String
    }
    private static let fields = [("commercial", "commercial_inference"), ("outputs", "commercial_outputs"),
                                 ("redistribution", "redistribution"), ("voice", "voice_rights")]
    public let rights: [Right]
    public let notes: String?
    public let evidenceURL: URL?
    public let sourceURL: URL?
    public init(terms: [String: JSONValue]) {
        rights = Self.fields.map { key, field in Right(key: key, status: terms[field]?.string == "restricted" ? "restricted" : "unknown") }
        notes = terms["notes"]?.string.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.flatMap { $0.isEmpty ? nil : $0 }
        evidenceURL = Self.link(terms["evidence_url"]?.string)
        sourceURL = Self.link(terms["source_url"]?.string)
    }
    /// Only plain https links are tappable.
    private static func link(_ text: String?) -> URL? {
        guard let text, let url = URL(string: text), url.scheme == "https", url.host?.isEmpty == false, url.user == nil else { return nil }
        return url
    }
}

/// One declared term as label and value. Every key of `terms` appears, so the
/// readable list shows exactly what the fingerprint covers.
public struct TermLine: Sendable, Equatable {
    public let key: String
    public let label: String
    public let value: String
    public static func lines(_ terms: [String: JSONValue]) -> [TermLine] {
        terms.keys.sorted().map { key in
            let words = key.replacingOccurrences(of: "_", with: " ")
            return TermLine(key: key, label: words.prefix(1).uppercased() + words.dropFirst(), value: text(terms[key] ?? .null))
        }
    }
    private static func text(_ value: JSONValue) -> String {
        switch value {
        case .string(let v): v.isEmpty ? "–" : v
        case .number(let v): v == v.rounded() ? String(Int(v)) : String(v)
        case .bool(let v): v ? "true" : "false"
        case .null: "–"
        case .array(let items): items.isEmpty ? "–" : items.map(text).joined(separator: ", ")
        case .object(let fields): fields.isEmpty ? "–" : fields.keys.sorted().map { "\($0): \(text(fields[$0] ?? .null))" }.joined(separator: "; ")
        }
    }
}
