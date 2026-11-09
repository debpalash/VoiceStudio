import Foundation
import Observation

/// Reporting generated content (contract: SPEC.md → Reporting generated content).
public enum ContentReportReason: String, CaseIterable, Codable, Sendable {
    case sexual, hateful, impersonation, misleading, other
    public var labelKey: String { "mobile.report.reasons." + rawValue }
}
public enum ContentReportTask: String, Codable, Sendable { case speak, clone, dub }
public enum ContentReportRunsOn: String, Codable, Sendable { case phone, computer }

/// One generated result the person can report, and what produced it.
public struct ContentReportSubject: Identifiable, Hashable, Sendable {
    /// Stable per result; drafts are kept under it until sent.
    public let id: String
    public let task: ContentReportTask
    public let runsOn: ContentReportRunsOn
    public let modelID: String
    public let modelRevision: String?
    /// Text the person typed for this result, offered only behind the "include text" checkbox.
    public let typedText: String?
    public init(id: String, task: ContentReportTask, runsOn: ContentReportRunsOn, modelID: String?, modelRevision: String? = nil, typedText: String? = nil) {
        self.id = id; self.task = task; self.runsOn = runsOn
        self.modelID = Self.sanitizeModelID(modelID)
        self.modelRevision = runsOn == .phone ? Self.sanitizeRevision(modelRevision) : nil
        self.typedText = typedText?.nilIfBlank
    }
    /// A result made on the phone: the catalogue model's `repo` and `revision`.
    public static func phone(_ task: ContentReportTask, id: String, repo: String?, revision: String?, typedText: String? = nil) -> Self {
        Self(id: task.rawValue + ":phone:" + id, task: task, runsOn: .phone, modelID: repo, modelRevision: revision, typedText: typedText)
    }
    /// A result made on the paired computer: the engine id the host reported, else `unknown`.
    public static func computer(_ task: ContentReportTask, id: String, engine: String?, typedText: String? = nil) -> Self {
        Self(id: task.rawValue + ":computer:" + id, task: task, runsOn: .computer, modelID: engine, typedText: typedText)
    }
    public var hasTypedText: Bool { typedText != nil }
    /// Characters outside `0-9A-Za-z._/:-` removed, at most 128; `unknown` when nothing is left.
    public static func sanitizeModelID(_ raw: String?) -> String {
        let kept = String(String.UnicodeScalarView((raw ?? "").unicodeScalars.filter { allowed($0, extra: "._/:-") }).prefix(128))
        return kept.isEmpty ? "unknown" : kept
    }
    /// Server pattern `^[0-9A-Za-z._-]{1,64}$`; omitted when nothing is left.
    public static func sanitizeRevision(_ raw: String?) -> String? {
        let kept = String(String.UnicodeScalarView((raw ?? "").unicodeScalars.filter { allowed($0, extra: "._-") }).prefix(64))
        return kept.isEmpty ? nil : kept
    }
    static func allowed(_ scalar: Unicode.Scalar, extra: String) -> Bool {
        scalar.isASCII && (CharacterSet.alphanumerics.contains(scalar) || extra.unicodeScalars.contains(scalar))
    }
}

/// What the person has entered so far; kept in memory per result until it is sent.
public struct ContentReportDraft: Equatable, Sendable {
    public static let noteLimit = 1000
    public static let promptLimit = 5000
    public var reason: ContentReportReason?
    public var note: String { didSet { let capped = note.prefix(utf16Limit: Self.noteLimit); if capped != note { note = capped } } }
    public var includeText: Bool
    /// Made when the sheet first opens and reused on Retry, so a retried report is stored once.
    public internal(set) var idempotencyKey: String
    public init(reason: ContentReportReason? = nil, note: String = "", includeText: Bool = false, idempotencyKey: String = IdempotencyKey.make()) {
        self.reason = reason; self.note = note.prefix(utf16Limit: Self.noteLimit); self.includeText = includeText; self.idempotencyKey = idempotencyKey
    }
    public var canSend: Bool { reason != nil }
}

