import Foundation
import Testing
@testable import VoiceStudioKit

@Test func themePreferencesSurviveSchemaChanges() throws {
    let saved = Data(#"{"mode":"futureMode","darkPalette":"signal","removed":1}"#.utf8)
    let prefs = try JSONDecoder().decode(ThemePreferences.self, from: saved)
    #expect(prefs.mode == .dark)
    #expect(prefs.darkPalette == "signal")
    #expect(prefs.lightPalette == "default")
}

@Test func savedHostsSurviveMissingFields() throws {
    let saved = Data(#"[{"url":"http://127.0.0.1:3900/","future":true}]"#.utf8)
    let hosts = try JSONDecoder().decode([SavedHost].self, from: saved)
    #expect(hosts.single?.label == "127.0.0.1")
}

private extension Array { var single: Element? { count == 1 ? first : nil } }
