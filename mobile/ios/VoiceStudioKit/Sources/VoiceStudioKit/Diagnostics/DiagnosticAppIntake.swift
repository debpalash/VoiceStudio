import Foundation
import CoreFoundation

/// Build configuration only. Credentials are never part of a stored report.
public struct DiagnosticAppConfiguration: Sendable {
    public let appID: String
    let ingestKey: String
    public let host: String
    public let sessionPings: Bool
    public init(appID: String, ingestKey: String, host: String, sessionPings: Bool = false) throws {
        let appID = appID.trimmingCharacters(in: .whitespacesAndNewlines), ingestKey = ingestKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard diagnosticMatches(appID, "[a-z0-9]{6,32}"), diagnosticMatches(ingestKey, "smk_[a-z2-7]{39}") else { throw DiagnosticFailure.invalidDestination }
        _ = try DiagnosticDestination(site: appID, host: host, appUrl: "https://voicestudio.sh")
        self.appID = appID; self.ingestKey = ingestKey; self.host = host; self.sessionPings = sessionPings
    }
    public static func resolve(generated: Data?, environment: [String: String], fallbackHost: String) -> Self? {
        let raw = generated.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let appID = environment["SUPERMETRY_APP_ID"] ?? raw["app_id"] as? String ?? ""
        let key = environment["SUPERMETRY_INGEST_KEY"] ?? raw["ingest_key"] as? String ?? ""
        let selectedHost = environment["SUPERMETRY_APP_HOST"] ?? raw["host"] as? String ?? fallbackHost
        let validHost = (try? DiagnosticDestination(site: "native01", host: selectedHost, appUrl: "https://voicestudio.sh")) != nil
        let sessionFlag = (raw["diagnostics"] as? [String: Any])?["sessionPings"] as? NSNumber
        let sessionPings = sessionFlag.map { CFGetTypeID($0) == CFBooleanGetTypeID() && $0.boolValue } ?? false
        return try? Self(appID: appID, ingestKey: key, host: validHost ? selectedHost : fallbackHost,
                         sessionPings: sessionPings)
    }
}

public struct DiagnosticTransportReply: Sendable {
    public let status: Int
    public let body: Data
    public init(status: Int, body: Data = Data()) { self.status = status; self.body = body }
}
public struct DiagnosticDeliveryResult: Sendable {
    public let id: String
    public let status: Int
    public let reason: String
    public let warningTag: String?
}
private let diagnosticAppTagOrder = ["category", "source", "stage", "error_code", "engine_id", "uptime_bucket", "elapsed_bucket", "memory_bucket", "disk_bucket", "schema_version"]

/// This projection accepts already-sanitized closed properties, never an Error.
func diagnosticAppEvent(_ props: [String: DiagnosticValue], id: String, timestamp: Int64,
                        installID: String, sessionID: String? = nil, session: Bool = false) throws -> Data {
    func scalar(_ value: DiagnosticValue) -> Any {
        switch value { case .string(let s): return s; case .integer(let n): return n }
    }
    func bounded(_ key: String, _ cap: Int) -> String? { props[key]?.string.flatMap { $0.isEmpty || $0.utf8.count > cap ? nil : $0 } }
    let category = props["category"]?.string ?? "other"
    let crash = ["app_crash", "backend_crash", "renderer_crash"].contains(category)
    let operations = ["generation_failed", "model_load_failed", "engine_failed", "download_failed", "operation_failed"]
    let kind = crash ? "crash" : (["install_failed", "install_stalled", "incompatibility"].contains(category) ? category : (operations.contains(category) ? "operation_failed" : "error"))
    let platforms = ["darwin": "macos", "mac": "macos", "osx": "macos", "win32": "windows", "win": "windows"]
    let arches = ["x64": "x64", "amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64", "ia32": "x86", "x86": "x86", "arm": "arm", "armv7l": "arm"]
    let platform = props["platform"]?.string ?? "other"
    var event: [String: Any] = ["id": id, "ts": timestamp, "install_id": installID, "runtime": "swift",
        "kind": session ? "session" : kind,
        "platform": platforms[platform] ?? (["windows", "macos", "linux", "ios", "android", "web"].contains(platform) ? platform : "other"),
        "arch": arches[props["arch"]?.string ?? ""] ?? "other"]
    if let sessionID { event["session_id"] = sessionID }
    if let release = bounded("app_version", 64) { event["release"] = release }
    if !session {
        event["level"] = crash ? "fatal" : "error"; event["handled"] = !crash
        for (source, target, cap) in [("build_commit", "build", 64), ("device_family", "device_class", 32), ("reason", "exit_reason", 32), ("error_type", "error_type", 64)] {
            if let value = bounded(source, cap) { event[target] = value }
        }
        for key in ["exit_code", "frames", "fingerprint", "occurrences"] { if let value = props[key] { event[key] = scalar(value) } }
        if let signal = bounded("signal", 32) { event["signal"] = signal }
    }
    var data = try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys, .withoutEscapingSlashes])
    if !session {
        // Dictionaries/JSONEncoder do not guarantee this insertion order. Build
        // only the closed scalar tag object in the owner's canonical order.
        data.removeLast(); data.append(Data(",\"tags\":{".utf8))
        var separator = ""
        for key in diagnosticAppTagOrder {
            guard let value = props[key], value.string != "", (value.string?.utf8.count ?? 0) <= 200 else { continue }
            data.append(Data("\(separator)\"\(key)\":".utf8))
            data.append(try JSONSerialization.data(withJSONObject: scalar(value), options: [.fragmentsAllowed, .withoutEscapingSlashes]))
            separator = ","
        }
        data.append(Data("}}".utf8))
    }
    return data
}