/// The exact request body. Optional fields are omitted when empty (synthesized
/// `Encodable` skips nil optionals), so no other key can appear.
public struct ContentReportBody: Encodable, Equatable, Sendable {
    public let reason: String
    public let note: String?
    public let prompt_text: String?
    public let platform: String
    public let app_version: String
    public let task: String
    public let model_id: String
    public let model_revision: String?
    public let runs_on: String
    public init?(subject: ContentReportSubject, draft: ContentReportDraft, appVersion: String, platform: String = "ios") {
        guard let reason = draft.reason else { return nil }
        self.reason = reason.rawValue
        note = draft.note.trimmingCharacters(in: .whitespacesAndNewlines).prefix(utf16Limit: ContentReportDraft.noteLimit).nilIfBlank
        prompt_text = draft.includeText ? subject.typedText?.prefix(utf16Limit: ContentReportDraft.promptLimit).nilIfBlank : nil
        self.platform = platform
        app_version = appVersion
        task = subject.task.rawValue
        model_id = subject.modelID
        model_revision = subject.runsOn == .phone ? subject.modelRevision : nil
        runs_on = subject.runsOn.rawValue
    }
}

public enum IdempotencyKey {
    static let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
    /// 32 characters of `A-Za-z0-9` from the system CSPRNG.
    public static func make() -> String {
        var rng = SystemRandomNumberGenerator()
        return String((0..<32).map { _ in alphabet.randomElement(using: &rng)! })
    }
    public static func isValid(_ key: String) -> Bool {
        key.count == 32 && key.unicodeScalars.allSatisfy { $0.isASCII && CharacterSet.alphanumerics.contains($0) }
    }
}

public enum ContentReportOutcome: Equatable, Sendable {
    case sent, rateLimited, invalid, failed
    public var messageKey: String {
        switch self {
        case .sent: "mobile.report.sent"
        case .rateLimited: "mobile.report.rate_limited"
        case .invalid: "mobile.report.invalid"
        case .failed: "mobile.report.failed"
        }
    }
}

/// Drafts and send state per result. Nothing is sent until `send`.
@MainActor @Observable public final class ContentReports {
    public enum Phase: Equatable, Sendable { case editing, sending, failed, rateLimited, invalid }
    private var drafts: [String: ContentReportDraft] = [:]
    private var phases: [String: Phase] = [:]
    @ObservationIgnored private let client: ContentReportClient
    @ObservationIgnored private let appVersion: String
    public init(client: ContentReportClient, appVersion: String) { self.client = client; self.appVersion = appVersion }
    /// Returns the kept draft, or starts one (with a new idempotency key) when the sheet first opens.
    @discardableResult public func open(_ subject: ContentReportSubject) -> ContentReportDraft {
        if let draft = drafts[subject.id] { return draft }
        let draft = ContentReportDraft(idempotencyKey: client.makeKey())
        drafts[subject.id] = draft
        return draft
    }
    public func draft(for subject: ContentReportSubject) -> ContentReportDraft? { drafts[subject.id] }
    /// Applies the person's edits; the idempotency key stays the one this store holds.
    public func update(_ draft: ContentReportDraft, for subject: ContentReportSubject) {
        guard phase(for: subject) != .sending else { return }
        var next = draft
        next.includeText = subject.hasTypedText && draft.includeText
        if let current = drafts[subject.id] { next.idempotencyKey = current.idempotencyKey }
        drafts[subject.id] = next
    }
    public func phase(for subject: ContentReportSubject) -> Phase { phases[subject.id] ?? .editing }
    public func send(_ subject: ContentReportSubject) async -> ContentReportOutcome? {
        guard phase(for: subject) != .sending, let draft = drafts[subject.id],
              let body = ContentReportBody(subject: subject, draft: draft, appVersion: appVersion) else { return nil }
        phases[subject.id] = .sending
        let (outcome, key) = await client.submit(body, key: draft.idempotencyKey)
        if outcome == .sent { drafts[subject.id] = nil; phases[subject.id] = nil; return .sent }
        drafts[subject.id]?.idempotencyKey = key
        switch outcome {
        case .rateLimited: phases[subject.id] = .rateLimited
        case .invalid: phases[subject.id] = .invalid
        default: phases[subject.id] = .failed
        }
        return outcome
    }
}

extension String {
    /// The longest whole-character prefix of at most `limit` UTF-16 code units, the
    /// strictest of the length measures the server and the Android app use.
    func prefix(utf16Limit limit: Int) -> String {
        guard utf16.count > limit else { return self }
        var out = "", used = 0
        for character in self {
            let size = character.utf16.count
            if used + size > limit { break }
            out.append(character); used += size
        }
        return out
    }
}
