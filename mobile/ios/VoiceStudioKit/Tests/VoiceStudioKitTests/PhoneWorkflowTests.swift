import Foundation
import Testing
@preconcurrency import AVFoundation
@testable import VoiceStudioKit

private func fixtureRoot() throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("phone-workflow-"+UUID().uuidString)
    try PrivateFiles.directory(root); return root
}

@Test func phoneVideoMuxPreservesOrientationAndMarksPlayableAudio() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.mp4")
    let writer = try AVAssetWriter(outputURL: source, fileType: .mp4)
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 64, AVVideoHeightKey: 64
    ])
    input.transform = CGAffineTransform(rotationAngle: .pi / 2)
    let adapter = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        kCVPixelBufferWidthKey as String: 64, kCVPixelBufferHeightKey as String: 64
    ])
    try #require(writer.canAdd(input)); writer.add(input)
    try #require(writer.startWriting()); writer.startSession(atSourceTime: .zero)
    defer { if writer.status == .writing { writer.cancelWriting() } }
    for frame in 0..<30 {
        let deadline = ContinuousClock.now + .seconds(10)
        while !input.isReadyForMoreMediaData && ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(5))
        }
        try #require(input.isReadyForMoreMediaData)
        var buffer: CVPixelBuffer?
        try #require(CVPixelBufferCreate(kCFAllocatorDefault, 64, 64, kCVPixelFormatType_32BGRA, nil, &buffer) == kCVReturnSuccess)
        let pixels = try #require(buffer)
        CVPixelBufferLockBaseAddress(pixels, [])
        memset(CVPixelBufferGetBaseAddress(pixels), 0, CVPixelBufferGetDataSize(pixels))
        CVPixelBufferUnlockBaseAddress(pixels, [])
        try #require(adapter.append(pixels, withPresentationTime: CMTime(value: Int64(frame), timescale: 30)))
    }
    input.markAsFinished(); writer.endSession(atSourceTime: CMTime(seconds: 1, preferredTimescale: 30))
    await writer.finishWriting(); try #require(writer.status == .completed)
    let wave = root.appendingPathComponent("marked.wav")
    let samples: [Float] = (0..<24000).map { frame in
        let phase = Double(frame) * 2.0 * Double.pi * 440.0 / 24000.0
        return Float(sin(phase) * 0.3)
    }
    try LocalAudio(samples: samples, sampleRate: 24000).markSynthetic().write(to: wave)
    let result = try await AVPhoneDubMedia().finish(source: source, markedWave: wave, output: root.appendingPathComponent("dub"), audioOnly: false, progress: { _ in })
    let asset = AVURLAsset(url: result)
    let video = try #require(try await asset.loadTracks(withMediaType: .video).first)
    #expect(try await asset.loadTracks(withMediaType: .audio).count == 1)
    let transform = try await video.load(.preferredTransform)
    #expect(abs(transform.b - 1) < 0.001 && abs(transform.c + 1) < 0.001)
    #expect(abs(try await asset.load(.duration).seconds - 1) < 0.1)
    let metadata = try await asset.load(.commonMetadata)
    var marked = false
    for item in metadata where item.commonKey == .commonKeyDescription {
        if try await item.load(.stringValue) == "VoiceStudio synthetic audio" { marked = true }
    }
    #expect(marked)
    let decoded = try await AVPhoneAudioDecoder().decode(source: result, output: root.appendingPathComponent("decoded.pcm"), maxSeconds: 5, progress: { _ in })
    #expect(try decoded.frames >= 21600)
    #expect(try decoded.read(start: 0, count: min(24000, decoded.frames)).samples.contains { abs($0) > 0.01 })
    let second = try await BookExport.encode(wave: wave,format: "m4a",synthetic: true)
    let multi = try await PhoneDubMultiAudio.export(source: source,tracks: [PhoneDubMuxTrack(file: result,language: "fr",name: "French"),PhoneDubMuxTrack(file: second,language: "de",name: "German")],to: root.appendingPathComponent("multitrack.mp4"))
    let multilingual = AVURLAsset(url: multi)
    let audioTracks = try await multilingual.loadTracks(withMediaType: .audio)
    #expect(audioTracks.count == 2)
    #expect(try await audioTracks[0].load(.languageCode) == "fra")
    #expect(try await audioTracks[1].load(.languageCode) == "deu")
    let multiVideo = try #require(try await multilingual.loadTracks(withMediaType: .video).first)
    let multiTransform = try await multiVideo.load(.preferredTransform)
    #expect(abs(multiTransform.b-1) < 0.001 && abs(multiTransform.c+1) < 0.001)
    let group = try #require(try await multilingual.loadMediaSelectionGroup(for: .audible))
    #expect(group.options.count == 2); #expect(group.defaultOption?.locale?.language.languageCode?.identifier == "fr")
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(multi))
}
private func fixtureModel(_ capability: ModelCapability) throws -> PhoneModel {
    try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == capability }!
}
private struct FixtureDecoder: PhoneAudioDecoder {
    var frames = 24000
    func decode(source: URL,output: URL,maxSeconds: Double,progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        try Task.checkCancellation()
        try PCMFile.write(PCMChunk(samples: (0..<frames).map { Float(sin(Double($0)*0.1)*0.3) },sampleRate: 24000),to: output)
        await progress(1); return PCMFile(output)
    }
}
private actor FixtureEngine: LocalEngine {
    var transcript = "A reference voice"
    private(set) var transcriptions = 0
    private(set) var translations = 0
    private(set) var clones = 0
    private var failClone = 0
    private var longSegment = false
    private var denseSegments = false
    func useDenseSegments() { denseSegments = true }
    private(set) var references: [(frames: Int, text: String)] = []
    func useLongSegment() { longSegment = true }
    func failOnClone(_ count: Int) { failClone = count }
    func empty() { transcript = "" }
    func synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,progress: @escaping EngineProgressHandler) async throws -> LocalAudio { LocalAudio(samples: Array(repeating: 0.25,count: 24000),sampleRate: 24000) }
    func clone(model: PhoneModel,text: String,reference: PCMChunk,referenceText: String,progress: @escaping EngineProgressHandler) async throws -> LocalAudio {
        clones += 1
        if clones == failClone { throw CancellationError() }
        #expect(reference.sampleRate == 24000); #expect(!referenceText.isEmpty)
        references.append((reference.samples.count, referenceText))
        return LocalAudio(samples: Array(repeating: 0.4,count: 12000),sampleRate: 24000)
    }
    func transcribe(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler) async throws -> String { try Task.checkCancellation(); transcriptions += 1; return transcript }
    func transcribeSegments(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler) async throws -> [SpeechSegment] {
        transcriptions += 1
        if denseSegments { return (0..<16).map { index in SpeechSegment(start: Double(index)*0.05,end: Double(index)*0.05+0.04,text: "Source sentence \(index)") } }
        if longSegment { return [SpeechSegment(start: 0.5,end: 11.5,text: "Hello long")] }
        return [SpeechSegment(start: 0.1,end: 0.35,text: "Hello"), SpeechSegment(start: 0.5,end: 0.85,text: "World")]
    }
    func streamTranscribe(model: PhoneModel,chunks: AsyncThrowingStream<PCMChunk,any Error>,progress: @escaping EngineProgressHandler) async throws -> AsyncThrowingStream<TranscriptUpdate,any Error> { AsyncThrowingStream { $0.finish() } }
    func translate(model: PhoneModel,text: String,sourceLanguage: String,targetLanguage: String,progress: @escaping EngineProgressHandler) async throws -> String { translations += 1; return "Bonjour" }
}
@Test func phoneVoiceConsentTranscriptPersistenceRenameAndDelete() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneVoiceStore(root: root), engine = FixtureEngine(), asr = try fixtureModel(.asr)
    await #expect(throws: (any Error).self) { try await store.create(name: "Voice",source: root,consent: false,asr: asr,cloneModel: fixtureModel(.clone),engine: engine,decoder: FixtureDecoder()) }
    #expect(await engine.transcriptions == 0)
    let voice = try await store.create(name: " Voice ",source: root,consent: true,asr: asr,cloneModel: fixtureModel(.clone),engine: engine,decoder: FixtureDecoder())
    #expect(voice.name == "Voice"); #expect(voice.transcript == "A reference voice"); #expect(voice.sampleRate == 24000)
    #expect(voice.modelRepo == (try fixtureModel(.clone)).repo); #expect(voice.modelRevision == (try fixtureModel(.clone)).revision)
    #expect(try await store.reference(voice).samples.count == 24000)
    #expect(try Data(contentsOf: await store.preview(voice)).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    let restored = try PhoneVoiceStore(root: root)
    #expect(try await restored.list() == [voice])
    try await restored.rename(voice,name: "Renamed")
    #expect(try await restored.list().first?.name == "Renamed")
    #expect(getxattr(root.path, "com.apple.metadata:com_apple_backup_excludeItem", nil, 0, 0, 0) > 0)
    try await restored.delete(voice); #expect(try await restored.list().isEmpty)
}
@Test func phoneVoiceEmptyTranscriptAndCancellationNeverPublish() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneVoiceStore(root: root), engine = FixtureEngine(), asr = try fixtureModel(.asr)
    await engine.empty()
    await #expect(throws: (any Error).self) { try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: fixtureModel(.clone),engine: engine,decoder: FixtureDecoder()) }
    #expect(try await store.list().isEmpty)
    let task = Task { try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: fixtureModel(.clone),engine: engine,decoder: FixtureDecoder()) }
    task.cancel(); await #expect(throws: CancellationError.self) { try await task.value }
    #expect(try await store.list().isEmpty)
}
@Test func phoneRecentAudioPersistsMarkedAndCanBeRemoved() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneGenerationStore(root: root)
    let tts = try fixtureModel(.tts)
    let item = try await store.save(text: "Hello",audio: LocalAudio(samples: [0.2,-0.2],sampleRate: 24000),model: tts)
    #expect(try Data(contentsOf: await store.file(item)).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    let restored = try PhoneGenerationStore(root: root)
    #expect(try await restored.list().first?.text == "Hello")
    #expect(try await restored.list().first?.modelRepo == tts.repo)
    #expect(try await restored.list().first?.modelRevision == tts.revision)
    try await restored.delete(item); #expect(try await restored.list().isEmpty)
}
@Test func phoneAVExtractionResamplesAndRejectsLongReference() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav")
    try LocalAudio(samples: (0..<48000).map { Float(sin(Double($0)*0.1)*0.25) },sampleRate: 48000).markSynthetic().write(to: source)
    let audio = try await AVPhoneAudioDecoder().decode(source: source,output: root.appendingPathComponent("reference.pcm"),maxSeconds: 30,progress: { _ in })
    #expect(abs(try audio.frames - 24000) < 100)
    #expect(try audio.read(start: 0,count: 2400).samples.contains { abs($0) > 0.1 })
    await #expect(throws: (any Error).self) { try await AVPhoneAudioDecoder().decode(source: source,output: root.appendingPathComponent("long.pcm"),maxSeconds: 0.5,progress: { _ in }) }
}
@Test(arguments: ["wav", "caf", "aiff"])
func phoneAVExtractionKeepsTheRightStereoChannel(_ ext: String) async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("stereo").appendingPathExtension(ext)
    let format = try #require(AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 2, interleaved: false))
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 12000))
    buffer.frameLength = 12000
    let channels = try #require(buffer.floatChannelData)
    for frame in 0..<12000 {
        let phase = Double(frame) * 2.0 * Double.pi * 440.0 / format.sampleRate
        channels[0][frame] = 0
        channels[1][frame] = Float(sin(phase) * 0.4)
    }
    do {
        let writer = try AVAudioFile(forWriting: source, settings: format.settings)
        try writer.write(from: buffer)
    }
    let audio = try await AVPhoneAudioDecoder().decode(source: source, output: root.appendingPathComponent("mono.pcm"), maxSeconds: 1, progress: { _ in })
    let samples = try audio.read(start: 0, count: audio.frames).samples
    #expect(samples.contains { abs($0) > 0.05 })
}
@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_AUX"] == "1"))
func realAuxiliaryPhoneVoiceCreationAndSpeak() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let env = ProcessInfo.processInfo.environment
    let models = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    let tts = try fixtureModel(.tts), asr = try fixtureModel(.asr), clone = try fixtureModel(.clone)
    for model in [tts,asr,clone] { try await models.accept(model,fingerprint: model.licence.terms_fingerprint,appVersion: "inference-test",accepted: true) }
    let engine = NativeLocalEngine(store: models,libraries: [.sherpa: try #require(env["SHERPA_ONNX_LIBRARY"]),.omni: try #require(env["OMNIVOICE_LIBRARY"])])
    let reference = try await engine.synthesize(model: tts,text: "The quick brown fox jumps over the lazy dog.")
    let source = root.appendingPathComponent("reference.wav"); try reference.markSynthetic().write(to: source)
    let voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let voice = try await voices.create(name: "Phone voice",source: source,consent: true,asr: asr,cloneModel: fixtureModel(.clone),engine: engine,decoder: AVPhoneAudioDecoder())
    #expect(voice.transcript.lowercased().contains("fox"))
    let result = try await engine.clone(model: clone,text: "Hello world.",reference: voices.reference(voice),referenceText: voice.transcript)
    #expect(result.samples.count > 1000); #expect(result.samples.contains { abs($0) > 0.001 })
}

@Test func phoneTimingPadsCompressesAndMixesWithoutDuplicateSegments() throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let short = LocalAudio(samples: [0.2,0.4],sampleRate: 24000)
    #expect(try fitTiming(short,frames: 4).samples == [0.2,0.4,0,0])
    let wave = LocalAudio(samples: (0..<48000).map { Float(sin(Double($0)*0.1)*0.25) },sampleRate: 24000)
    let compressed = try fitTiming(wave,frames: 24000)
    #expect(compressed.samples.count == 24000)
    #expect(compressed.samples.allSatisfy { $0.isFinite })
    let segment = root.appendingPathComponent("segment.pcm"); try PCMFile.write(PCMChunk(samples: [0.7,0.7],sampleRate: 24000),to: segment)
    let output = root.appendingPathComponent("mix.pcm")
    for _ in 0..<2 {
        let mixed = try mixTimeline(to: output,frames: 4,segments: [(0,PCMFile(segment)),(1,PCMFile(segment))])
        #expect(try mixed.read(start: 0,count: 4).samples == [0.7,1,0.7,0])
        let wave = root.appendingPathComponent("marked.wav"); try mixed.markSynthetic(to: wave)
        #expect(try Data(contentsOf: wave).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    }
}
@Test func phoneDubCancellationResumesFromVerifiedSegmentsAndPersistsHistory() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("original.wav"); try Data("fixture".utf8).write(to: source)
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs")), engine = FixtureEngine()
    let request = try await store.create(source: source,title: "Original.wav",targets: [PhoneDubTarget(code: "fr",name: "French"),PhoneDubTarget(code: "de",name: "German")],models: models,consent: true,audioOnly: true)
    await engine.failOnClone(2)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    await #expect(throws: CancellationError.self) { try await pipeline.run(id: request.id,models: models) }
    let interrupted = try await store.checkpoint(request.id)
    #expect(interrupted.nextFrame == 24000); #expect(interrupted.renders.count == 1)
    #expect(interrupted.segments.allSatisfy { $0.translations["fr"] != nil })
    let calls = await engine.transcriptions
    let restored = try PhoneDubStore(root: root.appendingPathComponent("dubs"))
    let result = try await PhoneDubPipeline(store: restored,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia()).run(id: request.id,models: models)
    #expect(result.done); #expect(result.outputs.map(\.language) == ["fr","de"])
    #expect(await engine.clones == 5); #expect(await engine.translations == 4)
    #expect(await engine.transcriptions == calls)
    #expect(try await restored.list().first?.request.title == "Original.wav")
    for output in result.outputs {
        let url = try await restored.file(request.id,output.file)
        #expect(try ModelHash.file(url) == output.hash)
        #expect(try Data(contentsOf: url).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    }
    let clones = await engine.clones
    _ = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == clones)
    let output = try await store.file(request.id,result.outputs[0].file)
    try Data("corrupt".utf8).write(to: output)
    await #expect(throws: (any Error).self) { try await pipeline.run(id: request.id,models: models) }
    try await restored.delete(request.id); #expect(try await restored.list().isEmpty)
}
private struct FixtureMedia: PhoneDubMedia {
    func finish(source: URL,markedWave: URL,output: URL,audioOnly: Bool,progress: @escaping @Sendable (Double) async -> Void) async throws -> URL {
        let result = output.appendingPathExtension("wav")
        try? FileManager.default.removeItem(at: result); try FileManager.default.copyItem(at: markedWave,to: result)
        return result
    }
}
@Test func phoneDubConsentPinsAndLicenceWithdrawalBlockBeforeInference() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs")), engine = FixtureEngine()
    await #expect(throws: (any Error).self) { try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: false) }
    #expect(try await store.list().isEmpty)
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true)
    let blocked = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia(),authorize: { throw ClientError("mobile.local.licence_required") })
    await #expect(throws: (any Error).self) { try await blocked.run(id: request.id,models: models) }
    #expect(await engine.transcriptions == 0)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    await #expect(throws: (any Error).self) { try await pipeline.run(id: request.id,models: Array(models.prefix(2))) }
    #expect(await engine.transcriptions == 0)
    // The per-job lock makes a second run or a delete wait for the holder instead of failing.
    try await store.acquire(request.id)
    let waitingRun = Task { try await pipeline.run(id: request.id,models: models) }
    let waitingDelete = Task { try await store.delete(request.id) }
    try await Task.sleep(nanoseconds: 100_000_000)
    #expect(await engine.transcriptions == 0)
    await store.release(request.id)
    _ = try? await waitingRun.value
    _ = try? await waitingDelete.value
    await #expect(throws: (any Error).self) { try await store.file(request.id,"../outside") }
}
@Test(.enabled(if: ProcessInfo.processInfo.environment["VOICESTUDIO_REAL_AUX"] == "1"))
func realAuxiliaryPhoneDubOfflineAndResume() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let env = ProcessInfo.processInfo.environment
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let tts = try fixtureModel(.tts)
    let modelStore = try ModelStore(root: repository.appendingPathComponent("mobile/ios/.cache/inference-models"))
    for model in models+[tts] { try await modelStore.accept(model,fingerprint: model.licence.terms_fingerprint,appVersion: "inference-test",accepted: true) }
    let engine = NativeLocalEngine(store: modelStore,libraries: [.sherpa: try #require(env["SHERPA_ONNX_LIBRARY"]),.omni: try #require(env["OMNIVOICE_LIBRARY"]),.llama: try #require(env["LLAMA_LIBRARY"])])
    let reference = try await engine.synthesize(model: tts,text: "Hello, how are you?")
    let source = root.appendingPathComponent("source.wav"); try reference.markSynthetic().write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs"))
    let request = try await store.create(source: source,title: "Source.wav",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,authorize: { for model in models { try await modelStore.checkPermission(model) } })
    let result = try await pipeline.run(id: request.id,models: models)
    #expect(result.done); #expect(result.outputs.count == 1)
    let translated = result.segments.compactMap { $0.translations["fr"] }.joined(separator: " ").lowercased()
    print("Phone dub native segments:", result.segments.map { $0.speech.text })
    print("Phone dub native translation:", translated)
    // Must be French, not the source echoed back (the prompt once named the source "auto").
    let spoken = result.segments.map { $0.speech.text }.joined(separator: " ").lowercased()
    let words = Set(translated.split { !$0.isLetter && $0 != "'" }.map(String.init))
    #expect(translated != spoken && !words.isDisjoint(with: ["le", "la", "les", "des", "du", "un", "une", "et", "est", "par", "pour", "vous", "comment", "toi"]))
    let wave = try await store.file(request.id,result.outputs[0].file)
    let decoded = try await AVPhoneAudioDecoder().decode(source: wave,output: root.appendingPathComponent("result.pcm"),maxSeconds: 30,progress: { _ in })
    #expect(try decoded.frames > 1000)
    #expect(try decoded.read(start: 0,count: min(720000,decoded.frames)).samples.contains { abs($0) > 0.001 })
    let resumed = try await pipeline.run(id: request.id,models: models)
    #expect(resumed.done); #expect(resumed.outputs[0].hash == result.outputs[0].hash)
}

@Test func phoneMediaRejectsRemoteSourcesBeforeOpeningAssets() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let remote = URL(string: "https://example.invalid/media.wav")!
    await #expect(throws: (any Error).self) { try await AVPhoneAudioDecoder().decode(source: remote,output: root.appendingPathComponent("out.pcm"),maxSeconds: 30,progress: { _ in }) }
    await #expect(throws: (any Error).self) { try await AVPhoneDubMedia().finish(source: remote,markedWave: root.appendingPathComponent("marked.wav"),output: root.appendingPathComponent("out"),audioOnly: true,progress: { _ in }) }
}