func diagnosticAppBody(site: String, events: [Data]) throws -> Data {
    var body = Data("{\"site\":".utf8)
    body.append(try JSONSerialization.data(withJSONObject: site, options: [.fragmentsAllowed]))
    body.append(Data(",\"events\":[".utf8))
    for (index, event) in events.enumerated() { if index > 0 { body.append(44) }; body.append(event) }
    body.append(Data("]}".utf8)); return body
}

/// Unspecified/ambiguous acknowledgements never delete a record.
func diagnosticAppCompleted(_ reply: DiagnosticTransportReply, count: Int) -> Set<Int> {
    diagnosticAppAcknowledgement(reply, count: count)?.completed ?? []
}
struct DiagnosticAppAcknowledgement {
    let completed: Set<Int>
    let reasons: [Int: String]
    let warnings: [(index: Int, tag: String)]
}
func diagnosticAppAcknowledgement(_ reply: DiagnosticTransportReply, count: Int) -> DiagnosticAppAcknowledgement? {
    if reply.status == 400 { return DiagnosticAppAcknowledgement(completed: Set(0..<count), reasons: Dictionary(uniqueKeysWithValues: (0..<count).map { ($0, "invalid") }), warnings: []) }
    func integer(_ raw: Any?) -> Int? {
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue == Double(number.intValue) else { return nil }
        return number.intValue
    }
    guard reply.status == 202, let raw = try? JSONSerialization.jsonObject(with: reply.body) as? [String: Any],
          let accepted = integer(raw["accepted"]), accepted >= 0, accepted <= count,
          let dropped = raw["dropped"] as? [[String: Any]], dropped.count <= count else { return nil }
    var indices = Set<Int>(), terminal = Set<Int>(), reasons: [Int: String] = [:]
    for drop in dropped {
        guard let index = integer(drop["index"]), (0..<count).contains(index), indices.insert(index).inserted,
              let reason = drop["reason"] as? String, ["too_old", "invalid", "duplicate", "rate_limited"].contains(reason) else { return nil }
        reasons[index] = reason
        if reason != "rate_limited" { terminal.insert(index) }
    }
    guard accepted + indices.count == count else { return nil }
    for index in Set(0..<count).subtracting(indices) { reasons[index] = "accepted" }
    let warnings = (raw["warnings"] as? [[String: Any]] ?? []).prefix(20).compactMap { warning -> (index: Int, tag: String)? in
        guard let index = integer(warning["index"]), (0..<count).contains(index), warning["reason"] as? String == "tag_not_allowed", let tag = warning["tag"] as? String, diagnosticAppTagOrder.contains(tag) else { return nil }
        return (index, tag)
    }
    return DiagnosticAppAcknowledgement(completed: Set(0..<count).subtracting(indices).union(terminal), reasons: reasons, warnings: warnings)
}
