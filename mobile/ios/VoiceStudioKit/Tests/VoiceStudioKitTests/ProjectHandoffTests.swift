import Foundation
import Testing
@testable import VoiceStudioKit

private func handoffRoot() throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("handoff-test-"+UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); return root
}
private var handoffFixtures: URL { repository.appendingPathComponent("mobile/contract/project-fixtures") }
private actor HandoffEngine {
    var calls = 0
    func speak(_ segment: ProductionSegment,_ voice: String) -> LocalAudio { calls += 1; return LocalAudio(samples: Array(repeating: 0.2,count: 2400),sampleRate: 24000) }
}
@Test func desktopBookImportsAsNewEditableCopyAndPreservesUnknownSettingsOnPhoneEdits() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let source = handoffFixtures.appendingPathComponent("desktop-book.vsproject")
    #expect(try PhoneProjectHandoff.preview(source).voices == 0)
    let result = try await PhoneProjectHandoff.restore(source,production: production,dubs: dubs,voices: voices,models: [],configuration: "fixture")
    var project = try #require(await production.list().first)
    #expect(project.id == result.id); #expect(project.speed == 1.25); #expect(project.pronunciations?["portable"] == "port able")
    project.script = "# First chapter\nEdited on this phone."; project.speed = 0.75
    try await production.save(project)
    let file = root.appendingPathComponent("phone.vsproject")
    _ = try await PhoneProjectHandoff.exportProduction(project,store: production,voices: voices,configuration: "fixture",to: file)
    let unpack = root.appendingPathComponent("unpacked"), files = try await ProjectArchive.extract(file,to: unpack)
    let manifest = try JSONDecoder().decode(HandoffManifest.self,from: Data(contentsOf: files["manifest.json"]!))
    #expect(manifest.document["script"]?.string == project.script)
    #expect(manifest.document["speed"]?.number == 0.75)
    let draft = try #require(manifest.document["desktop"]?.handoffObject?["draft"]?.handoffObject)
    #expect(draft["metadata"]?.handoffObject?["author"]?.string == "Fixture author")
    #expect(draft["overrides"]?.handoffObject?["numStep"]?.number == 42)
    #expect(draft["customFutureSetting"]?.handoffObject?["value"]?.number == 7)
    let second = try await PhoneProjectHandoff.restore(file,production: production,dubs: dubs,voices: voices,models: [],configuration: "fixture")
    #expect(second.id != result.id); #expect(try await production.list().count == 2)
    #expect(try await production.list().first { $0.id == second.id }?.script == project.script)
}
@Test func nativeProductionArchiveKeepsCompatibleCompletedCacheWithoutSynthesizingAgain() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices")), engine = HandoffEngine()
    let project = ProductionProject(kind: "stories",title: "Cached",script: "One line")
    _ = try await production.render(project,configuration: "fixture",synthesize: { await engine.speak($0,$1) })
    let file = root.appendingPathComponent("cached.vsproject")
    _ = try await PhoneProjectHandoff.exportProduction(project,store: production,voices: voices,configuration: "fixture",to: file)
    let result = try await PhoneProjectHandoff.restore(file,production: production,dubs: dubs,voices: voices,models: [],configuration: "fixture")
    let copy = try #require(await production.list().first { $0.id == result.id })
    #expect(try await production.output(copy,configuration: "fixture") != nil)
    _ = try await production.render(copy,configuration: "fixture",synthesize: { await engine.speak($0,$1) })
    #expect(await engine.calls == 1)
    let unpacked = try await ProjectArchive.extract(file,to: root.appendingPathComponent("edit-cache"))
    let manifestFile = unpacked["manifest.json"]!
    let originalManifest = try Data(contentsOf: manifestFile)
    for (index,patch) in [["script":JSONValue.string("Edited manuscript")],["speed":.number(0.75)],["pronunciations":.object(["One":.string("Won")])],["defaultVoice":.string("preset:1")]].enumerated() {
        var manifest = try JSONDecoder().decode(HandoffManifest.self,from: originalManifest)
        manifest.document.merge(patch) { _,new in new }
        try JSONEncoder().encode(manifest).write(to: manifestFile)
        let modified = root.appendingPathComponent("edited-\(index).vsproject")
        _ = try await ProjectArchive.write(entries: unpacked.map { ProjectArchiveFile(path: $0.key,file: $0.value) },to: modified)
        let changed = try await PhoneProjectHandoff.restore(modified,production: production,dubs: dubs,voices: voices,models: [],configuration: "fixture")
        let current = try #require(await production.list().first { $0.id == changed.id })
        #expect(try await production.output(current,configuration: "fixture") == nil)
    }
}
@Test func voiceImportRequiresExplicitPermissionAndNeverOverwritesOrTrustsOwnershipMetadata() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let model = try #require(models.first { $0.capability == .clone })
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let original = try await voices.saveDesign(name: "Recipe",text: "Reference words",audio: LocalAudio(samples: Array(repeating: 0.2,count: 4800),sampleRate: 24000),model: model,instruction: "calm",seed: 77)
    let project = ProductionProject(kind: "stories",title: "Voice project",script: "Words",defaultVoice: "phone:"+original.id.uuidString)
    try await production.save(project)
    let file = root.appendingPathComponent("voice.vsproject")
    _ = try await PhoneProjectHandoff.exportProduction(project,store: production,voices: voices,to: file)
    await #expect(throws: (any Error).self) { try await PhoneProjectHandoff.restore(file,production: production,dubs: dubs,voices: voices,models: [],allowVoices: false) }
    #expect(try await voices.list().count == 1); #expect(try await production.list().count == 1)
    let result = try await PhoneProjectHandoff.restore(file,production: production,dubs: dubs,voices: voices,models: [],allowVoices: true)
    let imported = try #require(await production.list().first { $0.id == result.id })
    #expect(imported.defaultVoice != project.defaultVoice)
    let records = try await voices.list(); #expect(records.count == 2)
    let voice = try #require(records.first { $0.id != original.id })
    #expect(voice.designSeed == 77); #expect(voice.designInstruction == "calm"); #expect(voice.transcript == original.transcript)
    let importedSamples = try await voices.reference(voice).samples, originalSamples = try await voices.reference(original).samples
    #expect(importedSamples == originalSamples)
}
@Test func sharedDesktopDubKeepsStableTimingReferenceSpeakerAndSettings() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let result = try await PhoneProjectHandoff.restore(handoffFixtures.appendingPathComponent("desktop-dub.vsproject"),production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
    let checkpoint = try await dubs.checkpoint(result.id)
    #expect(checkpoint.frames == 24000); #expect(checkpoint.nextFrame == 24000)
    let segment = try #require(checkpoint.segments.first)
    #expect(segment.id == "segment-stable"); #expect(segment.speech.start == 0.1); #expect(segment.translations["fr"] == "Translated words"); #expect(segment.speaker == "Narrator"); #expect(segment.gain == 0.8)
    #expect(segment.referenceText == "Reference words"); #expect(segment.referenceFile != nil)
    let output = root.appendingPathComponent("dub.vsproject")
    _ = try await PhoneProjectHandoff.exportDub(result.id,store: dubs,models: models,voices: voices,to: output)
    let files = try await ProjectArchive.extract(output,to: root.appendingPathComponent("unpack"))
    let manifest = try JSONDecoder().decode(HandoffManifest.self,from: Data(contentsOf: files["manifest.json"]!))
    #expect(manifest.document["desktop"]?.handoffObject?["state"]?.handoffObject?["translationInstructions"]?.string == "Preserve this instruction")
    #expect(manifest.document["segments"]?.handoffArray?.first?.handoffObject?["speaker"]?.string == "Narrator")
}
@Test func projectArchiveRejectsUnsafeFilesCorruptionAndNeverOverwrites() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("file.txt"); try Data("test".utf8).write(to: input)
    for name in ["../outside","/absolute","C:/outside","assets/CON.wav","assets/item/../outside","assets/file."] { #expect(throws: (any Error).self) { try ProjectArchive.path(name) } }
    let output = root.appendingPathComponent("file.zip")
    _ = try await ProjectArchive.write(entries: [ProjectArchiveFile(path: "assets/file.txt",file: input)],to: output)
    await #expect(throws: (any Error).self) { try await ProjectArchive.write(entries: [ProjectArchiveFile(path: "assets/other.txt",file: input)],to: output) }
    let symlink = root.appendingPathComponent("link.txt"); try FileManager.default.createSymbolicLink(at: symlink,withDestinationURL: input)
    await #expect(throws: (any Error).self) { try await ProjectArchive.write(entries: [ProjectArchiveFile(path: "assets/link.txt",file: symlink)],to: root.appendingPathComponent("link.zip")) }
    var bytes = try Data(contentsOf: output); bytes[30+"assets/file.txt".utf8.count] ^= 1; try bytes.write(to: output)
    let staging = root.appendingPathComponent("partial")
    await #expect(throws: (any Error).self) { try await ProjectArchive.extract(output,to: staging) }
    #expect(!FileManager.default.fileExists(atPath: staging.path))
}

