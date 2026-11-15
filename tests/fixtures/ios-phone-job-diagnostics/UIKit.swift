// Host-only lifecycle facade. No simulator, device, native inference or OS grants.
import Foundation
public struct UIBackgroundTaskIdentifier: Equatable, Sendable {
    public let rawValue: Int
    public static let invalid = Self(rawValue: -1)
}
@MainActor public final class UIApplication {
    public enum State { case active, background }
    public static let shared = UIApplication()
    public var applicationState = State.active
    public func beginBackgroundTask(withName: String, expirationHandler: @escaping @Sendable () -> Void) -> UIBackgroundTaskIdentifier { .init(rawValue: 1) }
    public func endBackgroundTask(_ identifier: UIBackgroundTaskIdentifier) {}
}
@MainActor public final class UIDevice {
    public enum BatteryState { case full, unplugged }
    public static let current = UIDevice()
    public var isBatteryMonitoringEnabled = false
    public var batteryState = BatteryState.full
    public var batteryLevel: Float = 1
}
