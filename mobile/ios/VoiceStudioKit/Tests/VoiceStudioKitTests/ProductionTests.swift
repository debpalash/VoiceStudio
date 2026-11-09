import Foundation
import Testing
@testable import VoiceStudioKit

@Test func productionPreservesChaptersAndCastAndBoundsInference() throws {
    let plan = try ProductionPlan(script: "# Arrival\n[voice:Alice] Hello there.\nStill Alice.\n# Return\n[voice:Bob] Welcome back.\n" + String(repeating: "word ", count: 250))
    #expect(plan.roles == ["Alice", "Bob"])
    #expect(plan.chapters == ["Arrival", "Return"])
    #expect(plan.segments[1].role == "Alice")
    #expect(plan.segments.allSatisfy { !$0.text.isEmpty && $0.text.count <= 400 })
    #expect(throws: (any Error).self) { try ProductionPlan(script: "# Empty") }
    let unicode = try ProductionPlan(script: String(repeating: "😀",count: 401))
    #expect(unicode.segments.allSatisfy { $0.text.utf16.count <= 400 })
    #expect(unicode.segments.map(\.text).joined() == String(repeating: "😀",count: 401))
}

@Test func desktopEmotionTagsAreNeverReadAloud() throws {
    let plan = try ProductionPlan(script: "[voice:Alice] [emotion:quietly terrified] [speed:1.5] Did you hear that?\n[EMOTION:sad] [voice:Bob] Only the wind.")
    #expect(plan.segments.map(\.text) == ["Did you hear that?", "Only the wind."])
    #expect(plan.segments.map(\.role) == ["Alice", "Bob"])
    #expect(plan.segments[0].speed == 1.5)
    // A desktop tag may span lines; neither half is spoken.
    #expect(try ProductionPlan(script: "[emotion:  very\n  tired  ] Yawn.").segments.map(\.text) == ["Yawn."])
    // Its line break is kept, so a voice tag after it still switches the voice.
    let switched = try ProductionPlan(script: "[voice:Alice] Hello [emotion:sad\nquiet] [voice:Bob] Bye")
    #expect(switched.segments.map(\.text) == ["Hello", "Bye"])
    #expect(switched.segments.map(\.role) == ["Alice", "Bob"])
    // An unclosed tag never swallows the next chapter.
    let unclosed = try ProductionPlan(script: "[emotion:quiet\n# Two\nA bracket ] closes here.")
    #expect(unclosed.chapters.last == "Two")
    #expect(unclosed.segments.last?.text == "A bracket ] closes here.")
}

@Test func designedVoiceRetainsSeedRecipeAndMarkedReferenceWithoutASR() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let model = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == .clone })
    let store = try PhoneVoiceStore(root: root)
    let voice = try await store.saveDesign(name: "Designed",text: "A designed reference",audio: LocalAudio(samples: Array(repeating: 0.2,count: 4800),sampleRate: 24000),model: model,instruction: "female, young adult",seed: 77)
    let restored = try #require(await store.list().first)
    #expect(restored.designSeed == 77); #expect(restored.designInstruction == "female, young adult")
    #expect(restored.modelRepo == model.repo); #expect(restored.transcript == "A designed reference")
    #expect(try await store.reference(voice).samples.count == 4800)
    #expect(try Data(contentsOf: await store.preview(voice)).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
}

private actor WorkflowFixture {
    var translated = 0
    var spoken = 0
    func execute(_ step: PhoneWorkflowStep,_ text: String) throws -> WorkflowValue {
        if step.kind == "translate" { translated += 1; #expect(text == "Hello world"); return WorkflowValue(text: "Bonjour") }
        spoken += 1
        if spoken == 1 { throw CancellationError() }
        #expect(text == "Bonjour")
        return WorkflowValue(text: text,audio: LocalAudio(samples: Array(repeating: 0.2,count: 2400),sampleRate: 24000))
    }
}
@Test func workflowsPersistAndResumeFinishedTransformations() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneWorkflowStore(root: root), fixture = WorkflowFixture()
    let workflow = PhoneWorkflow(name: "Translate and speak",steps: [PhoneWorkflowStep(kind: "normalize"),PhoneWorkflowStep(kind: "translate",language: "fr"),PhoneWorkflowStep(kind: "speak")])
    do { _ = try await store.run(workflow,text: "  Hello   world  ",configuration: "v1",execute: { try await fixture.execute($0,$1) }); Issue.record("Expected interruption") } catch is CancellationError {}
    let restarted = try PhoneWorkflowStore(root: root)
    let result = try await restarted.run(workflow,text: "  Hello   world  ",configuration: "v1",execute: { try await fixture.execute($0,$1) })
    #expect(result.text == "Bonjour"); #expect(result.audio != nil)
    #expect(await fixture.translated == 1); #expect(await fixture.spoken == 2)
    #expect(try await restarted.list().first?.id == workflow.id)
    #expect(try await restarted.list().first?.inputText == "  Hello   world  ")
    _ = try await restarted.run(workflow,text: "  Hello   world  ",configuration: "v2",execute: { try await fixture.execute($0,$1) })
    #expect(await fixture.translated == 2); #expect(await fixture.spoken == 3)
    #expect(!PhoneWorkflow(name: "Invalid",steps: [PhoneWorkflowStep(kind: "speak"),PhoneWorkflowStep(kind: "translate")]).valid)
}

