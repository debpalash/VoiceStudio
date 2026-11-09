import Foundation
import CryptoKit

public struct WorkflowGraphStep: Codable, Sendable, Identifiable, Equatable {
    public var id: String
    public var kind: String
    public var title: String?
    public var text: String?
    public var voiceId: String?
    public var language: String?
    public var sourceLanguage: String?
    public var provider: String?
    public var match: String?
    public var speed: Double?
    public var targetDb: Double?
    public var retries: Int?
    public var position: Position?
    public var media: [Media]?
    public var scripts: [Script]?
    public struct Position: Codable, Sendable, Equatable { public var x: Double; public var y: Double }
    public struct Media: Codable, Sendable, Equatable { public var id: String; public var name: String; public var type: String; public var size: Int }
    public struct Script: Codable, Sendable, Equatable { public var name: String; public var text: String }
    public init(id: String = UUID().uuidString,kind: String,text: String? = nil,voiceId: String? = nil) { self.id = id; self.kind = kind; self.text = text; self.voiceId = voiceId }
    public func matches(_ value: String) -> Bool {
        let needle = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let current = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch match ?? "contains" { case "equals": return current == needle; case "starts": return current.hasPrefix(needle); case "ends": return current.hasSuffix(needle); default: return current.contains(needle) }
    }
}
public struct WorkflowGraphConnection: Codable, Sendable, Identifiable, Equatable {
    public var id: String
    public var source: String
    public var target: String
    public var sourceHandle: String?
    public init(id: String = UUID().uuidString,source: String,target: String,sourceHandle: String? = nil) { self.id = id; self.source = source; self.target = target; self.sourceHandle = sourceHandle }
}
public struct WorkflowGraph: Codable, Sendable, Equatable {
    public var id: String
    public var name: String
    public var updatedAt: Double
    public var steps: [WorkflowGraphStep]
    public var connections: [WorkflowGraphConnection]
    public init(name: String,kinds: [String] = ["start","speak","end"]) {
        id = UUID().uuidString; self.name = name; updatedAt = Date().timeIntervalSince1970*1000
        steps = kinds.map { WorkflowGraphStep(kind: $0,voiceId: ["speak","convert"].contains($0) ? "preset:0" : nil) }
        connections = zip(steps,steps.dropFirst()).map { WorkflowGraphConnection(source: $0.id,target: $1.id) }
    }
    public var inputText: String { let first = steps.first { $0.kind == "start" }; if let scripts = first?.scripts,!scripts.isEmpty { return scripts.map(\.text).joined(separator: "\n---\n") }; return first?.text ?? "" }
    public var needsAudio: Bool { steps.contains { $0.kind == "audio" } }
    public func destination(_ id: String,_ branch: String? = nil) -> String? { connections.first { $0.source == id && ($0.sourceHandle ?? "") == (branch ?? "") }?.target }
    public mutating func connect(_ source: String,to target: String,branch: String? = nil) {
        connections.removeAll { $0.source == source && ($0.sourceHandle ?? "") == (branch ?? "") }
        if !target.isEmpty { connections.append(WorkflowGraphConnection(source: source,target: target,sourceHandle: branch)) }
    }
    /// Same typed DAG rules as Electron compileWorkflow: one input, no cycles,
    /// both labelled condition edges, consistent merge types and no stranded nodes.
    public func validate(requireVoices: Bool = false) throws {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,name.count <= 200,(3...40).contains(steps.count),connections.count <= 80,
              Set(steps.map(\.id)).count == steps.count,Set(connections.map(\.id)).count == connections.count,
              steps.allSatisfy({ !$0.id.isEmpty && $0.id.count <= 128 }),steps.filter({ ["start","audio"].contains($0.kind) }).count == 1 else { throw ClientError("mobile.workflowParity.invalid_graph") }
        let supported = ["start","audio","speak","transcribe","translate","convert","normalize","condition","end"]
        for step in steps {
            guard supported.contains(step.kind) else { throw ClientError("mobile.workflowParity.unsupported",params: ["kind": step.kind]) }
            guard (step.text?.utf16.count ?? 0) <= 20000,(0...3).contains(step.retries ?? 0),["contains","equals","starts","ends"].contains(step.match ?? "contains"),
                  (step.speed ?? 1).isFinite,(0.5...2).contains(step.speed ?? 1),(step.targetDb ?? -2).isFinite,(-24...(-1)).contains(step.targetDb ?? -2),
                  (step.scripts?.count ?? 0) <= 50,(step.media?.count ?? 0) <= 1,(step.voiceId?.count ?? 0) <= 200,(step.language?.count ?? 0) <= 80,(step.sourceLanguage?.count ?? 0) <= 80,["argos","nllb"].contains(step.provider ?? "argos"),step.scripts?.allSatisfy({ !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.text.utf16.count <= 20000 && $0.name.count <= 120 }) ?? true else { throw ClientError("mobile.workflowParity.unsupported_settings") }
            if ["speak","convert"].contains(step.kind),requireVoices,(step.voiceId ?? "").isEmpty { throw ClientError("mobile.workflowParity.bind_voices") }
            if step.kind == "speak",(step.voiceId ?? "").hasPrefix("phone:"),let language = step.language,!["Auto","auto"].contains(language) { throw ClientError("mobile.workflowParity.auto_voice_language") }
            if step.kind == "translate",(step.language ?? "").isEmpty || step.language == "Auto" || (step.sourceLanguage ?? "").isEmpty { throw ClientError("mobile.workflowParity.invalid_language") }
        }
        let ids = Set(steps.map(\.id)), byID = Dictionary(uniqueKeysWithValues: steps.map { ($0.id,$0) })
        guard connections.allSatisfy({ ids.contains($0.source) && ids.contains($0.target) && $0.source != $0.target }) else { throw ClientError("mobile.workflowParity.invalid_graph") }
        for step in steps {
            let count = connections.filter { $0.target == step.id }.count
            guard ["start","audio"].contains(step.kind) ? count == 0 : count > 0 else { throw ClientError("mobile.workflowParity.invalid_graph") }
        }
        var entered: [String:String] = [:]
        func walk(_ id: String,_ type: String,_ path: Set<String>) throws {
            guard !path.contains(id),let step = byID[id] else { throw ClientError("mobile.workflowParity.invalid_graph") }
            if let previous = entered[id] { guard previous == type else { throw ClientError("mobile.workflowParity.incompatible_merge") }; return }
            entered[id] = type
            let edges = connections.filter { $0.source == id }, onward = path.union([id])
            if step.kind == "end" { guard edges.isEmpty else { throw ClientError("mobile.workflowParity.invalid_graph") }; return }
            if step.kind == "condition" {
                guard type == "text",!(step.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,edges.count == 2,edges.filter({ $0.sourceHandle == "yes" }).count == 1,edges.filter({ $0.sourceHandle == "no" }).count == 1 else { throw ClientError("mobile.workflowParity.invalid_graph") }
                for edge in edges { try walk(edge.target,type,onward) }; return
            }
            guard edges.count == 1,(edges[0].sourceHandle ?? "").isEmpty else { throw ClientError("mobile.workflowParity.invalid_graph") }
            let forward: String
            switch step.kind {
            case "start","audio": forward = type
            case "speak" where type == "text": forward = "speech"
            case "convert" where type != "text": forward = "speech"
            case "transcribe" where type != "text": forward = "text"
            case "translate" where type == "text": forward = "text"
            case "normalize" where type == "speech": forward = "speech"
            default: throw ClientError("mobile.workflowParity.incompatible_merge")
            }
            try walk(edges[0].target,forward,onward)
        }
        let input = steps.first { ["start","audio"].contains($0.kind) }!
        try walk(input.id,input.kind == "audio" ? "audio" : "text",[])
        guard entered.count == steps.count else { throw ClientError("mobile.workflowParity.invalid_graph") }
    }
}
public enum WorkflowGraphInterchange {
    private struct Library: Codable { var version: Int; var activeId: String; var documents: [WorkflowGraph] }
    public static func decode(_ data: Data) throws -> WorkflowGraph {
        guard data.count <= 4*1024*1024 else { throw ClientError("mobile.errors.file") }
        let decoder = JSONDecoder()
        let graph: WorkflowGraph
        if let library = try? decoder.decode(Library.self,from: data) {
            guard library.version == 1,library.documents.count <= 50,let selected = library.documents.first(where: { $0.id == library.activeId }) else { throw ClientError("mobile.workflowParity.invalid_graph") }
            graph = selected
        } else { graph = try decoder.decode(WorkflowGraph.self,from: data) }
        try graph.validate(); guard !graph.steps.contains(where: { ($0.scripts?.count ?? 0) > 1 }) else { throw ClientError("mobile.workflowParity.single_input") }; return graph
    }
    public static func encode(_ graph: WorkflowGraph,input: String) throws -> Data {
        var document = graph; try document.validate()
        for index in document.steps.indices {
            document.steps[index].position = .init(x: Double(index%3)*275+40,y: Double(index/3)*155+80)
            if document.steps[index].kind == "start" { document.steps[index].text = input; document.steps[index].scripts = nil }
        }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted,.sortedKeys]
        return try encoder.encode(Library(version: 1,activeId: document.id,documents: [document]))
    }
}
public struct WorkflowGraphValue: Sendable {
    public let text: String?
    public let audio: URL?
    public let synthetic: Bool
    public init(text: String? = nil,audio: URL? = nil,synthetic: Bool = false) { self.text = text; self.audio = audio; self.synthetic = synthetic }
}
public struct WorkflowGraphOutput: Sendable {
    public let text: String?
    public let audio: LocalAudio?
    public let file: URL?
    public init(text: String? = nil,audio: LocalAudio? = nil,file: URL? = nil) { self.text = text; self.audio = audio; self.file = file }
}
public struct WorkflowGraphRun: Codable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var updatedAt: Double
    public var done: Bool
    public var visited: [String]
    public var text: String
    public var audio: String?
    public var failure: String?
}
private struct GraphArtifact: Codable { var text: String?; var file: String?; var hash: String? }
private struct GraphCheckpoint: Codable { var artifacts: [String:GraphArtifact] = [:]; var visited: [String] = []; var cursor: String?; var done = false }

