import AppIntents

struct OpenSpeechWorkspace: AppIntent {
    static let title: LocalizedStringResource = "mobile.integration.shortcut_speak"
    static let openAppWhenRun = true
    @Parameter(title: "mobile.integration.text") var text: String?
    @MainActor func perform() async throws -> some IntentResult {
        if let text,!text.isEmpty { PhoneInbox.shared.incoming = PhoneIncoming(text: String(text.prefix(20000))) }
        else { PhoneInbox.shared.route = "speak" }
        return .result()
    }
}
struct OpenDictationWorkspace: AppIntent {
    static let title: LocalizedStringResource = "mobile.integration.shortcut_dictate"
    static let openAppWhenRun = true
    @MainActor func perform() async throws -> some IntentResult {
        PhoneInbox.shared.route = "dictate"
        return .result()
    }
}
