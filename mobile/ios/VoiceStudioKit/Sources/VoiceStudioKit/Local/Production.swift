import Foundation
import CryptoKit

public struct ProductionSegment: Sendable, Equatable {
    public let chapter: Int
    public let role: String
    public let text: String
    public let speed: Float
    public let pause: Double
    public init(chapter: Int = 0, role: String = "", text: String, speed: Float = 1, pause: Double = 0) { self.chapter = chapter; self.role = role; self.text = text; self.speed = speed; self.pause = pause }
}

/// The same small manuscript grammar on both phones: headings and persistent voice tags.
public struct ProductionPlan: Sendable {
    public let segments: [ProductionSegment]
    public let chapters: [String]
    public var roles: [String] { segments.map(\.role).filter { !$0.isEmpty }.reduce(into: []) { if !$0.contains($1) { $0.append($1) } } }
    public init(script: String, pronunciations: [String:String] = [:], speed: Float = 1) throws {
        guard speed.isFinite, (0.5...2).contains(speed), pronunciations.count <= 100 else { throw ClientError("mobile.books.invalid_controls") }
        let replacements = try NarrationText.rules(pronunciations)
        guard script.utf16.count <= 1_000_000 else { throw ClientError("mobile.simple.audio_long") }
        var segments: [ProductionSegment] = [], chapters = [""], role = ""
        // Desktop [emotion:TEXT] directions (#3422): phone models take no emotion, so the tag is
        // dropped (never read aloud). Stripped from the whole script before it is split into lines,
        // because a desktop tag may span lines; the line breaks inside a tag are kept so a voice tag
        // after it still starts its line. A tag never spans a "# " heading, which the desktop
        // parser splits first. Case-insensitive like the desktop parser.
        let emotionPattern = try NSRegularExpression(pattern: "\\[emotion:[^\\]\\[]*\\]", options: [.caseInsensitive])
        let stripped = NSMutableString(string: script)
        for tag in emotionPattern.matches(in: script, range: NSRange(script.startIndex..., in: script)).reversed() {
            let text = stripped.substring(with: tag.range)
            if text.components(separatedBy: .newlines).dropFirst().contains(where: { $0.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("# ") }) { continue }
            let breaks = text.filter(\.isNewline)
            stripped.replaceCharacters(in: tag.range, with: breaks.isEmpty ? " " : breaks)
        }
        let spoken = stripped as String
        for raw in spoken.components(separatedBy: .newlines) {
            var line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if line.hasPrefix("# ") {
                let title = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                if chapters.count == 1 && chapters[0].isEmpty && segments.isEmpty { chapters[0] = title }
                else { chapters.append(title) }
                continue
            }
            if line.hasPrefix("[voice:"), let end = line.firstIndex(of: "]") {
                role = String(line[line.index(line.startIndex, offsetBy: 7)..<end]).trimmingCharacters(in: .whitespaces)
                line = String(line[line.index(after: end)...]).trimmingCharacters(in: .whitespaces)
            }
            var lineSpeed = speed
            if line.hasPrefix("[speed:"), let end = line.firstIndex(of: "]") {
                guard let value = Float(line[line.index(line.startIndex,offsetBy: 7)..<end]), value.isFinite, (0.5...2).contains(value) else { throw ClientError("mobile.books.invalid_controls") }
                lineSpeed = value; line = String(line[line.index(after: end)...]).trimmingCharacters(in: .whitespaces)
            }
            let pausePattern = try NSRegularExpression(pattern: "\\[pause(?::|[ \\t]+)([0-9]+(?:\\.[0-9]+)?)(ms|s)?\\]")
            var cursor = line.startIndex
            func appendText(_ value: String) throws {
                let value = try NarrationText.apply(value,rules: replacements)
                var cursor = value.startIndex
                while cursor < value.endIndex {
                    var end = cursor, units = 0
                    while end < value.unicodeScalars.endIndex {
                        let size = value.unicodeScalars[end].value > 0xFFFF ? 2 : 1
                        if units+size > 400 { break }
                        units += size; end = value.unicodeScalars.index(after: end)
                    }
                    if end < value.endIndex, let boundary = value[cursor..<end].lastIndex(where: { $0.isWhitespace }), boundary > cursor { end = boundary }
                    let text = String(value[cursor..<end]).trimmingCharacters(in: .whitespaces)
                    if !text.isEmpty { segments.append(ProductionSegment(chapter: chapters.count-1,role: role,text: text,speed: lineSpeed)) }
                    guard segments.count <= 10000 else { throw ClientError("mobile.simple.audio_long") }
                    cursor = end
                    while cursor < value.endIndex && value[cursor].isWhitespace { cursor = value.index(after: cursor) }
                }
            }
            for match in pausePattern.matches(in: line,range: NSRange(line.startIndex...,in: line)) {
                guard let range = Range(match.range,in: line), let number = Range(match.range(at: 1),in: line), let rawDuration = Double(line[number]) else { throw ClientError("mobile.books.invalid_controls") }
                try appendText(String(line[cursor..<range.lowerBound]))
                let unit = Range(match.range(at: 2),in: line).map { String(line[$0]) } ?? "s"
                let duration = rawDuration/(unit == "ms" ? 1000 : 1)
                guard duration.isFinite, duration > 0, duration <= 30 else { throw ClientError("mobile.books.invalid_controls") }
                segments.append(ProductionSegment(chapter: chapters.count-1,role: role,text: "",pause: duration))
                cursor = range.upperBound
            }
            try appendText(String(line[cursor...]))
        }
        guard segments.contains(where: { !$0.text.isEmpty }), segments.count <= 10000 else { throw ClientError("audiobook.blocked.no_script") }
        self.segments = segments; self.chapters = chapters
    }
}

