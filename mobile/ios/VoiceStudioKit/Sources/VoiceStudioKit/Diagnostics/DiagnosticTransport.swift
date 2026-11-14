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
    func cancelAll()
}
private final class DiagnosticSessionDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }
}
/// Isolated from paired-host sessions, cookies, authentication and redirects.
public final class URLSessionDiagnosticTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var tasks: [UUID: URLSessionDataTask] = [:]
    private let session: URLSession
    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil; configuration.httpShouldSetCookies = false; configuration.urlCache = nil
        configuration.urlCredentialStorage = nil; configuration.timeoutIntervalForRequest = 2; configuration.timeoutIntervalForResource = 2
        session = URLSession(configuration: configuration, delegate: DiagnosticSessionDelegate(), delegateQueue: nil)
    }
    public func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int {
        var request = URLRequest(url: destination.endpoint); request.httpMethod = "POST"; request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue(destination.origin, forHTTPHeaderField: "Origin")
        let identifier = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let task = session.dataTask(with: request) { [weak self] _, response, error in
                    _ = self?.lock.withLock { self?.tasks.removeValue(forKey: identifier) }
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume(returning: (response as? HTTPURLResponse)?.statusCode ?? 0) }
                }
                lock.withLock {
                    guard allowed(), !Task.isCancelled else { continuation.resume(throwing: DiagnosticFailure.cancelled); return }
                    tasks[identifier] = task; task.resume()
                }
            }
        } onCancel: {
            self.lock.withLock { self.tasks.removeValue(forKey: identifier)?.cancel() }
        }
    }
    public func cancelAll() { lock.withLock { for task in tasks.values { task.cancel() }; tasks.removeAll() } }
}
