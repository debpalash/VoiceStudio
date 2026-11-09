import Foundation
import Observation

/// The single lifetime for data and work belonging to a paired computer.
/// Views also use scopeID as their identity so selections, media, and capture
/// are discarded even when switching directly between two connected hosts.
@MainActor @Observable public final class HostState {
    public private(set) var host: String?
    public private(set) var scopeID = UUID()
    public var profiles: [Profile] = []
    public var inventory: EngineInventory?
    public var history: [HistoryItem] = []
    public var dubHistory: [DubHistoryItem] = []
    public private(set) var generation: Generation?
    public let dub: DubWorkflow
    @ObservationIgnored private var tasks: [UUID: Task<Void, any Error>] = [:]
    public init(dub: DubWorkflow) { self.dub = dub }
    public func transition(to host: String?) {
        guard self.host != host || host == nil else { return }
        for task in tasks.values { task.cancel() }
        tasks.removeAll()
        self.host = host; scopeID = UUID()
        profiles = []; inventory = nil; history = []; dubHistory = []; generation = nil
        dub.scope(to: host)
    }
    public func run(_ work: @escaping @MainActor () async throws -> Void) async throws {
        let id = UUID()
        let scope = scopeID
        let task = Task { @MainActor in
            try Task.checkCancellation()
            guard scope == self.scopeID else { throw CancellationError() }
            try await work()
        }
        tasks[id] = task
        defer { tasks[id] = nil }
        try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
    }
    public func generate(api: APIClient, text: String, profile: String, engine: String, language: String) async throws -> Generation {
        let scope = scopeID
        guard host == api.address.baseURL.absoluteString else { throw CancellationError() }
        let result = try await api.generate(text: text, profile: profile, engine: engine, language: language)
        try Task.checkCancellation()
        guard scope == scopeID else { throw CancellationError() }
        generation = result
        return result
    }
}
