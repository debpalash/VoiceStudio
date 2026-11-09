import Foundation
import CryptoKit

public struct PhoneDubTarget: Codable, Sendable, Equatable {
    public let code: String
    public let name: String
    public init(code: String,name: String) { self.code = code; self.name = name }
}
public struct PhoneDubRequest: Codable, Identifiable, Sendable {
    public let id: UUID
    public let title: String
    public let sourceFile: String
    public let sourceHash: String
    public let targets: [PhoneDubTarget]
    public let modelPins: [String: String]
    public let createdAt: Date
    public let consentAt: Date
    public let referenceText: String?
    public let audioOnly: Bool
}
public struct PhoneDubSegment: Codable, Sendable, Equatable {
    public var id: String?
    public var referenceNeedsTranscription: Bool?
    public var gain: Double?
    public var muted: Bool?
    public var speaker: String?
    public var referenceOrigin: String?
    public var voiceID: String?
    public var speech: SpeechSegment
    public var referenceText: String?
    public var translations: [String: String] = [:]
    public var referenceFile: String? = nil
    public init(speech: SpeechSegment, referenceText: String? = nil, translations: [String:String] = [:], referenceFile: String? = nil, id: String? = UUID().uuidString, referenceNeedsTranscription: Bool? = nil, gain: Double? = nil, muted: Bool? = nil, speaker: String? = nil, referenceOrigin: String? = nil, voiceID: String? = nil) {
        self.id = id; self.referenceNeedsTranscription = referenceNeedsTranscription; self.gain = gain; self.muted = muted; self.speaker = speaker; self.referenceOrigin = referenceOrigin; self.voiceID = voiceID
        self.speech = speech; self.referenceText = referenceText; self.translations = translations; self.referenceFile = referenceFile
    }
}
public struct PhoneDubRender: Codable, Sendable {
    public let target: String
    public var index: Int
    public let hash: String
    public let frames: Int
    public var fingerprint: String? = nil
    public var file: String? = nil
    public var segmentID: String? = nil
    public var scope: String? = nil
    public var originalHash: String? = nil
    public var referenceHash: String? = nil
    public var clonePin: String? = nil
}
private struct PhoneDubCachedAudio: Codable { let hash: String; let frames: Int }
public struct PhoneDubOutput: Codable, Sendable, Identifiable {
    public var id: String { language }
    public let language: String
    public let name: String
    public let file: String
    public let hash: String
    public let duration: Double
    public var fingerprint: String? = nil
}
public struct PhoneDubCheckpoint: Codable, Sendable {
    public var frames = 0
    public var originalHash: String?
    public var nextFrame = 0
    public var segments: [PhoneDubSegment] = []
    public var renders: [PhoneDubRender] = []
    public var outputs: [PhoneDubOutput] = []
    public var stage = "mobile.android.stage_prepare"
    public var percent = 0
    public var done = false
    public init() {}
}
public struct PhoneDubRecord: Identifiable, Sendable {
    public var id: UUID { request.id }
    public let request: PhoneDubRequest
    public let checkpoint: PhoneDubCheckpoint
}
public struct PhoneDubProgress: Sendable {
    public let stage: String
    public let percent: Int
    public init(stage: String,percent: Int) { self.stage = stage; self.percent = percent }
}
public typealias PhoneDubReport = @Sendable (PhoneDubProgress) async -> Void
public func phoneModelPin(_ model: PhoneModel) throws -> String {
    let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys,.withoutEscapingSlashes]
    return SHA256.hash(data: try encoder.encode(model)).map { String(format: "%02x",$0) }.joined()
}
/// The catalogue model that voiced a saved dub, for content reports. The revision is
/// reported only while the catalogue entry still matches the dub's pin; after an update
/// changed the entry, the current revision would name a model that never made the audio.
public func phoneDubVoiceModel(pins: [String: String], in models: [PhoneModel]) -> (repo: String, revision: String?)? {
    guard let model = models.first(where: { $0.capability == .clone && pins[$0.id] != nil }) else { return nil }
    return (model.repo, (try? phoneModelPin(model)) == pins[model.id] ? model.revision : nil)
}
private struct PhoneDubReferenceBuffer: Hashable { let address: UInt; let frames: Int; let text: String }
public actor PhoneDubStore {
    private let root: URL
    internal private(set) var checkpointWrites = 0
    private var active = Set<UUID>()
    public init(root: URL) throws { self.root = root; try PrivateFiles.directory(root); try PrivateFiles.removeUnpublished(in: root,metadata: "request.json") }
    public func directory(_ id: UUID) throws -> URL { try PrivateFiles.child(root,id.uuidString) }
    public func file(_ id: UUID,_ name: String) throws -> URL { try PrivateFiles.child(directory(id),name) }
    private var waiters: [UUID: [CheckedContinuation<Void,Never>]] = [:]
    /// One lock per job. A resumed run or a delete waits for the previous run (including its cancel
    /// cleanup and final checkpoint save) instead of racing it; the lock is handed straight to the next waiter.
    public func acquire(_ id: UUID) async throws {
        if active.contains(id) { await withCheckedContinuation { waiters[id,default: []].append($0) } } else { active.insert(id) }
        if Task.isCancelled { release(id); throw CancellationError() }
    }
    public func release(_ id: UUID) {
        if var queue = waiters[id], !queue.isEmpty { let next = queue.removeFirst(); waiters[id] = queue.isEmpty ? nil : queue; next.resume() } else { active.remove(id) }
    }
    public func create(source: URL,title: String,targets: [PhoneDubTarget],models: [PhoneModel],consent: Bool,reference: PCMChunk? = nil,referenceText: String? = nil,audioOnly: Bool = false) throws -> PhoneDubRequest {
        guard consent, !targets.isEmpty, targets.count <= 20, Set(targets.map(\.code)).count == targets.count,
              targets.allSatisfy({ !$0.code.isEmpty && $0.code.count <= 80 && $0.code.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45,95].contains($0) }) && (2...120).contains($0.name.count) && !$0.name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) }),
              models.count == 3, Set(models.map(\.capability)) == Set([.asr,.translate,.clone]),
              (reference == nil) == (referenceText == nil), referenceText?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != true
        else { throw ClientError("mobile.simple.dub_failed") }
        let id = UUID(), dir = try directory(id), now = Date()
        try PrivateFiles.directory(dir)
        do {
            let ext = source.pathExtension
            let suffix = !ext.isEmpty && ext.count <= 10 && ext.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) }) ? ext : "media"
            let sourceName = "source."+suffix, destination = try file(id,"source."+suffix)
            let access = source.startAccessingSecurityScopedResource(); defer { if access { source.stopAccessingSecurityScopedResource() } }
            let input = try FileHandle(forReadingFrom: source); defer { try? input.close() }
            let output = try FileHandle(forWritingTo: PrivateFiles.empty(destination)); defer { try? output.close() }
            while let bytes = try input.read(upToCount: 262144), !bytes.isEmpty { try Task.checkCancellation(); try output.write(contentsOf: bytes) }
            try output.synchronize()
            if let reference {
                guard reference.samples.count <= 720000 else { throw ClientError("mobile.simple.audio_long") }
                try PCMFile.write(reference,to: file(id,"reference.pcm"))
            }
            let request = PhoneDubRequest(id: id,title: String(title.prefix(250)),sourceFile: sourceName,sourceHash: try ModelHash.file(destination),targets: targets,modelPins: try Dictionary(uniqueKeysWithValues: models.map { ($0.id,try phoneModelPin($0)) }),createdAt: now,consentAt: now,referenceText: referenceText,audioOnly: audioOnly)
            try save(id,PhoneDubCheckpoint())
            try Task.checkCancellation()
            try PrivateFiles.write(request,to: file(id,"request.json")); return request
        } catch { try? FileManager.default.removeItem(at: dir); throw error }
    }
    public func request(_ id: UUID) throws -> PhoneDubRequest {
        let value = try PrivateFiles.read(PhoneDubRequest.self,from: file(id,"request.json"))
        guard value.id == id else { throw ClientError("mobile.simple.checkpoint_failed") }
        _ = try file(id,value.sourceFile); return value
    }
    public func checkpoint(_ id: UUID) throws -> PhoneDubCheckpoint {
        var value = try PrivateFiles.read(PhoneDubCheckpoint.self,from: file(id,"checkpoint.json"))
        for index in value.segments.indices where value.segments[index].id == nil { value.segments[index].id = "legacy-\(index)" }
        guard value.frames >= 0, value.frames <= 345600000, value.nextFrame >= 0, value.nextFrame <= value.frames, value.segments.count <= 10000, value.outputs.count <= 20, Set(value.segments.compactMap(\.id)).count == value.segments.count, (0...100).contains(value.percent), value.segments.allSatisfy({ $0.speech.start.isFinite && $0.speech.end.isFinite && $0.speech.start >= 0 && $0.speech.end > $0.speech.start && $0.speech.end <= Double(value.frames)/24000+0.01 && !$0.speech.text.isEmpty && ($0.gain ?? 1).isFinite && (0...2).contains($0.gain ?? 1) }) else { throw ClientError("mobile.simple.checkpoint_failed") }
        for output in value.outputs { _ = try file(id,output.file) }
        return value
    }
    public func save(_ id: UUID,_ value: PhoneDubCheckpoint) throws { try PrivateFiles.write(value,to: file(id,"checkpoint.json")); checkpointWrites += 1 }
    public func list() throws -> [PhoneDubRecord] {
        try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil).compactMap { dir in
            guard let id = UUID(uuidString: dir.lastPathComponent), FileManager.default.fileExists(atPath: dir.appendingPathComponent("request.json").path) else { return nil }
            return try? PhoneDubRecord(request: request(id),checkpoint: checkpoint(id))
        }.sorted { $0.request.createdAt > $1.request.createdAt }
    }
    /// Save edits atomically, preserving the previous checkpoint if any reference write fails.
    public func edit(_ id: UUID,segments: [PhoneDubSegment],references: [Int: (PCMChunk,String)] = [:]) async throws {
        try await acquire(id); defer { release(id) }
        let request = try request(id), current = try checkpoint(id)
        let duration = Double(current.frames)/24000
        guard FileManager.default.fileExists(atPath: try file(id,"original.pcm").path), !segments.isEmpty, segments.count <= 10000, Set(segments.compactMap(\.id)).count == segments.count,
              segments.allSatisfy({ $0.speech.start.isFinite && $0.speech.end.isFinite && $0.speech.start >= 0 && $0.speech.end > $0.speech.start && $0.speech.end <= duration && $0.speech.end-$0.speech.start <= 30 && !$0.speech.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.speech.text.utf16.count <= 4000 && ($0.gain ?? 1).isFinite && (0...2).contains($0.gain ?? 1) && $0.translations.allSatisfy { key,value in request.targets.contains { $0.code == key } && !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && value.utf16.count <= 4000 } })
        else { throw ClientError("mobile.simple.media_failed") }
        var updated = segments.map { cue in var value = cue; value.speaker = cue.speaker?.trimmingCharacters(in: .whitespacesAndNewlines); if value.speaker?.isEmpty == true { value.speaker = nil }; return value }, created: [URL] = [], sharedReferences: [String:String] = [:]
        var referenceFingerprints: [PhoneDubReferenceBuffer:String] = [:]
        guard segments.allSatisfy({ PhoneDubCast.valid($0) }) else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        do {
            for (index,reference) in references {
                guard updated.indices.contains(index), !reference.1.isEmpty, reference.0.sampleRate == 24000, (2400...720000).contains(reference.0.samples.count) else { throw ClientError("mobile.simple.media_failed") }
                let buffer = reference.0.samples.withUnsafeBufferPointer { PhoneDubReferenceBuffer(address: UInt(bitPattern: $0.baseAddress!),frames: $0.count,text: reference.1) }
                let fingerprint: String
                if let known = referenceFingerprints[buffer] { fingerprint = known }
                else { fingerprint = phoneDubFingerprint([phoneDubAudioHash(reference.0),reference.1]); referenceFingerprints[buffer] = fingerprint }
                let name: String
                if let shared = sharedReferences[fingerprint] { name = shared }
                else {
                    name = "edited-reference-"+UUID().uuidString+".pcm"; let url = try file(id,name)
                    try PCMFile.write(reference.0,to: url); created.append(url); sharedReferences[fingerprint] = name
                }
                updated[index].referenceFile = name; updated[index].referenceText = reference.1
            }
            let previous = Dictionary(uniqueKeysWithValues: current.segments.compactMap { s in s.id.map { ($0,s) } })
            for index in updated.indices {
                if let old = updated[index].id.flatMap({ previous[$0] }), old.speech.text != updated[index].speech.text {
                    updated[index].translations = updated[index].translations.filter { key,value in value != old.translations[key] }
                }
                if let name = updated[index].referenceFile {
                    guard FileManager.default.fileExists(atPath: try file(id,name).path) else { throw ClientError("mobile.simple.media_failed") }
                } else if updated[index].speech != updated[index].id.flatMap({ previous[$0]?.speech }) {
                    updated[index].referenceText = nil; updated[index].referenceNeedsTranscription = true
                }
            }
            try Task.checkCancellation()
            var checkpoint = current
            checkpoint.segments = updated
            let updatedByID = Dictionary(uniqueKeysWithValues: updated.enumerated().map { ($0.element.id!,($0.offset,$0.element)) })
            checkpoint.renders = current.renders.compactMap { r in
                guard let id = r.segmentID, let (index,segment) = updatedByID[id], phoneDubSegmentScope(segment,target: r.target) == r.scope else { return nil }
                var moved = r; moved.index = index; return moved
            }
            checkpoint.done = false; checkpoint.percent = 35; checkpoint.stage = "mobile.android.stage_translate"
            try save(id,checkpoint)
        } catch { for url in created { try? FileManager.default.removeItem(at: url) }; throw error }
    }
    public func verifiedOutput(_ id: UUID,_ output: PhoneDubOutput) throws -> URL {
        let url = try file(id,output.file)
        guard try ModelHash.file(url) == output.hash else { throw ClientError("mobile.simple.media_failed") }
        return url
    }
    public func delete(_ id: UUID) async throws {
        try await acquire(id); defer { release(id) }
        try FileManager.default.removeItem(at: directory(id))
    }
}

