import Foundation

public struct PhoneVoice: Codable, Identifiable, Sendable, Equatable {
    public let id: UUID
    public var name: String
    public let transcript: String
    public let createdAt: Date
    public let consentAt: Date
    public let sampleRate: Int
    /// The catalogue model that transcribed this voice, for content reports. Absent on voices made before it was recorded.
    public var modelRepo: String? = nil
    public var modelRevision: String? = nil
    public var designInstruction: String? = nil
    public var designSeed: UInt64? = nil
    public var sourceAttribution: [String:String]? = nil
}
public protocol PhoneAudioDecoder: Sendable {
    func decode(source: URL, output: URL, maxSeconds: Double, progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile
}
public actor PhoneVoiceStore {
    private let root: URL
    public init(root: URL) throws { self.root = root; try PrivateFiles.directory(root); try PrivateFiles.removeUnpublished(in: root,metadata: "voice.json") }
    private func directory(_ id: UUID) throws -> URL { try PrivateFiles.child(root,id.uuidString) }
    internal func handoffFiles(_ voice: PhoneVoice) throws -> [String:URL] {
        var files: [String:URL] = [:]
        for name in ["reference.pcm","reference.wav","preview.wav"] {
            let file = try PrivateFiles.child(directory(voice.id),name)
            if FileManager.default.fileExists(atPath: file.path) { files[name] = file }
        }
        return files
    }
    internal func installHandoff(name: String,transcript: String,reference: PCMChunk,metadata: [String:JSONValue],synthetic: Bool,consent: Bool) throws -> PhoneVoice {
        guard consent, !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 200, !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, transcript.utf16.count <= 4000, reference.sampleRate == 24000, (2400...720000).contains(reference.samples.count), reference.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.handoff.voices_required") }
        let id = UUID(), now = Date(), dir = try directory(id)
        try PrivateFiles.directory(dir)
        do {
            try PCMFile.write(reference,to: dir.appendingPathComponent("reference.pcm"))
            if synthetic { try LocalAudio(samples: reference.samples,sampleRate: 24000).markSynthetic().write(to: dir.appendingPathComponent("reference.wav"),options: .atomic) }
            else { try ReferenceWave.write(reference,to: dir.appendingPathComponent("reference.wav")) }
            let recipe = metadata["kind"]?.string == "design" ? metadata["instruct"]?.string : nil
            let seed = metadata["phone_design_seed"]?.string.flatMap(UInt64.init)
            var attribution: [String:String]?
            if case .object(let raw) = metadata["sourceAttribution"] { let fields = raw.compactMapValues(\.string).filter { ["source","id","name","author","license","language","url"].contains($0.key) && $0.value.utf16.count <= 2000 }; if !fields.isEmpty { attribution = fields } }
            let voice = PhoneVoice(id: id,name: name,transcript: transcript,createdAt: now,consentAt: now,sampleRate: 24000,modelRepo: metadata["modelRepo"]?.string,modelRevision: metadata["modelRevision"]?.string,designInstruction: recipe,designSeed: seed,sourceAttribution: attribution)
            try Task.checkCancellation(); try PrivateFiles.write(voice,to: dir.appendingPathComponent("voice.json")); return voice
        } catch { try? FileManager.default.removeItem(at: dir); throw error }
    }
    public func list() throws -> [PhoneVoice] {
        try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil).filter { UUID(uuidString: $0.lastPathComponent) != nil }.compactMap { dir in
            do {
            let safe = try PrivateFiles.child(root,dir.lastPathComponent)
            let path = try PrivateFiles.child(safe,"voice.json")
            guard FileManager.default.fileExists(atPath: path.path) else { return nil }
            let voice = try PrivateFiles.read(PhoneVoice.self,from: path)
            let frames = try PCMFile(PrivateFiles.child(safe,"reference.pcm")).frames
            guard voice.id.uuidString == dir.lastPathComponent, voice.sampleRate == 24000, !voice.name.isEmpty, !voice.transcript.isEmpty, (2400...720000).contains(frames) else { throw ClientError("mobile.simple.clone_failed") }
            return voice
            } catch { return nil }
        }.sorted { $0.createdAt > $1.createdAt }
    }
    public func create(name: String, source: URL, consent: Bool, asr: PhoneModel, cloneModel: PhoneModel, engine: any LocalEngine, decoder: any PhoneAudioDecoder, progress: @escaping EngineProgressHandler = { _ in }) async throws -> PhoneVoice {
        guard consent, asr.capability == .asr, cloneModel.supportsCloning, !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 200 else { throw ClientError("mobile.simple.clone_failed") }
        let id = UUID(), now = Date()
        // Publish metadata only after extraction and successful ASR. Failed/cancelled work leaves no voice.
        let actualDir = root.appendingPathComponent(id.uuidString)
        try PrivateFiles.directory(actualDir)
        do {
            let pcm = try await decoder.decode(source: source, output: actualDir.appendingPathComponent("reference.pcm"), maxSeconds: 30, progress: { value in await progress(EngineProgress(stage: .running, fraction: value*0.25)) })
            let frames = try pcm.frames
            guard (2400...720000).contains(frames) else { throw ClientError("mobile.simple.audio_long") }
            let reference = try pcm.read(start: 0,count: frames)
            let transcript = try await engine.transcribe(model: asr, audio: reference, progress: { value in await progress(EngineProgress(stage: value.stage,fraction: 0.25+value.fraction*0.25)) }).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !transcript.isEmpty else { throw ClientError("mobile.simple.no_speech") }
            // This is the user's reference, not synthesized output.
            try ReferenceWave.write(reference, to: actualDir.appendingPathComponent("reference.wav"))
            let preview = try await engine.clone(model: cloneModel,text: transcript,reference: reference,referenceText: transcript,progress: { value in await progress(EngineProgress(stage: value.stage,fraction: 0.5+value.fraction*0.5)) })
            guard preview.sampleRate > 0, !preview.samples.isEmpty, preview.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.simple.clone_failed") }
            try preview.markSynthetic().write(to: actualDir.appendingPathComponent("preview.wav"),options: .atomic)
            try Task.checkCancellation()
            let voice = PhoneVoice(id: id,name: name.trimmingCharacters(in: .whitespacesAndNewlines),transcript: transcript,createdAt: now,consentAt: now,sampleRate: 24000,modelRepo: cloneModel.repo,modelRevision: cloneModel.revision)
            try PrivateFiles.write(voice,to: actualDir.appendingPathComponent("voice.json")); return voice
        } catch { try? FileManager.default.removeItem(at: actualDir); throw error }
    }
    public func importCommunity(_ entry: GalleryEntry,source: URL,consent: Bool,decoder: any PhoneAudioDecoder = AVPhoneAudioDecoder()) async throws -> PhoneVoice {
        guard consent, entry.type == "voice", let audio = entry.audio, (try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max) <= 32*1024*1024,
              try ModelHash.file(source) == audio.sha256 else { throw ClientError("mobile.handoff.voices_required") }
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".pcm")
        defer { try? FileManager.default.removeItem(at: scratch) }
        let pcm = try await decoder.decode(source: source,output: scratch,maxSeconds: 30,progress: { _ in })
        return try installHandoff(name: entry.name,transcript: audio.ref_text,reference: pcm.read(start: 0,count: pcm.frames),metadata: ["sourceAttribution":.object(entry.attribution.mapValues(JSONValue.string))],synthetic: try PhoneAudioProcessing.containsSyntheticMarker(source),consent: consent)
    }
    public func saveDesign(name: String, text: String, audio: LocalAudio, model: PhoneModel, instruction: String, seed: UInt64, attribution: [String:String]? = nil) throws -> PhoneVoice {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 200, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              audio.sampleRate == 24000, (2400...720000).contains(audio.samples.count), audio.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.simple.clone_failed") }
        let id = UUID(), now = Date(), dir = try directory(id)
        try PrivateFiles.directory(dir)
        do {
            try PCMFile.write(PCMChunk(samples: audio.samples,sampleRate: audio.sampleRate),to: dir.appendingPathComponent("reference.pcm"))
            try audio.markSynthetic().write(to: dir.appendingPathComponent("reference.wav"),options: .atomic)
            try Task.checkCancellation()
            let voice = PhoneVoice(id: id,name: name.trimmingCharacters(in: .whitespacesAndNewlines),transcript: text,createdAt: now,consentAt: now,sampleRate: 24000,modelRepo: model.repo,modelRevision: model.revision,designInstruction: instruction,designSeed: seed,sourceAttribution: attribution)
            try PrivateFiles.write(voice,to: dir.appendingPathComponent("voice.json")); return voice
        } catch { try? FileManager.default.removeItem(at: dir); throw error }
    }
    public func reference(_ voice: PhoneVoice) throws -> PCMChunk {
        let pcm = PCMFile(try PrivateFiles.child(directory(voice.id),"reference.pcm"))
        return try pcm.read(start: 0,count: pcm.frames)
    }
    public func preview(_ voice: PhoneVoice) throws -> URL {
        let preview = try PrivateFiles.child(directory(voice.id),"preview.wav")
        return FileManager.default.fileExists(atPath: preview.path) ? preview : try PrivateFiles.child(directory(voice.id),"reference.wav")
    }
    public func rename(_ voice: PhoneVoice, name: String) throws {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 200 else { throw ClientError("mobile.simple.clone_failed") }
        let url = try PrivateFiles.child(directory(voice.id),"voice.json")
        var current = try PrivateFiles.read(PhoneVoice.self,from: url); current.name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        try PrivateFiles.write(current,to: url)
    }
    public func delete(_ voice: PhoneVoice) throws { try FileManager.default.removeItem(at: directory(voice.id)) }
}
private enum ReferenceWave {
    static func write(_ audio: PCMChunk, to url: URL) throws {
        // Reference recordings must not be labelled synthetic.
        var data = Data()
        func ascii(_ text: String) { data.append(contentsOf: text.utf8) }
        func u16(_ value: UInt16) { var v = value.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        func u32(_ value: UInt32) { var v = value.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        ascii("RIFF"); u32(UInt32(36+audio.samples.count*2)); ascii("WAVEfmt "); u32(16); u16(1); u16(1); u32(24000); u32(48000); u16(2); u16(16); ascii("data"); u32(UInt32(audio.samples.count*2))
        for sample in audio.samples { u16(UInt16(bitPattern: Int16((min(1,max(-1,sample))*32767).rounded()))) }
        try data.write(to: url, options: .atomic)
    }
}
