import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public protocol DiagnosticClock: Sendable {
    func nowMs() -> Int64
    func sleep(milliseconds: Int) async throws
}
public struct SystemDiagnosticClock: DiagnosticClock {
    public init() {}
    public func nowMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }
    public func sleep(milliseconds: Int) async throws { try await Task.sleep(for: .milliseconds(milliseconds)) }
}
public protocol DiagnosticStorage: Sendable {
    func read(_ name: String, maxBytes: Int) throws -> Data?
    func write(_ name: String, data: Data) throws
    func remove(_ name: String) throws
}
protocol DiagnosticFileSynchronizing: Sendable {
    func synchronize(_ url: URL) throws
}
struct SystemDiagnosticFileSynchronizer: DiagnosticFileSynchronizing {
    func synchronize(_ url: URL) throws {
        let descriptor = open(url.path, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { _ = close(descriptor) }
        guard fsync(descriptor) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    }
}
public struct FileDiagnosticStorage: DiagnosticStorage {
    public let directory: URL
    private let synchronizer: any DiagnosticFileSynchronizing
    public init(directory: URL) { self.init(directory: directory, synchronizer: SystemDiagnosticFileSynchronizer()) }
    init(directory: URL, synchronizer: any DiagnosticFileSynchronizing) {
        self.directory = directory; self.synchronizer = synchronizer
    }
    private func target(_ name: String) throws -> URL {
        guard directory.isFileURL, ["consent.json", "outbox.json", "revoked.json"].contains(name) else { throw DiagnosticFailure.storageUnavailable }
        return directory.appendingPathComponent(name)
    }
    public func read(_ name: String, maxBytes: Int) throws -> Data? {
        let url = try target(name)
        if name == "revoked.json" {
            do {
                let info = try FileManager.default.attributesOfItem(atPath: url.path)
                guard info[.type] as? FileAttributeType == .typeRegular else { throw DiagnosticFailure.corruptStore }
            } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileNoSuchFile { return nil }
        }
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        let data = try handle.read(upToCount: maxBytes + 1) ?? Data()
        guard data.count <= maxBytes else { throw DiagnosticFailure.corruptStore }
        return data
    }
    public func write(_ name: String, data: Data) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let url = try target(name)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        // Atomic replacement prevents torn files; checked file and directory
        // barriers also commit the publication before consent can be enabled.
        try synchronizer.synchronize(url)
        try synchronizer.synchronize(directory)
    }
    public func remove(_ name: String) throws {
        let url = try target(name)
        if name == "revoked.json" {
            do { try FileManager.default.removeItem(at: url) }
            catch let error as CocoaError where error.code == .fileNoSuchFile || error.code == .fileReadNoSuchFile { return }
            return
        }
        if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
    }
}
public protocol DiagnosticTransport: Sendable {
    func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int
    func sendApp(destination: DiagnosticDestination, body: Data, userAgent: String, allowed: @escaping @Sendable () -> Bool) async throws -> DiagnosticTransportReply
    func cancelAll()
}
public extension DiagnosticTransport {
    func sendApp(destination: DiagnosticDestination, body: Data, userAgent: String, allowed: @escaping @Sendable () -> Bool) async throws -> DiagnosticTransportReply {
        DiagnosticTransportReply(status: try await send(destination: destination, body: body, allowed: allowed))
    }
}
private final class DiagnosticSessionDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private final class Transfer {
        let identifier: UUID
        let task: URLSessionDataTask
        let allowed: @Sendable () -> Bool
        let continuation: CheckedContinuation<DiagnosticTransportReply, any Error>
        var status = 0
        var body = Data()
        init(_ identifier: UUID, task: URLSessionDataTask, allowed: @escaping @Sendable () -> Bool, continuation: CheckedContinuation<DiagnosticTransportReply, any Error>) {
            self.identifier = identifier; self.task = task; self.allowed = allowed; self.continuation = continuation
        }
    }
    private let lock = NSLock()
    private var transfers: [Int: Transfer] = [:]
    private var identifiers: [UUID: Int] = [:]
    private let responseLimit = 16384

    func start(_ task: URLSessionDataTask, identifier: UUID, allowed: @escaping @Sendable () -> Bool, continuation: CheckedContinuation<DiagnosticTransportReply, any Error>) {
        let started = lock.withLock {
            guard allowed(), !Task.isCancelled else { return false }
            transfers[task.taskIdentifier] = Transfer(identifier, task: task, allowed: allowed, continuation: continuation)
            identifiers[identifier] = task.taskIdentifier
            task.resume()
            return true
        }
        if !started { task.cancel(); continuation.resume(throwing: DiagnosticFailure.cancelled) }
    }
    /// Call only under lock. Removing before resuming makes cancellation and
    /// URLSession completion races resolve each continuation exactly once.
    private func take(_ taskID: Int) -> Transfer? {
        guard let transfer = transfers.removeValue(forKey: taskID) else { return nil }
        identifiers.removeValue(forKey: transfer.identifier)
        return transfer
    }
    func cancel(_ identifier: UUID) {
        let transfer = lock.withLock { identifiers[identifier].flatMap { take($0) } }
        if let transfer { transfer.task.cancel(); transfer.continuation.resume(throwing: DiagnosticFailure.cancelled) }
    }
    func cancelAll() {
        let pending = lock.withLock {
            let pending = Array(transfers.values)
            transfers.removeAll(); identifiers.removeAll()
            return pending
        }
        for transfer in pending { transfer.task.cancel(); transfer.continuation.resume(throwing: DiagnosticFailure.cancelled) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        var disposition = URLSession.ResponseDisposition.allow
        let finished: (Transfer, Result<DiagnosticTransportReply, any Error>)? = lock.withLock {
            guard let transfer = transfers[dataTask.taskIdentifier] else { disposition = .cancel; return nil }
            transfer.status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if !transfer.allowed() {
                disposition = .cancel; _ = take(dataTask.taskIdentifier)
                return (transfer, .failure(DiagnosticFailure.cancelled))
            }
            if response.expectedContentLength > Int64(responseLimit) {
                disposition = .cancel; _ = take(dataTask.taskIdentifier)
                return (transfer, .success(DiagnosticTransportReply(status: transfer.status)))
            }
            return nil
        }
        completionHandler(disposition)
        if let (transfer, result) = finished { transfer.task.cancel(); transfer.continuation.resume(with: result) }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let finished: (Transfer, Result<DiagnosticTransportReply, any Error>)? = lock.withLock {
            guard let transfer = transfers[dataTask.taskIdentifier] else { return nil }
            if !transfer.allowed() {
                _ = take(dataTask.taskIdentifier)
                return (transfer, .failure(DiagnosticFailure.cancelled))
            }
            // Foundation delivers chunks instead of retaining the full response.
            // Test the remaining capacity before appending any bytes.
            if data.count > responseLimit - transfer.body.count {
                _ = take(dataTask.taskIdentifier)
                return (transfer, .success(DiagnosticTransportReply(status: transfer.status)))
            }
            transfer.body.append(data)
            return nil
        }
        if let (transfer, result) = finished { transfer.task.cancel(); transfer.continuation.resume(with: result) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        guard let transfer = lock.withLock({ take(task.taskIdentifier) }) else { return }
        if !transfer.allowed() { transfer.continuation.resume(throwing: DiagnosticFailure.cancelled) }
        else if let error { transfer.continuation.resume(throwing: error) }
        else { transfer.continuation.resume(returning: DiagnosticTransportReply(status: transfer.status, body: transfer.body)) }
    }
}
/// Isolated from paired-host sessions, cookies, authentication and redirects.
public final class URLSessionDiagnosticTransport: DiagnosticTransport, @unchecked Sendable {
    private let delegate: DiagnosticSessionDelegate
    private let session: URLSession
    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil; configuration.httpShouldSetCookies = false; configuration.urlCache = nil
        configuration.urlCredentialStorage = nil; configuration.timeoutIntervalForRequest = 2; configuration.timeoutIntervalForResource = 2
        let delegate = DiagnosticSessionDelegate(); self.delegate = delegate
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }
    deinit { session.invalidateAndCancel() }
    public func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int {
        try await request(destination: destination, body: body, userAgent: nil, allowed: allowed).status
    }
    public func sendApp(destination: DiagnosticDestination, body: Data, userAgent: String, allowed: @escaping @Sendable () -> Bool) async throws -> DiagnosticTransportReply {
        try await request(destination: destination, body: body, userAgent: userAgent, allowed: allowed)
    }
    private func request(destination: DiagnosticDestination, body: Data, userAgent: String?, allowed: @escaping @Sendable () -> Bool) async throws -> DiagnosticTransportReply {
        var request = URLRequest(url: destination.endpoint); request.httpMethod = "POST"; request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let app = destination.app {
            request.setValue(app.ingestKey, forHTTPHeaderField: "X-Supermetry-Key")
            request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        } else { request.setValue(destination.origin, forHTTPHeaderField: "Origin") }
        let identifier = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                delegate.start(session.dataTask(with: request), identifier: identifier, allowed: allowed, continuation: continuation)
            }
        } onCancel: { self.delegate.cancel(identifier) }
    }
    public func cancelAll() { delegate.cancelAll() }
}