private actor ProductionFixture {
    var calls = 0
    var interrupt = true
    func render(_ segment: ProductionSegment, _ voice: String) throws -> LocalAudio {
        calls += 1
        if calls == 2 && interrupt { throw CancellationError() }
        return LocalAudio(samples: Array(repeating: voice == "alice" ? 0.2 : 0.4, count: 2400), sampleRate: 24000)
    }
    func resume() { interrupt = false }
}

@Test func productionResumesAfterRestartAndInvalidatesChangedVoiceSettings() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try ProductionStore(root: root), engine = ProductionFixture()
    var project = ProductionProject(kind: "stories", title: "A story", script: "[voice:Alice] Hello\n[voice:Bob] World", defaultVoice: "alice", cast: ["Bob": "bob"])
    try await store.save(project)
    do { _ = try await store.render(project, configuration: "model-v1", synthesize: { try await engine.render($0,$1) }); Issue.record("Expected interruption") } catch is CancellationError {}
    #expect(try await store.output(project, configuration: "model-v1") == nil)
    await engine.resume()
    let restarted = try ProductionStore(root: root)
    let output = try await restarted.render(project, configuration: "model-v1", synthesize: { try await engine.render($0,$1) })
    #expect(await engine.calls == 3) // The first completed line survived the interruption.
    let wave = try Data(contentsOf: output)
    #expect(String(decoding: wave.prefix(4), as: UTF8.self) == "RIFF")
    #expect(String(decoding: wave, as: UTF8.self).contains("VoiceStudio synthetic audio"))
    #expect(try await restarted.list().map(\.id) == [project.id])
    project.cast["Bob"] = "alice"
    #expect(try await restarted.output(project, configuration: "model-v1") == nil)
    _ = try await restarted.render(project, configuration: "model-v1", synthesize: { try await engine.render($0,$1) })
    #expect(await engine.calls == 4)
    #expect(try await restarted.output(project, configuration: "model-v2") == nil)
}

private struct ConversionDecoder: PhoneAudioDecoder {
    func decode(source: URL, output: URL, maxSeconds: Double, progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        #expect(maxSeconds == 30)
        try PCMFile.write(PCMChunk(samples: Array(repeating: 0.1,count: 24000),sampleRate: 24000),to: output)
        return PCMFile(output)
    }
}
private actor ConversionEngine: LocalEngine {
    var silent = false
    var clones = 0
    func silence() { silent = true }
    func synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,progress: @escaping EngineProgressHandler) async throws -> LocalAudio { LocalAudio(samples: [0.1],sampleRate: 24000) }
    func clone(model: PhoneModel,text: String,reference: PCMChunk,referenceText: String,progress: @escaping EngineProgressHandler) async throws -> LocalAudio {
        clones += 1; #expect(text == "A real transcript"); #expect(referenceText == "A real transcript")
        return LocalAudio(samples: Array(repeating: 0.2,count: 12000),sampleRate: 24000)
    }
    func transcribe(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler) async throws -> String { silent ? " " : "A real transcript" }
    func streamTranscribe(model: PhoneModel,chunks: AsyncThrowingStream<PCMChunk,any Error>,progress: @escaping EngineProgressHandler) async throws -> AsyncThrowingStream<TranscriptUpdate,any Error> { AsyncThrowingStream { $0.finish() } }
    func translate(model: PhoneModel,text: String,sourceLanguage: String,targetLanguage: String,progress: @escaping EngineProgressHandler) async throws -> String { text }
}
@Test func conversionFitsDurationAndSilenceNeverSynthesizes() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let asr = try #require(models.first { $0.capability == .asr }), clone = try #require(models.first { $0.capability == .clone })
    let store = try PhoneVoiceStore(root: root.appendingPathComponent("voices")), engine = ConversionEngine()
    let voice = try await store.create(name: "Voice",source: root,consent: true,asr: asr,cloneModel: clone,engine: engine,decoder: ConversionDecoder())
    let workspace = root.appendingPathComponent("work")
    let result = try await VoiceConversion.run(source: root,workspace: workspace,voice: voice,voices: store,asr: asr,clone: clone,engine: engine,decoder: ConversionDecoder(),matchDuration: true)
    #expect(result.transcript == "A real transcript")
    #expect(result.audio.samples.count == 24000)
    #expect(try FileManager.default.contentsOfDirectory(atPath: workspace.path).isEmpty)
    await engine.silence()
    await #expect(throws: (any Error).self) {
        try await VoiceConversion.run(source: root,workspace: workspace,voice: voice,voices: store,asr: asr,clone: clone,engine: engine,decoder: ConversionDecoder(),matchDuration: false)
    }
    #expect(await engine.clones == 2)
}

@Test func emptyProductionDraftHasNoOutputsOrChaptersBeforeFirstRender() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try ProductionStore(root: root)
    let project = ProductionProject(kind: "audiobook",title: "New book")
    try await store.save(project)
    #expect(try await store.output(project,configuration: "model") == nil)
    #expect(try await store.chapterOutputs(project,configuration: "model").isEmpty)
    #expect(try await store.chapterCues(project,configuration: "model").isEmpty)
    #expect(try await store.list().count == 1)
}