@Test func phoneLibrariesRemoveUnpublishedWorkAfterInterruption() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    for metadata in ["voice.json","request.json"] {
        let library = root.appendingPathComponent(metadata)
        let pending = library.appendingPathComponent(UUID().uuidString)
        try PrivateFiles.directory(pending); try Data("partial".utf8).write(to: pending.appendingPathComponent("reference.pcm"))
        if metadata == "voice.json" { _ = try PhoneVoiceStore(root: library) } else { _ = try PhoneDubStore(root: library) }
        #expect(!FileManager.default.fileExists(atPath: pending.path))
    }
}

@Test func reviewBlankDubReferenceFallsBackAndRepairsSavedBlank() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs")), engine = FixtureEngine()
    await engine.empty(); await engine.failOnClone(2)
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    await #expect(throws: CancellationError.self) { try await pipeline.run(id: request.id,models: models) }
    var checkpoint = try await store.checkpoint(request.id)
    #expect(checkpoint.segments.first?.referenceText == "Hello")
    if checkpoint.segments.count > 1 { checkpoint.segments[1].referenceText = " \n"; try await store.save(request.id,checkpoint) }
    let result = try await pipeline.run(id: request.id,models: models)
    #expect(result.done)
    #expect(result.segments.map(\.referenceText) == ["Hello","World"])
}

