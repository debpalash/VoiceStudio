import Foundation

public struct PhoneTranscript: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let createdAt: Double
    public var title: String
    public var originalText: String
    public var editedText: String?
    public var segments: [SpeechSegment]
    public var kind: String
    public var modelID: String?
    public var modelPin: String?
    public var frames: Int
    public var nextFrame: Int
    public var sourceHash: String?
    public var complete: Bool
    public var text: String { editedText ?? originalText }
    public var progress: Double { frames > 0 ? Double(nextFrame)/Double(frames) : 1 }
    public init(id: String = UUID().uuidString.lowercased(),createdAt: Double = Date().timeIntervalSince1970*1000,title: String,originalText: String = "",editedText: String? = nil,segments: [SpeechSegment] = [],kind: String = "dictation",modelID: String? = nil,modelPin: String? = nil,frames: Int = 0,nextFrame: Int = 0,sourceHash: String? = nil,complete: Bool = true) {
        self.id = id; self.createdAt = createdAt; self.title = title; self.originalText = originalText; self.editedText = editedText; self.segments = segments; self.kind = kind; self.modelID = modelID; self.modelPin = modelPin; self.frames = frames; self.nextFrame = nextFrame; self.sourceHash = sourceHash; self.complete = complete
    }
    public var valid: Bool {
        UUID(uuidString: id)?.uuidString.lowercased() == id && createdAt.isFinite && createdAt >= 0 && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && title.utf16.count <= 300 && originalText.utf16.count <= 1_000_000 && (editedText?.utf16.count ?? 0) <= 1_000_000 && ["dictation","file"].contains(kind) && frames >= 0 && frames <= 345600000 && nextFrame >= 0 && nextFrame <= frames && segments.count <= 50000 && segments.allSatisfy { $0.start.isFinite && $0.end.isFinite && $0.start >= 0 && $0.end > $0.start && $0.end <= 14400 && $0.text.utf16.count <= 20000 } && (complete || (kind == "file" && modelID != nil && modelPin != nil && sourceHash != nil))
    }
}

