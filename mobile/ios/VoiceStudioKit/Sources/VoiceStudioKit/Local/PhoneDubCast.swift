import Foundation

/// Manual labels are intentionally independent of acoustic speaker detection.
public enum PhoneDubCast {
    public static func valid(_ segment: PhoneDubSegment) -> Bool {
        let speaker = segment.speaker ?? ""
        return speaker.utf16.count <= 80 && !speaker.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) && [nil,"cast","segment"].contains(segment.referenceOrigin) && (segment.voiceID?.utf8.count ?? 0) <= 100
    }
    public static func isLineOverride(_ segment: PhoneDubSegment) -> Bool {
        segment.referenceOrigin == "segment" || (segment.referenceOrigin == nil && segment.referenceFile != nil)
    }
    public static func speakers(_ segments: [PhoneDubSegment]) -> [String] {
        Array(Set(segments.compactMap { $0.speaker?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty })).sorted()
    }
    public static func assigning(_ segments: [PhoneDubSegment],speaker: String,voiceID: String,replaceOverrides: Bool = false) -> [PhoneDubSegment] {
        segments.map { segment in
            guard segment.speaker?.trimmingCharacters(in: .whitespacesAndNewlines) == speaker, replaceOverrides || !isLineOverride(segment) else { return segment }
            var changed = segment; changed.speaker = speaker; changed.referenceOrigin = "cast"; changed.voiceID = voiceID; return changed
        }
    }
}