@Test func blankRecognitionOfALongSegmentUsesTheWholeSegmentWithItsOwnText() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let store = try PhoneDubStore(root: root.appendingPathComponent("dubs")), engine = FixtureEngine()
    await engine.empty(); await engine.useLongSegment(); await engine.failOnClone(1)
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(frames: 24000 * 12),media: FixtureMedia())
    await #expect(throws: CancellationError.self) { try await pipeline.run(id: request.id,models: models) }
    // The resumed job must pair the same audio with the same words as the interrupted one would have.
    #expect(try await pipeline.run(id: request.id,models: models).done)
    let references = await engine.references
    #expect(references.count == 1)
    #expect(references.first?.text == "Hello long")
    #expect(references.first?.frames == 11 * 24000)
}

@Test func reviewCorruptRecordsDoNotHideHealthyLibraries() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let voicesRoot = root.appendingPathComponent("voices"), dubsRoot = root.appendingPathComponent("dubs")
    let voices = try PhoneVoiceStore(root: voicesRoot), dubs = try PhoneDubStore(root: dubsRoot)
    let recentRoot = root.appendingPathComponent("recent"), recent = try PhoneGenerationStore(root: recentRoot)
    let voice = try await voices.create(name: "Voice",source: root,consent: true,asr: fixtureModel(.asr),cloneModel: fixtureModel(.clone),engine: FixtureEngine(),decoder: FixtureDecoder())
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let request = try await dubs.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)],consent: true)
    let generation = try await recent.save(text: "Hello",audio: LocalAudio(samples: [0.2],sampleRate: 24000))
    for (directory,metadata) in [(voicesRoot,"voice.json"),(dubsRoot,"request.json")] {
        let bad = directory.appendingPathComponent(UUID().uuidString); try PrivateFiles.directory(bad)
        try Data("broken".utf8).write(to: bad.appendingPathComponent(metadata))
    }
    try Data("broken".utf8).write(to: recentRoot.appendingPathComponent("broken.json"))
    #expect(try await voices.list() == [voice])
    #expect(try await dubs.list().map(\.id) == [request.id])
    #expect(try await recent.list().map(\.id) == [generation.id])
}

