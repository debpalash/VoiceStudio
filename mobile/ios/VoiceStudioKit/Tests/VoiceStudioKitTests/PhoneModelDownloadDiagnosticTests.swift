#if VS_DIAGNOSTICS_APP_TESTS && canImport(UIKit) || VS_PHONE_MODEL_RESTORE_HOST_TESTS
import Foundation
import CryptoKit
import Testing
@testable import VoiceStudioKit

private final class ModelFixtureNoNetworkProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request:URLRequest)->Bool {true}
    override class func canonicalRequest(for request:URLRequest)->URLRequest {request}
    override func startLoading() {client?.urlProtocol(self,didFailWithError:URLError(.notConnectedToInternet))}
    override func stopLoading() {}
}
private final class RestoredModelReportTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock=NSLock()
    private var count=0
    var sends:Int {lock.withLock {count}}
    func send(destination:DiagnosticDestination,body:Data,allowed:@escaping @Sendable ()->Bool) async throws -> Int {
        guard allowed() else {throw CancellationError()}
        lock.withLock {count+=1};return 201
    }
    func cancelAll() {}
}
private actor RestoredModelTransferGate {
    private var continuation:CheckedContinuation<Void,Never>?
    private var released=false
    func wait() async {if !released {await withCheckedContinuation {continuation=$0}}}
    func release() {released=true;continuation?.resume();continuation=nil}
}
/// A persisted permanent response, returned without starting any network transfer.
private final class RestoredModelReceiptTransport: HTTPTransport, @unchecked Sendable {
    let receipt:URL
    let body:URL
    let gate:RestoredModelTransferGate?
    let entered:AsyncStream<Void>
    private let signal:AsyncStream<Void>.Continuation
    private let lock=NSLock()
    private var fresh=0
    private let status:Int
    private let payload:String
    var freshTransfers:Int {lock.withLock {fresh}}
    init(root:URL,held:Bool=false,status:Int=404,payload:String="missing") throws {
        self.status=status;self.payload=payload
        receipt=root.appendingPathComponent("receipt.json");body=root.appendingPathComponent("receipt.body")
        if !held {
            try Data(payload.utf8).write(to:body)
            try JSONEncoder().encode(["status":status]).write(to:receipt)
        }
        gate=held ? RestoredModelTransferGate() : nil
        (entered,signal)=AsyncStream.makeStream(bufferingPolicy:.bufferingNewest(1))
    }
    func send(_ request:URLRequest) async throws -> HTTPReply {throw ClientError("mobile.local.download_failed")}
    func download(_ request:URLRequest) async throws -> DownloadReply {
        signal.yield(())
        if let gate {await gate.wait()}
        let recovered=FileManager.default.fileExists(atPath:body.path)
        if !recovered {
            // A real ModelStore removes a consumed permanent receipt body.
            // This later explicit attempt receives a fresh fixture response.
            try Data(payload.utf8).write(to:body)
            try JSONEncoder().encode(["status":status]).write(to:receipt)
            lock.withLock {fresh+=1}
        }
        let saved=try JSONDecoder().decode([String:Int].self,from:Data(contentsOf:receipt))
        return DownloadReply(file:body,status:try #require(saved["status"]),headers:status==206 ? ["content-range":"bytes 0-6/7"] : [:],origin:recovered ? .recovered : .currentRequest)
    }
    func waitForEntry() async {for await _ in entered {return}}
}
private final class RestoredModelCapacityGate: @unchecked Sendable {
    let entered:AsyncStream<Void>
    private let signal:AsyncStream<Void>.Continuation
    private let release=DispatchSemaphore(value:0)
    private let lock=NSLock()
    private var first=true
    init() {(entered,signal)=AsyncStream.makeStream(bufferingPolicy:.bufferingNewest(1))}
    func capacity()->Int64 {
        let hold=lock.withLock {if first {first=false;return true};return false}
        if hold {signal.yield(());_ = release.wait(timeout:.now()+10)}
        return Int64.max
    }
    func finish() {release.signal()}
    func waitForEntry() async {for await _ in entered {return}}
}
private final class RestoredModelFirstCapacity: @unchecked Sendable {
    private let lock=NSLock()
    private var first=true
    func capacity()->Int64 {lock.withLock {if first {first=false;return 0};return Int64.max}}
}
@MainActor private final class ModelStallTestClock {
    var now:Int64=0
    private var continuation:CheckedContinuation<Void,any Error>?
    private let sleeping:AsyncStream<Void>
    private let signal:AsyncStream<Void>.Continuation
    init() {(sleeping,signal)=AsyncStream.makeStream(bufferingPolicy:.bufferingNewest(1))}
    func sleep() async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation {(next:CheckedContinuation<Void,any Error>) in
                if Task.isCancelled {next.resume(throwing:CancellationError());return}
                continuation=next;signal.yield(())
            }
        } onCancel: {Task {@MainActor in self.wake(cancelled:true)}}
    }
    func wake(by:Int64=0,cancelled:Bool=false) {
        now+=by;let next=continuation;continuation=nil
        if cancelled {next?.resume(throwing:CancellationError())} else {next?.resume()}
    }
    func waitForSleep() async {for await _ in sleeping {return}}
}

