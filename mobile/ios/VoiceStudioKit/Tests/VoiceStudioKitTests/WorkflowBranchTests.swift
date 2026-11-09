import Foundation
import Testing
@testable import VoiceStudioKit

private actor BranchFixture {
    var calls = 0
    var fail = true
    func execute(_ step: PhoneWorkflowStep,_ text: String) throws -> WorkflowValue {
        calls += 1
        if fail { fail = false; throw ClientError("mobile.errors.audio") }
        return WorkflowValue(text: text+" translated")
    }
}
@Test func workflowRetriesAndSkippedStepsResumeWithoutExecutingAgain() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneWorkflowStore(root: root),fixture = BranchFixture()
    let workflow = PhoneWorkflow(name: "Branch",steps: [PhoneWorkflowStep(kind: "condition",containsText: "missing",skipSteps: 1),PhoneWorkflowStep(kind: "speak"),PhoneWorkflowStep(kind: "translate",retries: 1)])
    // Speech must remain terminal even when a branch would bypass it.
    #expect(!workflow.valid)
    let valid = PhoneWorkflow(name: "Branch",steps: [PhoneWorkflowStep(kind: "condition",containsText: "missing",skipSteps: 1),PhoneWorkflowStep(kind: "translate"),PhoneWorkflowStep(kind: "translate",retries: 1)])
    let first = try await store.run(valid,text: "hello",configuration: "models-v1",execute: { try await fixture.execute($0,$1) })
    #expect(first.text == "hello translated"); #expect(first.audio == nil); #expect(await fixture.calls == 2)
    let restored = try PhoneWorkflowStore(root: root)
    let resumed = try await restored.run(valid,text: "hello",configuration: "models-v1",execute: { try await fixture.execute($0,$1) })
    #expect(resumed.text == first.text); #expect(await fixture.calls == 2)
}
@Test func workflowMediaIsCopiedAndChangedSourceCannotReuseACompletedStep() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true)
    let source = root.appendingPathComponent("voice.wav"); try Data("fixture".utf8).write(to: source)
    let store = try PhoneWorkflowStore(root: root.appendingPathComponent("flows"))
    let workflow = try await store.importAudio(source,into: PhoneWorkflow(name: "Transcribe",steps: [PhoneWorkflowStep(kind: "transcribe")]))
    try FileManager.default.removeItem(at: source)
    let imported = try await store.source(workflow)
    let result = try await store.run(workflow,text: "",configuration: "asr-v1") { _,_ in WorkflowValue(text: "recognized") }
    #expect(result.text == "recognized")
    try Data("changed".utf8).write(to: imported)
    await #expect(throws: (any Error).self) { try await store.run(workflow,text: "",configuration: "asr-v1") { _,_ in WorkflowValue(text: "wrong") } }
}
@Test func workflowBranchCanSkipTerminalSpeechWithoutPublishingAudio() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneWorkflowStore(root: root)
    let workflow = PhoneWorkflow(name: "Conditional speech",steps: [PhoneWorkflowStep(kind: "condition",containsText: "say",skipSteps: 1),PhoneWorkflowStep(kind: "speak")])
    let result = try await store.run(workflow,text: "quiet",configuration: "v1") { _,_ in Issue.record("Skipped step ran"); throw CancellationError() }
    #expect(result.text == "quiet"); #expect(result.audio == nil)
}
