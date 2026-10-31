import Foundation
import CryptoKit
import Darwin
import Testing
import VSNative
@testable import VoiceStudioKit

private func localDirectory() throws -> URL {
    let url = repository.appendingPathComponent("mobile/ios/.generated/local-test-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}
private func localFixture(bytes: Data = Data("model".utf8), gated: Bool = false, capability: String = "tts", fingerprint: String = String(repeating: "a", count: 64), id: String = "fixture") throws -> PhoneModel {
    let runtime = capability == "clone" ? "omnivoice.cpp" : capability == "translate" ? "llama.cpp" : "sherpa-onnx"
    let object: [String: Any] = ["schema_version": 1, "models": [[
        "id": id, "name": "Fixture", "capability": capability, "runtime": runtime, "repo": "owner/model", "revision": String(repeating: "b", count: 40),
        "files": [["path": "model.bin", "size": bytes.count, "sha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(), "role": "model"]],
        "total_bytes": bytes.count, "languages": ["en"], "min_ram_gb": 1,
        "licence": ["id": "test", "category": gated ? "non-commercial" : "commercial-ok", "requires_acceptance": false, "credit": "Fixture", "rights_holder": "Fixture", "terms_fingerprint": fingerprint],
        "config": ["model_type": "kitten", "prompt_template": "Translate {text} from {source_language} to {target_language}"]
    ]]]
    return try ModelCatalogue.decode(JSONSerialization.data(withJSONObject: object)).models[0]
}
private actor RangeServer: HTTPTransport {
    let bytes: Data
    var failAfter: Int?
    var corrupted = false
    var slowAfter: Int?
    private(set) var requests: [URLRequest] = []
    let events: AsyncStream<Int>
    let continuation: AsyncStream<Int>.Continuation
    init(bytes: Data, failAfter: Int? = nil, slowAfter: Int? = nil, corrupted: Bool = false) {
        self.bytes = bytes; self.failAfter = failAfter; self.slowAfter = slowAfter; self.corrupted = corrupted
        (events, continuation) = AsyncStream.makeStream()
    }
    func finishEvents() { continuation.finish() }
    func send(_ request: URLRequest) async throws -> HTTPReply { throw ClientError("mobile.local.download_failed") }
    func download(_ request: URLRequest) async throws -> DownloadReply {
        requests.append(request); continuation.yield(requests.count)
        if let slowAfter, requests.count > slowAfter { try await Task.sleep(for: .seconds(30)) }
        if let failAfter, requests.count > failAfter { throw ClientError("mobile.local.download_failed") }
        let range = request.value(forHTTPHeaderField: "Range")!.dropFirst(6).split(separator: "-").compactMap { Int($0) }
        let start = range[0], end = min(range[1], bytes.count - 1)
        var chunk = bytes.subdata(in: start..<(end + 1)); if corrupted { chunk[0] ^= 1 }
        let path = repository.appendingPathComponent("mobile/ios/.generated/range-" + UUID().uuidString)
        try chunk.write(to: path)
        return DownloadReply(file: path, status: 206, headers: ["content-range": "bytes \(start)-\(end)/\(bytes.count)"])
    }
}

@Test func localCatalogueValidatesPinsAndTraversal() throws {
    let url = repository.appendingPathComponent("mobile/contract/models.json")
    let actual = try ModelCatalogue.decode(Data(contentsOf: url))
    #expect(actual.models.count >= 7)
    #expect(actual.models.allSatisfy { !$0.files.isEmpty })
    for capability in ["tts", "asr", "asr-stream", "clone", "translate"] { _ = try localFixture(capability: capability) }
    let good = try JSONEncoder().encode(localFixture())
    let malformed = String(decoding: good, as: UTF8.self).replacingOccurrences(of: "model.bin", with: "../outside")
    let wrapper = "{\"schema_version\":1,\"models\":[" + malformed + "]}"
    #expect(throws: (any Error).self) { try ModelCatalogue.decode(Data(wrapper.utf8)) }
}
@Test func localDownloadResumesAcrossStoreRelaunchAndFallsBack() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let bytes = Data(repeating: 17, count: 4_194_304 + 73)
    let model = try localFixture(bytes: bytes)
    let first = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    let failing = RangeServer(bytes: bytes, failAfter: 1)
    await #expect(throws: (any Error).self) { try await first.download(model, wifi: { true }, transport: failing) }
    #expect(await !first.installed(model))
    #expect(await failing.requests.last?.url?.host == "hf-mirror.com")
    let restored = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    let server = RangeServer(bytes: bytes)
    try await restored.download(model, wifi: { true }, transport: server)
    #expect(await server.requests.first?.value(forHTTPHeaderField: "Range") == "bytes=4194304-4194376")
    #expect(await restored.installed(model))
    let path = try await restored.authorize(model)
    #expect(try Data(contentsOf: path.appendingPathComponent("model.bin")) == bytes)
    #expect(getxattr(root.path, "com.apple.metadata:com_apple_backup_excludeItem", nil, 0, 0, 0) > 0)
    try await restored.delete(model)
    #expect(await !restored.installed(model))
}
@Test func localDownloadChecksumFailureNeverInstalls() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let model = try localFixture(); let store = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    await #expect(throws: (any Error).self) { try await store.download(model, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8), corrupted: true)) }
    #expect(await !store.installed(model))
    await #expect(throws: (any Error).self) { try await store.authorize(model) }
}
@Test func localDownloadWifiAndStorageStopBeforeNetwork() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let model = try localFixture(); let store = try ModelStore(root: root)
    let host = RangeServer(bytes: Data("model".utf8))
    await #expect(throws: (any Error).self) { try await store.download(model, wifi: { false }, transport: host) }
    #expect(await host.requests.isEmpty)
    let full = try ModelStore(root: root, available: { _ in 0 })
    await #expect(throws: (any Error).self) { try await full.download(model, wifi: { true }, transport: host) }
    #expect(await host.requests.isEmpty)
    try await store.download(model, policy: DownloadPolicy(allowMobileData: true), wifi: { false }, transport: host)
    #expect(await store.installed(model))
}
@Test func concurrentModelDownloadsReserveSpaceAndReleaseItOnCancellation() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let first = try localFixture(), second = try localFixture(id: "second")
    let store = try ModelStore(root: root, available: { _ in 16_777_216 + 7 })
    let blocked = RangeServer(bytes: Data("model".utf8), slowAfter: 0)
    let work = Task { try await store.download(first, wifi: { true }, transport: blocked) }
    defer { work.cancel() }
    for await count in blocked.events { if count == 1 { break } }
    let other = RangeServer(bytes: Data("model".utf8))
    await #expect(throws: (any Error).self) { try await store.download(second, wifi: { true }, transport: other) }
    #expect(await other.requests.isEmpty)
    work.cancel()
    await #expect(throws: CancellationError.self) { try await work.value }
    try await store.download(second, wifi: { true }, transport: other)
    #expect(await store.installed(second))
}
@Test func downloadReservationDoesNotCountPersistedRangesTwice() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let bytes = Data(repeating: 23, count: 4_194_304 + 8), model = try localFixture(bytes: bytes)
    let part = root.appendingPathComponent(model.id).appendingPathComponent(".partial/" + model.files[0].sha256)
    let store = try ModelStore(root: root, available: { _ in
        let written = (try? FileManager.default.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? 0
        return 16_777_216 + Int64(bytes.count) - written
    })
    try await store.download(model, wifi: { true }, transport: RangeServer(bytes: bytes))
    #expect(await store.installed(model))
}
@Test func installedModelRevalidationNeedsNoSpaceDuringAnotherDownload() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let capacity = root.appendingPathComponent("available")
    try Data("16777223".utf8).write(to: capacity)
    let store = try ModelStore(root: root, available: { _ in
        Int64(try String(contentsOf: capacity, encoding: .utf8))!
    })
    let installed = try localFixture(id: "installed"), pending = try localFixture()
    try await store.download(installed, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8)))
    let blocked = RangeServer(bytes: Data("model".utf8), slowAfter: 0)
    let work = Task { try await store.download(pending, wifi: { true }, transport: blocked) }
    defer { work.cancel() }
    for await count in blocked.events { if count == 1 { break } }
    try Data("0".utf8).write(to: capacity)
    let network = RangeServer(bytes: Data("model".utf8))
    try await store.download(installed, wifi: { false }, transport: network)
    #expect(await network.requests.isEmpty)
    work.cancel()
    await #expect(throws: CancellationError.self) { try await work.value }
}
@Test func localDownloadCancellationRetainsRangeForResume() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let bytes = Data(repeating: 19, count: 4_194_304 + 8)
    let model = try localFixture(bytes: bytes), store = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    let host = RangeServer(bytes: bytes, slowAfter: 1)
    let work = Task {
        do { try await store.download(model, wifi: { true }, transport: host) }
        catch { await host.finishEvents(); throw error }
        await host.finishEvents()
    }
    for await count in host.events { if count == 2 { break } }
    work.cancel()
    await #expect(throws: CancellationError.self) { try await work.value }
    #expect(await !store.installed(model))
    let next = RangeServer(bytes: bytes)
    try await store.download(model, wifi: { true }, transport: next)
    #expect(await next.requests.first?.value(forHTTPHeaderField: "Range") == "bytes=4194304-4194311")
}
@Test func localLicenceGatePersistsWithdrawalAndTermsChanges() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let model = try localFixture(gated: true), store = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    let host = RangeServer(bytes: Data("model".utf8))
    await #expect(throws: (any Error).self) { try await store.download(model, wifi: { true }, transport: host) }
    #expect(await host.requests.isEmpty)
    try await store.accept(model, fingerprint: model.licence.terms_fingerprint, appVersion: "test-version", accepted: true)
    try await store.download(model, wifi: { true }, transport: host)
    _ = try await store.authorize(model)
    let restored = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    #expect(await restored.allowed(model))
    #expect(await restored.acceptance(model)?.appVersion == "test-version")
    let changed = try localFixture(gated: true, fingerprint: String(repeating: "c", count: 64))
    #expect(await !restored.allowed(changed))
    try await restored.accept(model, fingerprint: model.licence.terms_fingerprint, appVersion: "test-version", accepted: false)
    await #expect(throws: (any Error).self) { try await restored.authorize(model) }
    let engine = NativeLocalEngine(store: restored)
    await #expect(throws: (any Error).self) { try await engine.synthesize(model: model, text: "Speech") }
}
@Test func localLoadVerifiesBytesAndProtectsActiveLease() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let model = try localFixture(), store = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    try await store.download(model, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8)))
    let path = try await store.acquire(model)
    await #expect(throws: (any Error).self) { try await store.delete(model) }
    await store.release(model)
    try Data("other".utf8).write(to: path.appendingPathComponent("model.bin"))
    await #expect(throws: (any Error).self) { try await store.authorize(model) }
    try await store.delete(model)
}
@Test func localLowRamRecommendationAllowsVerifiedDownloadAndAcquire() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let model = try localFixture(), store = try ModelStore(root: root, ram: 1)
    let host = RangeServer(bytes: Data("model".utf8))
    #expect(!model.fits(ram: 1))
    try await store.download(model, wifi: { true }, transport: host)
    #expect(await host.requests.count == 1)
    let path = try await store.acquire(model)
    #expect(try Data(contentsOf: path.appendingPathComponent("model.bin")) == Data("model".utf8))
    await store.release(model)
}
@Test func localTaskRoutesWithoutAComputer() {
    #expect(LocalRouting.route(preference: nil, installed: true, paired: false) == .phone)
    #expect(LocalRouting.route(preference: nil, installed: false, paired: false) == .download)
    #expect(LocalRouting.route(preference: nil, installed: false, paired: true) == .computer)
    #expect(LocalRouting.route(preference: nil, installed: true, paired: true) == .phone)
    #expect(LocalRouting.route(preference: .phone, installed: false, paired: true) == .download)
    #expect(LocalRouting.route(preference: .computer, installed: true, paired: false) == .phone)
    #expect(LocalRouting.route(preference: .computer, installed: false, paired: false) == .download)
}
@Test func localRedirectsStayWithinModelDeliveryOrigins() {
    for url in ["https://huggingface.co/resolve", "https://cdn-lfs.hf.co/model", "https://cas-bridge.xethub.hf.co/model", "https://hf-mirror.com/model", "https://cdn-lfs.hf-mirror.com/model"] { #expect(ModelRedirectDelegate.allowed(URL(string: url)!)) }
    for url in ["http://huggingface.co/model", "https://huggingface.co.evil.test/model", "https://hf-mirror.com:8443/model", "https://example.org/model"] { #expect(!ModelRedirectDelegate.allowed(URL(string: url)!)) }
    var credentials = URLComponents()
    credentials.scheme = "https"; credentials.host = "huggingface.co"; credentials.path = "/model"
    credentials.user = "user"; credentials.password = "password"
    #expect(!ModelRedirectDelegate.allowed(credentials.url!))
}
@Test func localSyntheticAudioHasValidRiffSizeAndMark() throws {
    let bytes = try LocalAudio(samples: [0, 0.5, -0.5], sampleRate: 24000).markSynthetic()
    let size = bytes[4..<8].enumerated().reduce(UInt32(0)) { $0 | UInt32($1.element) << (8 * $1.offset) }
    #expect(Int(size) == bytes.count - 8)
    #expect(bytes.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    #expect(try PCMChunk(pcm16: Data([0,128,255,127])).samples == [-1, Float(32767) / 32768])
}
@Test func localCloneAndTranslateValidateInputsAndRuntimeBoundary() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try ModelStore(root: root, available: { _ in 100_000_000_000 }), engine = NativeLocalEngine(store: store)
    let clone = try localFixture(capability: "clone"), translate = try localFixture(capability: "translate")
    #if !os(iOS)
    // iOS links the runtimes statically, so a missing library path cannot make them unavailable there.
    #expect(!NativeLocalEngine.runtimeAvailable(.omni, library: "/does-not-exist"))
    #expect(!NativeLocalEngine.runtimeAvailable(.llama, library: "/does-not-exist"))
    #else
    #expect(NativeLocalEngine.runtimeAvailable(.omni))
    #expect(NativeLocalEngine.runtimeAvailable(.llama))
    #endif
    await #expect(throws: (any Error).self) { try await engine.clone(model: clone, text: "Hello", reference: PCMChunk(samples: [0], sampleRate: 16000), referenceText: "Reference") }
    try await store.download(translate, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8)))
    await #expect(throws: (any Error).self) { try await engine.translate(model: translate, text: "Hello", sourceLanguage: "en", targetLanguage: "fr") }
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_INFERENCE"] == "1"))
func realSherpaSynthesizesAndTranscribesOffline() async throws {
    let catalogue = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json")))
    let tts = try #require(catalogue.models.first { $0.id == "kitten-mini-en-fp16" })
    let asr = try #require(catalogue.models.first { $0.id == "whisper-tiny-int8" })
    let streaming = try #require(catalogue.models.first { $0.id == "zipformer-en-20m-int8" })
    let library = try #require(ProcessInfo.processInfo.environment["SHERPA_ONNX_LIBRARY"])
    #expect(NativeLocalEngine.runtimeAvailable(.sherpa, library: library))
    let store = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    // Isolated test fixtures. No app acceptance is granted and no network is used here.
    for model in [tts, asr] { try await store.accept(model, fingerprint: model.licence.terms_fingerprint, appVersion: "inference-test", accepted: true) }
    let engine = NativeLocalEngine(store: store, libraries: [.sherpa: library])
    let audio = try await engine.synthesize(model: tts, text: "The quick brown fox jumps over the lazy dog.")
    #expect(audio.samples.count > audio.sampleRate)
    #expect(audio.samples.contains { abs($0) > 0.01 })
    let pcm = PCMChunk(samples: audio.samples, sampleRate: audio.sampleRate)
    let transcript = try await engine.transcribe(model: asr, audio: pcm)
    print("Whisper real inference:", transcript)
    #expect(transcript.lowercased().contains("fox"))
    let (chunks, feed) = AsyncThrowingStream<PCMChunk, any Error>.makeStream()
    let updates = try await engine.streamTranscribe(model: streaming, chunks: chunks)
    for start in stride(from: 0, to: audio.samples.count, by: 4800) { feed.yield(PCMChunk(samples: Array(audio.samples[start..<min(start + 4800, audio.samples.count)]), sampleRate: audio.sampleRate)) }
    feed.finish()
    var last: TranscriptUpdate?
    for try await update in updates { last = update }
    print("Zipformer real inference:", last?.text ?? "")
    #expect(last?.finished == true)
    #expect(last?.text.lowercased().contains("dog") == true)
    let (events, signal) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
    let generation = Task {
        defer { signal.finish() }
        return try await engine.synthesize(model: tts, text: String(repeating: "The quick brown fox jumps over the lazy dog. ", count: 20), progress: { value in
            if value.stage == .running && value.fraction > 0 { signal.yield(()) }
        })
    }
    var observedProgress = false
    for await _ in events { observedProgress = true; generation.cancel(); break }
    #expect(observedProgress)
    await #expect(throws: CancellationError.self) { try await generation.value }
    // Cancel a real recognizer while its progress callback is suspended. Native
    // handles still exist here, so neither another model nor deletion may proceed.
    let held = NativeWorkGate(), nextEntered = NativeWorkGate()
    defer { held.finish.signal(); nextEntered.finish.signal() }
    let (pendingChunks,pendingFeed) = AsyncThrowingStream<PCMChunk,any Error>.makeStream()
    let pendingUpdates = try await engine.streamTranscribe(model: streaming,chunks: pendingChunks,progress: { value in
        if value.stage == .running { held.run() }
    })
    let consumer = Task { for try await _ in pendingUpdates {} }
    defer { consumer.cancel(); pendingFeed.finish() }
    pendingFeed.yield(PCMChunk(samples: Array(audio.samples.prefix(4800)),sampleRate: audio.sampleRate))
    let deadline = ContinuousClock.now + .seconds(10)
    while held.count == 0 && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
    try #require(held.count == 1)
    consumer.cancel(); _ = await consumer.result
    let next = Task { try await engine.execute(tts,capability: .tts,progress: { _ in }) { _,_,_,_ in nextEntered.run(); return true } }
    defer { next.cancel() }
    try await Task.sleep(for: .milliseconds(150))
    #expect(nextEntered.count == 0)
    await #expect(throws: (any Error).self) { try await store.delete(streaming) }
    held.finish.signal()
    while nextEntered.count == 0 && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
    try #require(nextEntered.count == 1)
    nextEntered.finish.signal()
    #expect(try await next.value)

}

