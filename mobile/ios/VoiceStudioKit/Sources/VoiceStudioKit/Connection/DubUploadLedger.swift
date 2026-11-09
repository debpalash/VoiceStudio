import Foundation

public struct PendingUpload: Codable, Sendable {
    public let host: String
    public let jobID: String
    public let languages: [DubLanguage]
    public let profile: String
    public let file: URL
    public let filename: String?
    public init(host: String, jobID: String, languages: [DubLanguage], profile: String, file: URL, filename: String? = nil) {
        self.host = host; self.jobID = jobID; self.languages = languages; self.profile = profile; self.file = file
        self.filename = filename
    }
}
public struct UploadReceipt: Codable, Sendable {
    public let pending: PendingUpload
    public let upload: DubUpload?
    public let failure: ClientError?
    public init(pending: PendingUpload, upload: DubUpload?, failure: ClientError?) {
        self.pending = pending; self.upload = upload; self.failure = failure
    }
}

/// Delegate callbacks and host transitions can race. Serialize their persisted
/// compare-and-update so an old completion cannot remove a new host's upload.
public final class DubUploadLedger: @unchecked Sendable {
    private let lock = NSLock()
    private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    public func pending() -> PendingUpload? { lock.withLock { readPending() } }
    public func receipt() -> UploadReceipt? {
        lock.withLock { defaults.data(forKey: "uploadReceipt").flatMap { try? JSONDecoder().decode(UploadReceipt.self, from: $0) } }
    }
    public func takeReceipt(host: String) -> UploadReceipt? {
        lock.withLock {
            guard let data = defaults.data(forKey: "uploadReceipt"),
                  let receipt = try? JSONDecoder().decode(UploadReceipt.self, from: data),
                  receipt.pending.host == host else { return nil }
            defaults.removeObject(forKey: "uploadReceipt")
            return receipt
        }
    }
    private func readPending() -> PendingUpload? {
        defaults.data(forKey: "pendingUpload").flatMap { try? JSONDecoder().decode(PendingUpload.self, from: $0) }
    }
    public func begin(_ pending: PendingUpload) throws {
        let data = try JSONEncoder().encode(pending)
        lock.withLock {
            defaults.set(data, forKey: "pendingUpload")
            defaults.removeObject(forKey: "uploadReceipt")
        }
    }
    public func discard(_ pending: PendingUpload) {
        lock.withLock {
            if readPending()?.jobID == pending.jobID { defaults.removeObject(forKey: "pendingUpload") }
        }
    }
    public func clearReceipt() { lock.withLock { defaults.removeObject(forKey: "uploadReceipt") } }
    public func complete(_ receipt: UploadReceipt) -> Bool {
        lock.withLock {
            guard let current = readPending(), current.jobID == receipt.pending.jobID,
                  current.host == receipt.pending.host else { return false }
            defaults.set(try? JSONEncoder().encode(receipt), forKey: "uploadReceipt")
            defaults.removeObject(forKey: "pendingUpload")
            return true
        }
    }
}

