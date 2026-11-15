import Foundation
import Testing
@testable import VoiceStudioKit

private func eventPage(_ visible: ClosedRange<Int>,failure: Int? = nil,eventTime: Double = 100,watermark: Int? = 2001,tailTime: Double? = 100) throws -> JobEvents {
    var body: [String:Any] = [
        "job":["id":"task","status":"running","created_at":100.0,"updated_at":100.0],
        "events":visible.map {sequence in ["seq":sequence,"created_at":eventTime,"payload":"data: {\"type\":\"\(sequence==failure ? "error" : "progress")\"}"] as [String:Any]}
    ]
    if let watermark {body["event_watermark"]=["seq":watermark,"created_at":tailTime.map {$0 as Any} ?? NSNull()]}
    return try JSONDecoder().decode(JobEvents.self,from:JSONSerialization.data(withJSONObject:body))
}
@Test func companionHistoryWatermarkExcludesErrorsOutsideTheFirstPageButKeepsNewFailures() throws {
    let a=CompanionDiagnosticObservation(revision:1,epoch:String(repeating:"a",count:32))
    var gate=CompanionTaskFailureGate()
    #expect(gate.observe(taskID:"task",page:try eventPage(1...500),before:a,after:a,submitted:nil)==nil)
    // The pre-existing tail is2001, even though the first ascending page ends500.
    #expect(gate.observe(taskID:"task",page:try eventPage(501...1000,failure:501),before:a,after:a,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(2002...2002,failure:2002,eventTime:99,watermark:2002),before:a,after:a,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(2003...2003,failure:2003,eventTime:101,watermark:2003,tailTime:101),before:a,after:a,submitted:nil)==a)
}
@Test(arguments:["missing","behind-page","missing-time"])
func companionUnknownHistoryTailCannotAuthorizeAnotherPage(_ scenario: String) throws {
    let a=CompanionDiagnosticObservation(revision:1,epoch:String(repeating:"a",count:32))
    var gate=CompanionTaskFailureGate()
    let page=try eventPage(1...500,watermark:scenario=="missing" ? nil : scenario=="behind-page" ? 499 : 2001,tailTime:scenario=="missing-time" ? nil : 100)
    #expect(gate.observe(taskID:"task",page:page,before:a,after:a,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(501...1000,failure:501),before:a,after:a,submitted:nil)==nil)
}
@Test func companionHistoryWatermarkPreservesRegrantAndIndependentSubmissionAuthority() throws {
    let a=CompanionDiagnosticObservation(revision:1,epoch:String(repeating:"a",count:32))
    let b=CompanionDiagnosticObservation(revision:2,epoch:String(repeating:"b",count:32))
    var gate=CompanionTaskFailureGate()
    #expect(gate.observe(taskID:"task",page:try eventPage(1...500),before:a,after:a,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(501...1000,failure:501),before:a,after:b,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(501...1000,failure:501),before:b,after:b,submitted:nil)==nil)
    #expect(gate.observe(taskID:"task",page:try eventPage(2002...2002,failure:2002,eventTime:101,watermark:2002,tailTime:101),before:b,after:b,submitted:nil)==b)
    var submitted=CompanionTaskFailureGate()
    #expect(submitted.observe(taskID:"new-task",page:try eventPage(1...1,failure:1,watermark:nil),before:b,after:b,submitted:b)==b)
}
