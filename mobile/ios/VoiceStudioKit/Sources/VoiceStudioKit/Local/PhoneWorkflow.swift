import Foundation
import CryptoKit

public struct PhoneWorkflowStep: Codable, Sendable, Identifiable, Equatable {
    public var id: UUID
    public var kind: String
    public var language: String
    public var containsText: String?
    public var skipSteps: Int?
    public var retries: Int?
    public init(id: UUID = UUID(),kind: String,language: String = "en",containsText: String? = nil,skipSteps: Int? = nil,retries: Int? = nil) {
        self.id = id; self.kind = kind; self.language = language; self.containsText = containsText; self.skipSteps = skipSteps; self.retries = retries
    }
}
public struct PhoneWorkflow: Codable, Sendable, Identifiable, Equatable {
    public var id: UUID
    public var name: String
    public var inputText: String? = nil
    public var voiceSelection: String? = nil
    public var inputFile: String? = nil
    public var inputName: String? = nil
    public var inputHash: String? = nil
    public var graph: WorkflowGraph? = nil
    public var steps: [PhoneWorkflowStep]
    public init(id: UUID = UUID(),name: String,steps: [PhoneWorkflowStep] = []) { self.id = id; self.name = name; self.steps = steps }
    public var valid: Bool {
        if let graph { return !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && name.count <= 200 && (try? graph.validate()) != nil }
        return
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && name.count <= 200 && !steps.isEmpty && steps.count <= 20 && Set(steps.map(\.id)).count == steps.count && steps.enumerated().allSatisfy { index,step in
            ["normalize","translate","speak","transcribe","condition"].contains(step.kind) && !step.language.isEmpty && (0...3).contains(step.retries ?? 0) && (step.kind != "transcribe" || index == 0) && (step.kind != "speak" || index == steps.count-1) && (step.kind != "condition" || (!(step.containsText ?? "").isEmpty && (1...20).contains(step.skipSteps ?? 1)))
        }
    }
}
public struct WorkflowValue: Sendable {
    public let text: String
    public let audio: LocalAudio?
    public init(text: String,audio: LocalAudio? = nil) { self.text = text; self.audio = audio }
}
public struct PhoneWorkflowResult: Sendable {
    public let text: String
    public let audio: URL?
    public init(text: String,audio: URL?) { self.text = text; self.audio = audio }
}
private struct WorkflowCheckpoint: Codable {
    var texts: [String] = []
    var nextIndex: Int?
    var spoken: Bool?
}

