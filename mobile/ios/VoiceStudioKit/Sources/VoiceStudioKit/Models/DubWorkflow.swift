import Foundation
import Observation

public enum DubStage: String, Codable, Sendable { case preparing, transcribing, translating, rendering, done, failed, cancelled }
public struct DubLanguage: Codable, Hashable, Identifiable, Sendable {
    public let code: String
    public let name: String
    public var id: String { code }
    public init(code: String, name: String) { self.code = code; self.name = name }

}
public struct DubCheckpoint: Codable, Sendable {
    public let host: String
    public let jobID: String
    public var taskID: String?
    public var stage: DubStage
    public var afterSequence = 0
    public var segments: [Segment] = []
    public var languages: [DubLanguage]
    public var languageIndex = 0
    public var approvedOnlineProvider: String?
    public var profileID: String
    public var title: String
    public init(host: String, upload: DubUpload, languages: [DubLanguage], profileID: String) {
        self.host = host; jobID = upload.job_id; taskID = upload.task_id; stage = .preparing
        self.languages = languages; self.profileID = profileID; title = upload.filename
    }
}

/// State is saved after each acknowledged host transition. UI navigation never owns the host job.
@MainActor @Observable public final class DubWorkflow {
    public private(set) var checkpoint: DubCheckpoint?
    public private(set) var progress: Double = 0
    public private(set) var error: ClientError?
    public private(set) var monitoring = false
    public private(set) var pendingTranslation: TranslationEngine?
    public private(set) var failureWasRemoteTask = false
    public private(set) var failureDiagnosticObservation: CompanionDiagnosticObservation?
    @ObservationIgnored public var diagnosticObservation: (@MainActor () -> CompanionDiagnosticObservation?)?
    @ObservationIgnored private var submittedTasks: [String:CompanionDiagnosticObservation]=[:]
    @ObservationIgnored private var revision = UUID()
    @ObservationIgnored private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults; checkpoint = defaults.data(forKey: "dubCheckpoint").flatMap { try? JSONDecoder().decode(DubCheckpoint.self, from: $0) } }
    public func begin(_ upload: DubUpload, api: APIClient, languages: [DubLanguage], profile: String,diagnosticSubmission: CompanionDiagnosticObservation? = nil) throws {
        try DubVoiceChoice.validate(profile)
        guard !languages.isEmpty else { throw ClientError("mobile.errors.choose_language") }
        revision = UUID(); pendingTranslation = nil; monitoring = false
        submittedTasks.removeAll()
        if let diagnosticSubmission {submittedTasks[upload.task_id]=diagnosticSubmission}
        checkpoint = DubCheckpoint(host: api.address.baseURL.absoluteString, upload: upload, languages: languages, profileID: profile)
        error = nil; progress = 0; save()
    }
    public func monitor(api: APIClient, pollInterval: Duration = .seconds(2)) async throws {
        guard !monitoring, checkpoint?.host == api.address.baseURL.absoluteString else { return }
        failureWasRemoteTask = false; failureDiagnosticObservation = nil
        var diagnosticGate=CompanionTaskFailureGate()
        let revision = self.revision
        monitoring = true
        defer { if revision == self.revision { monitoring = false } }
        do {
            while var cp = checkpoint, ![.done, .failed, .cancelled].contains(cp.stage) {
                try validate(revision)
                switch cp.stage {
                case .preparing, .rendering:
                    guard let task = cp.taskID else { throw ClientError("mobile.errors.invalid_response") }
                    let observation=diagnosticObservation?()
                    let page = try await api.events(task, after: cp.afterSequence)
                    try validate(revision)
                    let reportable=diagnosticGate.observe(taskID:task,page:page,before:observation,after:diagnosticObservation?(),submitted:submittedTasks[task])
                    for event in page.events {
                        cp.afterSequence = max(cp.afterSequence, event.seq)
                        if let eventProgress = DubProgress.parse(event) {
                            if ["error", "failed"].contains(eventProgress.stage) { failureWasRemoteTask = true; failureDiagnosticObservation=reportable; cp.stage = .failed; checkpoint = cp; save(); throw ClientError("mobile.errors.dub_failed") }
                            progress = eventProgress.fraction
                        }
                    }
                    if page.job.status == "failed" { failureWasRemoteTask = true; failureDiagnosticObservation=reportable; cp.stage = .failed; checkpoint = cp; save(); throw ClientError("mobile.errors.dub_failed") }
                    if page.job.status == "cancelled" { cp.stage = .cancelled }
                    if page.job.status == "done" {
                        progress = 0
                        if cp.stage == .preparing { cp.stage = .transcribing }
                        else { cp.languageIndex += 1; cp.stage = cp.languageIndex < cp.languages.count ? .translating : .done }
                        cp.taskID = nil; cp.afterSequence = 0
                    }
                    checkpoint = cp; save()
                    if !page.job.terminal { try await Task.sleep(for: pollInterval) }
                case .transcribing:
                    cp.segments = try await api.transcribe(cp.jobID).segments
                    try validate(revision)
                    cp.stage = .translating; checkpoint = cp; save()
                case .translating:
                    let language = cp.languages[cp.languageIndex]
                    let engine = try await api.translationEngineForJob()
                    try validate(revision)
                    if engine.requiresConfirmation && cp.approvedOnlineProvider != engine.consentIdentity {
                        pendingTranslation = engine
                        return
                    }
                    let translated = try await api.translate(cp.jobID, segments: cp.segments, language: language.code, provider: engine.id)
                    try validate(revision)
                    guard !translated.translated.contains(where: { $0.error != nil }) else { throw ClientError("mobile.errors.translation") }
                    let byID = Dictionary(translated.translated.map { ($0.id, $0.text) }, uniquingKeysWith: { _, new in new })
                    let segments = try cp.segments.map { segment -> Segment in
                        guard let text = byID[segment.id], !text.isEmpty else { throw ClientError("mobile.errors.invalid_response") }
                        var cue = segment
                        cue.text = text; cue.profile_id = cp.profileID; cue.target_lang = language.code; cue.cue_source_id = segment.id
                        return cue
                    }
                    let submission=diagnosticObservation?()
                    let task = try await api.render(cp.jobID, segments: segments, language: language.code, languageName: language.name)
                    try validate(revision)
                    if let submission {submittedTasks[task.task_id]=submission}
                    cp.taskID = task.task_id; cp.stage = .rendering; cp.afterSequence = 0
                    checkpoint = cp; save()
                case .done, .failed, .cancelled: break
                }
            }
        } catch is CancellationError { throw CancellationError() }
        catch let failure as ClientError { try validate(revision); error = failure; throw failure }
        catch { try validate(revision); let failure = ClientError("mobile.errors.dub_failed"); self.error = failure; throw failure }
    }
    public func abort(api: APIClient) async throws {
        guard var cp = checkpoint, cp.host == api.address.baseURL.absoluteString else { return }
        let revision = self.revision
        try await api.abort(cp.jobID, task: cp.taskID)
        try validate(revision)
        cp.stage = .cancelled; checkpoint = cp; save()
    }
    /// Consent applies to this job/provider only, never to later jobs or a new host.
    public func confirmTranslation(_ allowed: Bool) {
        guard let engine = pendingTranslation else { return }
        pendingTranslation = nil
        if allowed { checkpoint?.approvedOnlineProvider = engine.consentIdentity; save() }
        else { checkpoint?.stage = .cancelled; save() }
    }
    public func clear() {
        submittedTasks.removeAll()
        revision = UUID(); checkpoint = nil; progress = 0; error = nil
        pendingTranslation = nil; monitoring = false
        defaults.removeObject(forKey: "dubCheckpoint")
    }
    public func scope(to host: String?) {
        if checkpoint?.host != host { clear() }
    }
    private func validate(_ revision: UUID) throws {
        try Task.checkCancellation()
        guard self.revision == revision else { throw CancellationError() }
    }
    private func save() { defaults.set(try? JSONEncoder().encode(checkpoint), forKey: "dubCheckpoint") }
}
