import Foundation

public struct HTTPReply: Sendable {
    public let data: Data
    public let status: Int
    public let headers: [String: String]
    public init(data: Data, status: Int = 200, headers: [String: String] = [:]) {
        self.data = data; self.status = status
        self.headers = Dictionary(headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { _, new in new })
    }
}
public struct DownloadReply: Sendable {
    public let file: URL
    public let status: Int
    public let headers: [String: String]
    public init(file: URL, status: Int, headers: [String: String]) { self.file = file; self.status = status; self.headers = headers }
}
public protocol HTTPTransport: Sendable {
    var preferredRangeBytes: Int64 { get }
    func send(_ request: URLRequest) async throws -> HTTPReply
    func download(_ request: URLRequest) async throws -> DownloadReply
    func download(_ request: URLRequest, maxBytes: Int) async throws -> DownloadReply
    func download(_ request: URLRequest, progress: @escaping @Sendable (Int64) async -> Void) async throws -> DownloadReply
}
public extension HTTPTransport {
    var preferredRangeBytes: Int64 { 4_194_304 }
    func download(_ request: URLRequest, progress: @escaping @Sendable (Int64) async -> Void) async throws -> DownloadReply {
        let reply = try await download(request)
        await progress(Int64((try? reply.file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0))
        return reply
    }
    // In-memory test transports still enforce the same boundary. Network transports
    // must override this method to enforce the limit while receiving the response.
    func download(_ request: URLRequest, maxBytes: Int) async throws -> DownloadReply {
        guard maxBytes > 0 else { throw ClientError("mobile.errors.file") }
        let reply = try await download(request)
        do {
            guard (try reply.file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max) <= maxBytes else { throw ClientError("mobile.errors.file") }
            return reply
        } catch { try? FileManager.default.removeItem(at: reply.file); throw error }
    }
    func download(_ request: URLRequest) async throws -> DownloadReply {
        let reply = try await send(request)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try reply.data.write(to: file)
        return DownloadReply(file: file, status: reply.status, headers: reply.headers)
    }
}

/// Refuse redirects, including same-host redirects: credentials and media never leave the paired origin.
public final class HostSessionDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }
}
public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession
    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
        config.timeoutIntervalForRequest = 120; config.timeoutIntervalForResource = 3600
        session = URLSession(configuration: config, delegate: HostSessionDelegate(), delegateQueue: nil)
    }
    public init(session: URLSession) { self.session = session }
    public func download(_ request: URLRequest) async throws -> DownloadReply {
        let (file, response) = try await session.download(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError("mobile.errors.invalid_response") }
        let headers = http.allHeaderFields.reduce(into: [String: String]()) { out, field in
            if let key = field.key as? String { out[key.lowercased()] = String(describing: field.value) }
        }
        return DownloadReply(file: file, status: http.statusCode, headers: headers)
    }
    public func download(_ request: URLRequest, maxBytes: Int) async throws -> DownloadReply {
        guard maxBytes > 0 else { throw ClientError("mobile.errors.file") }
        // A download completion handler can suppress progress delegate callbacks.
        // Read the response stream ourselves so neither declared nor chunked bodies
        // can consume unbounded temporary storage before validation.
        let (bytes, response) = try await session.bytes(for: request, delegate: HostSessionDelegate())
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse else { throw ClientError("mobile.errors.invalid_response") }
        guard response.expectedContentLength <= Int64(maxBytes) else { throw ClientError("mobile.errors.file") }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        guard FileManager.default.createFile(atPath: file.path,contents: nil,attributes: [.posixPermissions: 0o600]) else { throw ClientError("mobile.errors.file") }
        var completed = false
        defer { if !completed { try? FileManager.default.removeItem(at: file) } }
        let writer = try FileHandle(forWritingTo: file)
        defer { try? writer.close() }
        var chunk = Data(), received = 0
        chunk.reserveCapacity(65_536)
        for try await byte in bytes {
            guard received < maxBytes else { throw ClientError("mobile.errors.file") }
            received += 1; chunk.append(byte)
            if chunk.count == 65_536 {
                try Task.checkCancellation(); try writer.write(contentsOf: chunk)
                chunk.removeAll(keepingCapacity: true)
            }
        }
        try Task.checkCancellation()
        if !chunk.isEmpty { try writer.write(contentsOf: chunk) }
        let headers = http.allHeaderFields.reduce(into: [String: String]()) { out, field in
            if let key = field.key as? String { out[key.lowercased()] = String(describing: field.value) }
        }
        completed = true
        return DownloadReply(file: file, status: http.statusCode, headers: headers)
    }
    public func send(_ request: URLRequest) async throws -> HTTPReply {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError("mobile.errors.invalid_response") }
        return HTTPReply(data: data, status: http.statusCode, headers: http.allHeaderFields.reduce(into: [:]) { out, field in
            if let key = field.key as? String { out[key] = String(describing: field.value) }
        })
    }
}

public struct Multipart: Sendable {
    public let boundary: String
    public private(set) var data = Data()
    public init(boundary: String = "VoiceStudio-" + UUID().uuidString) { self.boundary = boundary }
    public var contentType: String { "multipart/form-data; boundary=" + boundary }
    public mutating func field(_ name: String, _ value: String) {
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(safe(name))\"\r\n\r\n\(value)\r\n")
    }
    public mutating func file(_ name: String, filename: String, bytes: Data, mime: String) {
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(safe(name))\"; filename=\"\(safe(filename))\"\r\nContent-Type: \(mime)\r\n\r\n")
        data.append(bytes); append("\r\n")
    }
    public mutating func finish() { append("--\(boundary)--\r\n") }
    private mutating func append(_ s: String) { data.append(Data(s.utf8)) }
    private func safe(_ s: String) -> String { s.replacingOccurrences(of: "\"", with: "_").replacingOccurrences(of: "\r", with: "_").replacingOccurrences(of: "\n", with: "_") }

    /// Bounded-memory multipart staging for background file uploads. The caller retains output until completion.
    public static func stageDub(source: URL, output: URL, jobID: String, audioOnly: Bool, filename: String? = nil) throws -> String {
        var form = Multipart()
        form.field("job_id", jobID); form.field("input_type", audioOnly ? "audio" : "video")
        form.append("--\(form.boundary)\r\nContent-Disposition: form-data; name=\"video\"; filename=\"\(form.safe(filename ?? source.lastPathComponent))\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        let reader = try FileHandle(forReadingFrom: source)
        defer { try? reader.close() }
        guard FileManager.default.createFile(atPath: output.path, contents: form.data) else { throw ClientError("mobile.errors.file") }
        var completed = false
        defer { if !completed { try? FileManager.default.removeItem(at: output) } }
        let writer = try FileHandle(forWritingTo: output)
        defer { try? writer.close() }
        try writer.seekToEnd()
        while let chunk = try reader.read(upToCount: 1_048_576), !chunk.isEmpty {
            try Task.checkCancellation()
            try writer.write(contentsOf: chunk)
        }
        try writer.write(contentsOf: Data("\r\n--\(form.boundary)--\r\n".utf8))
        completed = true
        return form.contentType
    }
}
