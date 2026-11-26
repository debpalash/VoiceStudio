import Foundation
import Testing
@testable import VoiceStudioKit

private func catalog(_ json: String) -> Data { Data(json.utf8) }

private func l10n(_ locale: String, _ catalogs: [String: String]) throws -> L10n {
    try L10n(locale: locale, catalogs: catalogs.mapValues(catalog))
}

/// The iOS reader must pick the same CLDR cardinal category Android does, because
/// both read the same generated catalogs. It used to answer `one` or `other` only,
/// so a Polish or Russian entry for 2–4 items was never selected.
@Test func pluralCategoriesMatchAndroidForEverySupportedShape() throws {
    let counts = catalog(
        #"{"count_zero":"zero","count_one":"one","count_two":"two","count_few":"few","count_many":"many","count_other":"other"}"#
    )
    let plural = try L10n(locale: "ar", catalogs: ["en": counts, "ar": counts])
    #expect(plural.t("count", count: 0) == "zero")
    #expect(plural.t("count", count: 1) == "one")
    #expect(plural.t("count", count: 2) == "two")
    #expect(plural.t("count", count: 3) == "few")
    #expect(plural.t("count", count: 10) == "few")
    #expect(plural.t("count", count: 11) == "many")
    #expect(plural.t("count", count: 99) == "many")
    #expect(plural.t("count", count: 100) == "other")

    let slavic = try L10n(locale: "ru-RU", catalogs: ["en": counts, "ru": counts])
    #expect(slavic.t("count", count: 1) == "one")
    #expect(slavic.t("count", count: 2) == "few")
    #expect(slavic.t("count", count: 11) == "many")
    #expect(slavic.t("count", count: 21) == "one")
    #expect(slavic.t("count", count: 22) == "few")

    let polish = try L10n(locale: "pl-PL", catalogs: ["en": counts, "pl": counts])
    #expect(polish.t("count", count: 1) == "one")
    #expect(polish.t("count", count: 2) == "few")
    #expect(polish.t("count", count: 5) == "many")
    #expect(polish.t("count", count: 22) == "few")
}

@Test func pluralPrefersTheCategoryThenOtherThenThePlainKey() throws {
    let onlyOther = try l10n("ru-RU", ["en": #"{"count_other":"other only"}"#, "ru": #"{"count_other":"other only"}"#])
    #expect(onlyOther.t("count", count: 2) == "other only")

    let unsuffixed = try l10n("pl-PL", ["en": #"{"count":"plain"}"#, "pl": #"{"count":"plain"}"#])
    #expect(unsuffixed.t("count", count: 5) == "plain")

    let otherLanguages = try l10n("ja-JP", ["en": #"{"count_one":"one","count_other":"other"}"#, "ja": #"{"count_one":"one","count_other":"other"}"#])
    #expect(otherLanguages.t("count", count: 1) == "other")
}

@Test func hindiAndFrenchZeroReadAsSingular() throws {
    let counts = catalog(#"{"count_one":"one","count_other":"other"}"#)
    #expect(try L10n(locale: "hi-IN", catalogs: ["en": counts, "hi": counts]).t("count", count: 0) == "one")
    #expect(try L10n(locale: "fr-FR", catalogs: ["en": counts, "fr": counts]).t("count", count: 0) == "one")
    #expect(try L10n(locale: "fr-FR", catalogs: ["en": counts, "fr": counts]).t("count", count: 2) == "other")
}
