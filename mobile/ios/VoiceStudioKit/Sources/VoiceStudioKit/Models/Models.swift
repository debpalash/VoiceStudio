import Foundation

public struct Health: Codable, Sendable { public let status: String; public let device: String; public let version: String }
public struct Session: Codable, Sendable { public let token: String; public let expires_at: Double; public let expires_in: Int }
public struct Ticket: Codable, Sendable { public let ticket: String; public let expires_in: Int? }
public struct Profile: Codable, Identifiable, Sendable { public let id: String; public let name: String; public let language: String?; public let kind: String?
    public let ref_audio_path: String?
    public let locked_audio_path: String?
    public var hasReferenceAudio: Bool { [ref_audio_path, locked_audio_path].contains { !($0 ?? "").isEmpty } || kind == "clone" }
}
public struct Engine: Codable, Identifiable, Sendable {
    public let id: String
    public let display_name: String
    public let available: Bool
    public let supported_language_names: [String]?
    public let supported_languages: [String]?
    public let supports_cloning: Bool?
    public let reason: String?
    public let hint: String?
    public func selectable(for profile: Profile?) -> Bool { available && (profile?.hasReferenceAudio != true || supports_cloning == true) }
    public func explanation(for profile: Profile?, l10n: L10n) -> String? {
        if !available { return [reason, hint].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ").nilIfBlank ?? l10n.t("mobile.speak.engine_unavailable") }
        if profile?.hasReferenceAudio == true && supports_cloning != true { return l10n.t("mobile.speak.engine_no_cloning") }
        return hint?.nilIfBlank
    }
    public var languages: [String] { supported_language_names ?? supported_languages ?? [] }
}
public struct EngineFamily: Codable, Sendable { public let active: String; public let backends: [Engine] }
public struct EngineInventory: Codable, Sendable { public let tts: EngineFamily }
public struct Generation: Identifiable, Sendable {
    public let id: String
    public let audio: Data
    public init(id: String, audio: Data) { self.id = id; self.audio = audio }
}
public struct HistoryItem: Decodable, Identifiable, Sendable {
    public let id: String
    public let text: String
    public let audio_path: String?
    public let starred: Bool
    enum CodingKeys: CodingKey { case id, text, audio_path, starred }
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id); text = try c.decode(String.self, forKey: .text)
        audio_path = try c.decodeIfPresent(String.self, forKey: .audio_path)
        starred = (try? c.decode(Bool.self, forKey: .starred)) ?? ((try? c.decode(Int.self, forKey: .starred)) == 1)
    }
}
public struct DubUpload: Codable, Sendable {
    public let job_id: String; public let task_id: String; public let filename: String
    public init(job_id: String, task_id: String, filename: String) { self.job_id = job_id; self.task_id = task_id; self.filename = filename }
}
public struct TaskReference: Codable, Sendable { public let task_id: String }
public struct Job: Codable, Sendable {
    public let id: String; public let status: String; public let error: String?
    public var terminal: Bool { ["done", "failed", "cancelled"].contains(status) }
}
public struct JobEvent: Codable, Sendable { public let seq: Int; public let payload: String }
public struct JobEvents: Codable, Sendable { public let job: Job; public let events: [JobEvent] }
public struct Segment: Codable, Sendable {
    public let id: String
    public var start: Double
    public var end: Double
    public var text: String
    public var profile_id: String?
    public var target_lang: String?
    public var cue_source_id: String?
    public var speaker_id: String?
    public var text_original: String?
    public var gain: Double?
    public var speed: Double?
    public var direction: String?
    public var effect_preset: String?
    public var instruct: String?
    enum CodingKeys: String, CodingKey { case id, start, end, text, profile_id, target_lang, cue_source_id, speaker_id, text_original, gain, speed, direction, effect_preset, instruct }
    public init(id: String, start: Double, end: Double, text: String, profile: String? = nil, language: String? = nil) {
        self.id = id; self.start = start; self.end = end; self.text = text; profile_id = profile; target_lang = language; cue_source_id = id
    }
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let string = try? c.decode(String.self, forKey: .id) { id = string }
        else { id = String(try c.decode(Int.self, forKey: .id)) }
        start = try c.decodeIfPresent(Double.self, forKey: .start) ?? 0
        end = try c.decodeIfPresent(Double.self, forKey: .end) ?? start
        text = try c.decode(String.self, forKey: .text)
        profile_id = try c.decodeIfPresent(String.self, forKey: .profile_id)
        target_lang = try c.decodeIfPresent(String.self, forKey: .target_lang)
        cue_source_id = try c.decodeIfPresent(String.self, forKey: .cue_source_id)
        speaker_id = try c.decodeIfPresent(String.self, forKey: .speaker_id)
        text_original = try c.decodeIfPresent(String.self, forKey: .text_original)
        gain = try c.decodeIfPresent(Double.self, forKey: .gain)
        speed = try c.decodeIfPresent(Double.self, forKey: .speed)
        direction = try c.decodeIfPresent(String.self, forKey: .direction)
        effect_preset = try c.decodeIfPresent(String.self, forKey: .effect_preset)
        instruct = try c.decodeIfPresent(String.self, forKey: .instruct)
    }
}
public struct Transcript: Codable, Sendable { public let segments: [Segment]; public let source_lang: String? }
public struct TranslationLine: Codable, Sendable { public let id: String; public let text: String; public let error: String? }
public struct Translation: Codable, Sendable { public let translated: [TranslationLine] }
public struct DubHistoryItem: Codable, Identifiable, Sendable {
    public let id: String
    public let filename: String?
    public let job_id: String?
    public let language: String?
    public let language_code: String?
    public let duration: Double?
    public let created_at: Double?
    public var title: String { filename?.nilIfBlank ?? id }
    public func metadata(l10n: L10n) -> String {
        l10n.t("mobile.dub.history_meta", [
            "language": language?.nilIfBlank ?? l10n.t("mobile.android.stage_done"),
            "duration": PlaybackTime.format(duration ?? 0)
        ])
    }
}
public struct Readiness: Decodable, Sendable {
    public let ready: Bool
    public let missing: MissingModel?
    public struct MissingModel: Decodable, Sendable {
        public let message: String?
        public let detail: String?
        public let error: String?
        public let missing_repo_id: String?
        public let recommended: Recommendation?
    }
    public struct Recommendation: Decodable, Sendable {
        public let label: String?
        public let size_gb: Double?
    }
    public func reason(l10n: L10n) -> String {
        guard let missing else { return ready ? "" : l10n.t("mobile.errors.asr_missing") }
        if missing.error == "asr_model_missing" {
            let name = missing.recommended?.label?.nilIfBlank ?? missing.missing_repo_id?.nilIfBlank
            guard let name else { return l10n.t("mobile.errors.asr_missing") }
            if let size = missing.recommended?.size_gb, size.isFinite, size >= 0 {
                return l10n.t("mobile.dictate.install_model_size", ["model": name, "size": size.formatted(.number.locale(Locale(identifier: l10n.locale)).precision(.fractionLength(0...2)))])
            }
            return l10n.t("mobile.dictate.install_model", ["model": name])
        }
        return missing.message?.nilIfBlank ?? missing.detail?.nilIfBlank ?? l10n.t("mobile.errors.asr_missing")
    }
}
public struct DictationMessage: Decodable, Sendable {
    public let final_kind: String?
    public let refined_text: String?
    public var isSessionFinal: Bool { type == "final" && (final_kind == nil || final_kind == "summary") }
    public let type: String
    public let text: String?
    public let message: String?
    public let error: String?
}

public struct DubProgress: Sendable, Equatable {
    public let stage: String
    public let fraction: Double
    public static func parse(_ event: JobEvent) -> DubProgress? {
        let data = event.payload.split(separator: "\n").filter { $0.hasPrefix("data:") }.map { $0.dropFirst(5).trimmingCharacters(in: .whitespaces) }.joined(separator: "\n")
        guard let bytes = data.data(using: .utf8), let json = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { return nil }
        let stage = json["type"] as? String ?? "progress"
        let current = (json["current"] as? Double) ?? 0
        let total = (json["total"] as? Double) ?? 1
        let progress = (json["percent"] as? Double).map { $0 / 100 } ?? (json["progress"] as? Double) ?? (current / max(1, total))
        return .init(stage: stage, fraction: stage == "done" || stage == "ready" ? 1 : min(1, max(0, progress > 1 ? progress / 100 : progress)))
    }
}
