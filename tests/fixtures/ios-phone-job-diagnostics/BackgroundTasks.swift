// Host-only scheduler facade. A denied continuation leaves the finite CPU grant.
import Foundation
open class BGTask: @unchecked Sendable {
    public func setTaskCompleted(success: Bool) {}
}
public final class BGContinuedProcessingTask: BGTask, @unchecked Sendable {
    public let progress = Progress(totalUnitCount: 0)
    public var expirationHandler: (@Sendable () -> Void)?
}
public final class BGContinuedProcessingTaskRequest {
    public struct Resources: OptionSet, Sendable {
        public let rawValue: Int
        public init(rawValue: Int) { self.rawValue = rawValue }
        public static let gpu = Self(rawValue: 1)
    }
    public enum Strategy { case fail }
    public var strategy = Strategy.fail
    public var requiredResources: Resources = []
    public init(identifier: String, title: String, subtitle: String) {}
}
@MainActor public final class BGTaskScheduler {
    public static let shared = BGTaskScheduler()
    public static let supportedResources: BGContinuedProcessingTaskRequest.Resources = []
    public func register(forTaskWithIdentifier: String, using: DispatchQueue?, launchHandler: @escaping @Sendable (BGTask) -> Void) -> Bool { false }
    public func submit(_ request: BGContinuedProcessingTaskRequest) throws {}
    public func cancel(taskRequestWithIdentifier: String) {}
}
