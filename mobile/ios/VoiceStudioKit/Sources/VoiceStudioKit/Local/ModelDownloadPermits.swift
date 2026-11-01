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
