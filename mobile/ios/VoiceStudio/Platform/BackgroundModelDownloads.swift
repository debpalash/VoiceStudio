import Foundation
import CryptoKit
import VoiceStudioKit

/// URLSession owns network transfers while the process sleeps. Receipts are written before
/// delivering completions, so a relaunch can recover a finished range without downloading it again.
final class BackgroundModelDownloads: NSObject, HTTPTransport, URLSessionDownloadDelegate, @unchecked Sendable {
    let preferredRangeBytes: Int64 = 67_108_864
    static let wifiIdentifier = "sh.voicestudio.models.wifi"
    static let mobileIdentifier = "sh.voicestudio.models.mobile"
    private struct Receipt: Codable { let status: Int; let headers: [String:String] }
    private struct Pending {
        let continuation: CheckedContinuation<DownloadReply,any Error>
        let progress: @Sendable (Int64) async -> Void
    }
    private let root: URL
    private let lock = NSLock()
    private var pending: [Int:Pending] = [:]
    private var failures: [Int:any Error] = [:]
    private var session: URLSession!
    private var progressTimes: [Int:TimeInterval] = [:]
    private var eventsFinished = false
    private var completion: (@Sendable () -> Void)?
    init(root: URL, allowMobile: Bool) throws {
        self.root = root.appendingPathComponent(allowMobile ? "transfers-mobile" : "transfers-wifi")
        try FileManager.default.createDirectory(at: self.root,withIntermediateDirectories: true)
        super.init()
        let config = URLSessionConfiguration.background(withIdentifier: allowMobile ? Self.mobileIdentifier : Self.wifiIdentifier)
        config.isDiscretionary = false; config.sessionSendsLaunchEvents = true; config.waitsForConnectivity = true
        config.allowsCellularAccess = allowMobile; config.allowsExpensiveNetworkAccess = allowMobile
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCredentialStorage = nil; config.urlCache = nil
        config.httpMaximumConnectionsPerHost = 4
        config.timeoutIntervalForRequest = 120; config.timeoutIntervalForResource = 7*24*3600
        session = URLSession(configuration: config,delegate: self,delegateQueue: nil)
    }
    func finishEvents(_ completion: @escaping @Sendable () -> Void) {
        let finished = lock.withLock { () -> Bool in
            if eventsFinished { eventsFinished = false; return true }
            self.completion = completion; return false
        }
        if finished { DispatchQueue.main.async(execute: completion) }
    }
    func send(_ request: URLRequest) async throws -> HTTPReply { throw ClientError("mobile.local.download_failed") }
    func download(_ request: URLRequest) async throws -> DownloadReply { try await download(request,progress: { _ in }) }
    func download(_ request: URLRequest,progress: @escaping @Sendable (Int64) async -> Void) async throws -> DownloadReply {
        guard let url = request.url, ModelRedirectDelegate.allowed(url) else { throw ClientError("mobile.local.download_failed") }
        let identity = url.absoluteString + "|" + (request.value(forHTTPHeaderField: "Range") ?? "")
        let owner = URLProtocol.property(forKey: "VoiceStudioModel",in: request) as? String ?? "unscoped"
        let bounds = request.value(forHTTPHeaderField: "Range")?.dropFirst(6).split(separator: "-").compactMap { Int64($0) } ?? []
        let maximum = bounds.count == 2 ? bounds[1]-bounds[0]+1 : (URLProtocol.property(forKey: "VoiceStudioExpectedBytes",in: request) as? Int64 ?? 0)
        // Keep the byte bound in OS-owned task metadata, including after relaunch/resume.
        let key = ownerKey(owner)+"."+SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x",$0) }.joined()+".\(maximum)"
        if let cached = receipt(key) { return cached }
        let tasks = await session.allTasks
        let task: URLSessionDownloadTask
        if let existing = tasks.first(where: { $0.taskDescription == key && $0.state != .completed && $0.state != .canceling }) as? URLSessionDownloadTask { task = existing }
        else if let data = try? Data(contentsOf: path(key,"resume")) { task = session.downloadTask(withResumeData: data) }
        else { task = session.downloadTask(with: request) }
        task.taskDescription = key
        let startGate = ModelDownloadStartGate()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                lock.withLock { pending[task.taskIdentifier] = Pending(continuation: continuation,progress: progress) }
                // Completion may have arrived between inspecting the OS queue and registering.
                if let cached = receipt(key) {
                    task.cancel()
                    lock.withLock { pending.removeValue(forKey: task.taskIdentifier) }?.continuation.resume(returning: cached)
                } else if let error = lock.withLock({ failures.removeValue(forKey: task.taskIdentifier) }) {
                    task.cancel()
                    lock.withLock { pending.removeValue(forKey: task.taskIdentifier) }?.continuation.resume(throwing: error)
                } else if Task.isCancelled { self.stopWaiting(task) }
                else if !startGate.start({ task.resume() }) { self.stopWaiting(task) }
            }
        } onCancel: { startGate.cancel { self.stopWaiting(task) } }
    }
    private func stopWaiting(_ task: URLSessionDownloadTask) {
        // Suspending keeps the system-owned partial range available for Resume.
        task.suspend()
        lock.withLock { pending.removeValue(forKey: task.taskIdentifier) }?.continuation.resume(throwing: CancellationError())
    }
    private func ownerKey(_ id: String) -> String { SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x",$0) }.joined() }
    func reconcile(authorized: Set<String>) async {
        let keys = Set(authorized.map(ownerKey))
        for task in await session.allTasks {
            guard let key = task.taskDescription else { task.cancel(); continue }
            if !keys.contains(String(key.split(separator: ".").first ?? "")) { task.cancel() }
        }
    }
    func remove(modelID: String) async {
        let prefix = ownerKey(modelID)+"."
        for task in await session.allTasks where task.taskDescription?.hasPrefix(prefix) == true { task.cancel() }
        let files = (try? FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil)) ?? []
        for file in files where file.lastPathComponent.hasPrefix(prefix) { try? FileManager.default.removeItem(at: file) }
    }
    private func path(_ key: String,_ suffix: String) -> URL { root.appendingPathComponent(key+"."+suffix) }
    private func receipt(_ key: String) -> DownloadReply? {
        guard FileManager.default.fileExists(atPath: path(key,"body").path),
              let data = try? Data(contentsOf: path(key,"json")),let receipt = try? JSONDecoder().decode(Receipt.self,from: data) else { return nil }
        return DownloadReply(file: path(key,"body"),status: receipt.status,headers: receipt.headers)
    }
    func urlSession(_ session: URLSession,downloadTask: URLSessionDownloadTask,didFinishDownloadingTo location: URL) {
        guard let key = downloadTask.taskDescription,let http = downloadTask.response as? HTTPURLResponse else { return }
        guard let final = http.url,ModelRedirectDelegate.allowed(final) else {
            lock.withLock { failures[downloadTask.taskIdentifier] = ClientError("mobile.local.download_failed") }; return
        }
        let maximum = Int64(key.split(separator: ".").last ?? "") ?? 0
        if maximum > 0,let size = try? location.resourceValues(forKeys: [.fileSizeKey]).fileSize,Int64(size) > maximum {
            lock.withLock { failures[downloadTask.taskIdentifier] = ClientError("mobile.local.download_failed") }; return
        }
        do {
            let destination = path(key,"body")
            if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
            try FileManager.default.moveItem(at: location,to: destination)
            let headers = http.allHeaderFields.reduce(into: [String:String]()) { result,entry in
                if let key = entry.key as? String { result[key.lowercased()] = String(describing: entry.value) }
            }
            try JSONEncoder().encode(Receipt(status: http.statusCode,headers: headers)).write(to: path(key,"json"),options: .atomic)
            try? FileManager.default.removeItem(at: path(key,"resume"))
        } catch { lock.withLock { failures[downloadTask.taskIdentifier] = error } }
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,didCompleteWithError error: (any Error)?) {
        guard let key = task.taskDescription else { return }
        if let data = (error as NSError?)?.userInfo[NSURLSessionDownloadTaskResumeData] as? Data { try? data.write(to: path(key,"resume"),options: .atomic) }
        else if error != nil { try? FileManager.default.removeItem(at: path(key,"resume")) }
        let state = lock.withLock { () -> (Pending?,(any Error)?) in
            if failures[task.taskIdentifier] == nil,let error { failures[task.taskIdentifier] = error }
            let waiting = pending.removeValue(forKey: task.taskIdentifier)
            progressTimes.removeValue(forKey: task.taskIdentifier)
            return (waiting,waiting == nil ? failures[task.taskIdentifier] : failures.removeValue(forKey: task.taskIdentifier))
        }
        guard let waiting = state.0 else { return }
        if let cached = receipt(key),state.1 == nil { waiting.continuation.resume(returning: cached) }
        else { waiting.continuation.resume(throwing: state.1 ?? error ?? ClientError("mobile.local.download_failed")) }
    }
    func urlSession(_ session: URLSession,downloadTask: URLSessionDownloadTask,didWriteData bytesWritten: Int64,totalBytesWritten: Int64,totalBytesExpectedToWrite: Int64) {
        guard let key = downloadTask.taskDescription else { return }
        let maximum = Int64(key.split(separator: ".").last ?? "") ?? 0
        if maximum > 0 && (totalBytesWritten > maximum || totalBytesExpectedToWrite > maximum) {
            let ignoredRange = downloadTask.originalRequest?.value(forHTTPHeaderField: "Range") != nil && (downloadTask.response as? HTTPURLResponse)?.statusCode == 200
            lock.withLock { failures[downloadTask.taskIdentifier] = ignoredRange ? ModelRangeUnsupported() : ClientError("mobile.local.download_failed") }
            downloadTask.cancel();return
        }
        let now = ProcessInfo.processInfo.systemUptime
        guard let progress = lock.withLock({ () -> (@Sendable (Int64) async -> Void)? in
            guard now-progressTimes[downloadTask.taskIdentifier,default: -.infinity] >= 0.5 || totalBytesWritten == totalBytesExpectedToWrite else { return nil }
            progressTimes[downloadTask.taskIdentifier] = now
            return pending[downloadTask.taskIdentifier]?.progress
        }) else { return }
        Task { await progress(totalBytesWritten) }
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,willPerformHTTPRedirection response: HTTPURLResponse,newRequest request: URLRequest,completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let url = request.url,ModelRedirectDelegate.allowed(url) else { completionHandler(nil);return }
        var clean = request
        for header in ["Authorization","Cookie","Referer"] { clean.setValue(nil,forHTTPHeaderField: header) }
        completionHandler(clean)
    }
    // Background URLSession follows redirects in the system process. Reject TLS
    // challenges outside the public model hosts and validate the final response too.
    func urlSession(_ session: URLSession,task: URLSessionTask,didReceive challenge: URLAuthenticationChallenge,completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition,URLCredential?) -> Void) {
        let host = challenge.protectionSpace.host
        guard let url = URL(string: "https://\(host)"),ModelRedirectDelegate.allowed(url),challenge.protectionSpace.port == 443,challenge.protectionSpace.protocol == "https",challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust else { completionHandler(.cancelAuthenticationChallenge,nil);return }
        completionHandler(.performDefaultHandling,nil)
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        let handler = lock.withLock { let value = completion; completion = nil; eventsFinished = value == nil; return value }
        if let handler { DispatchQueue.main.async(execute: handler) }
    }
}