extension PhoneWorkflowStore {
    public func graphRuns(_ workflow: PhoneWorkflow) throws -> [WorkflowGraphRun] {
        let directory = root.appendingPathComponent(workflow.id.uuidString)
        return (try? FileManager.default.contentsOfDirectory(at: directory,includingPropertiesForKeys: nil))?.compactMap { try? PrivateFiles.read(WorkflowGraphRun.self,from: $0.appendingPathComponent("graph-run.json")) }.sorted { $0.updatedAt > $1.updatedAt } ?? []
    }
    public func graphRunAudio(_ workflow: PhoneWorkflow,_ run: WorkflowGraphRun) throws -> URL? {
        guard run.id.count == 64,run.id.allSatisfy({ $0.isHexDigit }),let audio = run.audio,audio == URL(fileURLWithPath: audio).lastPathComponent else { return nil }
        let file = root.appendingPathComponent(workflow.id.uuidString).appendingPathComponent(run.id).appendingPathComponent(audio)
        guard FileManager.default.fileExists(atPath: file.path) else { throw ClientError("mobile.simple.checkpoint_failed") }; return file
    }
    public func runGraph(_ workflow: PhoneWorkflow,text: String,configuration: String,progress: @escaping @Sendable (Double) async -> Void = { _ in },
                         execute: @escaping @Sendable (WorkflowGraphStep,WorkflowGraphValue,URL) async throws -> WorkflowGraphOutput) async throws -> PhoneWorkflowResult {
        guard let graph = workflow.graph else { throw ClientError("mobile.workflowParity.invalid_graph") }; try graph.validate(requireVoices: true)
        // One execution input per saved run keeps resumable ownership explicit.
        // A desktop batch is retained as a draft and must be split before running on phone.
        guard (graph.steps.first(where: { $0.kind == "start" })?.scripts?.count ?? 0) <= 1,text.range(of: "(?m)^\\s*---\\s*$",options: .regularExpression) == nil,text.utf16.count <= 20000,graph.needsAudio || !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ClientError("mobile.workflowParity.single_input") }
        let source = graph.needsAudio ? try self.source(workflow) : nil
        guard running.insert(workflow.id).inserted else { throw ClientError("audiobook.blocked.busy") }; defer { running.remove(workflow.id) }
        var saved = workflow; saved.inputText = text; try save(saved)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var settings = graph
        for i in settings.steps.indices { settings.steps[i].position = nil; settings.steps[i].title = nil }
        settings.name = ""; settings.updatedAt = 0
        let key = SHA256.hash(data: try encoder.encode([String(decoding: encoder.encode(settings),as: UTF8.self),text,configuration,workflow.inputHash ?? ""])).map { String(format: "%02x",$0) }.joined()
        let directory = root.appendingPathComponent(workflow.id.uuidString).appendingPathComponent(key); try PrivateFiles.directory(directory)
        let manifest = directory.appendingPathComponent("graph-checkpoint.json"), summary = directory.appendingPathComponent("graph-run.json")
        let input = graph.steps.first { ["start","audio"].contains($0.kind) }!
        var checkpoint = FileManager.default.fileExists(atPath: manifest.path) ? try PrivateFiles.read(GraphCheckpoint.self,from: manifest) : GraphCheckpoint(cursor: input.id)
        var run = WorkflowGraphRun(id: key,name: workflow.name,updatedAt: Date().timeIntervalSince1970*1000,done: false,visited: checkpoint.visited,text: text)
        try PrivateFiles.write(run,to: summary)
        func resolve(_ artifact: GraphArtifact) throws -> WorkflowGraphValue {
            if let file = artifact.file {
                guard file == URL(fileURLWithPath: file).lastPathComponent else { throw ClientError("mobile.simple.checkpoint_failed") }
                let url = directory.appendingPathComponent(file)
                guard try ModelHash.file(url) == artifact.hash else { throw ClientError("mobile.simple.checkpoint_failed") }
                return WorkflowGraphValue(audio: url,synthetic: true)
            }
            guard let text = artifact.text,!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,text.utf16.count <= 20000 else { throw ClientError("mobile.simple.checkpoint_failed") }
            return WorkflowGraphValue(text: text)
        }
        var value = WorkflowGraphValue(text: text,audio: source)
        // Rewalk immutable cached values; conditions take the same branch after resume.
        var cursor: String? = input.id, visited: [String] = []
        do {
            while let id = cursor {
                try Task.checkCancellation()
                guard let step = graph.steps.first(where: { $0.id == id }),!visited.contains(id) else { throw ClientError("mobile.workflowParity.invalid_graph") }
                visited.append(id)
                if step.kind == "end" { break }
                if ["start","audio"].contains(step.kind) { cursor = graph.destination(id); continue }
                if step.kind == "condition" { cursor = graph.destination(id,step.matches(value.text ?? "") ? "yes" : "no"); continue }
                if let artifact = checkpoint.artifacts[id] { value = try resolve(artifact) }
                else {
                    var output: WorkflowGraphOutput?; var retries = 0
                    while output == nil {
                        do { output = try await execute(step,value,directory) }
                        catch is CancellationError { throw CancellationError() }
                        catch { try Task.checkCancellation(); guard retries < (step.retries ?? 0) else { throw error }; retries += 1 }
                    }
                    try Task.checkCancellation()
                    let artifact: GraphArtifact
                    if let spoken = output!.audio {
                        guard !spoken.samples.isEmpty,spoken.samples.allSatisfy(\.isFinite),["speak","convert","normalize"].contains(step.kind) else { throw ClientError("mobile.errors.audio") }
                        let name = "node-"+SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x",$0) }.joined()+".wav", file = directory.appendingPathComponent(name)
                        try spoken.markSynthetic().write(to: file,options: .atomic)
                        artifact = GraphArtifact(file: name,hash: try ModelHash.file(file))
                    } else if let file = output!.file {
                        guard step.kind == "normalize",try PhoneAudioProcessing.containsSyntheticMarker(file) else { throw ClientError("mobile.errors.audio") }
                        let name = "node-"+SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x",$0) }.joined()+".wav", target = directory.appendingPathComponent(name)
                        guard file.isFileURL else { throw ClientError("mobile.errors.file") }
                        try Data(contentsOf: file,options: .mappedIfSafe).write(to: target,options: .atomic)
                        artifact = GraphArtifact(file: name,hash: try ModelHash.file(target))
                        if file.deletingLastPathComponent().lastPathComponent.hasPrefix("audio-edit-"),file.deletingLastPathComponent().deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
                    } else {
                        guard let text = output!.text,!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,text.utf16.count <= 20000 else { throw ClientError("mobile.errors.invalid_response") }
                        guard ["transcribe","translate"].contains(step.kind) else { throw ClientError("mobile.errors.audio") }
                        artifact = GraphArtifact(text: text)
                    }
                    checkpoint.artifacts[id] = artifact; value = try resolve(artifact)
                    checkpoint.visited = visited; checkpoint.cursor = graph.destination(id); try PrivateFiles.write(checkpoint,to: manifest)
                }
                guard ["transcribe","translate"].contains(step.kind) ? value.text != nil : value.audio != nil else { throw ClientError("mobile.simple.checkpoint_failed") }
                cursor = graph.destination(id)
                run.visited = visited; run.updatedAt = Date().timeIntervalSince1970*1000; try PrivateFiles.write(run,to: summary)
                await progress(Double(visited.count)/Double(graph.steps.count))
            }
            checkpoint.done = true; checkpoint.visited = visited; try PrivateFiles.write(checkpoint,to: manifest)
            run.done = true; run.visited = visited; run.text = value.text ?? ""; run.audio = value.audio?.lastPathComponent; run.updatedAt = Date().timeIntervalSince1970*1000; try PrivateFiles.write(run,to: summary)
            await progress(1); return PhoneWorkflowResult(text: value.text ?? "",audio: value.audio)
        } catch {
            run.failure = error is CancellationError ? "mobile.simple.cancelled" : (error as? ClientError)?.key ?? "mobile.errors.file"
            run.visited = visited; run.updatedAt = Date().timeIntervalSince1970*1000; try? PrivateFiles.write(run,to: summary); throw error
        }
    }
}
