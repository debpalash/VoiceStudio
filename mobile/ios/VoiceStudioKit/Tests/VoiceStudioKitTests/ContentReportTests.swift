import Foundation
import Testing
@testable import VoiceStudioKit

/// Mock app API: answers each request with the next queued status (or throws for `nil`).
private actor ReportServer: HTTPTransport {
    private(set) var requests: [URLRequest] = []
    private var statuses: [Int?]
    init(_ statuses: [Int?]) { self.statuses = statuses }
    func send(_ request: URLRequest) throws -> HTTPReply {
        requests.append(request)
        let status = statuses.isEmpty ? 201 : statuses.removeFirst()
        guard let status else { throw URLError(.notConnectedToInternet) }
        return HTTPReply(data: data(status == 201 ? #"{"report_id":"AAAAAAAAAAAAAAAA","received_at":"2026-10-24T00:00:00Z"}"# : #"{"error":"x"}"#), status: status)
    }
}
private final class KeySequence: @unchecked Sendable {
    private let lock = NSLock()
    private var next = 0
    func make() -> String { lock.withLock { next += 1; return String(repeating: "k", count: 31) + String(next) } }
}
private let base = URL(string: "https://dev-appapi.voicestudio.sh")!
private func body(_ request: URLRequest) throws -> [String: String] {
    let raw = try #require(request.httpBody)
    return try #require(JSONSerialization.jsonObject(with: raw) as? [String: String])
}
private let speak = ContentReportSubject.phone(.speak, id: "take", repo: "k2-fsa/sherpa-onnx", revision: "abc123", typedText: "Hello there")

@Test func contentReportBodyIsExactAndOmitsEmptyFields() throws {
    var draft = ContentReportDraft(idempotencyKey: IdempotencyKey.make())
    #expect(ContentReportBody(subject: speak, draft: draft, appVersion: "0.5.7-beta.2") == nil, "no reason, nothing to send")
    draft.reason = .hateful
    draft.note = "   "
    let client = ContentReportClient(baseURL: base)
    let minimal = try client.request(#require(ContentReportBody(subject: speak, draft: draft, appVersion: "0.5.7-beta.2")), key: draft.idempotencyKey)
    #expect(try body(minimal) == ["reason": "hateful", "platform": "ios", "app_version": "0.5.7-beta.2", "task": "speak",
                                  "model_id": "k2-fsa/sherpa-onnx", "model_revision": "abc123", "runs_on": "phone"])

    draft.note = "  rude words \n"
    draft.includeText = true
    let full = try client.request(#require(ContentReportBody(subject: speak, draft: draft, appVersion: "0.5.7")), key: draft.idempotencyKey)
    #expect(try body(full) == ["reason": "hateful", "note": "rude words", "prompt_text": "Hello there", "platform": "ios", "app_version": "0.5.7",
                               "task": "speak", "model_id": "k2-fsa/sherpa-onnx", "model_revision": "abc123", "runs_on": "phone"])

    // Computer results never carry a revision; an unreported engine is `unknown`.
    let computer = ContentReportSubject.computer(.dub, id: "job", engine: nil)
    var dub = ContentReportDraft(reason: .other, includeText: true)
    dub.note = ""
    #expect(try body(client.request(#require(ContentReportBody(subject: computer, draft: dub, appVersion: "0.5.7")), key: dub.idempotencyKey))
            == ["reason": "other", "platform": "ios", "app_version": "0.5.7", "task": "dub", "model_id": "unknown", "runs_on": "computer"])
}

@Test func contentReportTextIsCappedAndOnlySentWhenTicked() throws {
    let long = String(repeating: "a", count: 6000)
    let subject = ContentReportSubject.computer(.speak, id: "1", engine: "omnivoice", typedText: long)
    var draft = ContentReportDraft(reason: .misleading)
    draft.note = String(repeating: "n", count: 1500)
    #expect(draft.note.count == ContentReportDraft.noteLimit)
    #expect(ContentReportBody(subject: subject, draft: draft, appVersion: "1")?.prompt_text == nil)
    draft.includeText = true
    #expect(ContentReportBody(subject: subject, draft: draft, appVersion: "1")?.prompt_text?.count == ContentReportDraft.promptLimit)
    // Never split a character across the limit.
    let emoji = String(repeating: "a", count: 4999) + "\u{1F600}"
    #expect(emoji.prefix(utf16Limit: 5000) == String(repeating: "a", count: 4999))
    // Without typed text the checkbox does not exist, and the store will not tick it.
    #expect(!ContentReportSubject.computer(.clone, id: "p", engine: nil, typedText: "  ").hasTypedText)
}