@Test func reviewReferenceDecodeCapsAcceptedDurationTolerance() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("recorded.wav")
    try LocalAudio(samples: Array(repeating: 0.2,count: 720120),sampleRate: 24000).markSynthetic().write(to: source)
    let store = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let voice = try await store.create(name: "Voice",source: source,consent: true,asr: fixtureModel(.asr),cloneModel: fixtureModel(.clone),engine: FixtureEngine(),decoder: AVPhoneAudioDecoder())
    #expect(try await store.reference(voice).samples.count == 720000)
}

@Test func phoneDubEditsInvalidateRendersAndOverrideTheDefaultVoice() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,reference: PCMChunk(samples: Array(repeating: 0.1,count: 4800),sampleRate: 24000),referenceText: "Default voice",audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    #expect(complete.done)
    var edits = complete.segments
    edits[0].translations["fr"] = "Edited translation"
    edits[0] = PhoneDubSegment(speech: SpeechSegment(start: 0.05,end: 0.35,text: edits[0].speech.text),referenceText: edits[0].referenceText,translations: edits[0].translations)
    try await store.edit(request.id,segments: edits,references: [0: (PCMChunk(samples: Array(repeating: 0.3,count: 4800),sampleRate: 24000),"Replacement voice")])
    #expect(try await store.checkpoint(request.id).outputs.map(\.hash) == complete.outputs.map(\.hash))
    #expect(try await pipeline.run(id: request.id,models: models).done)
    #expect(await engine.clones == 3)
    #expect(await engine.translations == 2)
    #expect(await engine.references[2].text == "Replacement voice")
    var invalid = edits
    invalid[0] = PhoneDubSegment(speech: SpeechSegment(start: -1,end: 0.35,text: "Invalid"))
    await #expect(throws: (any Error).self) { try await store.edit(request.id,segments: invalid) }
    #expect(try await store.checkpoint(request.id).done)
}

