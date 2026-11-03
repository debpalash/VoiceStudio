import Foundation

/// One budget across models; cancelled waiters never hold a transfer slot.
actor ModelDownloadPermits {
    private var available: Int
    init(limit: Int) { available = limit }
    func acquire() async throws {
        while available == 0 { try await Task.sleep(for: .milliseconds(50)) }
        try Task.checkCancellation(); available -= 1
    }
    func release() { available += 1 }
}

/// Orders native task start and cancellation, including cancellation before registration.
public final class ModelDownloadStartGate: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    public init() {}
    public func start(_ operation: () -> Void) -> Bool {
        lock.withLock {
            guard !cancelled else { return false }
            operation(); return true
        }
    }
    public func cancel(_ operation: () -> Void) {
        lock.withLock {
            guard !cancelled else { return }
            cancelled = true; operation()
        }
    }
}