@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_NATIVE"] == "1"))
func realNativePinnedRuntimesAreLoadable() throws {
    for (runtime, key) in [(ModelRuntime.omni, "OMNIVOICE_LIBRARY"), (.llama, "LLAMA_LIBRARY")] {
        let library = try #require(ProcessInfo.processInfo.environment[key])
        #expect(NativeLocalEngine.runtimeAvailable(runtime, library: library))
    }
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_AUX"] == "1"))
func realAuxiliaryCloneAndTranslation() async throws {
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let tts = try #require(models.first { $0.id == "kitten-mini-en-fp16" })
    let clone = try #require(models.first { $0.capability == .clone })
    let translate = try #require(models.first { $0.capability == .translate })
    let env = ProcessInfo.processInfo.environment
    let store = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    for m in [tts, clone] { try await store.accept(m, fingerprint: m.licence.terms_fingerprint, appVersion: "inference-test", accepted: true) }
    let engine = NativeLocalEngine(store: store, libraries: [.sherpa: try #require(env["SHERPA_ONNX_LIBRARY"]), .omni: try #require(env["OMNIVOICE_LIBRARY"]), .llama: try #require(env["LLAMA_LIBRARY"])])
    let referenceText = "The quick brown fox jumps over the lazy dog."
    let reference = try await engine.synthesize(model: tts, text: referenceText)
    let audio = try await engine.clone(model: clone, text: "Hello world.", reference: PCMChunk(samples: reference.samples, sampleRate: reference.sampleRate), referenceText: referenceText)
    #expect(audio.sampleRate == 24000)
    #expect(audio.samples.count > 1000)
    #expect(audio.samples.contains { abs($0) > 0.001 })
    let translated = try await engine.translate(model: translate, text: "Hello, how are you?", sourceLanguage: "English", targetLanguage: "French")
    print("Real translation:", translated)
    #expect(translated.lowercased().contains("bonjour"))
}

#if os(macOS)
private final class LoopbackRangeHost {
    let process = Process()
    let port: Int
    init() throws {
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", "-u", "-c", #"""
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *args): pass
    def do_GET(self):
        Handler.calls += 1
        if Handler.calls in (2, 3):
            self.send_response(503); self.send_header('Content-Length', '0'); self.end_headers(); return
        start, end = map(int, self.headers['Range'][6:].split('-'))
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {start}-{end}/4194313')
        self.send_header('Content-Length', str(end-start+1))
        self.end_headers(); self.wfile.write(bytes([17])*(end-start+1))
server = HTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
"""#]
        process.standardOutput = output
        try process.run()
        var line = Data()
        while let byte = try output.fileHandleForReading.read(upToCount: 1), !byte.isEmpty { if byte == Data([10]) { break }; line.append(byte) }
        guard let port = Int(String(decoding: line, as: UTF8.self)) else { process.terminate(); throw ClientError("mobile.local.download_failed") }
        self.port = port
    }
    deinit { if process.isRunning { process.terminate(); process.waitUntilExit() } }
}
private struct LoopbackRangeTransport: HTTPTransport {
    let port: Int
    let transport = URLSessionTransport()
    func send(_ request: URLRequest) async throws -> HTTPReply { throw ClientError("mobile.local.download_failed") }
    func download(_ request: URLRequest) async throws -> DownloadReply {
        var local = request
        local.url = URL(string: "http://127.0.0.1:\(port)/model")!
        return try await transport.download(local)
    }
}
@Test func localDownloadResumesUsingRealURLSessionAndLoopbackServer() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let server = try LoopbackRangeHost()
    let transport = LoopbackRangeTransport(port: server.port)
    let bytes = Data(repeating: 17, count: 4_194_313)
    let model = try localFixture(bytes: bytes)
    let first = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    await #expect(throws: (any Error).self) { try await first.download(model, wifi: { true }, transport: transport) }
    #expect(await !first.installed(model))
    let restored = try ModelStore(root: root, available: { _ in 100_000_000_000 })
    try await restored.download(model, wifi: { true }, transport: transport)
    let path = try await restored.authorize(model)
    #expect(try Data(contentsOf: path.appendingPathComponent("model.bin")) == bytes)
}
#endif

@Test func modelProductNameIsRequiredAndSurvivesLanguageSelection() throws {
    let fixture = try localFixture()
    #expect(fixture.name == "Fixture")
    #expect(try fixture.selectingLanguage("en").name == fixture.name)
    let encoded = try JSONEncoder().encode(fixture)
    var object = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
    for invalid: String? in [nil, "", " \n "] {
        object["name"] = invalid
        let data = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "models": [object]])
        #expect(throws: (any Error).self) { try ModelCatalogue.decode(data) }
    }
}

@Test func downloadBatchRequiresEveryReceiptAndSkipsInstalledModels() throws {
    let first = try localFixture(gated: true)
    var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(first)) as! [String: Any]
    object["id"] = "second"; object["name"] = "Second"
    let second = try ModelCatalogue.decode(JSONSerialization.data(withJSONObject: ["schema_version": 1, "models": [object]])).models[0]
    var queue = ModelDownloadQueue([first, second, first])
    // Repeated calls cannot skip an unaccepted model (including persistence failure).
    for _ in 0..<2 {
        guard case .licence(let item) = queue.next(installed: [], accepted: []) else { Issue.record("Missing first licence gate"); return }
        #expect(item.id == first.id)
    }
    guard case .download(let item) = queue.next(installed: [], accepted: [first.id]) else { Issue.record("Missing first download"); return }
    #expect(item.id == first.id)
    guard case .licence(let item) = queue.next(installed: [], accepted: [first.id]) else { Issue.record("Missing second licence gate"); return }
    #expect(item.id == second.id)
    // Accepting an installed model authorizes use without downloading it again.
    #expect(queue.next(installed: [second.id], accepted: [first.id, second.id]) == nil)
    var permissive = ModelDownloadQueue([try localFixture()])
    guard case .download = permissive.next(installed: [], accepted: []) else { Issue.record("Permissive model was gated"); return }
    #expect(permissive.next(installed: [], accepted: []) == nil)
}

private final class HashCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    var count: Int { lock.withLock { value } }
    func hash(_ url: URL) throws -> String { lock.withLock { value += 1 }; return try ModelHash.file(url) }
}
@Test func reviewVerificationCachesInstallAndInvalidatesOnChangesAndNativeFailure() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let counter = HashCounter(), model = try localFixture()
    let store = try ModelStore(root: root,fileHash: { try counter.hash($0) },available: { _ in 100_000_000 })
    try await store.download(model,wifi: { true },transport: RangeServer(bytes: Data("model".utf8)))
    #expect(counter.count == 1)
    let path = try await store.authorize(model)
    _ = try await store.authorize(model)
    #expect(counter.count == 1)
    try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(10)],ofItemAtPath: path.appendingPathComponent("model.bin").path)
    _ = try await store.authorize(model)
    #expect(counter.count == 2)
    let changed = try localFixture(fingerprint: String(repeating: "d",count: 64))
    _ = try await store.authorize(changed)
    #expect(counter.count == 3)
    let engine = NativeLocalEngine(store: store,libraries: [.sherpa: "/missing-runtime"])
    await #expect(throws: (any Error).self) { try await engine.synthesize(model: changed,text: "Hello") }
    _ = try await store.authorize(changed)
    #expect(counter.count == 4)
    let oldDate = try path.appendingPathComponent("model.bin").resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate!
    try Data("other".utf8).write(to: path.appendingPathComponent("model.bin"))
    try FileManager.default.setAttributes([.modificationDate: oldDate.addingTimeInterval(10)],ofItemAtPath: path.appendingPathComponent("model.bin").path)
    await #expect(throws: (any Error).self) { try await store.authorize(changed) }
    #expect(counter.count == 5)
}
@Test func reviewResumeSkipsVerifiedCompletedFileForStorage() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let bytes = Data("model".utf8), base = try localFixture(bytes: bytes)
    var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(base)) as! [String: Any]
    var files = json["files"] as! [[String: Any]]
    var second = files[0]; second["path"] = "second.bin"; files.append(second)
    json["files"] = files; json["total_bytes"] = bytes.count*2
    let model = try ModelCatalogue.decode(JSONSerialization.data(withJSONObject: ["schema_version": 1,"models": [json]])).models[0]
    let directory = root.appendingPathComponent(model.id); try PrivateFiles.directory(directory)
    try bytes.write(to: directory.appendingPathComponent("model.bin"))
    let store = try ModelStore(root: root,available: { _ in 16_777_216 + 5 }), host = RangeServer(bytes: bytes)
    try await store.download(model,wifi: { true },transport: host)
    #expect(await host.requests.count == 1)
    #expect(await host.requests.first?.url?.lastPathComponent == "second.bin")
    #expect(await store.installed(model))
}
@Test func reviewPCM16HandlesNonZeroDataStartIndex() throws {
    let bytes = Data([99,99,0,128,255,127]).dropFirst(2)
    #expect(bytes.startIndex == 2)
    #expect(try PCMChunk(pcm16: bytes).samples == [-1,Float(32767)/32768])
    #expect(throws: ClientError.self) { try PCMChunk(pcm16: bytes.dropFirst()) }
}

private final class NativeWorkGate: @unchecked Sendable {
    let finish = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var calls = 0
    var count: Int { lock.withLock { calls } }
    func run() { lock.withLock { calls += 1 }; finish.wait() }
}
// These tests deliberately hold the process-wide native permit. Run them in
// sequence so one test cannot consume another test's worker-start deadline.
// Startup budgets allow busy CI hosts; the cancellation assertions remain immediate.
@Suite(.serialized) struct NativeLeaseRegressionTests {
    @Test func reviewCancelledExecuteKeepsBothLeasesUntilWorkerCleanup() async throws {
        let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
        let model = try localFixture(), store = try ModelStore(root: root,available: { _ in 100_000_000 })
        try await store.download(model,wifi: { true },transport: RangeServer(bytes: Data("model".utf8)))
        let engine = NativeLocalEngine(store: store), first = NativeWorkGate(), second = NativeWorkGate()
        defer { first.finish.signal(); second.finish.signal() }
        let work = Task { try await engine.execute(model,capability: .tts,progress: { _ in }) { _,_,_,_ in first.run(); return 1 } }
        defer { work.cancel() }
        let firstDeadline = ContinuousClock.now + .seconds(60)
        while first.count == 0 && ContinuousClock.now < firstDeadline { try await Task.sleep(for: .milliseconds(5)) }
        try #require(first.count == 1)
        work.cancel()
        let next = Task { try await engine.execute(model,capability: .tts,progress: { _ in }) { _,_,_,_ in second.run(); return 2 } }
        defer { next.cancel() }
        try await Task.sleep(for: .milliseconds(150))
        #expect(second.count == 0)
        await #expect(throws: (any Error).self) { try await store.delete(model) }
        first.finish.signal()
        await #expect(throws: CancellationError.self) { try await work.value }
        let secondDeadline = ContinuousClock.now + .seconds(60)
        while second.count == 0 && ContinuousClock.now < secondDeadline { try await Task.sleep(for: .milliseconds(5)) }
        try #require(second.count == 1)
        second.finish.signal()
        #expect(try await next.value == 2)
        try await store.delete(model)
    }
}

@Test func reviewCompactErrorsRetainLocalizedRecoveryAndConnectionCause() throws {
    let l10n = try L10n(locale: "en",catalogs: catalogs())
    for key in ["wifi_required","storage_required","ram_required","licence_required","checksum_failed","runtime_failed","inference_failed"] {
        let error = ClientError("mobile.local."+key,params: ["ram": "6"])
        let presentation = ErrorPresentation(error,l10n: l10n)
        #expect(presentation.title == l10n.t("mobile.simple.models_error"))
        #expect(presentation.detail == l10n.t(error.key,error.params))
        #expect(presentation.detail?.contains("{{") == false)
    }
    let active = ErrorPresentation(ClientError("sidebarTools.wait"),l10n: l10n)
    #expect(active.detail == l10n.t("sidebarTools.wait"))
    for key in ["mobile.connect.error_unreachable","mobile.connect.error_invalid_url","mobile.connect.error_api_key"] {
        let presentation = ErrorPresentation(ClientError(key,params: ["host": "192.168.1.2"]),l10n: l10n,titleKey: "mobile.simple.connection_error")
        #expect(presentation.detail == l10n.t(key,["host": "192.168.1.2"]))
        #expect(presentation.detail?.contains("{{") == false)
    }
}

extension NativeLeaseRegressionTests {
    @Test func reviewQueuedNativeWorkReverifiesAfterPriorNativeFailure() async throws {
        let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
        let model = try localFixture(), store = try ModelStore(root: root,available: { _ in 100_000_000 })
        try await store.download(model,wifi: { true },transport: RangeServer(bytes: Data("model".utf8)))
        let path = try await store.authorize(model), held = NativeWorkGate(), engine = NativeLocalEngine(store: store)
        defer { held.finish.signal() }
        let first = Task { try await engine.execute(model,capability: .tts,progress: { _ in }) { _,_,_,_ -> Bool in
            held.run(); throw ClientError("mobile.local.inference_failed")
        } }
        let deadline = ContinuousClock.now + .seconds(60)
        while held.count == 0 && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
        try #require(held.count == 1)
        let queued = Task { try await engine.execute(model,capability: .tts,progress: { _ in }) { _,_,_,_ in true } }
        defer { first.cancel(); queued.cancel() }
        try await Task.sleep(for: .milliseconds(100))
        let file = path.appendingPathComponent("model.bin")
        let modified = try file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate!
        try Data("other".utf8).write(to: file)
        try FileManager.default.setAttributes([.modificationDate: modified],ofItemAtPath: file.path)
        held.finish.signal()
        await #expect(throws: ClientError.self) { try await first.value }
        await #expect(throws: ClientError("mobile.local.checksum_failed")) { try await queued.value }
    }
}

@Test func pausesSplitOneWindowIntoTimedUtterances() {
    let tokens = [" Hello"," there","."," Much"," later","."]
    let ts: [Double] = [0.5,0.9,1.2,9.0,9.4,9.8]
    let ends: [Double] = [0.8,1.2,1.4,9.3,9.7,10.0]
    let parts = splitUtterances(text: "Hello there. Much later.",tokens: tokens,timestamps: ts,ends: ends,window: 20)
    #expect(parts.map(\.text) == ["Hello there.","Much later."])
    #expect(parts[0].start == 0.5 && parts[1].start == 9.0)
    #expect(parts[0].end <= 9.0 && parts[1].end <= 20 && parts[1].end > parts[1].start)
    // 0.4 s after sentence-final punctuation is enough; mid-sentence needs 1.0 s.
    #expect(splitUtterances(text: "a. b",tokens: ["a","."," b"],timestamps: [0,0.3,1.0],ends: [0.3,0.5,1.3],window: 20).count == 2)
    #expect(splitUtterances(text: "a b c",tokens: ["a"," b"," c"],timestamps: [0,0.4,1.2],ends: [0.3,0.7,1.5],window: 20).count == 1)
}
@Test func continuousSpeechAndMismatchedTimestampsStayOneSegment() {
    #expect(splitUtterances(text: "a b c",tokens: ["a"," b"," c"],timestamps: [0,0.4,0.8],ends: [],window: 20).count == 1)
    let bad = splitUtterances(text: "a b",tokens: ["a"," b"],timestamps: [0],ends: [],window: 20)
    #expect(bad.count == 1 && bad[0].text == "a b")
}
@Test func rawPCMIsPlacedByTimestampWithJitterTolerance() {
    #expect(timelineShift(timestamp: 1,written: 0) == 24000)
    #expect(timelineShift(timestamp: 1.5,written: 12000) == 24000)
    #expect(timelineShift(timestamp: 0.9,written: 24000) == -2400)
    #expect(timelineShift(timestamp: 1.004,written: 24000) == 0)
    #expect(timelineShift(timestamp: .nan,written: 500) == 0)
}
@Test func dubJobLockMakesResumeAndDeleteWaitForTheOldRun() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs")), id = UUID()
    try FileManager.default.createDirectory(at: try await store.directory(id),withIntermediateDirectories: true)
    try await store.acquire(id)
    let finished = Flag()
    let deleting = Task { try await store.delete(id); finished.set() }
    try await Task.sleep(for: .milliseconds(150))
    #expect(!finished.value)
    #expect(FileManager.default.fileExists(atPath: try await store.directory(id).path))
    await store.release(id)
    try await deleting.value
    #expect(!FileManager.default.fileExists(atPath: try await store.directory(id).path))
    // A different job is never blocked.
    try await store.acquire(UUID())
}
extension NativeLeaseRegressionTests {
    @Test func dictationMicrophoneWaitsForInferenceSlot() async throws {
        let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
        let model = try localFixture(), stream = try localFixture(capability: "asr-stream")
        let store = try ModelStore(root: root, available: { _ in 100_000_000 })
        try await store.download(model, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8)))
        try await store.download(stream, wifi: { true }, transport: RangeServer(bytes: Data("model".utf8)))
        let engine = NativeLocalEngine(store: store), gate = NativeWorkGate()
        defer { gate.finish.signal() }
        let holder = Task { try await engine.execute(model, capability: .tts, progress: { _ in }) { _, _, _, _ in gate.run(); return 1 } }
        let deadline = ContinuousClock.now + .seconds(60)
        while gate.count == 0 && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
        try #require(gate.count == 1)
        let (chunks, feed) = AsyncThrowingStream<PCMChunk, any Error>.makeStream()
        defer { feed.finish() }
        let returned = Flag()
        let waiting = Task { _ = try? await engine.streamTranscribe(model: stream, chunks: chunks); returned.set() }
        try await Task.sleep(for: .milliseconds(200))
        #expect(!returned.value, "streamTranscribe must not return (so the mic must not start) while the slot is held")
        gate.finish.signal()
        _ = try await holder.value
        await waiting.value
        #expect(returned.value)
    }
}
private final class Flag: @unchecked Sendable {
    private let lock = NSLock(); private var flag = false
    func set() { lock.withLock { flag = true } }
    var value: Bool { lock.withLock { flag } }
}

@Test func localSpeechFamiliesKeepReferenceRequirementsAndPinnedAssetSources() throws {
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    #expect(models.filter(\.supportsSpeech).count == 7)
    #expect(models.filter(\.supportsDesign).map(\.id) == ["omnivoice-q4-k-m"])
    for id in ["pocket-tts-int8-en", "zipvoice-distill-int8-zh-en"] {
        let model = try #require(models.first { $0.id == id })
        #expect(model.requiresReference && model.supportsCloning && model.supports(.tts))
        #expect(model.config["num_speakers"]?.number == 0)
    }
    #expect(ModelAssetSource.validURL("https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx"))
    #expect(ModelAssetSource.validURL("https://huggingface.co/owner/model/resolve/" + String(repeating: "a",count: 40) + "/encoder.onnx"))
    for url in ["https://github.com/other/repo/releases/download/tag/model.onnx", "https://huggingface.co/owner/model/resolve/main/model.onnx", "https://huggingface.co/owner/model/resolve/" + String(repeating: "a",count: 40) + "/../outside", "http://github.com/k2-fsa/sherpa-onnx/releases/download/tag/model.onnx"] { #expect(!ModelAssetSource.validURL(url)) }
    #expect(!ModelAssetSource.validBundled("../outside"))
}

@Test func localBundledSpeechFrontendInstallsOfflineAndRejectsCorruption() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let assets = root.appendingPathComponent("assets/tts"); try FileManager.default.createDirectory(at: assets,withIntermediateDirectories: true)
    let bytes = Data("model".utf8)
    let encoded = try JSONEncoder().encode(localFixture(bytes: bytes))
    var object = try #require(JSONSerialization.jsonObject(with: encoded) as? [String:Any])
    var files = try #require(object["files"] as? [[String:Any]])
    files[0]["bundled"] = "tts/frontend.txt"; object["files"] = files
    let catalogue: [String:Any] = ["schema_version":1,"models":[object]]
    let model = try ModelCatalogue.decode(JSONSerialization.data(withJSONObject: catalogue)).models[0]
    let store = try ModelStore(root: root.appendingPathComponent("models"),bundledRoot: assets.deletingLastPathComponent())
    let host = RangeServer(bytes: bytes)
    try Data("wrong".utf8).write(to: assets.appendingPathComponent("frontend.txt"))
    await #expect(throws: (any Error).self) { try await store.download(model,wifi: { false },transport: host) }
    #expect(await !store.installed(model))
    try bytes.write(to: assets.appendingPathComponent("frontend.txt"))
    try await store.download(model,wifi: { false },transport: host)
    #expect(await store.installed(model))
    #expect(await host.requests.isEmpty)
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_SPEECH"] == "1"),
      arguments: ["piper-lessac-en-medium", "supertonic3-int8", "pocket-tts-int8-en", "zipvoice-distill-int8-zh-en"])
func realSpeechModelsProduceMarkedAudioOffline(id: String) async throws {
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let model = try #require(models.first { $0.id == id })
    let store = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    // A fixture receipt is independent of the installed app's licence decisions.
    try await store.accept(model,fingerprint: model.licence.terms_fingerprint,appVersion: "inference-test",accepted: true)
    let engine = NativeLocalEngine(store: store,libraries: [.sherpa: try #require(ProcessInfo.processInfo.environment["SHERPA_ONNX_LIBRARY"])])
    let phrase = "Hello. This is a local speech test."
    let audio: LocalAudio
    if model.requiresReference {
        let referenceModel = try #require(models.first { $0.id == "piper-lessac-en-medium" })
        try await store.accept(referenceModel,fingerprint: referenceModel.licence.terms_fingerprint,appVersion: "inference-test",accepted: true)
        let referenceText = "The quick brown fox jumps over the lazy dog. This is a local speech test."
        let reference = try await engine.synthesize(model: referenceModel,text: referenceText)
        let decoded = try localDirectory(); defer { try? FileManager.default.removeItem(at: decoded) }
        let referenceURL = decoded.appendingPathComponent("reference.wav")
        try reference.markSynthetic().write(to: referenceURL)
        let pcm = try await AVPhoneAudioDecoder().decode(source: referenceURL,output: decoded.appendingPathComponent("reference.pcm"),maxSeconds: 30,progress: { _ in })
        audio = try await engine.clone(model: model,text: phrase,reference: pcm.read(start: 0,count: pcm.frames),referenceText: referenceText)
        if id == "zipvoice-distill-int8-zh-en" {
            // Several sentences give reference-dependent speech duration margin
            // above the nontrivial-audio guard without prescribing a speaking rate.
            let chinese = try await engine.clone(model: model,text: "\u{4f60}\u{597d}\u{ff0c}\u{8fd9}\u{662f}\u{672c}\u{5730}\u{8bed}\u{97f3}\u{6d4b}\u{8bd5}\u{3002}\u{6240}\u{6709}\u{58f0}\u{97f3}\u{90fd}\u{5728}\u{8fd9}\u{53f0}\u{8bbe}\u{5907}\u{4e0a}\u{751f}\u{6210}\u{ff0c}\u{4e0d}\u{9700}\u{8981}\u{8fde}\u{63a5}\u{4e91}\u{7aef}\u{670d}\u{52a1}\u{3002}",reference: pcm.read(start: 0,count: pcm.frames),referenceText: referenceText)
            #expect(chinese.sampleRate == Int(model.config["sample_rate"]?.number ?? 24000))
            #expect(chinese.samples.count > chinese.sampleRate)
            #expect(chinese.samples.allSatisfy { $0.isFinite })
            #expect(chinese.samples.contains { abs($0) > 0.01 })
            print("Speech inference \(id) Chinese: \(Double(chinese.samples.count)/Double(chinese.sampleRate)) seconds at \(chinese.sampleRate) Hz")
        }
    } else { audio = try await engine.synthesize(model: model.selectingLanguage("en"),text: phrase) }
    #expect(audio.sampleRate == Int(model.config["sample_rate"]?.number ?? 24000))
    #expect(audio.samples.count > audio.sampleRate / 2)
    #expect(audio.samples.allSatisfy { $0.isFinite })
    #expect(audio.samples.contains { abs($0) > 0.01 })
    if id == "supertonic3-int8" {
        let alternate = try await engine.synthesize(model: model.selectingLanguage("es"),text: "Hola. Esta es una prueba de voz local.",speaker: 9)
        #expect(alternate.sampleRate == 44100)
        #expect(alternate.samples.contains { abs($0) > 0.01 })
    }
    let wave = try audio.markSynthetic()
    #expect(wave.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    let output = repository.appendingPathComponent("mobile/ios/.generated/" + id + "-speech.wav")
    try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
    try wave.write(to: output)
    print("Speech inference \(id): \(Double(audio.samples.count)/Double(audio.sampleRate)) seconds at \(audio.sampleRate) Hz")
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_OMNI_SPEECH"] == "1"))
func realOmniSavedVoiceSpeechIsRecognizableWithoutRepeats() async throws {
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let omni = try #require(models.first { $0.id == "omnivoice-q4-k-m" })
    let kitten = try #require(models.first { $0.id == "kitten-mini-en-fp16" })
    let asr = try #require(models.first { $0.id == "whisper-tiny-int8" })
    let store = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    for model in [omni,kitten,asr] { try await store.accept(model,fingerprint: model.licence.terms_fingerprint,appVersion: "inference-test",accepted: true) }
    let env = ProcessInfo.processInfo.environment
    let engine = NativeLocalEngine(store: store,libraries: [.sherpa: try #require(env["SHERPA_ONNX_LIBRARY"]),.omni: try #require(env["OMNIVOICE_LIBRARY"])])
    let referenceText = "Hello. This is VoiceStudio running directly on my iPhone. This speech was generated on the phone."
    let reference = try await engine.synthesize(model: kitten,text: referenceText)
    for text in ["Hi hello", "Hello. This is a local speech test."] {
        let audio = try await engine.clone(model: omni,text: text,reference: PCMChunk(samples: reference.samples,sampleRate: reference.sampleRate),referenceText: referenceText)
        let recognized = try await engine.transcribe(model: asr,audio: PCMChunk(samples: audio.samples,sampleRate: audio.sampleRate))
        print("OmniVoice saved-voice speech recognized:",recognized)
        let words = recognized.lowercased().split { !$0.isLetter }.map(String.init)
        #expect(words.filter { $0 == "hello" }.count == 1)
        if text == "Hi hello" { #expect(words.filter { $0 == "hi" }.count == 1) }
        else { #expect(words.contains("speech") && words.contains("test")) }
        #expect(audio.samples.allSatisfy { $0.isFinite })
        let wave = try audio.markSynthetic()
        #expect(wave.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    }
}

@Test func localOmniSpeechRequiresASavedVoiceBeforeLoadingTheModel() async throws {
    let root = try localDirectory(); defer { try? FileManager.default.removeItem(at: root) }
    let engine = NativeLocalEngine(store: try ModelStore(root: root))
    let omni = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.id == "omnivoice-q4-k-m" })
    #expect(omni.supportsSpeech && omni.supportsDesign && omni.requiresReference)
    do {
        _ = try await engine.synthesize(model: omni,text: "Hi hello")
        Issue.record("OmniVoice must not silently use unconditioned voice design for Speech")
    } catch let error as ClientError {
        #expect(error.key == "mobile.speak.reference_required")
    }
}