private func boundedTranscriptTitle(_ title: String) -> String {
    var value = String(title.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
    while value.utf16.count > 300 { value.removeLast() }
    return value
}

/// Completed text and immutable source/checkpoints stay private to the phone. No model downloads.
public actor PhoneTranscriptStore {
    public static let maxSourceBytes = 1024*1024*1024
    private let root: URL
    private var running = Set<String>()
    public init(root: URL) throws { self.root = root; try PrivateFiles.directory(root) }
    private func directory(_ id: String) throws -> URL {
        guard UUID(uuidString: id)?.uuidString.lowercased() == id else { throw ClientError("mobile.errors.file") }
        return try PrivateFiles.child(root,id)
    }
    private func write(_ value: PhoneTranscript) throws {
        guard value.valid else { throw ClientError("mobile.errors.file") }
        let dir = try directory(value.id); try PrivateFiles.directory(dir)
        try PrivateFiles.write(value,to: PrivateFiles.child(dir,"transcript.json"))
    }
    public func record(_ id: String) throws -> PhoneTranscript {
        let file = try PrivateFiles.child(directory(id),"transcript.json")
        let size = try file.resourceValues(forKeys: [.fileSizeKey,.isRegularFileKey])
        guard size.isRegularFile == true,(size.fileSize ?? Int.max) <= 16*1024*1024 else { throw ClientError("mobile.errors.file") }
        let record = try PrivateFiles.read(PhoneTranscript.self,from: file)
        guard record.id == id,record.valid else { throw ClientError("mobile.errors.file") }; return record
    }
    public func list(query: String = "") throws -> [PhoneTranscript] {
        let items = try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil)
            .compactMap { try? record($0.lastPathComponent) }
        return items.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) || $0.text.localizedCaseInsensitiveContains(query) }.sorted { $0.createdAt > $1.createdAt }
    }
    @discardableResult public func saveDictation(id: String,title: String,text: String,modelID: String? = nil) throws -> PhoneTranscript {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw ClientError("mobile.simple.no_speech") }
        if let existing = try? record(id) { return existing }
        let value = PhoneTranscript(id: id,title: boundedTranscriptTitle(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? clean : title),originalText: clean,modelID: modelID)
        try write(value); return value
    }
    public func edit(_ id: String,text: String?) throws {
        guard !running.contains(id) else { throw ClientError("mobile.errors.file") }
        var value = try record(id); guard value.complete else { throw ClientError("mobile.errors.file") }
        value.editedText = text; try write(value)
    }
    public func delete(_ id: String) throws {
        guard !running.contains(id) else { throw ClientError("mobile.errors.file") }
        try FileManager.default.removeItem(at: directory(id))
    }
    public func prepare(source: URL,title: String,model: PhoneModel,decoder: any PhoneAudioDecoder = AVPhoneAudioDecoder(),progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws -> PhoneTranscript {
        guard model.capability == .asr else { throw ClientError("mobile.local.model_incompatible") }
        guard source.isFileURL else { throw ClientError("mobile.errors.file") }
        let access = source.startAccessingSecurityScopedResource()
        defer { if access { source.stopAccessingSecurityScopedResource() } }
        let info = try source.resourceValues(forKeys: [.fileSizeKey,.isRegularFileKey])
        guard info.isRegularFile == true, let bytes = info.fileSize, bytes <= Self.maxSourceBytes else { throw ClientError("mobile.errors.file") }
        let id = UUID().uuidString.lowercased(), dir = try directory(id); try PrivateFiles.directory(dir)
        do {
            let pcm = try await decoder.decode(source: source,output: dir.appendingPathComponent("source.pcm"),maxSeconds: 14400,progress: progress)
            let frames = try pcm.frames; guard frames > 0 else { throw ClientError("mobile.simple.no_speech") }
            let value = PhoneTranscript(id: id,title: boundedTranscriptTitle(title),kind: "file",modelID: model.id,modelPin: try phoneModelPin(model),frames: frames,sourceHash: try ModelHash.file(pcm.url),complete: false)
            try Task.checkCancellation(); try write(value); return value
        } catch { try? FileManager.default.removeItem(at: dir); throw error }
    }
    public func run(_ id: String,model: PhoneModel,progress: @escaping @Sendable (Double) async -> Void = { _ in },recognize: @Sendable (PCMChunk) async throws -> [SpeechSegment]) async throws -> PhoneTranscript {
        guard running.insert(id).inserted else { throw ClientError("mobile.errors.file") }; defer { running.remove(id) }
        var value = try record(id)
        if value.complete { return value }
        guard model.capability == .asr,value.modelID == model.id,value.modelPin == (try phoneModelPin(model)) else { throw ClientError("mobile.local.model_incompatible") }
        let pcm = PCMFile(try PrivateFiles.child(directory(id),"source.pcm"))
        guard try pcm.frames == value.frames,try ModelHash.file(pcm.url) == value.sourceHash else { throw ClientError("mobile.errors.file") }
        while value.nextFrame < value.frames {
            try Task.checkCancellation()
            let first = value.nextFrame,count = min(480000,value.frames-first),duration = Double(count)/24000
            let segments = try await recognize(pcm.read(start: first,count: count))
            try Task.checkCancellation()
            guard segments.count <= 2000,segments.allSatisfy({ $0.start.isFinite && $0.end.isFinite && $0.start >= 0 && $0.end > $0.start && $0.end <= duration+0.001 && $0.text.utf16.count <= 20000 }) else { throw ClientError("mobile.errors.invalid_response") }
            let shifted = segments.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }.map { SpeechSegment(start: Double(first)/24000+$0.start,end: min(Double(value.frames)/24000,Double(first)/24000+$0.end),text: $0.text) }
            value.segments += shifted
            value.originalText = value.segments.map(\.text).joined(separator: "\n")
            value.nextFrame += count; try write(value); await progress(value.progress)
        }
        value.complete = true; try write(value); try? FileManager.default.removeItem(at: pcm.url); return value
    }
}

public enum PhoneTranscriptExport {
    public static func text(_ records: [PhoneTranscript]) -> String {
        records.map { $0.title+"\n"+Date(timeIntervalSince1970: $0.createdAt/1000).formatted(.iso8601)+"\n\n"+$0.text }.joined(separator: "\n\n---\n\n")+"\n"
    }
    public static func subtitles(_ record: PhoneTranscript,vtt: Bool) throws -> String {
        guard record.editedText == nil,!record.segments.isEmpty,record.valid else { throw ClientError("mobile.errors.file") }
        func time(_ seconds: Double) -> String {
            let ms = Int((seconds*1000).rounded());return String(format: "%02d:%02d:%02d%@%03d",ms/3600000,(ms/60000)%60,(ms/1000)%60,vtt ? "." : ",",ms%1000)
        }
        return (vtt ? "WEBVTT\n\n" : "")+record.segments.enumerated().map { index,cue in
            let safe = cue.text.replacingOccurrences(of: "&",with: "&amp;").replacingOccurrences(of: "<",with: "&lt;").replacingOccurrences(of: ">",with: "&gt;").components(separatedBy: .newlines).filter { !$0.isEmpty }.joined(separator: "\n")
            return "\(index+1)\n\(time(cue.start)) --> \(time(cue.end))\n\(safe)\n"
        }.joined(separator: "\n")
    }
}
