import Foundation

/// Local permission/provenance only; never stored in jobs or diagnostic properties.
public struct CompanionDiagnosticObservation: Equatable, Sendable {
    public let revision: Int64
    public let epoch: String
    public init(revision: Int64,epoch: String) {self.revision=revision;self.epoch=epoch}
}
public struct CompanionTaskFailureGate: Sendable {
    private var task: String?
    private var observation: CompanionDiagnosticObservation?
    private var sequence=0
    private var serverFloor: Double?
    public init() {}
    public mutating func observe(taskID: String,page: JobEvents,before: CompanionDiagnosticObservation?,after: CompanionDiagnosticObservation?,submitted: CompanionDiagnosticObservation?) -> CompanionDiagnosticObservation? {
        guard let before,before==after else {observation=nil;return nil}
        if submitted==after {return after}
        if task != taskID || observation != after {
            task=taskID;observation=after
            sequence=page.events.map(\.seq).max() ?? 0
            serverFloor=(page.events.compactMap(\.created_at)+[page.job.created_at,page.job.updated_at].compactMap {$0})
                .filter {$0.isFinite && $0>0}.max()
            return nil // The first page is history, including an already-terminal task.
        }
        guard let serverFloor else {return nil}
        return page.events.contains {event in
            event.seq>sequence && event.created_at.map {$0.isFinite && $0>=serverFloor}==true &&
                ["error","failed"].contains(DubProgress.parse(event)?.stage ?? "")
        } ? after : nil
    }
}
