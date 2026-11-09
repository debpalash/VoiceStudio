import Foundation

/// Mirrors live-dictation.ts: utterance commits continue capture; a whole-session
/// summary replaces the live text instead of appending duplicate phrases.
public struct DictationTranscript: Sendable {
    public private(set) var text = ""
    public private(set) var finished = false
    private var committed: [String] = []
    public init() {}
    public mutating func receive(_ message: DictationMessage) {
        guard !finished else { return }
        if message.type == "partial" {
            text = (committed + [message.text ?? ""]).filter { !$0.isEmpty }.joined(separator: " ")
        } else if message.type == "final" {
            let value = message.refined_text.flatMap { $0.isEmpty ? nil : $0 } ?? message.text ?? ""
            if message.final_kind == "utterance" {
                guard !(message.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                committed.append(value); text = committed.joined(separator: " ")
            } else if message.isSessionFinal {
                text = value.isEmpty ? committed.joined(separator: " ") : value
                finished = true
            }
        }
    }
}