/// Immutable sources and model pins; atomic segment checkpoints make interruption/retry idempotent.
public struct PhoneDubPipeline: Sendable {
    private let store: PhoneDubStore
    private let engine: any LocalEngine
    private let decoder: any PhoneAudioDecoder
    private let media: any PhoneDubMedia
    private let authorize: @Sendable () async throws -> Void
    public init(store: PhoneDubStore,engine: any LocalEngine,decoder: any PhoneAudioDecoder = AVPhoneAudioDecoder(),media: any PhoneDubMedia = AVPhoneDubMedia(),authorize: @escaping @Sendable () async throws -> Void = {}) {
        self.store = store; self.engine = engine; self.decoder = decoder; self.media = media; self.authorize = authorize
    }
    public func run(id: UUID,models: [PhoneModel],report: @escaping PhoneDubReport = { _ in }) async throws -> PhoneDubCheckpoint {
        try await store.acquire(id)
        do { let result = try await execute(id: id,models: models,report: report); await store.release(id); return result }
        catch { await store.release(id); throw error }
    }
    private func execute(id: UUID,models: [PhoneModel],report: @escaping PhoneDubReport) async throws -> PhoneDubCheckpoint {
        let request = try await store.request(id)
        guard models.count == 3, Set(models.map(\.capability)) == Set([.asr,.translate,.clone]), request.modelPins == (try Dictionary(uniqueKeysWithValues: models.map { ($0.id,try phoneModelPin($0)) })),
              let asr = models.first(where: { $0.capability == .asr }), let translator = models.first(where: { $0.capability == .translate }), let clone = models.first(where: { $0.capability == .clone }) else { throw ClientError("mobile.local.model_incompatible") }
        var cp = try await store.checkpoint(id)
        guard !request.targets.isEmpty, request.targets.count <= 20, cp.outputs.count <= request.targets.count,
              cp.outputs.enumerated().allSatisfy({ $0.element.language == request.targets[$0.offset].code }) else { throw ClientError("mobile.simple.checkpoint_failed") }
        func check() async throws { try Task.checkCancellation(); try await authorize() }
        func save(_ checkpoint: PhoneDubCheckpoint) async throws { try await check(); try await store.save(id,checkpoint) }
        func stage(_ key: String,_ percent: Int) async throws {
            cp.stage = key; cp.percent = percent; try await save(cp); await report(PhoneDubProgress(stage: key,percent: percent))
        }
        if cp.done {
            guard cp.outputs.count == request.targets.count else { throw ClientError("mobile.simple.checkpoint_failed") }
            for output in cp.outputs { guard try ModelHash.file(await store.file(id,output.file)) == output.hash else { throw ClientError("mobile.simple.media_failed") } }
            await report(PhoneDubProgress(stage: cp.stage,percent: 100)); return cp
        }
        try await check()
        let source = try await store.file(id,request.sourceFile)
        guard try ModelHash.file(source) == request.sourceHash else { throw ClientError("mobile.simple.media_failed") }
        let original = PCMFile(try await store.file(id,"original.pcm"))
        let validOriginal = cp.frames > 0 && (try? original.frames) == cp.frames && cp.originalHash == (try? ModelHash.file(original.url))
        if !validOriginal {
            try await stage("mobile.android.stage_prepare",0)
            let audio = try await decoder.decode(source: source,output: original.url,maxSeconds: 14400,progress: { fraction in await report(PhoneDubProgress(stage: "mobile.android.stage_prepare",percent: Int(fraction*10))) })
            let frames = try audio.frames
            guard cp.frames == 0 || cp.frames == frames else { throw ClientError("mobile.simple.media_failed") }
            cp.frames = frames; cp.originalHash = try ModelHash.file(audio.url)
            try await save(cp)
        }
        while cp.nextFrame < cp.frames {
            try await check()
            let start = cp.nextFrame, count = min(480000,cp.frames-start)
            try await stage("mobile.android.stage_transcribe",10+Int(Double(start)/Double(cp.frames)*25))
            let audio = try original.read(start: start,count: count)
            let segments = count >= 2400 && audio.samples.contains(where: { abs($0) > 0.001 }) ? try await engine.transcribeSegments(model: asr,audio: audio,progress: { _ in }) : []
            let offset = Double(start)/24000, duration = Double(count)/24000
            guard segments.allSatisfy({ $0.start.isFinite && $0.end.isFinite && $0.start >= 0 && $0.end > $0.start && $0.end <= duration+0.01 && !$0.text.isEmpty }) else { throw ClientError("mobile.local.inference_failed") }
            cp.segments += segments.map { PhoneDubSegment(speech: SpeechSegment(start: offset+$0.start,end: min(Double(cp.frames)/24000,offset+$0.end),text: $0.text)) }
            guard cp.segments.count <= 10000 else { throw ClientError("mobile.simple.audio_long") }
            cp.nextFrame = start+count; try await save(cp)
        }
        guard !cp.segments.isEmpty else { throw ClientError("mobile.simple.no_speech") }
        let cloneIdentity = try phoneModelPin(clone)
        var referenceHashes: [String:String] = [:]
        func referenceHash(_ segment: PhoneDubSegment) async throws -> String {
            guard let name = segment.referenceFile ?? (request.referenceText == nil ? nil : "reference.pcm") else { return "" }
            if let hash = referenceHashes[name] { return hash }
            let hash = try ModelHash.file(await store.file(id,name)); referenceHashes[name] = hash; return hash
        }
        for targetIndex in request.targets.indices {
            let target = request.targets[targetIndex]
            let previousRenders = cp.renders.filter { $0.target == target.code }.reduce(into: [Int:PhoneDubRender]()) { $0[$1.index] = $1 }
            @Sendable func percent(_ fraction: Double) -> Int { min(99,35+Int((Double(targetIndex)+fraction)/Double(request.targets.count)*65)) }
            for index in cp.segments.indices {
                try await check()
                var segment = cp.segments[index]
                let progress = percent(Double(index)/Double(cp.segments.count)*0.85)
                if segment.translations[target.code] == nil {
                    try await stage("mobile.android.stage_translate",progress)
                    let text = try await engine.translate(model: translator,text: segment.speech.text,sourceLanguage: "auto",targetLanguage: target.name,progress: { value in await report(PhoneDubProgress(stage: "mobile.android.stage_translate",percent: progress+Int(value.fraction))) }).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { throw ClientError("mobile.simple.dub_failed") }
                    segment.translations[target.code] = text; cp.segments[index] = segment; try await save(cp)
                }
                let frames = max(1,Int(segment.speech.end*24000)-Int(segment.speech.start*24000))
                let referenceIdentity = try await referenceHash(segment)
                if let saved = previousRenders[index], saved.fingerprint != nil, let name = saved.file, saved.segmentID == segment.id, saved.scope == phoneDubSegmentScope(segment,target: target.code), saved.originalHash == cp.originalHash, saved.referenceHash == referenceIdentity, saved.clonePin == cloneIdentity, saved.frames == frames {
                    let cached = try await store.file(id,name)
                    if (try? PCMFile(cached).frames) == frames, (try? ModelHash.file(cached)) == saved.hash { continue }
                }
                try await stage("mobile.android.stage_render",progress)
                let savedText = (segment.referenceFile != nil ? segment.referenceText : (request.referenceText ?? segment.referenceText))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                let segmentText = segment.speech.text.trimmingCharacters(in: .whitespacesAndNewlines)
                // When recognition of the 8 s clip came back blank, the saved text is the segment's own transcript and
                // the reference is the whole segment, so a resumed job pairs the same audio with the same words.
                let wholeSegment = request.referenceText == nil && !savedText.isEmpty && savedText == segmentText && segment.speech.end - segment.speech.start > 8
                var reference: PCMChunk
                if let file = segment.referenceFile {
                    let pcm = PCMFile(try await store.file(id,file)); reference = try pcm.read(start: 0,count: pcm.frames)
                } else if request.referenceText != nil {
                    let pcm = PCMFile(try await store.file(id,"reference.pcm")); reference = try pcm.read(start: 0,count: pcm.frames)
                } else { reference = try original.clip(start: segment.speech.start,end: wholeSegment ? segment.speech.end : min(segment.speech.end,segment.speech.start+8)) }
                let referenceText: String
                if !savedText.isEmpty { referenceText = savedText }
                else {
                    let recognized = try await engine.transcribe(model: asr,audio: reference,progress: { _ in }).trimmingCharacters(in: .whitespacesAndNewlines)
                    if recognized.isEmpty {
                        guard segment.referenceNeedsTranscription != true else { throw ClientError("mobile.simple.no_speech") }
                        guard !segmentText.isEmpty else { throw ClientError("mobile.simple.no_speech") }
                        // Quiet or short clip: use the whole segment with its own transcript, never a clip with someone else's words.
                        reference = try original.clip(start: segment.speech.start,end: segment.speech.end)
                        referenceText = segmentText
                    } else { referenceText = recognized }
                }
                if segment.referenceText != referenceText {
                    segment.referenceText = referenceText; cp.segments[index] = segment; try await save(cp)
                }
                let fingerprint = phoneDubFingerprint([target.code,segment.speech.text,segment.translations[target.code]!,String(segment.speech.start),String(segment.speech.end),String(frames),cp.originalHash ?? "",phoneDubAudioHash(reference),referenceText,cloneIdentity])
                let rendered = try await store.file(id,"cache-"+fingerprint+".pcm")
                let metadata = try await store.file(id,"cache-"+fingerprint+".json")
                if let cached = try? PrivateFiles.read(PhoneDubCachedAudio.self,from: metadata), cached.frames == frames, (try? PCMFile(rendered).frames) == frames, (try? ModelHash.file(rendered)) == cached.hash {
                    cp.renders.removeAll { $0.target == target.code && $0.index == index }
                    cp.renders.append(PhoneDubRender(target: target.code,index: index,hash: cached.hash,frames: frames,fingerprint: fingerprint,file: rendered.lastPathComponent,segmentID: segment.id,scope: phoneDubSegmentScope(segment,target: target.code),originalHash: cp.originalHash,referenceHash: referenceIdentity,clonePin: cloneIdentity))
                    try await save(cp); continue
                }
                let generated = try await engine.clone(model: clone,text: segment.translations[target.code]!,reference: reference,referenceText: referenceText,progress: { value in await report(PhoneDubProgress(stage: "mobile.android.stage_render",percent: progress+Int(value.fraction))) })
                let fitted = try fitTiming(generated,frames: frames)
                try await check(); try PCMFile.write(fitted,to: rendered)
                cp.renders.removeAll { $0.target == target.code && $0.index == index }
                let hash = try ModelHash.file(rendered)
                try PrivateFiles.write(PhoneDubCachedAudio(hash: hash,frames: frames),to: metadata)
                cp.renders.append(PhoneDubRender(target: target.code,index: index,hash: hash,frames: frames,fingerprint: fingerprint,file: rendered.lastPathComponent,segmentID: segment.id,scope: phoneDubSegmentScope(segment,target: target.code),originalHash: cp.originalHash,referenceHash: referenceIdentity,clonePin: cloneIdentity))
                try await save(cp)
            }
            let renderedByIndex = cp.renders.filter { $0.target == target.code }.reduce(into: [Int:PhoneDubRender]()) { $0[$1.index] = $1 }
            let targetRenders = try cp.segments.indices.map { index -> PhoneDubRender in
                guard let render = renderedByIndex[index] else { throw ClientError("mobile.simple.checkpoint_failed") }; return render
            }
            var outputInputs: [String] = [cp.originalHash ?? "",request.sourceHash,String(request.audioOnly)]
            outputInputs.append(contentsOf: targetRenders.map { $0.fingerprint ?? "" })
            for segment in cp.segments { outputInputs.append(String(segment.gain ?? 1)); outputInputs.append(String(segment.muted == true)) }
            let outputFingerprint = phoneDubFingerprint(outputInputs)
            let previousOutput = cp.outputs.first(where: { $0.language == target.code })
            if let previous = previousOutput, previous.fingerprint == outputFingerprint, (try? ModelHash.file(await store.file(id,previous.file))) == previous.hash { continue }
            try await stage("mobile.simple.mixing",percent(0.9))
            var pieces: [(Int,PCMFile)] = []
            for (index,segment) in cp.segments.enumerated() { pieces.append((Int(segment.speech.start*24000),PCMFile(try await store.file(id,targetRenders[index].file!)))) }
            let mix = try mixTimeline(to: await store.file(id,"mix.pcm"),frames: cp.frames,segments: pieces,gains: cp.segments.map { $0.muted == true ? 0 : ($0.gain ?? 1) })
            let wave = try await store.file(id,"marked.wav"); try mix.markSynthetic(to: wave)
            try await stage("mobile.simple.exporting",percent(0.95))
            let result = try await media.finish(source: source,markedWave: wave,output: store.file(id,"output-\(targetIndex)-"+String(outputFingerprint.prefix(20))),audioOnly: request.audioOnly,progress: { fraction in await report(PhoneDubProgress(stage: "mobile.simple.exporting",percent: percent(0.95)+Int(fraction*2))) })
            try await check()
            guard result.deletingLastPathComponent().standardizedFileURL == (try await store.directory(id)).standardizedFileURL else { throw ClientError("mobile.simple.media_failed") }
            cp.outputs.removeAll { $0.language == target.code }
            cp.outputs.append(PhoneDubOutput(language: target.code,name: target.name,file: result.lastPathComponent,hash: try ModelHash.file(result),duration: Double(cp.frames)/24000,fingerprint: outputFingerprint))
            cp.outputs.sort { a,b in request.targets.firstIndex(where: { $0.code == a.language })! < request.targets.firstIndex(where: { $0.code == b.language })! }
            try await save(cp)
            if let previousOutput, previousOutput.file != result.lastPathComponent { try? FileManager.default.removeItem(at: await store.file(id,previousOutput.file)) }
            try? FileManager.default.removeItem(at: mix.url); try? FileManager.default.removeItem(at: wave)
        }
        cp.done = true; try await stage("mobile.android.stage_done",100)
        try? prunePhoneDubCaches(directory: await store.directory(id),referenced: Set(cp.renders.compactMap(\.file)))
        // Keep the immutable source until explicit deletion. It allows validating interrupted export recovery.
        return cp
    }
}