/// Immutable source files and model/configuration fingerprints make every completed step resumable.
public actor PhoneWorkflowStore {
    let root: URL
    var running = Set<UUID>()
    public init(root: URL) throws { self.root = root; try PrivateFiles.directory(root) }
    public func save(_ workflow: PhoneWorkflow) throws {
        guard !workflow.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, workflow.name.count <= 200, workflow.steps.count <= 20 else { throw ClientError("mobile.errors.file") }
        try PrivateFiles.write(workflow,to: PrivateFiles.child(root,workflow.id.uuidString+".json"))
    }
    public func importAudio(_ source: URL,into workflow: PhoneWorkflow) throws -> PhoneWorkflow {
        guard source.isFileURL else { throw ClientError("mobile.errors.file") }
        let size = try source.resourceValues(forKeys: [.fileSizeKey,.isRegularFileKey])
        guard size.isRegularFile == true, let bytes = size.fileSize, bytes > 0, bytes <= 512*1024*1024 else { throw ClientError("mobile.errors.file") }
        let directory = try PrivateFiles.child(root,workflow.id.uuidString); try PrivateFiles.directory(directory)
        let suffix = source.pathExtension.lowercased().filter { $0.isASCII && $0.isLetter || $0.isNumber }
        let filename = "source-"+UUID().uuidString+(suffix.isEmpty ? "" : "."+String(suffix.prefix(10)))
        let target = try PrivateFiles.child(directory,filename)
        try FileManager.default.copyItem(at: source,to: target)
        var result = workflow; result.inputFile = filename; result.inputName = source.lastPathComponent; result.inputHash = try hash(target)
        try save(result)
        return result
    }
    public func source(_ workflow: PhoneWorkflow) throws -> URL {
        guard let filename = workflow.inputFile, filename.hasPrefix("source-"), let expected = workflow.inputHash else { throw ClientError("mobile.errors.file") }
        let file = try PrivateFiles.child(PrivateFiles.child(root,workflow.id.uuidString),filename)
        guard try hash(file) == expected else { throw ClientError("mobile.simple.checkpoint_failed") }
        return file
    }
    private func hash(_ file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file); defer { try? handle.close() }
        var digest = SHA256()
        while let data = try handle.read(upToCount: 1024*1024), !data.isEmpty { digest.update(data: data) }
        return digest.finalize().map { String(format: "%02x",$0) }.joined()
    }
    public func list() throws -> [PhoneWorkflow] {
        try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil).filter { $0.pathExtension == "json" }.compactMap { try? PrivateFiles.read(PhoneWorkflow.self,from: PrivateFiles.child(root,$0.lastPathComponent)) }.sorted { $0.name < $1.name }
    }
    public func delete(_ workflow: PhoneWorkflow) throws {
        guard !running.contains(workflow.id) else { throw ClientError("audiobook.blocked.busy") }
        try FileManager.default.removeItem(at: PrivateFiles.child(root,workflow.id.uuidString+".json"))
        let directory = try PrivateFiles.child(root,workflow.id.uuidString)
        if FileManager.default.fileExists(atPath: directory.path) { try FileManager.default.removeItem(at: directory) }
    }
    public func run(_ workflow: PhoneWorkflow,text: String,configuration: String,
                    progress: @escaping @Sendable (Double) async -> Void = { _ in },
                    execute: @escaping @Sendable (PhoneWorkflowStep,String) async throws -> WorkflowValue) async throws -> PhoneWorkflowResult {
        guard workflow.valid, (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || workflow.steps.first?.kind == "transcribe"), text.utf16.count <= 20000 else { throw ClientError("mobile.errors.invalid_response") }
        if workflow.steps.first?.kind == "transcribe" { _ = try source(workflow) }
        guard running.insert(workflow.id).inserted else { throw ClientError("audiobook.blocked.busy") }
        defer { running.remove(workflow.id) }
        var saved = workflow; saved.inputText = text; try save(saved)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let key = SHA256.hash(data: try encoder.encode([text,configuration,workflow.inputHash ?? "",String(decoding: encoder.encode(workflow.steps),as: UTF8.self)])).map { String(format: "%02x",$0) }.joined()
        let parent = try PrivateFiles.child(root,workflow.id.uuidString); try PrivateFiles.directory(parent)
        let dir = try PrivateFiles.child(parent,key); try PrivateFiles.directory(dir)
        let manifest = try PrivateFiles.child(dir,"checkpoint.json")
        var checkpoint = FileManager.default.fileExists(atPath: manifest.path) ? try PrivateFiles.read(WorkflowCheckpoint.self,from: manifest) : WorkflowCheckpoint()
        var index = checkpoint.nextIndex ?? checkpoint.texts.count
        guard (0...workflow.steps.count).contains(index), checkpoint.texts.count <= workflow.steps.count else { throw ClientError("mobile.simple.checkpoint_failed") }
        while index < workflow.steps.count {
            try Task.checkCancellation()
            let input = checkpoint.texts.last ?? text, step = workflow.steps[index]
            var next = index+1
            let value: WorkflowValue
            if step.kind == "normalize" { value = WorkflowValue(text: input.split(whereSeparator: \.isWhitespace).joined(separator: " ")) }
            else if step.kind == "condition" {
                value = WorkflowValue(text: input)
                if !input.contains(step.containsText ?? "") { next = min(workflow.steps.count,next+(step.skipSteps ?? 1)) }
            } else {
                var attempt = 0
                var completed: WorkflowValue?
                while completed == nil {
                    do { completed = try await execute(step,input) }
                    catch is CancellationError { throw CancellationError() }
                    catch { try Task.checkCancellation(); guard attempt < (step.retries ?? 0) else { throw error }; attempt += 1 }
                }
                value = completed!
            }
            try Task.checkCancellation()
            guard !value.text.isEmpty, value.text.utf16.count <= 20000 else { throw ClientError("mobile.errors.invalid_response") }
            if step.kind == "speak" {
                guard let audio = value.audio else { throw ClientError("mobile.errors.audio") }
                try audio.markSynthetic().write(to: dir.appendingPathComponent("output.wav"),options: .atomic); checkpoint.spoken = true
            }
            checkpoint.texts.append(value.text); checkpoint.nextIndex = next; try PrivateFiles.write(checkpoint,to: manifest)
            index = next; await progress(Double(index)/Double(workflow.steps.count))
        }
        let spoken = checkpoint.spoken ?? (workflow.steps.last?.kind == "speak" && checkpoint.texts.count == workflow.steps.count)
        let audio = spoken ? dir.appendingPathComponent("output.wav") : nil
        guard audio == nil || FileManager.default.fileExists(atPath: audio!.path) else { throw ClientError("mobile.errors.audio") }
        return PhoneWorkflowResult(text: checkpoint.texts.last ?? text,audio: audio)
    }
}

public enum WorkflowTranscription {
    public static func run(source: URL,model: PhoneModel,engine: any LocalEngine,decoder: any PhoneAudioDecoder,progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws -> String {
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".pcm")
        defer { try? FileManager.default.removeItem(at: scratch) }
        let pcm = try await decoder.decode(source: source,output: scratch,maxSeconds: 14400,progress: { value in await progress(value*0.2) })
        let frames = try pcm.frames
        guard frames > 0 else { throw ClientError("mobile.errors.audio") }
        var text = ""
        for start in stride(from: 0,to: frames,by: 480000) {
            try Task.checkCancellation()
            let chunk = try pcm.read(start: start,count: min(480000,frames-start))
            let part = try await engine.transcribe(model: model,audio: chunk,progress: { _ in }).trimmingCharacters(in: .whitespacesAndNewlines)
            if !part.isEmpty { text += (text.isEmpty ? "" : " ")+part }
            guard text.utf16.count <= 20000 else { throw ClientError("mobile.errors.invalid_response") }
            await progress(0.2+0.8*Double(min(frames,start+480000))/Double(frames))
        }
        guard !text.isEmpty else { throw ClientError("mobile.errors.invalid_response") }; return text
    }
}