@Test func phoneCloneRequiresCloneEngineAndPublishesOnlyGeneratedPreview() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneVoiceStore(root: root), engine = FixtureEngine(), asr = try fixtureModel(.asr), clone = try fixtureModel(.clone)
    await #expect(throws: (any Error).self) { try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: asr,engine: engine,decoder: FixtureDecoder()) }
    #expect(await engine.transcriptions == 0); #expect(try await store.list().isEmpty)
    await engine.failOnClone(1)
    await #expect(throws: CancellationError.self) { try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: clone,engine: engine,decoder: FixtureDecoder()) }
    #expect(try await store.list().isEmpty)
    let voice = try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: clone,engine: engine,decoder: FixtureDecoder())
    #expect(await engine.clones == 2); #expect(voice.modelRepo == clone.repo)
    #expect(try await store.preview(voice).lastPathComponent == "preview.wav")
    let preview = try Data(contentsOf: await store.preview(voice))
    #expect(preview.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    let reference = try Data(contentsOf: root.appendingPathComponent(voice.id.uuidString+"/reference.wav"))
    #expect(reference.range(of: Data("VoiceStudio synthetic audio".utf8)) == nil)
}

@Test func phoneDubSelectiveRegenerationKeepsOtherLanguageOutputsAndRejectsStalePreviews() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French"),PhoneDubTarget(code: "de",name: "German")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    var edits = complete.segments; edits[0].translations["fr"] = "Edited French line"
    let original = try await store.preview(request.id,segment: edits[0],target: "fr",generated: false)
    #expect(try Data(contentsOf: original).range(of: Data("VoiceStudio synthetic audio".utf8)) == nil)
    await #expect(throws: (any Error).self) { try await store.preview(request.id,segment: edits[0],target: "fr",generated: true) }
    try await store.edit(request.id,segments: edits)
    #expect(try await store.checkpoint(request.id).outputs.map(\.file) == complete.outputs.map(\.file))
    let updated = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == 5); #expect(await engine.translations == 4)
    #expect(updated.outputs[1].file == complete.outputs[1].file); #expect(updated.outputs[0].file != complete.outputs[0].file)
    let generated = try await store.preview(request.id,segment: updated.segments[0],target: "fr",generated: true)
    #expect(try Data(contentsOf: generated).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    try await store.edit(request.id,segments: updated.segments)
    #expect(try await pipeline.run(id: request.id,models: models).outputs.map(\.file) == updated.outputs.map(\.file))
    #expect(await engine.clones == 5)
}
@Test func phoneDubCancelledRegenerationPreservesPublishedResults() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    var edits = complete.segments; edits[0].translations["fr"] = "Replacement"
    try await store.edit(request.id,segments: edits); await engine.failOnClone(3)
    await #expect(throws: CancellationError.self) { try await pipeline.run(id: request.id,models: models) }
    let interrupted = try await store.checkpoint(request.id)
    #expect(!interrupted.done); #expect(interrupted.outputs.map(\.file) == complete.outputs.map(\.file))
    for output in interrupted.outputs { _ = try await store.verifiedOutput(request.id,output) }
    #expect(try await pipeline.run(id: request.id,models: models).done); #expect(await engine.clones == 4)
}
@Test func phoneDubStructuralEditsAndOriginalRecoveryPreserveTheEditedTimeline() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    let split = try PhoneDubEditing.split(complete.segments,index: 0,at: 0.2)
    try await store.edit(request.id,segments: split)
    try Data("damaged".utf8).write(to: await store.file(request.id,"original.pcm"))
    let updated = try await pipeline.run(id: request.id,models: models)
    #expect(updated.segments.map(\.speech) == split.map(\.speech)); #expect(await engine.clones == 4); #expect(await engine.translations == 2)
    let merged = try PhoneDubEditing.merge(updated.segments,index: 0)
    try await store.edit(request.id,segments: merged)
    #expect(try await pipeline.run(id: request.id,models: models).done); #expect(await engine.clones == 5)
}

