import Foundation
import Testing
@testable import VoiceStudioKit

@Test func dubMetadataInterpolatesTheSelectedCatalogWithoutAddingADate() throws {
    let l10n = try L10n(locale: "fr", catalogs: [
        "fr": data(#"{"mobile":{"dub":{"history_meta":"{{duration}} / {{language}}"},"android":{"stage_done":"Done"}}}"#)
    ])
    let item = try JSONDecoder().decode(DubHistoryItem.self, from: data(
        #"{"id":"job","language":"Spanish","duration":125,"created_at":1700000000}"#))
    #expect(item.metadata(l10n: l10n) == "2:05 / Spanish")
    // Android uses language, not language_code, with its completed-stage fallback.
    let legacy = try JSONDecoder().decode(DubHistoryItem.self, from: data(
        #"{"id":"old","language":"  ","language_code":"es","duration":null}"#))
    #expect(legacy.metadata(l10n: l10n) == "0:00 / Done")
}

@Test(arguments: [0.0, -5.0, 59.9, 60.0, 125.9])
func dubMetadataUsesTheSameMinuteSecondDurationAsAndroid(_ duration: Double) throws {
    let l10n = try L10n(locale: "en", catalogs: catalogs())
    let item = try JSONDecoder().decode(DubHistoryItem.self, from: data(
        "{\"id\":\"job\",\"language\":\"Spanish\",\"duration\":\(duration)}"))
    let expected = [0.0: "0:00", -5.0: "0:00", 59.9: "0:59", 60.0: "1:00", 125.9: "2:05"][duration]!
    #expect(item.metadata(l10n: l10n) == "Spanish dub, \(expected) long")
}
