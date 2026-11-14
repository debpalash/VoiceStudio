import Foundation
import CryptoKit

public struct DiagnosticFrame: Sendable {
    public let path: String
    public let line: Int
    public let column: Int?
    public init(path: String, line: Int, column: Int? = nil) { self.path = path; self.line = line; self.column = column }
}
public struct DiagnosticInput: Sendable {
    public var category: String
    public var stage: String
    public var errorCode: String?
    public var errorType: String
    public var engineID: String?
    public var frames: [DiagnosticFrame]
    public var reason: String?
    public var deviceFamily: String?
    public var exitCode: Int64?
    public var signal: String?
    public init(category: String, stage: String = "other", errorCode: String? = nil, errorType: String = "OtherError", engineID: String? = nil, frames: [DiagnosticFrame] = [], reason: String? = nil, deviceFamily: String? = nil, exitCode: Int64? = nil, signal: String? = nil) {
        self.category = category; self.stage = stage; self.errorCode = errorCode; self.errorType = errorType
        self.engineID = engineID; self.frames = frames; self.reason = reason; self.deviceFamily = deviceFamily; self.exitCode = exitCode; self.signal = signal
    }
}
public struct DiagnosticStatus: Sendable, Equatable {
    public var enabled = false
    public var optedIn = false
    public var prompted = false
    public var available = true
    public var pendingCount = 0
    public var droppedCount = 0
    public var consentSaveFailed = false
}
public struct DiagnosticRecoveryContext: Sendable {
    public let epoch: String
    public let enabledAtMs: Int64
    public let appVersion: String
}
public enum DiagnosticFailure: Error { case invalidPolicy, invalidDestination, corruptStore, storageUnavailable, cancelled, disabled }

/// Parsed once from the canonical shared taxonomy; capture never reads a file.
public struct DiagnosticPolicy: Sendable {
    private let enums: [String: Set<String>]
    private let paths: Set<String>
    private let limits: [String: Int]
    public init(data: Data) throws {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any], object["schema_version"] as? Int == 1 else { throw DiagnosticFailure.invalidPolicy }
        var values: [String: Set<String>] = [:]
        for key in ["categories", "stages", "error_codes", "error_types", "engine_ids", "reasons", "platforms", "arches", "device_families", "signals", "buckets"] {
            guard let list = object[key] as? [String] else { throw DiagnosticFailure.invalidPolicy }
            values[key] = Set(list)
        }
        guard let framePaths = object["frame_paths"] as? [String], let rawLimits = object["limits"] as? [String: Int] else { throw DiagnosticFailure.invalidPolicy }
        enums = values; paths = Set(framePaths); limits = rawLimits
    }
    func known(_ key: String, _ value: String?, _ fallback: String) -> String {
        guard let value, value.utf8.count <= 64, enums[key]?.contains(value) == true else { return fallback }
        return value
    }
    func limit(_ key: String, _ ceiling: Int) -> Int { min(max(limits[key] ?? ceiling, 1), ceiling) }
    func frames(_ input: [DiagnosticFrame]) -> String {
        var lines: [String] = []; var bytes = 0
        for frame in input.prefix(24) {
            guard frame.path.utf8.count <= 512, paths.contains(frame.path), (1...10_000_000).contains(frame.line), frame.column == nil || (1...10_000_000).contains(frame.column!) else { continue }
            let line = "\(frame.path):\(frame.line)" + (frame.column.map { ":\($0)" } ?? "")
            let size = line.utf8.count + (lines.isEmpty ? 0 : 1)
            if bytes + size <= limit("frame_bytes", 1800) { lines.append(line); bytes += size }
        }
        return lines.joined(separator: "\n")
    }
    func parseFrames(_ text: String?) -> [DiagnosticFrame] {
        guard let text, text.utf8.count <= limit("frame_bytes", 1800) else { return [] }
        return text.split(separator: "\n").prefix(24).compactMap { line in
            let parts = line.split(separator: ":", omittingEmptySubsequences: false)
            guard (2...3).contains(parts.count), let number = Int(parts[1]), parts.count == 2 || Int(parts[2]) != nil else { return nil }
            return DiagnosticFrame(path: String(parts[0]), line: number, column: parts.count == 3 ? Int(parts[2]) : nil)
        }
    }
}

