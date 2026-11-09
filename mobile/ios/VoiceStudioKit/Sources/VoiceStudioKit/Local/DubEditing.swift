import Foundation

public enum PhoneDubEditing {
    public static func split(_ segments: [PhoneDubSegment],index: Int,at: Double) throws -> [PhoneDubSegment] {
        guard segments.indices.contains(index) else { throw ClientError("mobile.editing.split_invalid") }
        let cue = segments[index], speech = cue.speech
        guard at.isFinite, at-speech.start >= 0.05, speech.end-at >= 0.05 else { throw ClientError("mobile.editing.split_invalid") }
        let fraction = (at-speech.start)/(speech.end-speech.start), source = try splitText(speech.text,fraction: fraction)
        let translated = try cue.translations.mapValues { try splitText($0,fraction: fraction) }
        var a = cue, b = cue
        a.speech = SpeechSegment(start: speech.start,end: at,text: source.0)
        b.speech = SpeechSegment(start: at,end: speech.end,text: source.1); b.id = UUID().uuidString
        a.translations = translated.mapValues(\.0); b.translations = translated.mapValues(\.1)
        if cue.referenceFile == nil { a.referenceText = nil; b.referenceText = nil; a.referenceNeedsTranscription = true; b.referenceNeedsTranscription = true }
        var result = segments; result[index] = a; result.insert(b,at: index+1); return result
    }
    public static func merge(_ segments: [PhoneDubSegment],index: Int) throws -> [PhoneDubSegment] {
        guard index >= 0, index+1 < segments.count else { throw ClientError("mobile.editing.merge_invalid") }
        var a = segments[index]; let b = segments[index+1]
        guard a.speaker == b.speaker else { throw ClientError("mobile.dub_advanced.merge_speakers") }
        guard (a.gain ?? 1) == (b.gain ?? 1), (a.muted == true) == (b.muted == true) else { throw ClientError("mobile.processing.merge_levels") }
        guard b.speech.start >= a.speech.start, b.speech.end > a.speech.end, b.speech.end-a.speech.start <= 30,
              a.referenceFile == b.referenceFile, a.referenceFile == nil || a.referenceText == b.referenceText else { throw ClientError("mobile.editing.merge_invalid") }
        a.speech = SpeechSegment(start: a.speech.start,end: b.speech.end,text: a.speech.text+"\n"+b.speech.text)
        a.translations = [:] // Populate only languages translated in both cues.
        for key in Set(segments[index].translations.keys).intersection(b.translations.keys) { a.translations[key] = segments[index].translations[key]!+"\n"+b.translations[key]! }
        if a.referenceFile == nil { a.referenceText = nil; a.referenceNeedsTranscription = true }
        var result = segments; result[index] = a; result.remove(at: index+1); return result
    }
    public static func importTranslation(_ segments: [PhoneDubSegment],cues: [PhoneDubSubtitleCue],target: String,duration: Double) throws -> [PhoneDubSegment] {
        guard !cues.isEmpty, cues.allSatisfy({ $0.end <= duration }) else { throw ClientError("mobile.editing.subtitle_invalid") }
        return try cues.map { cue in
            let overlap = segments.filter { $0.speech.start < cue.end && $0.speech.end > cue.start }
            guard let first = overlap.first else { throw ClientError("mobile.editing.subtitle_invalid") }
            if overlap.count == 1, first.speech.start == cue.start, first.speech.end == cue.end {
                var exact = first; exact.translations[target] = cue.text; return exact
            }
            guard overlap.allSatisfy({ $0.speaker == first.speaker }) else { throw ClientError("mobile.dub_advanced.merge_speakers") }
            guard overlap.allSatisfy({ ($0.gain ?? 1) == (first.gain ?? 1) && ($0.muted == true) == (first.muted == true) }) else { throw ClientError("mobile.processing.merge_levels") }
            guard overlap.allSatisfy({ $0.referenceFile == first.referenceFile && (first.referenceFile == nil || $0.referenceText == first.referenceText) }) else { throw ClientError("mobile.editing.merge_invalid") }
            return PhoneDubSegment(speech: SpeechSegment(start: cue.start,end: cue.end,text: overlap.map(\.speech.text).joined(separator: "\n")),referenceText: first.referenceFile == nil ? nil : first.referenceText,translations: [target:cue.text],referenceFile: first.referenceFile,referenceNeedsTranscription: first.referenceFile == nil,gain: first.gain,muted: first.muted,speaker: first.speaker,referenceOrigin: first.referenceOrigin,voiceID: first.voiceID)
        }
    }
    private static func splitText(_ text: String,fraction: Double) throws -> (String,String) {
        let letters = Array(text)
        guard letters.count >= 2 else { throw ClientError("mobile.editing.split_invalid") }
        let desired = max(1,min(letters.count-1,Int(Double(letters.count)*fraction)))
        let boundaries = (1..<letters.count).filter { letters[$0-1].isWhitespace || letters[$0].isWhitespace }
        let index = boundaries.min(by: { abs($0-desired) < abs($1-desired) }) ?? desired
        let a = String(letters[..<index]).trimmingCharacters(in: .whitespacesAndNewlines), b = String(letters[index...]).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !a.isEmpty, !b.isEmpty else { throw ClientError("mobile.editing.split_invalid") }; return (a,b)
    }
}
public struct PhoneDubSubtitleCue: Sendable, Equatable {
    public let start: Double
    public let end: Double
    public let text: String
    public init(start: Double,end: Double,text: String) { self.start = start; self.end = end; self.text = text }
}
public enum PhoneDubSubtitles {
    public static func parse(_ text: String) throws -> [PhoneDubSubtitleCue] {
        guard text.utf8.count <= 4_000_000 else { throw ClientError("mobile.editing.subtitle_invalid") }
        let normalized = text.replacingOccurrences(of: "\u{FEFF}",with: "").replacingOccurrences(of: "\r\n",with: "\n").replacingOccurrences(of: "\r",with: "\n")
        let blocks = normalized.replacingOccurrences(of: "\n[ \t]+\n",with: "\n\n",options: .regularExpression).components(separatedBy: "\n\n")
        var cues: [PhoneDubSubtitleCue] = []
        for block in blocks {
            let lines = block.trimmingCharacters(in: .whitespacesAndNewlines).components(separatedBy: "\n")
            guard let first = lines.first, !first.isEmpty else { continue }
            if first.hasPrefix("WEBVTT") || first.hasPrefix("NOTE") || first == "STYLE" || first == "REGION" { continue }
            let index = first.contains("-->") ? 0 : 1
            guard lines.count > index+1 else { throw ClientError("mobile.editing.subtitle_invalid") }
            let timing = lines[index].components(separatedBy: "-->")
            guard timing.count == 2, let endValue = timing[1].split(whereSeparator: \.isWhitespace).first else { throw ClientError("mobile.editing.subtitle_invalid") }
            let start = try time(timing[0].trimmingCharacters(in: .whitespaces)), end = try time(String(endValue))
            let words = decodeEntities(lines.dropFirst(index+1).joined(separator: "\n").replacingOccurrences(of: "<[^>]*>",with: "",options: .regularExpression)).trimmingCharacters(in: .whitespacesAndNewlines)
            guard start >= 0, end > start, end <= 14400, end-start <= 30, !words.isEmpty, words.utf16.count <= 4000 else { throw ClientError("mobile.editing.subtitle_invalid") }
            cues.append(PhoneDubSubtitleCue(start: start,end: end,text: words))
        }
        guard !cues.isEmpty, cues.count <= 10000, zip(cues,cues.dropFirst()).allSatisfy({ pair in pair.0.start <= pair.1.start }) else { throw ClientError("mobile.editing.subtitle_invalid") }; return cues
    }
    public static func export(_ segments: [PhoneDubSegment],target: String,vtt: Bool) throws -> String {
        guard !segments.isEmpty else { throw ClientError("mobile.editing.subtitle_invalid") }
        return (vtt ? "WEBVTT\n\n" : "")+(try segments.enumerated().map { index,segment in
            guard let text = segment.translations[target], !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ClientError("mobile.editing.subtitle_missing") }
            let escaped = text.replacingOccurrences(of: "&",with: "&amp;").replacingOccurrences(of: "<",with: "&lt;").replacingOccurrences(of: ">",with: "&gt;").replacingOccurrences(of: "\r\n",with: "\n").replacingOccurrences(of: "\r",with: "\n").replacingOccurrences(of: "\n[ \t]*\n",with: "\n",options: .regularExpression)
            return "\(index+1)\n\(try stamp(segment.speech.start,vtt: vtt)) --> \(try stamp(segment.speech.end,vtt: vtt))\n\(escaped)"
        }).joined(separator: "\n\n")+"\n"
    }
    private static func decodeEntities(_ value: String) -> String {
        let pattern = try! NSRegularExpression(pattern: "&(?:amp|lt|gt|quot|apos|#39|nbsp);")
        let entities = ["&amp;":"&","&lt;":"<","&gt;":">","&quot;":"\"","&apos;":"'","&#39;":"'","&nbsp;":" "]
        var result = value
        for match in pattern.matches(in: value,range: NSRange(value.startIndex...,in: value)).reversed() {
            guard let range = Range(match.range,in: result), let replacement = entities[String(result[range])] else { continue }
            result.replaceSubrange(range,with: replacement)
        }
        return result
    }
    private static func time(_ value: String) throws -> Double {
        let parts = value.replacingOccurrences(of: ",",with: ".").components(separatedBy: ":")
        guard (2...3).contains(parts.count), let seconds = Double(parts.last!), let minutes = Int(parts[parts.count-2]), let hours = parts.count == 3 ? Int(parts[0]) : 0,
              seconds.isFinite, seconds >= 0, seconds < 60, (0...59).contains(minutes), hours >= 0 else { throw ClientError("mobile.editing.subtitle_invalid") }
        return Double(hours*3600+minutes*60)+seconds
    }
    private static func stamp(_ value: Double,vtt: Bool) throws -> String {
        guard value.isFinite, value >= 0, value <= 14400 else { throw ClientError("mobile.editing.subtitle_invalid") }
        let ms = Int((value*1000).rounded())
        return String(format: "%02d:%02d:%02d%@%03d",ms/3600000,ms/60000%60,ms/1000%60,vtt ? "." : ",",ms%1000)
    }
}