@Test func bundledDesktopVoiceBindingBecomesActualDubReferenceWithoutAnExplicitClip() async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let models = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let chosen = try [ModelCapability.asr,.translate,.clone].map { capability in try #require(models.first { $0.capability == capability }) }
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let voice = try await voices.saveDesign(name: "Selected",text: "Selected reference transcript",audio: LocalAudio(samples: Array(repeating: 0.4,count: 4800),sampleRate: 24000),model: chosen[2],instruction: "calm",seed: 9)
    let files = try await ProjectArchive.extract(handoffFixtures.appendingPathComponent("desktop-dub.vsproject"),to: root.appendingPathComponent("fixture"))
    let request = try await dubs.create(source: files["assets/source.wav"]!,title: "Selected voice",targets: [PhoneDubTarget(code: "fr",name: "French")],models: chosen,consent: true,audioOnly: true)
    try FileManager.default.copyItem(at: files["assets/original.pcm"]!,to: await dubs.file(request.id,"original.pcm"))
    var checkpoint = PhoneDubCheckpoint(); checkpoint.frames = 24000; checkpoint.nextFrame = 24000; checkpoint.originalHash = try ModelHash.file(files["assets/original.pcm"]!)
    var segment = PhoneDubSegment(speech: SpeechSegment(start: 0.1,end: 0.8,text: "Original words"),translations: ["fr":"Translated words"]); segment.voiceID = voice.id.uuidString; checkpoint.segments = [segment]
    try await dubs.save(request.id,checkpoint)
    let archive = root.appendingPathComponent("selected.vsproject")
    _ = try await PhoneProjectHandoff.exportDub(request.id,store: dubs,models: chosen,voices: voices,to: archive)
    let result = try await PhoneProjectHandoff.restore(archive,production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
    let restored = try #require(await dubs.checkpoint(result.id).segments.first)
    let name = try #require(restored.referenceFile)
    #expect(restored.referenceText == voice.transcript); #expect(restored.voiceID != voice.id.uuidString)
    let reference = PCMFile(try await dubs.file(result.id,name))
    #expect(try reference.frames == 4800); #expect(try reference.read(start: 0,count: 4800).samples.allSatisfy { abs($0-0.4)<0.001 })
}

private actor HandoffDubEngine: LocalEngine {
    private(set) var clones = 0
    func synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,progress: @escaping EngineProgressHandler) async throws -> LocalAudio { throw ClientError("unexpected") }
    func clone(model: PhoneModel,text: String,reference: PCMChunk,referenceText: String,progress: @escaping EngineProgressHandler) async throws -> LocalAudio { clones += 1; return LocalAudio(samples: Array(repeating: 0.3,count: 4800),sampleRate: 24000) }
    func transcribe(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler) async throws -> String { throw ClientError("unexpected") }
    func streamTranscribe(model: PhoneModel,chunks: AsyncThrowingStream<PCMChunk,any Error>,progress: @escaping EngineProgressHandler) async throws -> AsyncThrowingStream<TranscriptUpdate,any Error> { throw ClientError("unexpected") }
    func translate(model: PhoneModel,text: String,sourceLanguage: String,targetLanguage: String,progress: @escaping EngineProgressHandler) async throws -> String { throw ClientError("unexpected") }
}
@Test(arguments: [false,true])
func nativeDubHandoffKeepsVerifiedPackagesAndInvalidatesEditedInputs(_ globalReference: Bool) async throws {
    let root = try handoffRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let allModels = try ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models
    let models = try [ModelCapability.asr,.translate,.clone].map { capability in try #require(allModels.first { $0.capability == capability }) }
    let production = try ProductionStore(root: root.appendingPathComponent("production")), dubs = try PhoneDubStore(root: root.appendingPathComponent("dubs")), voices = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let original = try await PhoneProjectHandoff.restore(handoffFixtures.appendingPathComponent("desktop-dub.vsproject"),production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
    var id = original.id
    if globalReference {
        let initial = try await dubs.request(id), cp = try await dubs.checkpoint(id)
        let reference = try PCMFile(await dubs.file(id,cp.segments[0].referenceFile!)).read(start: 0,count: 4800)
        let request = try await dubs.create(source: dubs.file(id,initial.sourceFile),title: "Global reference",targets: initial.targets,models: models,consent: true,reference: reference,referenceText: "Reference words",audioOnly: true)
        try FileManager.default.copyItem(at: await dubs.file(id,"original.pcm"),to: await dubs.file(request.id,"original.pcm"))
        var global = cp; global.segments[0].referenceFile = nil; global.segments[0].referenceText = nil
        try await dubs.save(request.id,global); id = request.id
    }
    let engine = HandoffDubEngine(), pipeline = PhoneDubPipeline(store: dubs,engine: engine)
    let rendered = try await pipeline.run(id: id,models: models)
    #expect(rendered.done); #expect(await engine.clones == 1)
    let archive = root.appendingPathComponent("rendered.vsproject")
    _ = try await PhoneProjectHandoff.exportDub(id,store: dubs,models: models,voices: voices,to: archive)
    let restored = try await PhoneProjectHandoff.restore(archive,production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
    let cp = try await dubs.checkpoint(restored.id)
    #expect(cp.done); #expect(cp.renders.map(\.file) == rendered.renders.map(\.file))
    _ = try await pipeline.run(id: restored.id,models: models); #expect(await engine.clones == 1)
    for kind in [PhoneDubPackage.clips,.stems] {
        let package = try await dubs.exportPackage(restored.id,target: "fr",kind: kind,workspace: root)
        #expect(try ProjectArchive.entries(package).contains { $0.path == "manifest.json" })
    }
    let files = try await ProjectArchive.extract(archive,to: root.appendingPathComponent("editing"))
    let manifestFile = files["manifest.json"]!, originalManifest = try Data(contentsOf: manifestFile)
    for (index,patch) in [["translations":JSONValue.object(["fr":.string("Edited translation")])],["reference":.string("native/dub/original.pcm")],["referenceText":.string("Edited reference transcript")],["referenceNeedsTranscription":.bool(true)]].enumerated() {
        var manifest = try JSONDecoder().decode(HandoffManifest.self,from: originalManifest)
        var segments = manifest.document["segments"]!.handoffArray!, segment = segments[0].handoffObject!
        segment.merge(patch) { _,new in new }; segments[0] = .object(segment); manifest.document["segments"] = .array(segments)
        try JSONEncoder().encode(manifest).write(to: manifestFile)
        let modified = root.appendingPathComponent("edited-\(index).vsproject")
        _ = try await ProjectArchive.write(entries: files.map { ProjectArchiveFile(path: $0.key,file: $0.value) },to: modified)
        let changed = try await PhoneProjectHandoff.restore(modified,production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
        let changedCheckpoint = try await dubs.checkpoint(changed.id)
        #expect(!changedCheckpoint.done); #expect(changedCheckpoint.renders.isEmpty); #expect(changedCheckpoint.outputs.isEmpty)
    }
    for key in ["scope","hash","referenceHash","clonePin"] {
        var manifest = try JSONDecoder().decode(HandoffManifest.self,from: originalManifest)
        var native = manifest.document["native"]!.handoffObject!, checkpoint = native["checkpoint"]!.handoffObject!
        var renders = checkpoint["renders"]!.handoffArray!, render = renders[0].handoffObject!
        render[key] = .string("stale"); renders[0] = .object(render); checkpoint["renders"] = .array(renders)
        native["checkpoint"] = .object(checkpoint); manifest.document["native"] = .object(native)
        try JSONEncoder().encode(manifest).write(to: manifestFile)
        let modified = root.appendingPathComponent("stale-"+key+".vsproject")
        _ = try await ProjectArchive.write(entries: files.map { ProjectArchiveFile(path: $0.key,file: $0.value) },to: modified)
        let changed = try await PhoneProjectHandoff.restore(modified,production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
        let changedCheckpoint = try await dubs.checkpoint(changed.id)
        #expect(!changedCheckpoint.done); #expect(changedCheckpoint.renders.isEmpty); #expect(changedCheckpoint.outputs.isEmpty)
    }

    for damage in ["missing-renders","duplicate-render","missing-outputs","output-hash"] {
        var manifest = try JSONDecoder().decode(HandoffManifest.self,from: originalManifest)
        var native = manifest.document["native"]!.handoffObject!, checkpoint = native["checkpoint"]!.handoffObject!
        switch damage {
        case "missing-renders": checkpoint["renders"] = .array([])
        case "duplicate-render": let renders = checkpoint["renders"]!.handoffArray!; checkpoint["renders"] = .array(renders+[renders[0]])
        case "missing-outputs": checkpoint["outputs"] = .array([])
        default: checkpoint["outputs"] = .array(checkpoint["outputs"]!.handoffArray!.map { value in var output = value.handoffObject!; output["hash"] = .string("stale"); return .object(output) })
        }
        native["checkpoint"] = .object(checkpoint); manifest.document["native"] = .object(native)
        try JSONEncoder().encode(manifest).write(to: manifestFile)
        let modified = root.appendingPathComponent(damage+".vsproject")
        _ = try await ProjectArchive.write(entries: files.map { ProjectArchiveFile(path: $0.key,file: $0.value) },to: modified)
        let changed = try await PhoneProjectHandoff.restore(modified,production: production,dubs: dubs,voices: voices,models: models,allowVoices: true)
        let changedCheckpoint = try await dubs.checkpoint(changed.id)
        #expect(!changedCheckpoint.done); #expect(changedCheckpoint.renders.isEmpty); #expect(changedCheckpoint.outputs.isEmpty)
    }

}
