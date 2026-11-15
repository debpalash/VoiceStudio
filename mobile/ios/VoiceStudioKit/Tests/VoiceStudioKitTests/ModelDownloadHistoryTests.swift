import Foundation
import CryptoKit
import Testing
@testable import VoiceStudioKit

private actor ModelHistoryTransport:HTTPTransport {
    let root:URL
    let fallbackSuccess:Bool
    let historical:Bool
    private(set) var hosts:[String]=[]
    init(root:URL,fallbackSuccess:Bool,historical:Bool) {self.root=root;self.fallbackSuccess=fallbackSuccess;self.historical=historical}
    func send(_ request:URLRequest) async throws -> HTTPReply {throw ClientError("fixture.unused")}
    func download(_ request:URLRequest) async throws -> DownloadReply {
        hosts.append(request.url!.host!)
        let success=fallbackSuccess && request.url?.host=="hf-mirror.com"
        let file=root.appendingPathComponent("reply-\(hosts.count)")
        try Data((success ? "fixture" : "missing").utf8).write(to:file)
        return DownloadReply(file:file,status:success ? 200 : 404,headers:[:],origin:historical && !success ? .recovered : .currentRequest)
    }
}
private func modelHistoryFixture()->PhoneModel {
    let hash=SHA256.hash(data:Data("fixture".utf8)).map {String(format:"%02x",$0)}.joined()
    return PhoneModel(id:"history-model",name:"Fixture",capability:.tts,runtime:.sherpa,
        repo:"fixture/model",revision:String(repeating:"b",count:40),
        files:[PhoneModel.File(path:"model.bin",size:7,sha256:hash,role:"model")],
        total_bytes:7,languages:["en"],min_ram_gb:1,
        licence:PhoneModel.Licence(id:"fixture",category:"commercial-ok",requires_acceptance:false,credit:"Fixture",rights_holder:"Fixture",terms_fingerprint:String(repeating:"a",count:64),terms:nil),
        config:["model_type":.string("kitten")])
}

@Test func historicalPermanentResponseStillAllowsTheNormalMirrorFallback() async throws {
    let root=FileManager.default.temporaryDirectory.appendingPathComponent("model-history-fallback-"+UUID().uuidString)
    defer {try? FileManager.default.removeItem(at:root)}
    let store=try ModelStore(root:root,available:{_ in Int64.max})
    let transport=ModelHistoryTransport(root:root,fallbackSuccess:true,historical:true)
    let model=modelHistoryFixture()
    try await store.download(model,wifi:{true},transport:transport)
    #expect(await store.installed(model))
    #expect(await transport.hosts==["huggingface.co","hf-mirror.com"])
}

@Test(arguments:[false,true]) func downloadFailureKeepsItsLocalDisplayContractAndOrigin(_ historical:Bool) async throws {
    let root=FileManager.default.temporaryDirectory.appendingPathComponent("model-history-failure-"+UUID().uuidString)
    defer {try? FileManager.default.removeItem(at:root)}
    let store=try ModelStore(root:root,available:{_ in Int64.max})
    let transport=ModelHistoryTransport(root:root,fallbackSuccess:false,historical:historical)
    do {
        try await store.download(modelHistoryFixture(),wifi:{true},transport:transport)
        Issue.record("Expected permanent download refusal")
    } catch let error as HistoricalModelDownloadFailure {
        #expect(historical);#expect(error.failure.key=="mobile.local.download_failed")
    } catch let error as ClientError {
        #expect(!historical);#expect(error.key=="mobile.local.download_failed")
    }
    #expect(await transport.hosts==["huggingface.co","hf-mirror.com"])
}
