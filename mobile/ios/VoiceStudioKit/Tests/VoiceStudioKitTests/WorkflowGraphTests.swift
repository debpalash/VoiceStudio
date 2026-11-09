import Foundation
import Testing
@testable import VoiceStudioKit

private func branchingGraph() -> WorkflowGraph {
    var graph = WorkflowGraph(name: "Branch",kinds: ["start","condition","translate","translate","translate","end"])
    for i in [2,3,4] { graph.steps[i].sourceLanguage = "auto"; graph.steps[i].language = "fr" }
    graph.steps[1].text = "URGENT"; let n = graph.steps
    graph.connections = [.init(source: n[0].id,target: n[1].id),.init(source: n[1].id,target: n[2].id,sourceHandle: "yes"),.init(source: n[1].id,target: n[3].id,sourceHandle: "no"),.init(source: n[2].id,target: n[4].id),.init(source: n[3].id,target: n[4].id),.init(source: n[4].id,target: n[5].id)]
    return graph
}
private actor GraphExecutionFixture {
    var visits: [String] = []
    var interrupt = true
    let yes: String,tail: String
    init(_ graph: WorkflowGraph) { yes = graph.steps[2].id; tail = graph.steps[4].id }
    func execute(_ step: WorkflowGraphStep,_ value: WorkflowGraphValue,_ directory: URL) throws -> WorkflowGraphOutput {
        visits.append(step.id)
        if step.id == tail && interrupt { interrupt = false; throw CancellationError() }
        return WorkflowGraphOutput(text: step.id == tail ? (value.text ?? "")+" rejoined" : step.id == yes ? "YES" : "NO")
    }
}
@Test func desktopGraphBothBranchesRejoinAndResumeCachedSteps() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString); defer { try? FileManager.default.removeItem(at: root) }
    let graph = branchingGraph(),fixture = GraphExecutionFixture(graph),store = try PhoneWorkflowStore(root: root)
    var workflow = PhoneWorkflow(name: "Branch"); workflow.graph = graph
    await #expect(throws: CancellationError.self) { try await store.runGraph(workflow,text: "  an urgent task  ",configuration: "models-v1",execute: { try await fixture.execute($0,$1,$2) }) }
    let restored = try PhoneWorkflowStore(root: root)
    let result = try await restored.runGraph(workflow,text: "  an urgent task  ",configuration: "models-v1",execute: { try await fixture.execute($0,$1,$2) })
    #expect(result.text == "YES rejoined"); #expect(await fixture.visits.filter { $0 == graph.steps[2].id }.count == 1)
    #expect(await fixture.visits.filter { $0 == graph.steps[3].id }.isEmpty)
    #expect(await fixture.visits.filter { $0 == graph.steps[4].id }.count == 2)
    #expect(try await restored.runGraph(workflow,text: "ordinary",configuration: "models-v1",execute: { try await fixture.execute($0,$1,$2) }).text == "NO rejoined")
    #expect(try await restored.graphRuns(workflow).count == 2)
}
@Test func graphRejectsCyclesMissingBranchMixedMergeAndUnsupportedNodesBeforeSideEffects() throws {
    let graph = branchingGraph(); try graph.validate()
    var bad = graph; bad.connections.removeAll { $0.sourceHandle == "no" }
    #expect(throws: (any Error).self) { try bad.validate() }
    bad = graph; bad.connections.removeAll { $0.source == graph.steps[4].id }; bad.connections.append(.init(source: graph.steps[4].id,target: graph.steps[1].id))
    #expect(throws: (any Error).self) { try bad.validate() }
    bad = graph; bad.steps[2].kind = "speak"; bad.steps[2].voiceId = "preset:0"
    #expect(throws: (any Error).self) { try bad.validate() }
    bad = graph; bad.steps[3].kind = "agent"
    #expect(throws: (any Error).self) { try bad.validate() }
}
@Test func desktopJSONRoundTripPreservesBranchHandlesAndRejectsUnsupportedDrafts() throws {
    let graph = branchingGraph(),json = try WorkflowGraphInterchange.encode(graph,input: "Hello")
    let object = try #require(JSONSerialization.jsonObject(with: json) as? [String:Any]); #expect(object["version"] as? Int == 1)
    let imported = try WorkflowGraphInterchange.decode(json)
    #expect(imported.connections == graph.connections); #expect(imported.steps.first?.text == "Hello")
    #expect(imported.steps.allSatisfy { $0.position != nil })
    var unsupported = graph; unsupported.steps[2].kind = "call"
    let data = try JSONEncoder().encode(unsupported)
    #expect(throws: (any Error).self) { try WorkflowGraphInterchange.decode(data) }
    var many = graph; many.steps[0].scripts = [.init(name: "one",text: "one"),.init(name: "two",text: "two")]
    #expect(throws: (any Error).self) { try WorkflowGraphInterchange.decode(JSONEncoder().encode(many)) }
}
@Test func graphConditionMatchesDesktopWithoutLocaleSpecificCasing() {
    var step = WorkflowGraphStep(kind: "condition",text: " I ")
    #expect(step.matches("i")); step.match = "equals"; #expect(step.matches(" I ")); #expect(!step.matches("i now"))
    step.match = "starts"; #expect(step.matches("I now")); step.match = "ends"; #expect(step.matches("now I"))
}
@Test func graphGeneratedAudioIsMarkedAndCorruptedNodeCheckpointNeverResumes() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneWorkflowStore(root: root); var workflow = PhoneWorkflow(name: "Speak"); workflow.graph = WorkflowGraph(name: "Speak")
    let execute: @Sendable (WorkflowGraphStep,WorkflowGraphValue,URL) async throws -> WorkflowGraphOutput = { _,_,_ in WorkflowGraphOutput(audio: LocalAudio(samples: [0.2,-0.2],sampleRate: 24000)) }
    let result = try await store.runGraph(workflow,text: "Hello",configuration: "voice-v1",execute: execute)
    let file = try #require(result.audio); #expect(try Data(contentsOf: file).range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
    try Data("broken".utf8).write(to: file)
    await #expect(throws: (any Error).self) { try await store.runGraph(workflow,text: "Hello",configuration: "voice-v1",execute: execute) }
    #expect(try await store.graphRuns(workflow).first?.done == false)
}
