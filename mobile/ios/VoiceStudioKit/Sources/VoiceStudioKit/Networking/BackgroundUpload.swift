import Foundation
import Observation

private final class UploadBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var data: [ObjectIdentifier: Data] = [:]
    func append(_ bytes: Data, task: URLSessionTask) { lock.withLock { data[ObjectIdentifier(task), default: Data()].append(bytes) } }
    func take(task: URLSessionTask) -> Data { lock.withLock { data.removeValue(forKey: ObjectIdentifier(task)) ?? Data() } }
}

@MainActor @Observable public final class BackgroundUpload: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    public static let shared = BackgroundUpload()
    public private(set) var fraction: Double = 0
    public private(set) var pending: PendingUpload?
    public private(set) var receipt: UploadReceipt?
    public var eventsCompletion: (() -> Void)?
    public var onReceipt: (() -> Void)?
    @ObservationIgnored private nonisolated let buffer = UploadBuffer()
    @ObservationIgnored private nonisolated let persistence: DubUploadLedger
    @ObservationIgnored private let stagingDirectory: URL?
    @ObservationIgnored private var session: URLSession!
    @ObservationIgnored private var foregroundSession: URLSession!
    private override convenience init() {
        let config = URLSessionConfiguration.background(withIdentifier: "com.voicestudio.mobile.upload")
        config.isDiscretionary = false; config.sessionSendsLaunchEvents = true
        self.init(configuration: config)
    }
    /// Configuration injection lets CLT checks exercise the real start/delegate
    /// path with a loopback host; production always uses the background session.
    init(configuration config: URLSessionConfiguration, defaults: UserDefaults = .standard, stagingDirectory: URL? = nil) {
        persistence = DubUploadLedger(defaults: defaults)
        self.stagingDirectory = stagingDirectory
        super.init()
        pending = persistence.pending()
        receipt = persistence.receipt()
        config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        let foreground = URLSessionConfiguration.ephemeral
        foreground.httpCookieStorage = nil; foreground.urlCredentialStorage = nil
        foregroundSession = URLSession(configuration: foreground, delegate: self, delegateQueue: nil)
    }
    public func start(source: URL, api: APIClient, languages: [DubLanguage], profile: String, audioOnly: Bool, filename: String? = nil) async throws {
        try DubVoiceChoice.validate(profile)
        guard pending == nil else { throw ClientError("mobile.errors.upload_active") }
        guard !languages.isEmpty else { throw ClientError("mobile.errors.choose_language") }
        let id = UUID().uuidString
        let directory = try stagingDirectory ?? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let output = directory.appendingPathComponent("upload-\(id).multipart")
        let staging = Task.detached {
            try Multipart.stageDub(source: source, output: output, jobID: id, audioOnly: audioOnly, filename: filename)
        }
        let type = try await withTaskCancellationHandler { try await staging.value } onCancel: { staging.cancel() }
        let meta = PendingUpload(host: api.address.baseURL.absoluteString, jobID: id, languages: languages, profile: profile, file: output, filename: filename ?? source.lastPathComponent)
        do {
            try Task.checkCancellation()
            var request = try api.request("/dub/upload", method: "POST", contentType: type)
            request.httpBody = nil
            let task = session.uploadTask(with: request, fromFile: output)
            task.taskDescription = String(data: try JSONEncoder().encode(meta), encoding: .utf8)
            try persistence.begin(meta)
            pending = meta; fraction = 0; receipt = nil
            task.resume()
        } catch { try? FileManager.default.removeItem(at: output); throw error }
    }
    public func scope(to host: String?) {
        if let meta = pending, meta.host != host {
            pending = nil; fraction = 0
            persistence.discard(meta)
            Task {
                await cancelTasks(for: meta)
                try? FileManager.default.removeItem(at: meta.file)
            }
        }
        if receipt?.pending.host != host { clearReceipt() }
    }
    public func cancel() async {
        guard let meta = pending else { return }
        persistence.discard(meta); pending = nil; fraction = 0
        await cancelTasks(for: meta)
        try? FileManager.default.removeItem(at: meta.file)
    }
    private func cancelTasks(for meta: PendingUpload) async {
        for uploadSession in [session, foregroundSession] {
            guard let uploadSession else { continue }
            for task in await uploadSession.allTasks {
                guard let description = task.taskDescription,
                      let data = description.data(using: .utf8),
                      let taskMeta = try? JSONDecoder().decode(PendingUpload.self, from: data),
                      taskMeta.host == meta.host, taskMeta.jobID == meta.jobID else { continue }
                task.cancel()
            }
        }
    }
    public func clearReceipt() { receipt = nil; persistence.clearReceipt() }
    public func takeReceipt(host: String) -> UploadReceipt? {
        guard let receipt = persistence.takeReceipt(host: host) else { return nil }
        self.receipt = nil
        return receipt
    }
    nonisolated public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) { buffer.append(data, task: dataTask) }
    nonisolated public func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        let fraction = Double(totalBytesSent) / Double(max(1, totalBytesExpectedToSend))
        let jobID = task.taskDescription.flatMap { $0.data(using: .utf8) }.flatMap { try? JSONDecoder().decode(PendingUpload.self, from: $0).jobID }
        Task { @MainActor in if let jobID, self.pending?.jobID == jobID { self.fraction = min(1, fraction) } }
    }
    nonisolated public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }
    nonisolated public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        let body = buffer.take(task: task)
        guard let description = task.taskDescription, let data = description.data(using: .utf8), let meta = try? JSONDecoder().decode(PendingUpload.self, from: data) else { return }
        let status = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let authorization = task.originalRequest?.value(forHTTPHeaderField: "Authorization") != nil
        let result = UploadCompletion.resolve(pending: meta, reply: HTTPReply(data: body, status: status), error: error,
                                              authenticated: authorization, background: session.configuration.identifier != nil)
        let receipt: UploadReceipt
        switch result {
        case .retryInForeground:
            if let request = task.originalRequest {
                Task { @MainActor in
                    guard self.pending?.jobID == meta.jobID, self.persistence.pending()?.jobID == meta.jobID else { return }
                    let retry = self.foregroundSession.uploadTask(with: request, fromFile: meta.file)
                    retry.taskDescription = description
                    retry.resume()
                }
                return
            }
            receipt = UploadReceipt(pending: meta, upload: nil, failure: ClientError("mobile.ios.upload_failed"))
        case .cancelled:
            persistence.discard(meta)
            try? FileManager.default.removeItem(at: meta.file)
            Task { @MainActor in
                if self.pending?.jobID == meta.jobID { self.pending = nil; self.fraction = 0 }
            }
            return
        case .finished(let completion): receipt = completion
        }
        // A canceled upload must never replace a newer host's pending upload.
        // Persist before iOS's background-session completion handler is called.
        guard persistence.complete(receipt) else {
            try? FileManager.default.removeItem(at: meta.file)
            return
        }
        try? FileManager.default.removeItem(at: meta.file)
        Task { @MainActor in
            guard self.pending?.jobID == meta.jobID, self.pending?.host == meta.host else { return }
            self.pending = nil; self.receipt = receipt; self.onReceipt?()
        }
    }
    nonisolated public func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Task { @MainActor in self.eventsCompletion?(); self.eventsCompletion = nil }
    }
}
