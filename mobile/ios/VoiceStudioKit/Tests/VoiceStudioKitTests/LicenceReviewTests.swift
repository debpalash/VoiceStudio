import Foundation
import Testing
@testable import VoiceStudioKit

@Test func licenceReviewSummarisesDeclaredTermsAndKeepsOnlyHttpsLinks() {
    let review = LicenceReview(terms: [
        "commercial_inference": .string("restricted"), "commercial_outputs": .string("unknown"),
        "notes": .string("  GPL phonemizer data.  "),
        "evidence_url": .string("https://huggingface.co/x/blob/main/LICENSE"), "source_url": .string("http://insecure.example/model"),
    ])
    #expect(review.rights.map(\.key) == ["commercial", "outputs", "redistribution", "voice"])
    #expect(review.rights.map(\.status) == ["restricted", "unknown", "unknown", "unknown"])
    #expect(review.notes == "GPL phonemizer data.")
    #expect(review.evidenceURL?.host == "huggingface.co")
    #expect(review.sourceURL == nil)
}
@Test func licenceReviewTreatsMissingOrUnrecognisedTermsAsNotStated() {
    let review = LicenceReview(terms: ["commercial_inference": .string("allowed-ish"), "notes": .string("   "), "evidence_url": .string("https://user@evil.example/x")])
    #expect(review.rights.allSatisfy { $0.status == "unknown" })
    #expect(review.notes == nil)
    #expect(review.evidenceURL == nil)
}

@Test func termLinesCoverEveryDeclaredKeyReadably() {
    let terms: [String: JSONValue] = ["evidence_revision": .string("abc"), "variants": .array([]), "notes": .string("n"),
                                      "count": .number(3), "flag": .bool(true), "nested": .object(["a": .string("x")])]
    let lines = TermLine.lines(terms)
    #expect(Set(lines.map(\.key)) == Set(terms.keys))
    #expect(lines.first { $0.key == "evidence_revision" }?.label == "Evidence revision")
    #expect(lines.first { $0.key == "variants" }?.value == "–")
    #expect(lines.first { $0.key == "count" }?.value == "3")
    #expect(lines.first { $0.key == "nested" }?.value == "a: x")
}
