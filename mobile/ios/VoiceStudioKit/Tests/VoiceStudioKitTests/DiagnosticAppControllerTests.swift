#if VS_DIAGNOSTICS_APP_TESTS
import Foundation
import Testing
#if canImport(UIKit)
import CryptoKit
#endif
#if VS_DIAGNOSTICS_APP_HOST_TESTS
@testable import VoiceStudio
#endif
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
    let running = #"{"job":{"id":"old-prep","status":"running","created_at":100,"updated_at":100},"events":[{"seq":7,"created_at":100,"payload":"data: {\"type\":\"progress\",\"percent\":50}\n\n"}],"event_watermark":{"seq":7,"created_at":100}}"#
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

#if canImport(UIKit)
/// Holds an existing ModelStore actor operation, so the real native worker must
/// await invalidateVerification/release after its body has already failed.
private final class PhoneDiagnosticCleanupGate: @unchecked Sendable {
    let entered: AsyncStream<Void>
    private let signal: AsyncStream<Void>.Continuation
    private let release = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var token: NativeCancellation?
    private var heldOnce = false
    var nativeFinished: Bool { lock.withLock {token?.done == true} }
    init() { (entered,signal) = AsyncStream.makeStream(bufferingPolicy: .bufferingNewest(1)) }
    func hold(token: NativeCancellation? = nil) { lock.withLock {self.token=token};signal.yield(()); _ = release.wait(timeout: .now() + 60) }
    func holdOnce() {
        let first=lock.withLock {if heldOnce {return false};heldOnce=true;return true}
        if first {hold()}
    }
    func finish() { release.signal() }
    func waitUntilEntered() async { for await _ in entered { return } }
}
private func phoneDiagnosticFixture(root: URL,id: String) throws -> PhoneModel {
    let bytes=Data("fixture".utf8)
    let hash=SHA256.hash(data:bytes).map {String(format:"%02x",$0)}.joined()
    let object: [String:Any] = ["schema_version":1,"models":[[
        "id":id,"name":"Fixture","capability":"tts","runtime":"sherpa-onnx",
        "repo":"owner/model","revision":String(repeating:"b",count:40),
        "files":[["path":"model.bin","size":bytes.count,"sha256":hash,"role":"model"]],
        "total_bytes":bytes.count,"languages":["en"],"min_ram_gb":1,
        "licence":["id":"test","category":"commercial-ok","requires_acceptance":false,
                   "credit":"Fixture","rights_holder":"Fixture","terms_fingerprint":String(repeating:"a",count:64)],
        "config":["model_type":"kitten"]
    ]]]
    let model=try ModelCatalogue.decode(JSONSerialization.data(withJSONObject:object)).models[0]
    let directory=root.appendingPathComponent(id)
    try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
    try bytes.write(to:directory.appendingPathComponent("model.bin"))
    try JSONEncoder().encode(["model.bin":hash]).write(to:directory.appendingPathComponent("verified.json"))
    return model
}
@Suite(.serialized) struct PhoneJobDiagnosticConsentTests {
    @MainActor @Test(.timeLimit(.minutes(1)),arguments:["off-on","regrant","same-grant","cancel"])
    func nativeCleanupCannotAttributeAnEarlierFailureToLaterConsent(_ scenario: String) async throws {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
        let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
        let controller=AppDiagnostics(reporter:reports);await controller.load()
        if scenario != "off-on" {await controller.setConsent(true)}
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent("phone-cleanup-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let model=try phoneDiagnosticFixture(root:directory,id:"native-failure")
        let holder=try phoneDiagnosticFixture(root:directory,id:"cleanup-holder")
        let body=PhoneDiagnosticCleanupGate(),cleanup=PhoneDiagnosticCleanupGate()
        defer {body.finish();cleanup.finish()}
        let store=try ModelStore(root:directory,fileHash:{url in
            if url.deletingLastPathComponent().lastPathComponent==holder.id {cleanup.hold()}
            return try ModelHash.file(url)
        },available:{_ in 100_000_000})
        let engine=NativeLocalEngine(store:store),jobs=PhoneJobs.shared
        let id="generate:"+UUID().uuidString
        let originalFailure=jobs.onFailure,originalObservation=jobs.diagnosticFailureObservation
        jobs.diagnosticFailureObservation={controller.failureObservation}
        jobs.onFailure={stage,error,observation in controller.captureObservedFailure(error,observation:observation,stage:stage)}
        defer {jobs.onFailure=originalFailure;jobs.diagnosticFailureObservation=originalObservation;jobs.cancel(id:id)}
        #expect(jobs.start(id:id,title:"Fixture",requiresGPU:false) {_ in
            let _: Bool = try await engine.execute(model,capability:.tts,progress:{_ in}) {_,_,_,token in
                body.hold(token:token)
                if scenario=="cancel" {throw CancellationError()}
                throw ClientError("mobile.local.inference_failed")
            }
            return PhoneJobResult(file:nil)
        })
        await body.waitUntilEntered()
        let blockedStore=Task.detached {try await store.authorize(holder)}
        await cleanup.waitUntilEntered()
        // execute's native body now fails while its cleanup cannot enter ModelStore.
        body.finish()
        while !body.nativeFinished {await Task.yield()}
        if scenario=="regrant" {await controller.setConsent(false)}
        if scenario=="off-on" || scenario=="regrant" {await controller.setConsent(true)}
        if scenario=="cancel" {jobs.cancel(id:id)}
        cleanup.finish();_ = try await blockedStore.value
        while jobs.states[id]?.running==true {await Task.yield()}
        #expect(jobs.states[id]?.error != nil || scenario=="cancel")
        await reports.flush()
        #expect(transport.count == (scenario=="same-grant" ? 1 : 0))
        // A genuinely new semantic unit observes the current grant normally.
        let fresh="transcript:"+UUID().uuidString
        #expect(jobs.start(id:fresh,title:"Fresh fixture",requiresGPU:false) {_ in
            let _: Bool = try await engine.execute(model,capability:.tts,progress:{_ in}) {_,_,_,_ in throw ClientError("mobile.local.runtime_failed")}
            return PhoneJobResult(file:nil)
        })
        while jobs.states[fresh]?.running==true {await Task.yield()}
        await reports.flush()
        #expect(transport.count == (scenario=="same-grant" ? 2 : 1))
        await reports.close()
    }
    @MainActor @Test(.timeLimit(.minutes(1)),arguments:["off-on","regrant","same-grant"])
    func downloadAwaitCannotBorrowLaterConsent(_ scenario: String) async throws {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
        let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
        let controller=AppDiagnostics(reporter:reports);await controller.load()
        if scenario != "off-on" {await controller.setConsent(true)}
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent("phone-download-owner-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let fixture=try phoneDiagnosticFixture(root:directory,id:"download-failure")
        try FileManager.default.removeItem(at:directory.appendingPathComponent(fixture.id))
        let held=PhoneDiagnosticCleanupGate();defer {held.finish()}
        let store=try ModelStore(root:directory,available:{_ in held.holdOnce();return 0})
        let phone=PhoneModels(modelStore:(directory,store)),originalMobileData=UserDefaults.standard.bool(forKey:"models.mobile-data")
        phone.allowMobileData=true
        defer {phone.allowMobileData=originalMobileData}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,observation in controller.captureObservedFailure(error,observation:observation,stage:stage)}
        phone.download(fixture)
        await held.waitUntilEntered()
        if scenario=="regrant" {await controller.setConsent(false)}
        if scenario=="off-on" || scenario=="regrant" {await controller.setConsent(true)}
        held.finish()
        while phone.progress[fixture.id] != nil {await Task.yield()}
        #expect(phone.downloadFailures[fixture.id]?.key=="mobile.local.storage_required")
        await reports.flush()
        #expect(transport.count == (scenario=="same-grant" ? 1 : 0))
        if scenario != "same-grant" {
            phone.download(fixture)
            while phone.progress[fixture.id] != nil {await Task.yield()}
            await reports.flush()
            #expect(transport.count==1)
        }
        await reports.close()
    }
    #if VS_DIAGNOSTICS_APP_HOST_TESTS
    @MainActor @Test(.timeLimit(.minutes(1)),arguments:["phone-off-on","phone-regrant","host-off-on","host-regrant","phone-same-grant","host-same-grant","phone-cancel","host-cancel"])
    func appAwaitedWorkKeepsItsOriginalConsent(_ scenario: String) async throws {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
        let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
        // The application module's real controller and AppModel own this path.
        let controller=VoiceStudio.AppDiagnostics(reporter:reports);await controller.load()
        if !scenario.hasSuffix("off-on") {await controller.setConsent(true)}
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent("phone-operation-owner-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let originalFailure=VoiceStudio.PhoneJobs.shared.onFailure,originalObservation=VoiceStudio.PhoneJobs.shared.diagnosticFailureObservation
        defer {VoiceStudio.PhoneJobs.shared.onFailure=originalFailure;VoiceStudio.PhoneJobs.shared.diagnosticFailureObservation=originalObservation}
        let model=VoiceStudio.AppModel(mock:false,diagnostics:controller,phone:VoiceStudio.PhoneModels(modelStore:(directory,try ModelStore(root:directory))))
        let held=PhoneDiagnosticAsyncGate()
        let operation=Task {
            let work: @MainActor () async throws -> Void = {
                await held.hold()
                try Task.checkCancellation()
                throw ClientError("mobile.local.inference_failed")
            }
            if scenario.hasPrefix("phone") {await model.performPhone(diagnosticStage:"generation",work)}
            else {await model.perform(connectionChange:true,diagnosticStage:"generation",work)}
        }
        await held.waitUntilEntered()
        if scenario.hasSuffix("regrant") {await controller.setConsent(false)}
        if scenario.hasSuffix("off-on") || scenario.hasSuffix("regrant") {await controller.setConsent(true)}
        if scenario.hasSuffix("cancel") {operation.cancel()}
        held.finish();await operation.value
        await reports.flush()
        #expect(transport.count == (scenario.hasSuffix("same-grant") ? 1 : 0))
        let fresh: @MainActor () async throws -> Void = {throw ClientError("mobile.local.runtime_failed")}
        if scenario.hasPrefix("phone") {await model.performPhone(diagnosticStage:"generation",fresh)}
        else {await model.perform(connectionChange:true,diagnosticStage:"generation",fresh)}
        await reports.flush()
        #expect(transport.count == (scenario.hasSuffix("same-grant") ? 2 : 1))
        await reports.close()
    }
    @MainActor @Test(.timeLimit(.minutes(1)),arguments:["off-on","regrant","same-grant","cancel","submitted-off-on","submitted-regrant"])
    func monitorHTTPFailuresKeepTheirOwnerAndFreshSubmittedTasksKeepTheirOwnProof(_ scenario: String) async throws {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
        let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
        let controller=VoiceStudio.AppDiagnostics(reporter:reports);await controller.load()
        if !scenario.hasSuffix("off-on") {await controller.setConsent(true)}
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent("phone-monitor-owner-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let suite="phone-monitor-"+UUID().uuidString
        let defaults=UserDefaults(suiteName:suite)!
        defer {defaults.removePersistentDomain(forName:suite)}
        let previousCheckpoint=UserDefaults.standard.data(forKey:"dubCheckpoint")
        defer {UserDefaults.standard.set(previousCheckpoint,forKey:"dubCheckpoint")}
        let held=PhoneDiagnosticAsyncGate(),submitted=scenario.hasPrefix("submitted-")
        let host=PhoneMonitorDiagnosticTransport(held:held,submitted:submitted)
        let connection=ConnectionStore(defaults:defaults,vault:PhoneMonitorDiagnosticVault(),transport:host)
        let api=try await connection.connect(address:PairingAddress("http://127.0.0.1:3900"))
        let originalFailure=VoiceStudio.PhoneJobs.shared.onFailure,originalObservation=VoiceStudio.PhoneJobs.shared.diagnosticFailureObservation
        defer {VoiceStudio.PhoneJobs.shared.onFailure=originalFailure;VoiceStudio.PhoneJobs.shared.diagnosticFailureObservation=originalObservation}
        let model=VoiceStudio.AppModel(mock:false,diagnostics:controller,phone:VoiceStudio.PhoneModels(modelStore:(directory,try ModelStore(root:directory))),connection:connection)
        try model.dub.begin(DubUpload(job_id:"monitor-job",task_id:"monitor-prep",filename:"fixture.wav"),api:api,languages:[DubLanguage(code:"fr",name:"French")],profile:"")
        model.monitor();let first=model.monitorTask
        try #require(first != nil)
        await held.waitUntilEntered()
        if scenario.hasSuffix("regrant") {await controller.setConsent(false)}
        if scenario.hasSuffix("off-on") || scenario.hasSuffix("regrant") {await controller.setConsent(true)}
        if scenario=="cancel" {model.pauseMonitor()}
        held.finish();await first?.value
        #expect(model.dub.failureWasRemoteTask==submitted)
        if submitted {
            #expect(model.dub.failureDiagnosticObservation==controller.failureObservation)
            #expect(await host.renderSubmissions==1)
        }
        await reports.flush()
        #expect(transport.count == (scenario=="same-grant" || submitted ? 1 : 0))
        if !submitted {
            // The next monitor execution owns the now-current grant. A different
            // mapped HTTP failure also proves delivery rather than coalescing.
            model.monitor();await model.monitorTask?.value
            await reports.flush()
            #expect(transport.count == (scenario=="same-grant" ? 2 : 1))
        }
        await reports.close()
    }
    @MainActor @Test(.timeLimit(.minutes(1)),arguments:["native-off-on","native-regrant","native-same-grant","native-cancel","ticket-off-on","ticket-regrant","ticket-same-grant","ticket-cancel","socket-off-on","socket-regrant","socket-same-grant","socket-cancel"])
    func directDictationCallbacksKeepRecordingConsentThroughProducerCleanup(_ scenario: String) async throws {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let storage=DiagnosticControllerStore(),transport=DiagnosticControllerTransport();storage.allowWrites()
        let reports=try DiagnosticReports(directory:URL(fileURLWithPath:"/unused-test-store"),policy:DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json"))),destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://app.test.invalid"),appVersion:"0.5.9-beta.2",transport:transport,storage:storage,disabled:{false})
        let controller=VoiceStudio.AppDiagnostics(reporter:reports);await controller.load()
        if !scenario.hasSuffix("off-on") {await controller.setConsent(true)}
        // This is the exact callback factory used by both production recording starts.
        let reportFailure=controller.failureHandler(stage:"transcription")
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent("direct-dictation-owner-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let fixture=try phoneDiagnosticFixture(root:directory,id:"dictation-failure")
        let holder=try phoneDiagnosticFixture(root:directory,id:"dictation-cleanup")
        let body=PhoneDiagnosticCleanupGate(),cleanup=PhoneDiagnosticCleanupGate(),held=PhoneDiagnosticAsyncGate()
        defer {body.finish();cleanup.finish();held.finish()}
        let store=try ModelStore(root:directory,fileHash:{url in
            if url.deletingLastPathComponent().lastPathComponent==holder.id {cleanup.hold()}
            return try ModelHash.file(url)
        },available:{_ in 100_000_000})
        let engine=NativeLocalEngine(store:store)
        let socket=PhoneDiagnosticSocket(held:held)
        let stream=DictationStream(socketFactory:{_ in socket})
        let credentials=Credentials(session:scenario.hasPrefix("ticket") ? "fixture-session" : nil)
        let api=APIClient(address:try PairingAddress("http://127.0.0.1:3900"),credentials:credentials,transport:PhoneDictationTicketFailure(held:held))
        let recording=Task {
            if scenario.hasPrefix("native") {
                do {
                    let _: Bool = try await engine.execute(fixture,capability:.tts,progress:{_ in}) {_,_,_,token in
                        body.hold(token:token)
                        if scenario.hasSuffix("cancel") {throw CancellationError()}
                        throw ClientError("mobile.local.inference_failed")
                    }
                } catch {reportFailure(error)}
            } else {
                do {
                    let messages=try await stream.start(api:api)
                    for try await _ in messages {try Task.checkCancellation()}
                } catch {
                    // Paired startup currently awaits socket cleanup before the UI catch.
                    await stream.close()
                    reportFailure(error)
                }
            }
        }
        var blockedStore: Task<URL,any Error>?
        if scenario.hasPrefix("native") {
            await body.waitUntilEntered()
            blockedStore=Task.detached {try await store.authorize(holder)}
            await cleanup.waitUntilEntered();body.finish()
            while !body.nativeFinished {await Task.yield()}
        } else {await held.waitUntilEntered()}
        if scenario.hasSuffix("regrant") {await controller.setConsent(false)}
        if scenario.hasSuffix("off-on") || scenario.hasSuffix("regrant") {await controller.setConsent(true)}
        if scenario.hasSuffix("cancel") {recording.cancel()}
        cleanup.finish();held.finish();_ = try await blockedStore?.value;await recording.value
        await stream.close();await reports.flush()
        #expect(transport.count == (scenario.hasSuffix("same-grant") ? 1 : 0))
        // A later user-started recording obtains a fresh callback and can report.
        let freshRecording=controller.failureHandler(stage:"transcription")
        do {
            let _: Bool = try await engine.execute(fixture,capability:.tts,progress:{_ in}) {_,_,_,_ in throw ClientError("mobile.local.runtime_failed")}
        } catch {freshRecording(error)}
        await reports.flush()
        #expect(transport.count == (scenario.hasSuffix("same-grant") ? 2 : 1))
        await reports.close()
    }
    #endif
}
#if VS_DIAGNOSTICS_APP_HOST_TESTS
private struct PhoneDictationTicketFailure: HTTPTransport {
    let held: PhoneDiagnosticAsyncGate
    func send(_ request:URLRequest) async throws -> HTTPReply {
        guard request.url?.path=="/api/auth/ws-ticket" else {throw ClientError("mobile.errors.invalid_response")}
        // The server failure exists before its delayed body is released.
        let failed=HTTPReply(data:Data(#"{"detail":"fixture failure"}"#.utf8),status:500)
        await held.hold();try Task.checkCancellation();return failed
    }
}
private final class PhoneDiagnosticSocket: DictationSocket, @unchecked Sendable {
    let closeCode=URLSessionWebSocketTask.CloseCode.invalid
    private let held: PhoneDiagnosticAsyncGate
    init(held:PhoneDiagnosticAsyncGate) {self.held=held}
    func resume() {}
    func receive() async throws -> URLSessionWebSocketTask.Message {
        let failure=ClientError("mobile.errors.dictation")
        await held.hold();try Task.checkCancellation();throw failure
    }
    func send(_ message:URLSessionWebSocketTask.Message) async throws {}
    func cancel(with closeCode:URLSessionWebSocketTask.CloseCode,reason:Data?) {held.finish()}
}
private actor PhoneMonitorDiagnosticVault: CredentialVault {
    func read(host:String) -> Credentials? {nil}
    func write(_ credentials:Credentials?,host:String) {}
}
private actor PhoneMonitorDiagnosticTransport: HTTPTransport {
    private let held: PhoneDiagnosticAsyncGate
    private let submitted: Bool
    private var eventRequests=0
    private(set) var renderSubmissions=0
    init(held:PhoneDiagnosticAsyncGate,submitted:Bool) {self.held=held;self.submitted=submitted}
    func send(_ request:URLRequest) async throws -> HTTPReply {
        let path=request.url?.path ?? ""
        if path=="/health" {return reply(#"{"status":"ok","device":"cpu","version":"test"}"#,headers:["x-omnivoice-backend":"test"])}
        if path=="/jobs/monitor-prep/events" {
            eventRequests += 1
            if eventRequests==1 {await held.hold()}
            try Task.checkCancellation()
            if submitted {return reply(#"{"job":{"id":"monitor-prep","status":"done"},"events":[]}"#)}
            if eventRequests>1 {throw URLError(.cannotConnectToHost)}
            return HTTPReply(data:Data(#"{"detail":"fixture failure"}"#.utf8),status:500)
        }
        if submitted {
            switch path {
            case "/dub/transcribe/monitor-job":return reply(#"{"segments":[{"id":"1","start":0,"end":1,"text":"Fixture"}]}"#)
            case "/engines/translation":return reply(#"{"active":"argos","engines":[{"id":"argos","display_name":"Argos","category":"offline","installed":true,"ready":true}]}"#)
            case "/dub/translate":return reply(#"{"translated":[{"id":"1","text":"Fixture"}]}"#)
            case "/dub/generate/monitor-job":renderSubmissions += 1;return reply(#"{"task_id":"monitor-render"}"#)
            case "/jobs/monitor-render/events":return reply(#"{"job":{"id":"monitor-render","status":"failed","created_at":100,"updated_at":101},"events":[{"seq":1,"created_at":101,"payload":"data: {\"type\":\"error\"}\n\n"}]}"#)
            default:break
            }
        }
        throw ClientError("mobile.errors.invalid_response")
    }
    private func reply(_ json:String,headers:[String:String]=[:]) -> HTTPReply {HTTPReply(data:Data(json.utf8),headers:headers)}
}
#endif
private final class PhoneDiagnosticAsyncGate: Sendable {
    private let entered: AsyncStream<Void>
    private let enteredSignal: AsyncStream<Void>.Continuation
    private let released: AsyncStream<Void>
    private let releaseSignal: AsyncStream<Void>.Continuation
    init() {
        (entered,enteredSignal)=AsyncStream.makeStream(bufferingPolicy:.bufferingNewest(1))
        (released,releaseSignal)=AsyncStream.makeStream(bufferingPolicy:.bufferingNewest(1))
    }
    func hold() async {enteredSignal.yield(());for await _ in released {return}}
    func waitUntilEntered() async {for await _ in entered {return}}
    func finish() {releaseSignal.yield(())}
}
#endif