@MainActor enum ModelDownloadSessions {
    private static var sessions: [String:BackgroundModelDownloads] = [:]
    static func transport(allowMobile: Bool) throws -> BackgroundModelDownloads {
        let id = allowMobile ? BackgroundModelDownloads.mobileIdentifier : BackgroundModelDownloads.wifiIdentifier
        if let session = sessions[id] { return session }
        let root = try FileManager.default.url(for: .applicationSupportDirectory,in: .userDomainMask,appropriateFor: nil,create: true).appendingPathComponent("PhoneModels")
        let session = try BackgroundModelDownloads(root: root,allowMobile: allowMobile)
        sessions[id] = session; return session
    }
    static func reconcile(authorized: Set<String>) async {
        for mobile in [false,true] { try? await transport(allowMobile: mobile).reconcile(authorized: authorized) }
    }
    static func remove(modelID: String) async {
        for mobile in [false,true] { try? await transport(allowMobile: mobile).remove(modelID: modelID) }
    }
    static func handle(_ identifier: String,completion: @escaping @Sendable () -> Void) -> Bool {
        guard [BackgroundModelDownloads.wifiIdentifier,BackgroundModelDownloads.mobileIdentifier].contains(identifier) else { return false }
        do { try transport(allowMobile: identifier == BackgroundModelDownloads.mobileIdentifier).finishEvents(completion) }
        catch { completion() }
        return true
    }
}
