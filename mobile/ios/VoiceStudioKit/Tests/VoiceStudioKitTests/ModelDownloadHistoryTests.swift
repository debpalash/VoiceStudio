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
private func modelHistoryFixture(size:Int64=7,hash suppliedHash:String?=nil)->PhoneModel {
    let hash=suppliedHash ?? SHA256.hash(data:Data("fixture".utf8)).map {String(format:"%02x",$0)}.joined()
    return PhoneModel(id:"history-model",name:"Fixture",capability:.tts,runtime:.sherpa,
        repo:"fixture/model",revision:String(repeating:"b",count:40),
        files:[PhoneModel.File(path:"model.bin",size:size,sha256:hash,role:"model")],
        total_bytes:size,languages:["en"],min_ram_gb:1,
        licence:PhoneModel.Licence(id:"fixture",category:"commercial-ok",requires_acceptance:false,credit:"Fixture",rights_holder:"Fixture",terms_fingerprint:String(repeating:"a",count:64),terms:nil),
        config:["model_type":.string("kitten")])
}

private actor ModelChecksumRangeTransport:HTTPTransport {
    let root:URL
    let total:Int64
    let origin:ModelDownloadOrigin
    let preferredRangeBytes:Int64
    private(set) var calls=0
    init(root:URL,total:Int64,origin:ModelDownloadOrigin) {
        self.root=root;self.total=total;self.origin=origin
        preferredRangeBytes=total>7 ? 16_777_216 : 4_194_304
    }
    func send(_ request:URLRequest) async throws -> HTTPReply {throw ClientError("fixture.unused")}
    func download(_ request:URLRequest) async throws -> DownloadReply {
        calls+=1
        let range=try #require(request.value(forHTTPHeaderField:"Range"))
        let bounds=range.dropFirst(6).split(separator:"-").compactMap {Int64($0)}
        #expect(bounds.count==2)
        let file=root.appendingPathComponent("range-"+UUID().uuidString)
        try Data([1]).write(to:file)
        let handle=try FileHandle(forWritingTo:file)
        try handle.truncate(atOffset:UInt64(bounds[1]-bounds[0]+1));try handle.close()
        return DownloadReply(file:file,status:206,headers:["content-range":"bytes \(bounds[0])-\(bounds[1])/\(total)"],origin:origin)
    }
}

@Test(arguments:["fresh-range","recovered-range","disk-prefix","disk-parallel","fresh-parallel"])
func rangeChecksumRetainsExistingAndRecoveredByteLineage(_ scenario:String) async throws {
    let root=FileManager.default.temporaryDirectory.appendingPathComponent("model-checksum-ranges-"+UUID().uuidString)
    defer {try? FileManager.default.removeItem(at:root)}
    let parallel=scenario.hasSuffix("parallel")
    let model=parallel ? modelHistoryFixture(size:50_331_648,hash:String(repeating:"f",count:64)) : modelHistoryFixture()
    let store=try ModelStore(root:root,available:{_ in Int64.max})
    let part=root.appendingPathComponent(model.id+"/.partial/"+model.files[0].sha256)
    if scenario=="disk-prefix" {
        try FileManager.default.createDirectory(at:part.deletingLastPathComponent(),withIntermediateDirectories:true)
        try Data([1,1,1]).write(to:part)
    } else if scenario=="disk-parallel" {
        let ranges=part.appendingPathExtension("ranges")
        try FileManager.default.createDirectory(at:ranges,withIntermediateDirectories:true)
        let saved=ranges.appendingPathComponent("16777216-33554431")
        try Data([1]).write(to:saved)
        let handle=try FileHandle(forWritingTo:saved);try handle.truncate(atOffset:16_777_216);try handle.close()
    }
    let transport=ModelChecksumRangeTransport(root:root,total:model.total_bytes,origin:scenario=="recovered-range" ? .recovered : .currentRequest)
    do {
        try await store.download(model,wifi:{true},transport:transport)
        Issue.record("Expected checksum refusal")
    } catch let error as HistoricalModelDownloadFailure {
        #expect(!scenario.hasPrefix("fresh"));#expect(error.failure.key=="mobile.local.checksum_failed")
    } catch let error as ClientError {
        #expect(scenario.hasPrefix("fresh"));#expect(error.key=="mobile.local.checksum_failed")
    }
    #expect(await transport.calls>0)
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