public struct DiagnosticDestination: Sendable {
    public let site: String
    public let endpoint: URL
    public let origin: String
    public let eventURL: String
    public init(site: String, host: String, appUrl: String) throws {
        guard diagnosticMatches(site, "[a-z0-9]{6,32}"), let server = URLComponents(string: host), let app = URLComponents(string: appUrl) else { throw DiagnosticFailure.invalidDestination }
        for item in [server, app] {
            // HTTP is only for literal loopback fixtures. Inspect the encoded
            // host so escaped names and lookalikes cannot become an exception.
            let scheme = item.scheme?.lowercased()
            let loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].contains(item.percentEncodedHost?.lowercased() ?? "")
            guard scheme == "https" || (scheme == "http" && loopback), item.host?.isEmpty == false, item.user == nil, item.password == nil, item.query == nil, item.fragment == nil else { throw DiagnosticFailure.invalidDestination }
        }
        guard server.path.isEmpty || server.path == "/" else { throw DiagnosticFailure.invalidDestination }
        var endpoint = server; endpoint.path = "/api/public/v1/events"
        var origin = app; origin.path = ""; origin.query = nil; origin.fragment = nil
        guard let url = endpoint.url, let originString = origin.string else { throw DiagnosticFailure.invalidDestination }
        self.site = site; self.endpoint = url; self.origin = originString
        eventURL = appUrl.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/diagnostics"
    }
}

func diagnosticKillSwitch(_ value: String?) -> Bool {
    guard let value else { return false }
    return ["1", "true", "yes", "on"].contains(value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
}

func diagnosticMatches(_ value: String, _ pattern: String) -> Bool {
    value.range(of: "^(?:\(pattern))$", options: .regularExpression) != nil
}
func diagnosticVersion(_ value: String) -> Bool {
    value.utf8.count <= 64 && diagnosticMatches(value, "[0-9]{1,4}\\.[0-9]{1,4}\\.[0-9]{1,4}(?:(?:-(?:alpha|beta|rc|dev)(?:[.-][0-9]{1,6})?)|(?:(?:a|b|rc|dev)[0-9]{1,6}))?")
}
func diagnosticHash(_ value: Data) -> String { SHA256.hash(data: value).map { String(format: "%02x", $0) }.joined() }
func diagnosticID() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
func diagnosticEncode<T: Encodable>(_ value: T) throws -> Data {
    let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    return try encoder.encode(value)
}

enum DiagnosticValue: Codable, Sendable, Equatable {
    case string(String), integer(Int64)
    init(from decoder: any Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let string = try? value.decode(String.self) { self = .string(string) }
        else { self = .integer(try value.decode(Int64.self)) }
    }
    func encode(to encoder: any Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self { case .string(let string): try value.encode(string); case .integer(let number): try value.encode(number) }
    }
    var string: String? { if case .string(let value) = self { value } else { nil } }
    var integer: Int64? { if case .integer(let value) = self { value } else { nil } }
}
struct DiagnosticProjector: Sendable {
    let policy: DiagnosticPolicy
    let appVersion: String
    let buildCommit: String?
    let arch: String
    func props(_ input: DiagnosticInput, version: String?, commit: String?) -> [String: DiagnosticValue] {
        var values: [String: DiagnosticValue] = [
            "category": .string(policy.known("categories", input.category, "operation_failed")), "source": .string("ios"), "platform": .string("ios"),
            "stage": .string(policy.known("stages", input.stage, "other")), "error_code": .string(policy.known("error_codes", input.errorCode, "UNKNOWN")),
            "error_type": .string(policy.known("error_types", input.errorType, "OtherError")), "engine_id": .string(policy.known("engine_ids", input.engineID, "unknown")),
            "reason": .string(policy.known("reasons", input.reason, "unknown")), "device_family": .string(policy.known("device_families", input.deviceFamily, "unknown")),
            "arch": .string(policy.known("arches", arch, "unknown")), "signal": .string(policy.known("signals", input.signal, "unknown"))
        ]
        if let version, diagnosticVersion(version) { values["app_version"] = .string(version) }
        if let commit, diagnosticMatches(commit, "[a-f0-9]{7,40}") { values["build_commit"] = .string(commit) }
        let frames = policy.frames(input.frames)
        if !frames.isEmpty { values["frames"] = .string(frames) }
        if let code = input.exitCode, (-2_147_483_648...4_294_967_295).contains(code) { values["exit_code"] = .integer(code) }
        values["fingerprint"] = .string(diagnosticHash((try? diagnosticEncode(values)) ?? Data()))
        values["occurrences"] = .integer(1); values["schema_version"] = .integer(1)
        return values
    }
    func sanitize(_ raw: [String: DiagnosticValue]) -> [String: DiagnosticValue] {
        let input = DiagnosticInput(category: raw["category"]?.string ?? "operation_failed", stage: raw["stage"]?.string ?? "other", errorCode: raw["error_code"]?.string, errorType: raw["error_type"]?.string ?? "OtherError", engineID: raw["engine_id"]?.string, frames: policy.parseFrames(raw["frames"]?.string), reason: raw["reason"]?.string, deviceFamily: raw["device_family"]?.string, exitCode: raw["exit_code"]?.integer, signal: raw["signal"]?.string)
        var safe = props(input, version: raw["app_version"]?.string, commit: raw["build_commit"]?.string)
        safe["occurrences"] = .integer(min(max(raw["occurrences"]?.integer ?? 1, 1), 1_000_000))
        return safe
    }
}