@Test func phoneDubUnchangedCuesDoNotRewriteTheCheckpointForEveryCacheHit() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    await engine.useDenseSegments()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French"),PhoneDubTarget(code: "de",name: "German")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == 32)
    try await store.edit(request.id,segments: complete.segments)
    let before = await store.checkpointWrites
    let unchanged = try await pipeline.run(id: request.id,models: models)
    #expect(await store.checkpointWrites-before == 1); #expect(await engine.clones == 32)
    var edits = unchanged.segments; edits[0].translations["fr"] = "Edited line"
    try await store.edit(request.id,segments: edits)
    let changedBefore = await store.checkpointWrites
    _ = try await pipeline.run(id: request.id,models: models)
    #expect(await store.checkpointWrites-changedBefore <= 8); #expect(await engine.clones == 33)
}
@Test func phoneDubVerifiedRenderDoesNotHideChangedReferenceBytes() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,reference: PCMChunk(samples: Array(repeating: 0.1,count: 4800),sampleRate: 24000),referenceText: "Default voice",audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    try await store.edit(request.id,segments: complete.segments,references: [0:(PCMChunk(samples: Array(repeating: 0.3,count: 4800),sampleRate: 24000),"Custom voice")])
    let custom = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == 3)
    let reference = try await store.file(request.id,custom.segments[0].referenceFile!)
    try PCMFile.write(PCMChunk(samples: Array(repeating: 0.4,count: 4800),sampleRate: 24000),to: reference)
    try await store.edit(request.id,segments: custom.segments)
    let regenerated = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == 4); #expect(regenerated.outputs[0].file != custom.outputs[0].file)
    #expect(!FileManager.default.fileExists(atPath: try await store.file(request.id,custom.outputs[0].file).path))
    try PCMFile.write(PCMChunk(samples: Array(repeating: 0.45,count: 4800),sampleRate: 24000),to: await store.file(request.id,"reference.pcm"))
    try await store.edit(request.id,segments: regenerated.segments)
    #expect(try await pipeline.run(id: request.id,models: models).done); #expect(await engine.clones == 5)
}