/// Content hashes encode every synthesis input, independent of mutable segment positions.
private func phoneDubFingerprint(_ parts: [String]) -> String {
    let data = parts.map { String($0.utf8.count)+":"+$0 }.joined().data(using: .utf8)!
    return SHA256.hash(data: data).map { String(format: "%02x",$0) }.joined()
}
private func phoneDubAudioHash(_ audio: PCMChunk) -> String {
    var bytes = Data(capacity: audio.samples.count*4)
    for value in audio.samples { var bits = value.bitPattern.littleEndian; withUnsafeBytes(of: &bits) { bytes.append(contentsOf: $0) } }
    return SHA256.hash(data: bytes).map { String(format: "%02x",$0) }.joined()
}
public func phoneDubSegmentScope(_ segment: PhoneDubSegment,target: String) -> String {
    phoneDubFingerprint([String(segment.speech.start),String(segment.speech.end),segment.speech.text,segment.translations[target] ?? "",segment.referenceFile ?? "",segment.referenceText ?? "",String(segment.referenceNeedsTranscription == true)])
}

/// Bounded spare renders support undo without allowing edits to fill a phone indefinitely.
internal func prunePhoneDubCaches(directory: URL,referenced: Set<String>,maxBytes: Int = 256*1024*1024,maxFiles: Int = 512) throws {
    let keys: Set<URLResourceKey> = [.fileSizeKey,.contentModificationDateKey]
    let files = try FileManager.default.contentsOfDirectory(at: directory,includingPropertiesForKeys: Array(keys))
    let unused = files.filter { $0.lastPathComponent.range(of: "^cache-[0-9a-f]{64}\\.pcm$",options: .regularExpression) != nil && !referenced.contains($0.lastPathComponent) }.sorted {
        ((try? $0.resourceValues(forKeys: keys).contentModificationDate) ?? .distantPast) > ((try? $1.resourceValues(forKeys: keys).contentModificationDate) ?? .distantPast)
    }
    var bytes = 0, count = 0
    for file in unused {
        let size = (try? file.resourceValues(forKeys: keys).fileSize) ?? 0
        if count >= maxFiles || size > maxBytes-bytes {
            if (try? FileManager.default.removeItem(at: file)) != nil { try? FileManager.default.removeItem(at: file.deletingPathExtension().appendingPathExtension("json")) }
        } else { bytes += size; count += 1 }
    }
    for file in files {
        let name = file.lastPathComponent
        if name.range(of: "^cache-[0-9a-f]{64}\\.pcm\\.pending$",options: .regularExpression) != nil { try? FileManager.default.removeItem(at: file) }
        if name.range(of: "^cache-[0-9a-f]{64}\\.json$",options: .regularExpression) != nil, !FileManager.default.fileExists(atPath: file.deletingPathExtension().appendingPathExtension("pcm").path) { try? FileManager.default.removeItem(at: file) }
    }
}
