import Foundation
import CryptoKit
import VSNative

public struct HandoffAsset: Codable, Sendable { public let path: String; public let size: Int; public let sha256: String }
public struct HandoffVoice: Codable, Sendable {
    public let id: String
    public let metadata: [String:JSONValue]
    public let reference: String
    public var pcm: String?
    public var preview: String?
}
public struct HandoffManifest: Codable, Sendable {
    public var format = "voicestudio.project"
    public var version = 1
    public var kind: String
    public var title: String
    public var document: [String:JSONValue]
    public var voices: [HandoffVoice]
    public var assets: [HandoffAsset]
    public var createdAt = Date().timeIntervalSince1970
}
public struct HandoffPreview: Sendable { public let title: String; public let kind: String; public let voices: Int; public let bytes: Int }
public struct HandoffRestored: Sendable { public let id: UUID; public let kind: String; public let title: String }

/// File and paired-device handoff. Untrusted records never replace a local project/voice or grant model licences.
public enum PhoneProjectHandoff {
    private static let platform = "ios"
    public static func preview(_ source: URL) throws -> HandoffPreview {
        let manifest = try readManifest(source)
        return HandoffPreview(title: manifest.title,kind: manifest.kind,voices: max(manifest.voices.count,manifest.kind == "dub" ? 1 : 0),bytes: manifest.assets.reduce(0) { $0+$1.size })
    }
    public static func exportProduction(_ project: ProductionProject,store: ProductionStore,voices: PhoneVoiceStore,configuration: String = "",to output: URL) async throws -> URL {
        let root = try await store.handoffDirectory(project.id)
        var document = project.handoff?.handoffObject?["document"]?.handoffObject ?? [:]
        document["script"] = .string(project.script); document["defaultVoice"] = .string(project.defaultVoice)
        document["cast"] = .object(project.cast.mapValues(JSONValue.string)); document["pronunciations"] = .object((project.pronunciations ?? [:]).mapValues(JSONValue.string)); document["speed"] = .number(Double(project.speed ?? 1))
        var snapshot = project; snapshot.handoff = nil
        document["native"] = .object(["platform":.string(platform),"project":try json(snapshot),"configuration":.string(configuration)])
        var files = try attachments(project.handoff,root: root)
        for (name,file) in try regularFiles(root) where name != "project.json" && !name.hasPrefix("handoff-assets/") && !name.hasSuffix(".pending") { files["native/production/"+name] = file }
        let selections = Set([project.defaultVoice]+Array(project.cast.values))
        let voiceRecords = try await exportVoices(selections,store: voices,files: &files)
        return try await write(HandoffManifest(kind: project.kind,title: project.title,document: clean(.object(document)).handoffObject ?? [:],voices: voiceRecords,assets: []),files: files,to: output)
    }
    public static func exportDub(_ id: UUID,store: PhoneDubStore,models: [PhoneModel],voices: PhoneVoiceStore,to output: URL) async throws -> URL {
        try await store.acquire(id)
        do {
            let request = try await store.request(id), checkpoint = try await store.checkpoint(id), root = try await store.directory(id)
            var files: [String:URL] = [:]
            for (name,file) in try regularFiles(root) where !name.hasPrefix("handoff-assets/") && !name.hasSuffix(".pending") { files["native/dub/"+name] = file }
            let memory = try? Data(contentsOf: root.appendingPathComponent("handoff.json"))
            let old = memory.flatMap { try? JSONDecoder().decode(JSONValue.self,from: $0) }
            files.merge(try attachments(old,root: root)) { current,_ in current }
            var document = old?.handoffObject?["document"]?.handoffObject ?? [:]
            document["source"] = .string("native/dub/"+request.sourceFile); document["duration"] = .number(Double(checkpoint.frames)/24000)
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("original.pcm").path) { document["original"] = .string("native/dub/original.pcm") }
            document["targets"] = try json(request.targets); document["audioOnly"] = .bool(request.audioOnly)
            document["models"] = try json(models.filter { request.modelPins[$0.id] != nil })
            document["segments"] = .array(try checkpoint.segments.map { segment in
                var value = try json(segment).handoffObject ?? [:]
                value["start"] = .number(segment.speech.start); value["end"] = .number(segment.speech.end); value["text"] = .string(segment.speech.text)
                if let name = segment.referenceFile { value["reference"] = .string("native/dub/"+name) }
                else if let text = request.referenceText { value["reference"] = .string("native/dub/reference.pcm"); value["referenceText"] = .string(text) }
                value["speech"] = nil; return .object(value)
            })
            document["native"] = .object(["platform":.string(platform),"request":try json(request),"checkpoint":try json(checkpoint)])
            let available = Set(try await voices.list().map { $0.id.uuidString })
            let selected = Set(checkpoint.segments.compactMap { $0.voiceID }.filter { available.contains($0) }.map { "phone:"+$0 })
            document["segments"] = .array((document["segments"]?.handoffArray ?? []).map { raw in var value = raw.handoffObject ?? [:]; if let id = value["voiceID"]?.string, !available.contains(id) { value["sourceVoiceID"] = .string(id); value["voiceID"] = nil }; return .object(value) })
            let records = try await exportVoices(selected,store: voices,files: &files)
            let result = try await write(HandoffManifest(kind: "dub",title: request.title,document: clean(.object(document)).handoffObject ?? [:],voices: records,assets: []),files: files,to: output)
            await store.release(id); return result
        } catch { await store.release(id); throw error }
    }
    public static func restore(_ source: URL,production: ProductionStore,dubs: PhoneDubStore,voices: PhoneVoiceStore,models: [PhoneModel],configuration: String = "",allowVoices: Bool = false) async throws -> HandoffRestored {
        let manifest = try readManifest(source)
        guard (manifest.voices.isEmpty && manifest.kind != "dub") || allowVoices else { throw ClientError("mobile.handoff.voices_required") }
        let staging = FileManager.default.temporaryDirectory.appendingPathComponent("handoff-"+UUID().uuidString)
        let files = try await ProjectArchive.extract(source,to: staging)
        defer { try? FileManager.default.removeItem(at: staging) }
        for asset in manifest.assets { guard let file = files[asset.path], try ModelHash.file(file) == asset.sha256, (try file.resourceValues(forKeys: [.fileSizeKey]).fileSize) == asset.size else { throw ClientError("mobile.handoff.invalid") } }
        for asset in manifest.assets where asset.path.hasSuffix(".pcm") {
            let pcm = PCMFile(files[asset.path]!); let count = try pcm.frames
            for start in stride(from: 0,to: count,by: 8192) { _ = try pcm.read(start: start,count: min(8192,count-start)) }
        }
        guard Set(files.keys) == Set(manifest.assets.map(\.path)+["manifest.json"]) else { throw ClientError("mobile.handoff.invalid") }
        var imported: [PhoneVoice] = [], mapping: [String:String] = [:], published: URL?
        do {
            for voice in manifest.voices {
                guard let reference = files[voice.reference] else { throw ClientError("mobile.handoff.invalid") }
                let pcm: PCMFile
                if let name = voice.pcm, let file = files[name] { pcm = PCMFile(file) }
                else { pcm = try await AVPhoneAudioDecoder().decode(source: reference,output: staging.appendingPathComponent(UUID().uuidString+".pcm"),maxSeconds: 30,progress: { _ in }) }
                let frames = try pcm.frames; guard (2400...720000).contains(frames) else { throw ClientError("mobile.handoff.invalid") }
                let metadata = voice.metadata
                let saved = try await voices.installHandoff(name: metadata["profile_name"]?.string ?? manifest.title,transcript: metadata["ref_text"]?.string ?? "",reference: pcm.read(start: 0,count: frames),metadata: metadata,synthetic: metadata["kind"]?.string == "design" || containsMarker(reference),consent: allowVoices)
                imported.append(saved); mapping[voice.id] = "phone:"+saved.id.uuidString
                if voice.id.hasPrefix("phone:") { mapping[String(voice.id.dropFirst(6))] = "phone:"+saved.id.uuidString }
                if let name = voice.preview, let preview = files[name], (try preview.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) <= 6_000_000, let wave = try await voices.handoffFiles(saved)["reference.wav"] { try copy(preview,to: wave.deletingLastPathComponent().appendingPathComponent("preview.wav")) }
            }
            if manifest.kind != "dub" {
                let selections = [manifest.document["defaultVoice"]?.string ?? "preset:0"]+Array((manifest.document["cast"]?.handoffObject ?? [:]).compactMapValues(\.string).values)
                guard selections.allSatisfy({ $0.isEmpty || $0.hasPrefix("preset:") || mapping[$0] != nil }) else { throw ClientError("mobile.handoff.invalid") }
            }
            var memory = manifest; memory.document = rewrite(.object(manifest.document),mapping: mapping).handoffObject ?? [:]
            let id = UUID(), document = memory.document
            if manifest.kind != "dub" {
                let cast = (document["cast"]?.handoffObject ?? [:]).compactMapValues(\.string)
                let pronunciation = (document["pronunciations"]?.handoffObject ?? [:]).compactMapValues(\.string)
                var project = ProductionProject(id: id,kind: manifest.kind,title: manifest.title,script: document["script"]?.string ?? "",defaultVoice: document["defaultVoice"]?.string ?? "preset:0",cast: cast,pronunciations: pronunciation,speed: Float(document["speed"]?.number ?? 1))
                project.handoff = try json(memory)
                let root = try await production.handoffDirectory(id); guard !FileManager.default.fileExists(atPath: root.path) else { throw ClientError("mobile.handoff.invalid") }
                try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); published = root
                try copyAttachments(manifest,files: files,to: root)
                if manifest.document["native"]?.handoffObject?["platform"]?.string == platform {
                    for asset in manifest.assets where asset.path.hasPrefix("native/production/") {
                        let name = String(asset.path.dropFirst("native/production/".count)); guard name != "project.json" else { continue }
                        try copy(files[asset.path]!,to: root.appendingPathComponent(name))
                    }
                    // UUIDs intentionally change. Re-key compatible immutable line caches and complete renders.
                    if let native = manifest.document["native"]?.handoffObject, native["configuration"]?.string == configuration, let raw = native["project"], let old = try? decode(ProductionProject.self,raw) {
                        let plan = try? old.plan()
                        for segment in plan?.segments ?? [] {
                            let oldVoice = old.voice(for: segment.role), newVoice = mapping[oldVoice] ?? oldVoice
                            let before = segmentKey(configuration,oldVoice,segment), after = segmentKey(configuration,newVoice,segment)
                            let cached = root.appendingPathComponent("segments/"+before+".pcm")
                            if FileManager.default.fileExists(atPath: cached.path), before != after { try copy(cached,to: root.appendingPathComponent("segments/"+after+".pcm")) }
                        }
                        let oldRender = try await production.handoffRenderDirectory(old,configuration: configuration), newRender = try await production.handoffRenderDirectory(project,configuration: configuration)
                        let previous = root.appendingPathComponent(oldRender.lastPathComponent)
                        let sameInputs = old.script == project.script && (old.speed ?? 1) == (project.speed ?? 1) && (old.pronunciations ?? [:]) == (project.pronunciations ?? [:]) && (mapping[old.defaultVoice] ?? old.defaultVoice) == project.defaultVoice && old.cast.mapValues { mapping[$0] ?? $0 } == project.cast
                        if sameInputs, FileManager.default.fileExists(atPath: previous.path), previous.lastPathComponent != newRender.lastPathComponent { try FileManager.default.copyItem(at: previous,to: newRender) }
                    }
                }
                try Task.checkCancellation(); try await production.save(project)
            } else {
                let root = try await dubs.directory(id); guard !FileManager.default.fileExists(atPath: root.path) else { throw ClientError("mobile.handoff.invalid") }
                try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); published = root
                try copyAttachments(manifest,files: files,to: root)
                guard let sourceName = document["source"]?.string, let originalSource = files[sourceName] else { throw ClientError("mobile.handoff.invalid") }
                let sourceFile = "source."+originalSource.pathExtension; try copy(originalSource,to: root.appendingPathComponent(sourceFile))
                let pcm: PCMFile
                if let name = document["original"]?.string, let original = files[name] { try copy(original,to: root.appendingPathComponent("original.pcm")); pcm = PCMFile(root.appendingPathComponent("original.pcm")) }
                else { pcm = try await AVPhoneAudioDecoder().decode(source: originalSource,output: root.appendingPathComponent("original.pcm"),maxSeconds: 14400,progress: { _ in }) }
                let frames = try pcm.frames; guard frames > 0, frames <= 345600000 else { throw ClientError("mobile.handoff.invalid") }
                let targets = try decode([PhoneDubTarget].self,document["targets"] ?? .array([])); guard !targets.isEmpty, targets.count <= 20 else { throw ClientError("mobile.handoff.invalid") }
                let selected = selectModels(document["models"],from: models)
                guard selected.count == 3 else { throw ClientError("mobile.local.model_required") }
                let request = PhoneDubRequest(id: id,title: manifest.title,sourceFile: sourceFile,sourceHash: try ModelHash.file(root.appendingPathComponent(sourceFile)),targets: targets,modelPins: try Dictionary(uniqueKeysWithValues: selected.map { ($0.id,try phoneModelPin($0)) }),createdAt: Date(),consentAt: Date(),referenceText: nil,audioOnly: document["audioOnly"]?.handoffBool ?? false)
                var checkpoint = PhoneDubCheckpoint(); checkpoint.frames = frames; checkpoint.originalHash = try ModelHash.file(pcm.url); checkpoint.nextFrame = frames
                var referenceNames: [String:String] = [:]
                for raw in document["segments"]?.handoffArray ?? [] {
                    guard let value = raw.handoffObject, let start = value["start"]?.number, let end = value["end"]?.number, let text = value["text"]?.string, start >= 0, end > start, end <= Double(frames)/24000+0.01, !text.isEmpty else { throw ClientError("mobile.handoff.invalid") }
                    var segment = PhoneDubSegment(speech: SpeechSegment(start: start,end: end,text: text),referenceText: value["referenceText"]?.string,translations: (value["translations"]?.handoffObject ?? [:]).compactMapValues(\.string),id: value["id"]?.string ?? UUID().uuidString,referenceNeedsTranscription: value["referenceNeedsTranscription"]?.handoffBool,gain: value["gain"]?.number,muted: value["muted"]?.handoffBool)
                    segment.speaker = value["speaker"]?.string; segment.referenceOrigin = value["referenceOrigin"]?.string; segment.voiceID = value["voiceID"]?.string.flatMap { text in mapping.values.contains("phone:"+text) || mapping.values.contains(text) ? text.replacingOccurrences(of: "phone:",with: "") : nil }
                    if let path = value["reference"]?.string, let existing = referenceNames[path] { segment.referenceFile = existing }
                    else if let path = value["reference"]?.string, let reference = files[path] {
                        let refPCM: PCMFile
                        if reference.pathExtension == "pcm" { refPCM = PCMFile(reference) }
                        else { refPCM = try await AVPhoneAudioDecoder().decode(source: reference,output: staging.appendingPathComponent(UUID().uuidString+".pcm"),maxSeconds: 30,progress: { _ in }) }
                        guard (2400...720000).contains(try refPCM.frames) else { throw ClientError("mobile.handoff.invalid") }
                        let name = "edited-reference-"+UUID().uuidString+".pcm"; try copy(refPCM.url,to: root.appendingPathComponent(name)); segment.referenceFile = name; referenceNames[path] = name
                    }
                    if segment.referenceFile == nil, let selected = segment.voiceID, let voice = imported.first(where: { $0.id.uuidString == selected }) {
                        let key = "voice:"+selected
                        if let name = referenceNames[key] { segment.referenceFile = name }
                        else if let reference = try await voices.handoffFiles(voice)["reference.pcm"] { let name = "edited-reference-"+UUID().uuidString+".pcm"; try copy(reference,to: root.appendingPathComponent(name)); referenceNames[key] = name; segment.referenceFile = name }
                        segment.referenceText = voice.transcript
                        if segment.referenceOrigin == nil { segment.referenceOrigin = "cast" }
                    }
                    checkpoint.segments.append(segment)
                }
                checkpoint.stage = "mobile.android.stage_translate"; checkpoint.percent = 35
                // Same-platform untouched checkpoints retain validated cache receipts and generated outputs.
                if let native = manifest.document["native"]?.handoffObject, native["platform"]?.string == platform, (native["request"]?.handoffObject?["audioOnly"]?.handoffBool ?? false) == request.audioOnly, let targetValue = native["request"]?.handoffObject?["targets"], (try? decode([PhoneDubTarget].self,targetValue)) == request.targets, let pins = native["request"]?.handoffObject?["modelPins"]?.handoffObject, pins.compactMapValues(\.string) == request.modelPins, let rawCheckpoint = native["checkpoint"], let old = try? decode(PhoneDubCheckpoint.self,rawCheckpoint) {
                    for asset in manifest.assets where asset.path.hasPrefix("native/dub/") {
                        let name = String(asset.path.dropFirst("native/dub/".count)); guard !["request.json","checkpoint.json","handoff.json"].contains(name) else { continue }; _ = try ProjectArchive.path(name); try copy(files[asset.path]!,to: root.appendingPathComponent(name))
                    }
                    let hashes = Dictionary(uniqueKeysWithValues: manifest.assets.map { ($0.path,$0.sha256) })
                    var restoredReferenceHashes: [String:String] = [:]
                    for name in Set(checkpoint.segments.compactMap(\.referenceFile)) { restoredReferenceHashes[name] = try ModelHash.file(root.appendingPathComponent(name)) }
                    let referencesMatch = old.segments.enumerated().allSatisfy { index,segment in
                        let before = segment.referenceFile.map { "native/dub/"+$0 } ?? (native["request"]?.handoffObject?["referenceText"]?.string == nil ? nil : "native/dub/reference.pcm")
                        let after = checkpoint.segments.indices.contains(index) ? checkpoint.segments[index].referenceFile : nil
                        if before == nil && after == nil { return true }
                        guard let before, let after, let hash = hashes[before] else { return false }; return hash == restoredReferenceHashes[after]
                    }
                    if referencesMatch, native["request"]?.handoffObject?["sourceHash"]?.string == request.sourceHash, old.originalHash == checkpoint.originalHash, old.frames == frames, old.segments.count == checkpoint.segments.count, zip(old.segments,checkpoint.segments).allSatisfy({ before,after in before.speech == after.speech && before.translations == after.translations && (before.referenceText ?? native["request"]?.handoffObject?["referenceText"]?.string) == after.referenceText && before.gain == after.gain && before.muted == after.muted && before.speaker == after.speaker && (before.referenceNeedsTranscription == true) == (after.referenceNeedsTranscription == true) }) {
                        let current = checkpoint.segments
                        // References are copied under fresh names (global references become per-line).
                        // Rebind receipts only when their original scope was valid and all inputs above match.
                        let uniqueReceipts = Set(old.renders.map { $0.target+":"+String($0.index) }).count == old.renders.count
                        let completeCoverage = !old.done || (!current.isEmpty && old.renders.count == current.count*request.targets.count && old.outputs.count == request.targets.count)
                        var validReceipts = uniqueReceipts && completeCoverage
                        for (index,output) in old.outputs.enumerated() {
                            guard request.targets.indices.contains(index), request.targets[index].code == output.language,
                                  let file = try? await dubs.file(id,output.file), (try? ModelHash.file(file)) == output.hash else { validReceipts = false; break }
                        }
                        for render in old.renders {
                            guard validReceipts else { break }
                            guard old.segments.indices.contains(render.index), request.targets.contains(where: { $0.code == render.target }) else { validReceipts = false; break }
                            let before = old.segments[render.index]
                            let referenceName = before.referenceFile ?? (native["request"]?.handoffObject?["referenceText"]?.string == nil ? nil : "reference.pcm")
                            let referenceHash = referenceName.flatMap { hashes["native/dub/"+$0] } ?? ""
                            guard let name = render.file, let file = try? await dubs.file(id,name), render.fingerprint != nil,
                                  render.segmentID == before.id, render.scope == phoneDubSegmentScope(before,target: render.target),
                                  render.referenceHash == referenceHash, render.originalHash == old.originalHash,
                                  render.clonePin == selected.first(where: { $0.capability == .clone }).flatMap({ request.modelPins[$0.id] }),
                                  render.frames == max(1,Int(before.speech.end*24000)-Int(before.speech.start*24000)),
                                  (try? PCMFile(file).frames) == render.frames, (try? ModelHash.file(file)) == render.hash else { validReceipts = false; break }
                        }
                        if validReceipts {
                            checkpoint = old; checkpoint.segments = current
                            checkpoint.renders = old.renders.map { render in
                                var updated = render; updated.segmentID = current[render.index].id
                                updated.scope = phoneDubSegmentScope(current[render.index],target: render.target)
                                return updated
                            }
                        }
                    }
                }
                try await dubs.save(id,checkpoint); try Task.checkCancellation()
                try PrivateFiles.write(request,to: root.appendingPathComponent("request.json"))
                try JSONEncoder().encode(memory).write(to: root.appendingPathComponent("handoff.json"),options: .atomic)
                _ = try await dubs.checkpoint(id)
            }
            return HandoffRestored(id: id,kind: manifest.kind,title: manifest.title)
        } catch {
            if let published { try? FileManager.default.removeItem(at: published) }
            for voice in imported { try? await voices.delete(voice) }; throw error
        }
    }
    private static func readManifest(_ source: URL) throws -> HandoffManifest {
        let entries = try ProjectArchive.entries(source)
        guard let entry = entries.first(where: { $0.path == "manifest.json" }), entry.size <= 4_194_304 else { throw ClientError("mobile.handoff.invalid") }
        let reader = try FileHandle(forReadingFrom: source); defer { try? reader.close() }; try reader.seek(toOffset: entry.offset)
        let data = try reader.read(upToCount: entry.size) ?? Data()
        guard data.count == entry.size, data.withUnsafeBytes({ vs_book_crc32($0.bindMemory(to: UInt8.self).baseAddress,$0.count) }) == entry.crc else { throw ClientError("mobile.handoff.invalid") }
        var manifest = try JSONDecoder().decode(HandoffManifest.self,from: data)
        guard manifest.format == "voicestudio.project", manifest.version == 1, ["stories","audiobook","dub"].contains(manifest.kind), !manifest.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, manifest.title.count <= 250, manifest.voices.count <= 100, manifest.assets.count+1 <= ProjectArchive.maxFiles else { throw ClientError("mobile.handoff.invalid") }
        var names = Set<String>(), total = 0
        let allowed = Set(["json","pcm","wav","mp3","m4a","m4b","ogg","flac","aac","aiff","caf","mp4","mov","mkv","webm","media","jpg","jpeg","png","webp","srt","vtt","txt"])
        for asset in manifest.assets {
            _ = try ProjectArchive.path(asset.path)
            guard ["assets/","voices/","native/"].contains(where: asset.path.hasPrefix), allowed.contains((asset.path as NSString).pathExtension.lowercased()), asset.size >= 0, asset.size <= ProjectArchive.maxMember, asset.sha256.count == 64, asset.sha256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }), names.insert(asset.path.lowercased()).inserted else { throw ClientError("mobile.handoff.invalid") }
            total += asset.size
        }
        guard total <= ProjectArchive.maxBytes, Set(manifest.voices.map(\.id)).count == manifest.voices.count, manifest.voices.allSatisfy({ voice in manifest.assets.contains { $0.path == voice.reference } }), entries.count == manifest.assets.count+1 else { throw ClientError("mobile.handoff.invalid") }
        manifest.document = try cleanChecked(.object(manifest.document),depth: 0).handoffObject ?? [:]
        if manifest.kind != "dub" { guard let text = manifest.document["script"]?.string, text.utf16.count <= 1_000_000, (0.5...2).contains(manifest.document["speed"]?.number ?? 1) else { throw ClientError("mobile.handoff.invalid") } }
        return manifest
    }
    private static func write(_ manifest: HandoffManifest,files: [String:URL],to output: URL) async throws -> URL {
        var manifest = manifest
        manifest.assets = try files.sorted(by: { $0.key < $1.key }).map { name,file in HandoffAsset(path: name,size: try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0,sha256: try ModelHash.file(file)) }
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent("manifest-"+UUID().uuidString+".json")
        defer { try? FileManager.default.removeItem(at: temp) }
        let data = try JSONEncoder().encode(manifest); guard data.count <= 4_194_304 else { throw ClientError("mobile.handoff.too_large") }; try data.write(to: temp)
        return try await ProjectArchive.write(entries: [ProjectArchiveFile(path: "manifest.json",file: temp)]+files.map { ProjectArchiveFile(path: $0.key,file: $0.value) },to: output)
    }
    private static func exportVoices(_ selected: Set<String>,store: PhoneVoiceStore,files: inout [String:URL]) async throws -> [HandoffVoice] {
        var records: [HandoffVoice] = []
        for voice in try await store.list() where selected.contains("phone:"+voice.id.uuidString) || selected.contains(voice.id.uuidString) {
            let source = try await store.handoffFiles(voice), prefix = "voices/"+voice.id.uuidString
            guard let wave = source["reference.wav"], let pcm = source["reference.pcm"] else { throw ClientError("mobile.handoff.invalid") }
            files[prefix+"/ref_audio.wav"] = wave; files[prefix+"/reference.pcm"] = pcm
            var metadata: [String:JSONValue] = ["bundle_version":.number(1),"profile_name":.string(voice.name),"ref_text":.string(voice.transcript),"kind":.string(voice.designInstruction == nil ? "clone" : "design"),"instruct":.string(voice.designInstruction ?? ""),"language":.string("Auto")]
            if let value = voice.designSeed { metadata["phone_design_seed"] = .string(String(value)); metadata["seed"] = .number(Double(value)) }
            if let attribution = voice.sourceAttribution { metadata["sourceAttribution"] = .object(attribution.mapValues(JSONValue.string)) }
            if let value = voice.modelRepo { metadata["modelRepo"] = .string(value) }; if let value = voice.modelRevision { metadata["modelRevision"] = .string(value) }
            var record = HandoffVoice(id: "phone:"+voice.id.uuidString,metadata: metadata,reference: prefix+"/ref_audio.wav",pcm: prefix+"/reference.pcm")
            if let preview = source["preview.wav"] { files[prefix+"/preview.wav"] = preview; record.preview = prefix+"/preview.wav" }
            records.append(record)
        }
        guard selected.filter({ $0.hasPrefix("phone:") }).count == records.count else { throw ClientError("mobile.handoff.invalid") }
        return records
    }
    private static func attachments(_ memory: JSONValue?,root: URL) throws -> [String:URL] {
        guard let memory else { return [:] }
        let manifest = try decode(HandoffManifest.self,memory)
        var files: [String:URL] = [:]
        for asset in manifest.assets { let file = root.appendingPathComponent("handoff-assets/"+asset.path); if FileManager.default.fileExists(atPath: file.path) { files[asset.path] = file } }
        return files
    }
    private static func copyAttachments(_ manifest: HandoffManifest,files: [String:URL],to root: URL) throws { for asset in manifest.assets { try copy(files[asset.path]!,to: root.appendingPathComponent("handoff-assets/"+asset.path)) } }
    private static func copy(_ source: URL,to destination: URL) throws { if source.standardizedFileURL == destination.standardizedFileURL { return }; try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(),withIntermediateDirectories: true); guard !FileManager.default.fileExists(atPath: destination.path) else { return }; try FileManager.default.copyItem(at: source,to: destination) }
    private static func regularFiles(_ root: URL) throws -> [String:URL] {
        guard FileManager.default.fileExists(atPath: root.path) else { return [:] }
        let canonicalRoot = root.resolvingSymlinksInPath().standardizedFileURL
        guard let items = FileManager.default.enumerator(at: canonicalRoot,includingPropertiesForKeys: [.isRegularFileKey,.isSymbolicLinkKey]) else { throw ClientError("mobile.errors.file") }
        var files: [String:URL] = [:]
        for case let file as URL in items {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey,.isSymbolicLinkKey]); guard values.isSymbolicLink != true else { throw ClientError("mobile.handoff.invalid") }
            if values.isRegularFile == true {
                let prefix = canonicalRoot.path+"/", canonicalFile = file.resolvingSymlinksInPath().standardizedFileURL
                guard canonicalFile.path.hasPrefix(prefix) else { throw ClientError("mobile.handoff.invalid") }
                let name = String(canonicalFile.path.dropFirst(prefix.count)); _ = try ProjectArchive.path(name); files[name] = canonicalFile
            }
        }; return files
    }
    private static func selectModels(_ raw: JSONValue?,from models: [PhoneModel]) -> [PhoneModel] {
        let specs = raw?.handoffArray ?? []
        return [ModelCapability.asr,.translate,.clone].compactMap { capability in
            let spec = specs.first { $0.handoffObject?["capability"]?.string == capability.rawValue }?.handoffObject
            return models.first { $0.capability == capability && (spec == nil || ($0.id == spec?["id"]?.string && $0.repo == spec?["repo"]?.string && $0.revision == spec?["revision"]?.string)) }
        }
    }
    private static func containsMarker(_ file: URL) -> Bool { guard let reader = try? FileHandle(forReadingFrom: file) else { return false }; defer { try? reader.close() }; let marker = Data("VoiceStudio synthetic audio".utf8); var tail = Data(); while let data = try? reader.read(upToCount: 262144), !data.isEmpty { let joined = tail+data; if joined.range(of: marker) != nil { return true }; tail = joined.suffix(marker.count) }; return false }
    private static func segmentKey(_ config: String,_ voice: String,_ segment: ProductionSegment) -> String { SHA256.hash(data: Data("v2|\(config)|\(voice)|\(segment.speed)|\(segment.pause)|\(segment.text)".utf8)).map { String(format: "%02x",$0) }.joined() }
    private static func rewrite(_ value: JSONValue,mapping: [String:String]) -> JSONValue {
        if case .object(let values) = value { return .object(values.mapValues { rewrite($0,mapping: mapping) }.mapValues { $0 }.reduce(into: [:]) { result,pair in
            let key = pair.key, child = pair.value
            if ["defaultVoice","profileId","voice","voiceID","voice_id"].contains(key), let id = child.string { let changed = mapping[id] ?? id; result[key] = .string(key == "voiceID" ? changed.replacingOccurrences(of: "phone:",with: "") : changed) }
            else if ["cast","voiceCast"].contains(key), let map = child.handoffObject { result[key] = .object(map.mapValues { value in value.string.map { .string(mapping[$0] ?? $0) } ?? value }) }
            else { result[key] = child }
        }) }
        if case .array(let values) = value { return .array(values.map { rewrite($0,mapping: mapping) }) }; return value
    }
    private static func clean(_ value: JSONValue) -> JSONValue { (try? cleanChecked(value,depth: 0)) ?? .object([:]) }
    private static func cleanChecked(_ value: JSONValue,depth: Int) throws -> JSONValue {
        guard depth <= 64 else { throw ClientError("mobile.handoff.invalid") }
        let denied = Set(["credentials","authorization","cookies","cookie_file","api_key","apikey","access_token","refresh_token","sessiontoken","verified","verified_owner","ownershipverified","consentat","consent_at","licencereceipts","licensereceipts","verified_own_voice","verifiedownvoice","consent_text","consent_audio_path","consent_recorded_at","consentrecordedat","consenttext","consentaudiopath"])
        if case .object(let values) = value { var result: [String:JSONValue] = [:]; for (key,child) in values where !denied.contains(key.lowercased()) {
            if let text = child.string, (key.lowercased().contains("path") || key.lowercased().hasSuffix("file")), text.hasPrefix("/") || text.hasPrefix("\\") || text.range(of: "^[A-Za-z]:",options: .regularExpression) != nil { result[key] = .null }
            else { result[key] = try cleanChecked(child,depth: depth+1) }
        }; return .object(result) }
        if case .array(let values) = value { guard values.count <= 20000 else { throw ClientError("mobile.handoff.invalid") }; return .array(try values.map { try cleanChecked($0,depth: depth+1) }) }; return value
    }
    private static func json<T: Encodable>(_ value: T) throws -> JSONValue { try JSONDecoder().decode(JSONValue.self,from: JSONEncoder().encode(value)) }
    private static func decode<T: Decodable>(_ type: T.Type,_ value: JSONValue) throws -> T { try JSONDecoder().decode(type,from: JSONEncoder().encode(value)) }
}

extension JSONValue {
    var handoffObject: [String:JSONValue]? { if case .object(let value) = self { return value }; return nil }
    var handoffArray: [JSONValue]? { if case .array(let value) = self { return value }; return nil }
    var handoffBool: Bool? { if case .bool(let value) = self { return value }; return nil }
}