@Test func phoneDubGainAndMuteRemixCachedSpeechWithoutCallingTheModel() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let complete = try await pipeline.run(id: request.id,models: models)
    var edits = complete.segments; edits[0].gain = 0.5; edits[1].muted = true
    try await store.edit(request.id,segments: edits)
    let mixed = try await pipeline.run(id: request.id,models: models)
    #expect(await engine.clones == 2); #expect(mixed.outputs[0].file != complete.outputs[0].file)
    #expect(mixed.renders.map(\.file) == complete.renders.map(\.file))
    #expect(mixed.segments[0].gain == 0.5); #expect(mixed.segments[1].muted == true)
    let output = try await store.file(request.id,mixed.outputs[0].file)
    let pcm = try await AVPhoneAudioDecoder().decode(source: output,output: root.appendingPathComponent("check.pcm"),maxSeconds: 2,progress: { _ in })
    #expect(try pcm.read(start: 2500,count: 100).samples.allSatisfy { abs($0-0.2) < 0.0001 })
    #expect(try pcm.read(start: 13000,count: 100).samples.allSatisfy { $0 == 0 })
}

@Test func nativeDubPackagesShareCastReferencesAndExportVerifiedMarkedClipsWithoutReferenceAssets() async throws {
    let root = try fixtureRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("source.wav"); try Data("natural recording".utf8).write(to: source)
    let store = try PhoneDubStore(root: root.appendingPathComponent("jobs")), engine = FixtureEngine()
    let models = try [fixtureModel(.asr),fixtureModel(.translate),fixtureModel(.clone)]
    let request = try await store.create(source: source,title: "Source",targets: [PhoneDubTarget(code: "fr",name: "French")],models: models,consent: true,audioOnly: true)
    let pipeline = PhoneDubPipeline(store: store,engine: engine,decoder: FixtureDecoder(),media: FixtureMedia())
    let first = try await pipeline.run(id: request.id,models: models)
    var edits = first.segments.map { cue in var value = cue; value.speaker = "../Alex"; value.referenceOrigin = "cast"; value.voiceID = "voice"; return value }
    edits[0].gain = 0.5; edits[1].muted = true
    let reference = PCMChunk(samples: Array(repeating: 0.3,count: 4800),sampleRate: 24000)
    try await store.edit(request.id,segments: edits,references: [0:(reference,"Shared cast"),1:(reference,"Shared cast")])
    let saved = try await store.checkpoint(request.id)
    #expect(Set(saved.segments.compactMap(\.referenceFile)).count == 1)
    let complete = try await pipeline.run(id: request.id,models: models)
    let clips = try await store.exportPackage(request.id,target: "fr",kind: .clips,workspace: root)
    let entries = try ProjectArchive.entries(clips).map(\.path)
    #expect(entries.contains("segments/00001.wav")); #expect(entries.contains("segments/00002.wav")); #expect(entries.contains("subtitles.ass")); #expect(!entries.contains { $0.contains("reference") || $0.contains("..") })
    let files = try await ProjectArchive.extract(clips,to: root.appendingPathComponent("clips"))
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(files["segments/00001.wav"]!))
    let manifest = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: files["manifest.json"]!)) as? [String:Any])
    let exportedSegments = try #require(manifest["segments"] as? [[String:Any]])
    #expect(exportedSegments.first?["speaker"] as? String == "../Alex"); #expect(manifest["originalIsIsolatedBackground"] as? Bool == false)
    for segment in exportedSegments {
        for key in ["voiceID","referenceFile","referenceText","referenceOrigin","referenceNeedsTranscription"] { #expect(segment[key] == nil) }
    }
    #expect(try await store.checkpoint(request.id).segments == complete.segments)
    let stems = try await store.exportPackage(request.id,target: "fr",kind: .stems,workspace: root)
    let stemFiles = try await ProjectArchive.extract(stems,to: root.appendingPathComponent("stems"))
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(stemFiles["dubbed.wav"]!)); #expect(try !PhoneAudioProcessing.containsSyntheticMarker(stemFiles["original.wav"]!))
    let stemManifest = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: stemFiles["manifest.json"]!)) as? [String:Any])
    let stemSegments = try #require(stemManifest["segments"] as? [[String:Any]])
    for segment in stemSegments {
        for key in ["voiceID","referenceFile","referenceText","referenceOrigin","referenceNeedsTranscription"] { #expect(segment[key] == nil) }
    }
    let cache = try await store.file(request.id,complete.renders.first!.file!); try Data("corrupt".utf8).write(to: cache)
    await #expect(throws: (any Error).self) { try await store.exportPackage(request.id,target: "fr",kind: .clips,workspace: root) }
    #expect(try await store.checkpoint(request.id).outputs.map(\.file) == complete.outputs.map(\.file)); #expect(try !ProjectArchive.entries(clips).isEmpty)
}