@MainActor @Suite(.serialized) struct PhoneModelDownloadDiagnosticTests {
    @Test(.timeLimit(.minutes(1))) func actualPlatformCachedReceiptCarriesHistoricalOriginWithoutNetwork() async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("model-cached-origin-"+UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:root)}
        let url=try #require(URL(string:"https://github.com/k2-fsa/sherpa-onnx/releases/download/fixture/model.onnx"))
        let request=NSMutableURLRequest(url:url)
        request.setValue("bytes=0-6",forHTTPHeaderField:"Range")
        URLProtocol.setProperty("fixture",forKey:"VoiceStudioModel",in:request)
        let hash:(Data)->String = {data in SHA256.hash(data:data).map {String(format:"%02x",$0)}.joined()}
        let key=hash(Data("fixture".utf8))+"."+hash(Data((url.absoluteString+"|bytes=0-6").utf8))+".7"
        let directory=root.appendingPathComponent("transfers-mobile")
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        try Data("missing".utf8).write(to:directory.appendingPathComponent(key+".body"))
        try JSONSerialization.data(withJSONObject:["status":404,"headers":[:]]).write(to:directory.appendingPathComponent(key+".json"))
        let configuration=URLSessionConfiguration.ephemeral
        configuration.protocolClasses=[ModelFixtureNoNetworkProtocol.self]
        let transport=try BackgroundModelDownloads(root:root,allowMobile:true,configuration:configuration)
        let reply=try await transport.download(request as URLRequest)
        #expect(reply.status==404)
        #expect(reply.origin == .recovered)
    }
    private func fixture(root:URL) throws -> (PhoneModel,Bundle) {
        let hash=SHA256.hash(data:Data("fixture".utf8)).map {String(format:"%02x",$0)}.joined()
        let object:[String:Any]=["schema_version":1,"models":[[
            "id":"restored-download","name":"Fixture","capability":"tts","runtime":"sherpa-onnx",
            "repo":"fixture/model","revision":String(repeating:"b",count:40),
            "files":[["path":"model.bin","size":7,"sha256":hash,"role":"model","url":"https://github.com/k2-fsa/sherpa-onnx/releases/download/fixture/model.onnx"]],
            "total_bytes":7,"languages":["en"],"min_ram_gb":1,
            "licence":["id":"test","category":"commercial-ok","requires_acceptance":false,"credit":"Fixture","rights_holder":"Fixture","terms_fingerprint":String(repeating:"a",count:64)],
            "config":["model_type":"kitten"]
        ]]]
        let data=try JSONSerialization.data(withJSONObject:object)
        let bundle=root.appendingPathComponent("Fixture.bundle")
        for prefix in [bundle,bundle.appendingPathComponent("Contents/Resources")] {
            try FileManager.default.createDirectory(at:prefix.appendingPathComponent("Catalogs"),withIntermediateDirectories:true)
            try data.write(to:prefix.appendingPathComponent("Catalogs/models.json"))
        }
        let info=try PropertyListSerialization.data(fromPropertyList:["CFBundleIdentifier":"sh.voicestudio.fixture.models","CFBundlePackageType":"BNDL"],format:.xml,options:0)
        try info.write(to:bundle.appendingPathComponent("Info.plist"))
        try info.write(to:bundle.appendingPathComponent("Contents/Info.plist"))
        return (try ModelCatalogue.decode(data).models[0],try #require(Bundle(url:bundle)))
    }
    private func reports(root:URL) async throws -> (DiagnosticReports,AppDiagnostics,RestoredModelReportTransport) {
        var repository=URL(fileURLWithPath:#filePath);for _ in 0..<6 {repository.deleteLastPathComponent()}
        let policy=try DiagnosticPolicy(data:Data(contentsOf:repository.appendingPathComponent("config/diagnostics.json")))
        let transport=RestoredModelReportTransport()
        let reporter=try DiagnosticReports(directory:root.appendingPathComponent("diagnostics"),policy:policy,
            destination:DiagnosticDestination(site:"localtest123",host:"http://127.0.0.1:1",appUrl:"https://fixture.invalid"),
            appVersion:"0.5.9-beta.3",transport:transport,disabled:{false})
        let controller=AppDiagnostics(reporter:reporter);await controller.load()
        return (reporter,controller,transport)
    }
    private func waitForFailure(_ phone:PhoneModels,id:String,key:String="mobile.local.download_failed") async throws {
        let deadline=ContinuousClock.now.advanced(by:.seconds(5))
        while phone.downloadFailures[id]==nil || phone.progress[id] != nil,ContinuousClock.now<deadline {await Task.yield()}
        #expect(phone.downloadFailures[id]?.key==key)
        #expect(phone.paused.contains(id))
    }

    @Test(.timeLimit(.minutes(1)),arguments:["recovered-200","fresh-200","recovered-206","fresh-206"])
    func checksumFailureRetainsTheResponseOrigin(_ scenario:String) async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-checksum-origin-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        let recovered=scenario.hasPrefix("recovered")
        let receipt=try RestoredModelReceiptTransport(root:root,held:!recovered,status:scenario.hasSuffix("200") ? 200 : 206,payload:"broken!")
        await controller.setConsent(true)
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in Int64.max})
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,owner in controller.captureObservedFailure(error,observation:owner,stage:stage)}
        phone.download(model)
        await receipt.gate?.release()
        try await waitForFailure(phone,id:model.id,key:"mobile.local.checksum_failed")
        await reporter.flush()
        #expect(sink.sends==(recovered ? 0 : 1))
        await reporter.close()
    }

    @Test(.timeLimit(.minutes(1)),arguments:["off-on","regrant","same-grant","withdrawn","restored"])
    func actualStallLoopKeepsTheDownloadStartPermission(_ scenario:String) async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-stall-owner-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        if scenario != "off-on" {await controller.setConsent(true)}
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in Int64.max})
        if scenario=="restored" {
            let intent:[[String:Any]]=[["id":model.id,"pin":try phoneModelPin(model),"mobile":true,"paused":false]]
            try JSONSerialization.data(withJSONObject:intent).write(to:storeRoot.appendingPathComponent(".download-queue.json"))
        }
        let clock=ModelStallTestClock(),receipt=try RestoredModelReceiptTransport(root:root,held:true)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt,
            downloadMonitorSample:{(clock.now,true,true)},downloadMonitorSleep:{try await clock.sleep()})
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.diagnosticObservation={(controller.collecting,controller.observationRevision,600_000)}
        phone.onDiagnosticStall={controller.captureStall(observation:$0)}
        if scenario != "restored" {phone.download(model)}
        await receipt.waitForEntry();await clock.waitForSleep()
        clock.wake();await clock.waitForSleep()
        if scenario=="regrant" || scenario=="withdrawn" {await controller.setConsent(false)}
        if scenario=="off-on" || scenario=="regrant" {await controller.setConsent(true)}
        clock.wake();await clock.waitForSleep()
        clock.wake(by:600_001);await clock.waitForSleep()
        await reporter.flush()
        #expect(sink.sends==(scenario=="same-grant" ? 1 : 0))
        phone.pause(model);await receipt.gate?.release();clock.wake(cancelled:true)
        let deadline=ContinuousClock.now.advanced(by:.seconds(5))
        while phone.progress[model.id] != nil,ContinuousClock.now<deadline {await Task.yield()}
        #expect(phone.progress[model.id]==nil)
        await reporter.close()
    }
    @Test(.timeLimit(.minutes(1)))
    func downloadNowReplacesTheRunningTransferAndKeepsItsEntries() async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-download-now-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in Int64.max})
        let receipt=try RestoredModelReceiptTransport(root:root,held:true)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.download(model);await receipt.waitForEntry()
        phone.downloadNow(model);await receipt.waitForEntry()
        // The cancelled first task must not clear the replacement's entries.
        for _ in 0..<50 {await Task.yield()}
        #expect(phone.progress[model.id] != nil)
        #expect(receipt.freshTransfers >= 2)
        phone.pause(model);await receipt.gate?.release()
    }
    @Test(.timeLimit(.minutes(1)),arguments:[false,true])
    func restoredPermanentReceiptStaysDisplayOnlyUnderANewGrant(explicitBeforeReconcile:Bool) async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-restored-receipt-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in Int64.max})
        let intent:[[String:Any]]=[["id":model.id,"pin":try phoneModelPin(model),"mobile":true,"paused":false]]
        try JSONSerialization.data(withJSONObject:intent).write(to:storeRoot.appendingPathComponent(".download-queue.json"))
        let receipt=try RestoredModelReceiptTransport(root:root)
        await controller.setConsent(true)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,owner in controller.captureObservedFailure(error,observation:owner,stage:stage)}
        // This runs synchronously before the initializer's queued Task can
        // reconcile/await the store, exercising the competing Resume action.
        if explicitBeforeReconcile {phone.download(model)}
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(sink.sends==0,"Replaying an old receipt is not a newly observed transfer failure")
        #expect(receipt.freshTransfers==0)
        #expect(phone.error?.key=="mobile.local.download_failed")
        phone.download(model)
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(receipt.freshTransfers==1)
        #expect(sink.sends==1,"A new explicit transfer may use its own current grant")
        await reporter.close()
    }
    @Test(.timeLimit(.minutes(1)),arguments:["off-on","regrant","same-grant"])
    func freshTransferRetainsThePermissionObservedBeforeItsAwait(_ scenario:String) async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-live-transfer-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        if scenario != "off-on" {await controller.setConsent(true)}
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in Int64.max})
        let receipt=try RestoredModelReceiptTransport(root:root,held:true)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,owner in controller.captureObservedFailure(error,observation:owner,stage:stage)}
        phone.download(model)
        await receipt.waitForEntry()
        if scenario=="regrant" {await controller.setConsent(false)}
        if scenario != "same-grant" {await controller.setConsent(true)}
        await receipt.gate?.release()
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(sink.sends==(scenario=="same-grant" ? 1 : 0))
        #expect(receipt.freshTransfers==1)
        await reporter.close()
    }

    @Test(.timeLimit(.minutes(1))) func pausedUnknownReceiptCannotBorrowConsentOnResume() async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-unknown-pause-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        let held=RestoredModelCapacityGate();defer {held.finish()}
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in held.capacity()})
        let intent:[[String:Any]]=[["id":model.id,"pin":try phoneModelPin(model),"mobile":true,"paused":false]]
        try JSONSerialization.data(withJSONObject:intent).write(to:storeRoot.appendingPathComponent(".download-queue.json"))
        let receipt=try RestoredModelReceiptTransport(root:root)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,owner in controller.captureObservedFailure(error,observation:owner,stage:stage)}
        await held.waitForEntry()
        phone.pause(model)
        await controller.setConsent(true)
        held.finish()
        let deadline=ContinuousClock.now.advanced(by:.seconds(5))
        while phone.progress[model.id] != nil,ContinuousClock.now<deadline {await Task.yield()}
        #expect(phone.progress[model.id]==nil)
        #expect(FileManager.default.fileExists(atPath:receipt.body.path))
        phone.download(model)
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(sink.sends==0)
        await reporter.close()
    }

    @Test(.timeLimit(.minutes(1))) func preflightFailureCannotGiveTheUnconsumedReceiptANewGrant() async throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("phone-unknown-preflight-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:root)}
        let (model,bundle)=try fixture(root:root)
        let (reporter,controller,sink)=try await reports(root:root)
        let capacity=RestoredModelFirstCapacity()
        let storeRoot=root.appendingPathComponent("models"),store=try ModelStore(root:storeRoot,available:{_ in capacity.capacity()})
        let intent:[[String:Any]]=[["id":model.id,"pin":try phoneModelPin(model),"mobile":true,"paused":false]]
        try JSONSerialization.data(withJSONObject:intent).write(to:storeRoot.appendingPathComponent(".download-queue.json"))
        let receipt=try RestoredModelReceiptTransport(root:root)
        let phone=PhoneModels(bundle:bundle,modelStore:(storeRoot,store),downloadTransport:receipt)
        let previous=phone.allowMobileData;phone.allowMobileData=true
        defer {phone.allowMobileData=previous}
        phone.diagnosticFailureObservation={controller.failureObservation}
        phone.onFailure={stage,error,owner in controller.captureObservedFailure(error,observation:owner,stage:stage)}
        let deadline=ContinuousClock.now.advanced(by:.seconds(5))
        while phone.downloadFailures[model.id]==nil || phone.progress[model.id] != nil,ContinuousClock.now<deadline {await Task.yield()}
        #expect(phone.downloadFailures[model.id]?.key=="mobile.local.storage_required")
        #expect(FileManager.default.fileExists(atPath:receipt.body.path))
        await controller.setConsent(true)
        phone.download(model)
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(sink.sends==0)
        phone.download(model)
        try await waitForFailure(phone,id:model.id)
        await reporter.flush()
        #expect(receipt.freshTransfers==1)
        #expect(sink.sends==1)
        await reporter.close()
    }
}
#endif