@Test func contentReportModelIDIsSanitized() {
    #expect(ContentReportSubject.sanitizeModelID("Omni Voice/v2:large?x=1&é") == "OmniVoice/v2:largex1")
    #expect(ContentReportSubject.sanitizeModelID(nil) == "unknown")
    #expect(ContentReportSubject.sanitizeModelID("  ") == "unknown")
    #expect(ContentReportSubject.sanitizeModelID(String(repeating: "m", count: 200)).count == 128)
    #expect(ContentReportSubject.sanitizeRevision("a/b c") == "abc")
    #expect(ContentReportSubject.sanitizeRevision("///") == nil)
    #expect(ContentReportSubject.computer(.speak, id: "1", engine: "index-tts").modelRevision == nil)
}

@Test func contentReportHeadersCarryKeyAndNoHostCredentials() async throws {
    let server = ReportServer([201])
    let reports = await ContentReports(client: ContentReportClient(baseURL: base, transport: server), appVersion: "0.5.7")
    let draft = await reports.open(speak)
    #expect(IdempotencyKey.isValid(draft.idempotencyKey))
    var picked = draft; picked.reason = .sexual
    await reports.update(picked, for: speak)
    #expect(await reports.send(speak) == .sent)
    let request = try #require(await server.requests.first)
    #expect(request.url?.absoluteString == "https://dev-appapi.voicestudio.sh/v1/content-reports")
    #expect(request.httpMethod == "POST")
    #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
    #expect(request.value(forHTTPHeaderField: "Idempotency-Key") == draft.idempotencyKey)
    #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
    #expect(request.value(forHTTPHeaderField: "X-OmniVoice-Pin") == nil)
    #expect(Set(request.allHTTPHeaderFields?.keys.map { $0.lowercased() } ?? []) == ["content-type", "accept", "idempotency-key"])
    // 201 discards the draft and its key.
    #expect(await reports.draft(for: speak) == nil)
    #expect(await reports.open(speak).idempotencyKey != draft.idempotencyKey)
}

@Test func idempotencyKeysAreRandomAlphanumeric() {
    let keys = (0..<200).map { _ in IdempotencyKey.make() }
    #expect(keys.allSatisfy(IdempotencyKey.isValid))
    #expect(Set(keys).count == keys.count)
    #expect(!IdempotencyKey.isValid("short"))
    #expect(!IdempotencyKey.isValid(String(repeating: "-", count: 32)))
}

@Test func contentReportRetryReusesKeyAndKeepsDraft() async throws {
    let server = ReportServer([nil, 503, 404, 201])
    let reports = await ContentReports(client: ContentReportClient(baseURL: base, transport: server), appVersion: "0.5.7")
    var draft = await reports.open(speak)
    draft.reason = .impersonation; draft.note = "kept"; draft.includeText = true
    await reports.update(draft, for: speak)
    #expect(await reports.send(speak) == .failed)   // no connection
    #expect(await reports.phase(for: speak) == .failed)
    // Closing and reopening the sheet keeps the reason, note and checkbox.
    #expect(await reports.open(speak) == draft)
    #expect(await reports.send(speak) == .failed)   // 503
    #expect(await reports.send(speak) == .failed)   // 404
    #expect(await reports.send(speak) == .sent)
    let keys = await server.requests.map { $0.value(forHTTPHeaderField: "Idempotency-Key") }
    #expect(keys.count == 4 && Set(keys) == [draft.idempotencyKey])
}

@Test func contentReportConflictMakesNewKeyAndResendsOnce() async throws {
    let sequence = KeySequence()
    let server = ReportServer([409, 201])
    let reports = await ContentReports(client: ContentReportClient(baseURL: base, transport: server, makeKey: { sequence.make() }), appVersion: "0.5.7")
    var draft = await reports.open(speak); draft.reason = .other
    await reports.update(draft, for: speak)
    #expect(await reports.send(speak) == .sent)
    let keys = await server.requests.compactMap { $0.value(forHTTPHeaderField: "Idempotency-Key") }
    #expect(keys == [draft.idempotencyKey, String(repeating: "k", count: 31) + "2"])

    // Two conflicts: only one automatic resend, then Retry uses a fresh key.
    let twice = ReportServer([409, 409, 201])
    let client = ContentReportClient(baseURL: base, transport: twice, makeKey: { sequence.make() })
    let body = try #require(ContentReportBody(subject: speak, draft: draft, appVersion: "1"))
    let (outcome, key) = await client.submit(body, key: "first")
    #expect(outcome == .failed)
    #expect(await twice.requests.count == 2)
    #expect(await client.submit(body, key: key).0 == .sent)
    let sent = await twice.requests.compactMap { $0.value(forHTTPHeaderField: "Idempotency-Key") }
    #expect(sent[0] == "first" && sent[1] != sent[0] && sent[2] == key && sent[2] != sent[1])
}