public struct ProductionProject: Codable, Sendable, Identifiable, Equatable {
    public var id: UUID
    public var kind: String
    public var title: String
    public var script: String
    /// `preset:N` or `phone:<UUID>`. Empty cast values use the default voice.
    public var defaultVoice: String
    public var cast: [String: String]
    public var pronunciations: [String:String]?
    public var speed: Float?
    public var handoff: JSONValue?
    public var modifiedAt: Date
    public init(id: UUID = UUID(), kind: String, title: String, script: String = "", defaultVoice: String = "preset:0", cast: [String:String] = [:], modifiedAt: Date = Date(), pronunciations: [String:String]? = nil, speed: Float? = nil, handoff: JSONValue? = nil) {
        self.id = id; self.kind = kind; self.title = title; self.script = script; self.defaultVoice = defaultVoice; self.cast = cast; self.modifiedAt = modifiedAt; self.pronunciations = pronunciations; self.speed = speed; self.handoff = handoff
    }
    public func plan() throws -> ProductionPlan { try ProductionPlan(script: script,pronunciations: pronunciations ?? [:],speed: speed ?? 1) }
    public func voice(for role: String) -> String { cast[role].flatMap { $0.isEmpty ? nil : $0 } ?? defaultVoice }
}

/// Durable drafts and immutable render caches. Only a completed render publishes an output.
public actor ProductionStore {
    private let root: URL
    private var rendering = Set<UUID>()
    public init(root: URL) throws { self.root = root; try PrivateFiles.directory(root) }
    private func directory(_ id: UUID) throws -> URL { try PrivateFiles.child(root,id.uuidString) }
    internal func handoffDirectory(_ id: UUID) throws -> URL {
        guard !rendering.contains(id) else { throw ClientError("audiobook.blocked.busy") }
        return try directory(id)
    }
    internal func handoffRenderDirectory(_ project: ProductionProject,configuration: String) throws -> URL { try renderDirectory(project,configuration) }
    public func save(_ project: ProductionProject) throws {
        guard ["stories","audiobook"].contains(project.kind), !project.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, project.title.count <= 200, project.script.utf16.count <= 1_000_000 else { throw ClientError("mobile.errors.file") }
        let dir = try directory(project.id); try PrivateFiles.directory(dir)
        try PrivateFiles.write(project,to: dir.appendingPathComponent("project.json"))
    }
    public func list() throws -> [ProductionProject] {
        try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil).compactMap { url in
            guard let id = UUID(uuidString: url.lastPathComponent), let dir = try? directory(id) else { return nil }
            return try? PrivateFiles.read(ProductionProject.self,from: PrivateFiles.child(dir,"project.json"))
        }.sorted { $0.modifiedAt > $1.modifiedAt }
    }
    public func delete(_ project: ProductionProject) throws {
        guard !rendering.contains(project.id) else { throw ClientError("audiobook.blocked.busy") }
        try FileManager.default.removeItem(at: directory(project.id))
    }
    private func key(_ project: ProductionProject, _ configuration: String) throws -> String {
        // Titles and save timestamps don't affect audio; script, cast and engine revisions do.
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        // Scripts with a desktop [emotion:] tag parse differently since #3422 (the tag is no longer
        // spoken), so only they get a new key; every other script keeps its renders.
        let version = project.script.range(of: "[emotion:", options: .caseInsensitive) == nil ? "narration-v2" : "narration-v3"
        let data = try encoder.encode([version,project.script, project.defaultVoice, String(project.speed ?? 1), String(decoding: encoder.encode(project.pronunciations ?? [:]),as: UTF8.self), String(decoding: encoder.encode(project.cast),as: UTF8.self), configuration])
        return SHA256.hash(data: data).map { String(format: "%02x",$0) }.joined()
    }
    private func renderDirectory(_ project: ProductionProject, _ configuration: String) throws -> URL {
        try PrivateFiles.child(directory(project.id),key(project,configuration))
    }
    public func output(_ project: ProductionProject, configuration: String) throws -> URL? {
        let url = try PrivateFiles.child(renderDirectory(project,configuration),"output.wav")
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
    public func chapterOutputs(_ project: ProductionProject, configuration: String) throws -> [URL] {
        guard try output(project,configuration: configuration) != nil else { return [] }
        let plan = try project.plan(), dir = try renderDirectory(project,configuration)
        return try plan.chapters.indices.filter { index in plan.segments.contains { $0.chapter == index } }.map { try PrivateFiles.child(dir,"chapter-\($0).wav") }
    }
    public func chapterCues(_ project: ProductionProject,configuration: String) throws -> [BookChapter] {
        guard try output(project,configuration: configuration) != nil else { return [] }
        let plan = try project.plan(), dir = try renderDirectory(project,configuration)
        var cues: [BookChapter] = [], previous: Int?, frames = 0
        for (index,segment) in plan.segments.enumerated() {
            if let previous, plan.segments[previous].pause == 0, segment.pause == 0 { frames += 6000 }
            if previous == nil || plan.segments[previous!].chapter != segment.chapter {
                let title = plan.chapters[segment.chapter]
                cues.append(BookChapter(title: title.isEmpty ? String(cues.count+1) : title,start: Double(frames)/24000))
            }
            frames += try PCMFile(PrivateFiles.child(dir,"\(index).pcm")).frames; previous = index
        }
        return cues
    }
    public func render(_ project: ProductionProject, configuration: String,
                       progress: @escaping @Sendable (Double) async -> Void = { _ in },
                       synthesize: @escaping @Sendable (ProductionSegment,String) async throws -> LocalAudio) async throws -> URL {
        guard rendering.insert(project.id).inserted else { throw ClientError("audiobook.blocked.busy") }
        defer { rendering.remove(project.id) }
        let plan = try project.plan()
        try save(project)
        let dir = try renderDirectory(project,configuration); try PrivateFiles.directory(dir)
        if let output = try output(project,configuration: configuration) { return output }
        for (index,segment) in plan.segments.enumerated() {
            try Task.checkCancellation()
            let file = try PrivateFiles.child(dir,"\(index).pcm")
            if !FileManager.default.fileExists(atPath: file.path) {
                let voice = project.voice(for: segment.role)
                let cacheKey = SHA256.hash(data: Data("v2|\(configuration)|\(voice)|\(segment.speed)|\(segment.pause)|\(segment.text)".utf8)).map { String(format: "%02x",$0) }.joined()
                let cache = try PrivateFiles.child(directory(project.id),"segments"); try PrivateFiles.directory(cache)
                let cached = try PrivateFiles.child(cache,cacheKey+".pcm")
                if !FileManager.default.fileExists(atPath: cached.path) {
                    let samples: [Float]
                    if segment.pause > 0 { samples = [Float](repeating: 0,count: Int(segment.pause*24000)) }
                    else {
                        let audio = try await synthesize(segment,voice)
                        try Task.checkCancellation()
                        guard audio.sampleRate > 0, !audio.samples.isEmpty, audio.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.errors.audio") }
                        samples = try NarrationAudio.speed(resampleProduction(audio),rate: segment.speed)
                    }
                    try PCMFile.write(PCMChunk(samples: samples,sampleRate: 24000),to: cached)
                }
                do { try FileManager.default.linkItem(at: cached,to: file) } catch { try FileManager.default.copyItem(at: cached,to: file) }
            }
            await progress(Double(index+1)/Double(plan.segments.count+1))
        }
        // Concatenate from disk; memory use stays bounded even for a whole book.
        func assemble(_ indexes: [Int], _ name: String) throws -> URL {
            let pcmURL = try PrivateFiles.child(dir,"assembly.pcm")
            let handle = try FileHandle(forWritingTo: PrivateFiles.empty(pcmURL))
            do {
                var total = 0
                for (position,index) in indexes.enumerated() {
                    let input = try FileHandle(forReadingFrom: PrivateFiles.child(dir,"\(index).pcm"))
                    defer { try? input.close() }
                    while let bytes = try input.read(upToCount: 32768), !bytes.isEmpty {
                        try Task.checkCancellation(); total += bytes.count
                        guard total <= 1_382_400_000 else { throw ClientError("mobile.simple.audio_long") }
                        try handle.write(contentsOf: bytes)
                    }
                    if position < indexes.count-1, plan.segments[index].pause == 0, plan.segments[indexes[position+1]].pause == 0 {
                        total += 24000
                        guard total <= 1_382_400_000 else { throw ClientError("mobile.simple.audio_long") }
                        try handle.write(contentsOf: Data(count: 24000))
                    }
                }
                try handle.synchronize(); try handle.close()
                let pending = try PrivateFiles.child(dir,"\(name).pending")
                try PCMFile(pcmURL).markSynthetic(to: pending)
                try Task.checkCancellation()
                let output = try PrivateFiles.child(dir,name)
                try FileManager.default.moveItem(at: pending,to: output)
                try? FileManager.default.removeItem(at: pcmURL)
                return output
            } catch { try? handle.close(); throw error }
        }
        for index in plan.chapters.indices {
            let members = plan.segments.indices.filter { plan.segments[$0].chapter == index }
            if !members.isEmpty {
                let name = "chapter-\(index).wav"
                if !FileManager.default.fileExists(atPath: dir.appendingPathComponent(name).path) { _ = try assemble(members,name) }
            }
        }
        let output = try assemble(Array(plan.segments.indices),"output.wav")
        await progress(1); return output
    }
}

private func resampleProduction(_ audio: LocalAudio) -> [Float] {
    if audio.sampleRate == 24000 { return audio.samples }
    let count = Int(Double(audio.samples.count)*24000/Double(audio.sampleRate))
    return (0..<count).map { index in
        let position = Double(index)*Double(audio.sampleRate)/24000
        let low = min(Int(position),audio.samples.count-1), high = min(low+1,audio.samples.count-1)
        return audio.samples[low]+(audio.samples[high]-audio.samples[low])*Float(position-Double(low))
    }
}
