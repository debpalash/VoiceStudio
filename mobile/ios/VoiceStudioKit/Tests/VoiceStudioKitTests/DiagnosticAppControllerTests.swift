#if VS_DIAGNOSTICS_APP_TESTS
import Foundation
import Testing
@testable import VoiceStudioKit

private final class DiagnosticControllerStore: DiagnosticStorage, @unchecked Sendable {
    private let lock=NSLock()
    private var files: [String:Data]=[:]
    private var fail=true
    func allowWrites() { lock.withLock { fail=false } }
    func read(_ name: String,maxBytes: Int) throws -> Data? { lock.withLock { files[name] } }
    func remove(_ name: String) throws { _ = lock.withLock { files.removeValue(forKey: name) } }
    func write(_ name: String,data: Data) throws {
        try lock.withLock { if fail { throw DiagnosticFailure.storageUnavailable };files[name]=data }
    }
}
private final class DiagnosticControllerTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock=NSLock()
    private var sends=0
    var count: Int { lock.withLock { sends } }
    func send(destination: DiagnosticDestination,body: Data,allowed: @escaping @Sendable () -> Bool) async throws -> Int { lock.withLock { sends += 1 };return 201 }
    func cancelAll() {}
}
@MainActor @Test func diagnosticAppControllerRetriesTheDesiredEnabledChoice() async throws {
    var root=URL(fileURLWithPath: #filePath)
    for _ in 0..<6 { root.deleteLastPathComponent() }
    let policy=try DiagnosticPolicy(data: Data(contentsOf: root.appendingPathComponent("config/diagnostics.json")))
    let store=DiagnosticControllerStore(), transport=DiagnosticControllerTransport()
    let reports=try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-test-store"),policy: policy,destination: DiagnosticDestination(site: "localtest123",host: "http://127.0.0.1:1",appUrl: "https://app.test.invalid"),appVersion: "0.5.9-beta.2",transport: transport,storage: store,disabled: { false })
    let controller=AppDiagnostics(reporter: reports)
    await controller.load();await controller.setConsent(true)
    #expect(!controller.enabled);#expect(controller.errorKey=="diagnostics.save_failed")
    store.allowWrites();await controller.retryConsent()
    #expect(controller.enabled);#expect(controller.errorKey==nil)
    await controller.setConsent(false);await reports.close()
    #expect(transport.count==0)
}

private actor ArchivedCompanionHost: HTTPTransport {
    let eventFailure: Bool
    init(eventFailure: Bool) { self.eventFailure=eventFailure }
    func send(_ request: URLRequest) throws -> HTTPReply {
        guard request.url?.path=="/jobs/old-prep/events" else {throw ClientError("mobile.errors.invalid_response")}
        let body=eventFailure
            ? #"{"job":{"id":"old-prep","status":"done","created_at":100,"updated_at":101},"events":[{"seq":1,"created_at":101,"payload":"data: {\"type\":\"error\"}\n\n"}]}"#
            : #"{"job":{"id":"old-prep","status":"failed","created_at":100,"updated_at":101},"events":[]}"#
        return HTTPReply(data: Data(body.utf8))
    }
}
@MainActor @Test(arguments: [false,true]) func archivedCompanionFailureIsDisplayedWithoutRetroactiveDiagnosticConsent(_ eventFailure: Bool) async throws {
    var root=URL(fileURLWithPath: #filePath)
    for _ in 0..<6 {root.deleteLastPathComponent()}
    let policy=try DiagnosticPolicy(data: Data(contentsOf: root.appendingPathComponent("config/diagnostics.json")))
    let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport()
    storage.allowWrites()
    let reports=try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-test-store"),policy: policy,destination: DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
    let controller=AppDiagnostics(reporter:reports)
    let suite="archived-companion-"+UUID().uuidString
    let defaults=UserDefaults(suiteName:suite)!
    defer {defaults.removePersistentDomain(forName:suite)}
    let client=APIClient(address:try PairingAddress("http://127.0.0.1:3900"),transport:ArchivedCompanionHost(eventFailure:eventFailure))
    let previous=DubWorkflow(defaults:defaults)
    try previous.begin(DubUpload(job_id:"old-job",task_id:"old-prep",filename:"fixture.wav"),api:client,languages:[DubLanguage(code:"fr",name:"French")],profile:"")
    // A real saved checkpoint predates opt-in; the SDK then reads the host's persisted tail.
    await controller.load();await controller.setConsent(true)
    let restored=DubWorkflow(defaults:defaults)
    restored.diagnosticObservation={controller.failureObservation}
    do {try await restored.monitor(api:client,pollInterval:.milliseconds(1));Issue.record("Expected stored terminal failure")}
    catch {
        #expect((error as? ClientError)?.key=="mobile.errors.dub_failed")
        controller.captureCompanionFailure(error,observation:restored.failureDiagnosticObservation,remoteTask:restored.failureWasRemoteTask)
    }
    #expect(restored.checkpoint?.stage == .failed)
    await reports.flush()
    #expect(transport.count==0)
    controller.captureCompanionFailure(ClientError("mobile.local.inference_failed"),observation:nil,remoteTask:false)
    await reports.flush()
    #expect(transport.count==1)
    await reports.close()
}

private actor SequencedCompanionHost: HTTPTransport {
    private let pages: [String]
    private var index=0
    private let beforeReply: (@Sendable (Int) async -> Void)?
    init(_ pages: [String],beforeReply: (@Sendable (Int) async -> Void)? = nil) {self.pages=pages;self.beforeReply=beforeReply}
    func send(_ request: URLRequest) async throws -> HTTPReply {
        guard request.url?.path=="/jobs/old-prep/events",index<pages.count else {throw ClientError("mobile.errors.invalid_response")}
        let current=index;index += 1
        await beforeReply?(current)
        return HTTPReply(data:Data(pages[current].utf8))
    }
}
@MainActor @Test(arguments:["fresh","old-sequence","old-time","missing-time","new-epoch"])
func companionFailureRequiresNewServerEventAndTheSameObservedGrant(_ scenario: String) async throws {
    var root=URL(fileURLWithPath:#filePath);for _ in 0..<6 {root.deleteLastPathComponent()}
    let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
    let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:root.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
    let controller=AppDiagnostics(reporter:reports);await controller.load();await controller.setConsent(true)
    let running = #"{"job":{"id":"old-prep","status":"running","created_at":100,"updated_at":100},"events":[{"seq":7,"created_at":100,"payload":"data: {\"type\":\"progress\",\"percent\":50}\n\n"}]}"#
    var failure = #"{"job":{"id":"old-prep","status":"failed","created_at":100,"updated_at":101},"events":[{"seq":8,"created_at":101,"payload":"data: {\"type\":\"error\"}\n\n"}]}"#
    if scenario=="old-sequence" {failure=failure.replacingOccurrences(of:"\"seq\":8",with:"\"seq\":7")}
    if scenario=="old-time" {failure=failure.replacingOccurrences(of:"\"created_at\":101",with:"\"created_at\":99")}
    if scenario=="missing-time" {failure=failure.replacingOccurrences(of:",\"created_at\":101",with:"")}
    let host=SequencedCompanionHost([running,failure]) {page in
        if page==1 && scenario=="new-epoch" {await controller.setConsent(false);await controller.setConsent(true)}
    }
    let api=APIClient(address:try PairingAddress("http://127.0.0.1:3900"),transport:host)
    let suite="companion-baseline-"+UUID().uuidString
    let defaults=UserDefaults(suiteName:suite)!
    defer {defaults.removePersistentDomain(forName:suite)}
    let workflow=DubWorkflow(defaults:defaults)
    try workflow.begin(DubUpload(job_id:"old-job",task_id:"old-prep",filename:"fixture.wav"),api:api,languages:[DubLanguage(code:"fr",name:"French")],profile:"")
    workflow.diagnosticObservation={controller.failureObservation}
    do {try await workflow.monitor(api:api,pollInterval:.milliseconds(1));Issue.record("Expected terminal failure")}
    catch {controller.captureCompanionFailure(error,observation:workflow.failureDiagnosticObservation,remoteTask:workflow.failureWasRemoteTask)}
    #expect(workflow.checkpoint?.stage == .failed)
    await reports.flush()
    #expect(transport.count == (scenario=="fresh" ? 1:0))
    await reports.close()
}

@MainActor @Test func newlySubmittedCompanionTaskCanReportAnImmediateFailure() async throws {
    var root=URL(fileURLWithPath:#filePath);for _ in 0..<6 {root.deleteLastPathComponent()}
    let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
    let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:root.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
    let controller=AppDiagnostics(reporter:reports);await controller.load();await controller.setConsent(true)
    let host=ArchivedCompanionHost(eventFailure:false),api=APIClient(address:try PairingAddress("http://127.0.0.1:3900"),transport:host)
    let suite="owned-companion-"+UUID().uuidString
    let defaults=UserDefaults(suiteName:suite)!
    defer {defaults.removePersistentDomain(forName:suite)}
    let workflow=DubWorkflow(defaults:defaults);workflow.diagnosticObservation={controller.failureObservation}
    try workflow.begin(DubUpload(job_id:"new-job",task_id:"old-prep",filename:"fixture.wav"),api:api,languages:[DubLanguage(code:"fr",name:"French")],profile:"",diagnosticSubmission:controller.failureObservation)
    do {try await workflow.monitor(api:api);Issue.record("Expected terminal failure")}
    catch {controller.captureCompanionFailure(error,observation:workflow.failureDiagnosticObservation,remoteTask:workflow.failureWasRemoteTask)}
    await reports.flush();#expect(transport.count==1);await reports.close()
}
#endif