@Test func contentReportRateLimitAndInvalidKeepTheDraft() async throws {
    let server = ReportServer([429, 400])
    let reports = await ContentReports(client: ContentReportClient(baseURL: base, transport: server), appVersion: "0.5.7")
    var draft = await reports.open(speak); draft.reason = .sexual
    await reports.update(draft, for: speak)
    #expect(await reports.send(speak) == .rateLimited)
    #expect(await reports.phase(for: speak) == .rateLimited)
    #expect(await reports.draft(for: speak) == draft)
    #expect(await reports.send(speak) == .invalid)
    #expect(await reports.phase(for: speak) == .invalid)
    #expect(await server.requests.count == 2, "400 is never retried automatically")
    #expect(ContentReportOutcome.rateLimited.messageKey == "mobile.report.rate_limited")
    #expect(ContentReportOutcome.invalid.messageKey == "mobile.report.invalid")
    #expect(ContentReportOutcome.failed.messageKey == "mobile.report.failed")
}

@Test func contentReportDraftsArePerResult() async {
    let reports = await ContentReports(client: ContentReportClient(baseURL: base, transport: ReportServer([])), appVersion: "0.5.7")
    let other = ContentReportSubject.phone(.speak, id: "other", repo: "r", revision: nil)
    var draft = await reports.open(speak); draft.reason = .hateful
    await reports.update(draft, for: speak)
    #expect(await reports.open(other).reason == nil)
    #expect(await reports.open(other).idempotencyKey != draft.idempotencyKey)
    // Edits never replace the key the store holds.
    var forged = draft; forged.idempotencyKey = "x"
    await reports.update(forged, for: speak)
    #expect(await reports.draft(for: speak)?.idempotencyKey == draft.idempotencyKey)
}

@Test func contentReportBaseURLAndAppVersionPerBuild() {
    // Production unless the build carries an https override; debug builds get no special default.
    #expect(ContentReportClient.baseURL(info: nil).absoluteString == "https://appapi.voicestudio.sh")
    #expect(ContentReportClient.baseURL(info: ["VoiceStudioContentReportsURL": " "]).absoluteString == "https://appapi.voicestudio.sh")
    #expect(ContentReportClient.baseURL(info: ["VoiceStudioContentReportsURL": "http://dev-appapi.voicestudio.sh"]).absoluteString == "https://appapi.voicestudio.sh")
    #expect(ContentReportClient.baseURL(info: ["VoiceStudioContentReportsURL": "https://dev-appapi.voicestudio.sh"]).absoluteString == "https://dev-appapi.voicestudio.sh")
    #expect(ContentReportClient.appVersion(info: ["VoiceStudioAppVersion": "0.5.7-beta.2", "CFBundleShortVersionString": "0.5.7"]) == "0.5.7-beta.2")
    #expect(ContentReportClient.appVersion(info: ["CFBundleShortVersionString": "0.5.7"]) == "0.5.7")
    #expect(ContentReportClient.appVersion(info: nil) == "unknown")
    // About and licence receipts show the same full version as Android's versionName.
    #expect(AppVersion.full(info: ["VoiceStudioAppVersion": "0.5.7-beta.2", "CFBundleShortVersionString": "0.5.7"]) == "0.5.7-beta.2")
    #expect(AppVersion.full(info: ["VoiceStudioAppVersion": " ", "CFBundleShortVersionString": "0.5.7"]) == "0.5.7")
    #expect(AppVersion.full(info: nil) == nil)
    #expect(ContentReportClient.appVersion(info: ["VoiceStudioAppVersion": "1.0 (beta)"]) == "1.0beta")
}

@Test func phoneTakesAndVoicesRecordTheirModel() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneGenerationStore(root: root)
    // A take saved before the model was recorded still loads and reports `unknown`.
    let legacy = #"{"id":"6F9619FF-8B86-D011-B42D-00CF4FC964FF","text":"Old","createdAt":0}"#
    try data(legacy).write(to: root.appendingPathComponent("6F9619FF-8B86-D011-B42D-00CF4FC964FF.json"))
    let old = try #require(try await store.list().first)
    #expect(old.modelRepo == nil)
    #expect(ContentReportSubject.phone(.speak, id: old.id.uuidString, repo: old.modelRepo, revision: old.modelRevision).modelID == "unknown")
    let voice = try JSONDecoder().decode(PhoneVoice.self, from: data(#"{"id":"6F9619FF-8B86-D011-B42D-00CF4FC964FF","name":"V","transcript":"t","createdAt":0,"consentAt":0,"sampleRate":24000}"#))
    #expect(voice.modelRepo == nil)
}

@Test func dubReportNamesTheRevisionOnlyWhileThePinMatches() throws {
    // #3268 review: a dub saved before a catalogue update must not blame the new revision.
    let catalogue = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json")))
    let clone = try #require(catalogue.models.first { $0.capability == .clone })
    let current = try #require(phoneDubVoiceModel(pins: [clone.id: try phoneModelPin(clone)], in: catalogue.models))
    #expect(current.repo == clone.repo && current.revision == clone.revision)
    let stale = try #require(phoneDubVoiceModel(pins: [clone.id: "stale-pin"], in: catalogue.models))
    #expect(stale.repo == clone.repo && stale.revision == nil)
    #expect(phoneDubVoiceModel(pins: [:], in: catalogue.models) == nil)
}
