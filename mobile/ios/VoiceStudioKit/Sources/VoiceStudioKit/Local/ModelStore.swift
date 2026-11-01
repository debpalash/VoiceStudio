import Foundation

public struct LicenceAcceptance: Codable, Sendable {
    public let fingerprint: String
    public let appVersion: String
    public let time: Date
    public let accepted: Bool
}
public struct DownloadPolicy: Sendable {
    public var allowMobileData: Bool
    public init(allowMobileData: Bool = false) { self.allowMobileData = allowMobileData }
}
/// Dedicated unauthenticated model session. Never shares paired-host cookies or credentials.
public final class ModelRedirectDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    public static func allowed(_ url: URL) -> Bool {
        guard url.scheme == "https", url.user == nil, url.password == nil, url.port == nil || url.port == 443, let host = url.host?.lowercased() else { return false }
        return ["huggingface.co", "hf-mirror.com", "hf.co", "xethub.hf.co", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"].contains(host) || host.hasSuffix(".huggingface.co") || host.hasSuffix(".hf.co") || host.hasSuffix(".hf-mirror.com")
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let url = request.url, Self.allowed(url) else { completionHandler(nil); return }
        var clean = request; clean.setValue(nil, forHTTPHeaderField: "Authorization"); clean.setValue(nil, forHTTPHeaderField: "Cookie"); clean.setValue(nil, forHTTPHeaderField: "Referer")
        completionHandler(clean)
    }
}
public struct ModelDownloadTransport: HTTPTransport {
    private let session: URLSession
    public init(policy: DownloadPolicy) {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
        config.allowsCellularAccess = policy.allowMobileData
        config.allowsExpensiveNetworkAccess = policy.allowMobileData
        config.timeoutIntervalForRequest = 60; config.timeoutIntervalForResource = 86_400
        session = URLSession(configuration: config, delegate: ModelRedirectDelegate(), delegateQueue: nil)
    }
    public func send(_ request: URLRequest) async throws -> HTTPReply { throw ClientError("mobile.local.download_failed") }
    public func download(_ request: URLRequest) async throws -> DownloadReply {
        guard let url = request.url, ModelRedirectDelegate.allowed(url) else { throw ClientError("mobile.local.download_failed") }
        let (file, response) = try await session.download(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError("mobile.local.download_failed") }
        return DownloadReply(file: file, status: http.statusCode, headers: http.allHeaderFields.reduce(into: [:]) { out, entry in
            if let key = entry.key as? String { out[key.lowercased()] = String(describing: entry.value) }
        })
    }
}
/// App-private, backup-excluded files. A completed marker alone never authorizes loading.
/// The retained ram initializer argument is advisory and does not restrict an explicit attempt.
public actor ModelStore {
    public let root: URL
    private var acceptances: [String: [LicenceAcceptance]] = [:]
    private var active = Set<String>()
    private var sourceRates: [String:Double] = [:]
    private var sourceCooldowns: [String:Date] = [:]
    private var reservations: [String: [PhoneModel.File]] = [:]
    private var leases: [String: Int] = [:]
    private struct VerificationStamp: Equatable {
        let size: Int64
        let modified: Date
        let identity: String
    }
    private var verified: [URL: VerificationStamp] = [:]
    private let bundledRoot: URL?
    private let fileHash: @Sendable (URL) throws -> String
    private let available: @Sendable (URL) throws -> Int64
    public init(root: URL, ram _: UInt64 = ProcessInfo.processInfo.physicalMemory,
                bundledRoot: URL? = nil,
                fileHash: @escaping @Sendable (URL) throws -> String = { try ModelHash.file($0) },
                available: @escaping @Sendable (URL) throws -> Int64 = { url in
                    let values = try url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey])
                    if let capacity = values.volumeAvailableCapacityForImportantUsage, capacity > 0 { return capacity }
                    if let capacity = values.volumeAvailableCapacity, capacity > 0 { return Int64(capacity) }
                    return (try FileManager.default.attributesOfFileSystem(forPath: url.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
                }) throws {
        self.bundledRoot = bundledRoot ?? Bundle.main.resourceURL?.appendingPathComponent("Catalogs")
        self.root = root; self.available = available; self.fileHash = fileHash
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var excluded = root; var values = URLResourceValues(); values.isExcludedFromBackup = true; try excluded.setResourceValues(values)
        if FileManager.default.fileExists(atPath: root.appendingPathComponent("acceptance.json").path) {
            // Corrupt acceptance never grants permission.
            acceptances = (try? JSONDecoder().decode([String: [LicenceAcceptance]].self, from: Data(contentsOf: root.appendingPathComponent("acceptance.json")))) ?? [:]
        }
    }
    public func acceptance(_ model: PhoneModel) -> LicenceAcceptance? { acceptances[model.id]?.last }
    public func allowed(_ model: PhoneModel) -> Bool { !model.licence.gated || (acceptance(model)?.accepted == true && acceptance(model)?.fingerprint == model.licence.terms_fingerprint) }
    public func accept(_ model: PhoneModel, fingerprint: String, appVersion: String, accepted: Bool) throws {
        guard fingerprint == model.licence.terms_fingerprint, !appVersion.isEmpty else { throw ClientError("mobile.local.licence_required") }
        var next = acceptances
        next[model.id, default: []].append(LicenceAcceptance(fingerprint: fingerprint, appVersion: appVersion, time: Date(), accepted: accepted))
        next[model.id] = Array(next[model.id, default: []].suffix(20))
        try JSONEncoder().encode(next).write(to: root.appendingPathComponent("acceptance.json"), options: .atomic)
        acceptances = next
    }
    public func installed(_ model: PhoneModel) -> Bool {
        guard let data = try? Data(contentsOf: directory(model).appendingPathComponent("verified.json")),
              let hashes = try? JSONDecoder().decode([String: String].self, from: data) else { return false }
        return model.files.allSatisfy { f in hashes[f.path] == f.sha256 && size(directory(model).appendingPathComponent(f.path)) == f.size }
    }
    public func authorize(_ model: PhoneModel) throws -> URL {
        guard allowed(model) else { throw ClientError("mobile.local.licence_required") }
        guard installed(model) else { throw ClientError("mobile.local.model_required") }
        let identity = try phoneModelPin(model)
        for f in model.files { guard try verify(directory(model).appendingPathComponent(f.path), file: f, identity: identity) else { throw ClientError("mobile.local.checksum_failed") } }
        return directory(model)
    }
    public func acquire(_ model: PhoneModel) throws -> URL { let path = try authorize(model); leases[model.id, default: 0] += 1; return path }
    public func release(_ model: PhoneModel) { leases[model.id] = max(0, (leases[model.id] ?? 0) - 1) }
    public func checkPermission(_ model: PhoneModel) throws { guard allowed(model) else { throw ClientError("mobile.local.licence_required") } }
    /// A native load failure forces a fresh hash before another use, even if metadata is unchanged.
    public func invalidateVerification(_ model: PhoneModel) {
        for file in model.files { verified.removeValue(forKey: directory(model).appendingPathComponent(file.path)) }
    }
    private func stamp(_ url: URL, identity: String) throws -> VerificationStamp {
        let values = try FileManager.default.attributesOfItem(atPath: url.path)
        guard values[.type] as? FileAttributeType == .typeRegular,
              let size = values[.size] as? NSNumber, let modified = values[.modificationDate] as? Date else { throw ClientError("mobile.local.checksum_failed") }
        return VerificationStamp(size: size.int64Value,modified: modified,identity: identity)
    }
    private func verify(_ url: URL, file: PhoneModel.File, identity: String) throws -> Bool {
        guard FileManager.default.fileExists(atPath: url.path) else { return false }
        let before = try stamp(url,identity: identity)
        guard before.size == file.size else { verified.removeValue(forKey: url); return false }
        if verified[url] == before { return true }
        verified.removeValue(forKey: url)
        guard try fileHash(url) == file.sha256, try stamp(url,identity: identity) == before else { return false }
        verified[url] = before
        return true
    }
    public func storageUsed() -> Int64 { directoryStorage(root) }
    public func modelStorage(_ model: PhoneModel) -> Int64 { directoryStorage(directory(model)) }
    private func directoryStorage(_ directory: URL) -> Int64 {
        guard let paths = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: nil) else { return 0 }
        var result: Int64 = 0
        for case let url as URL in paths {
            if let attributes = try? FileManager.default.attributesOfItem(atPath: url.path), attributes[.type] as? FileAttributeType == .typeRegular { result += (attributes[.size] as? NSNumber)?.int64Value ?? 0 }
        }
        return result
    }
    public func delete(_ model: PhoneModel) throws {
        guard !active.contains(model.id), leases[model.id, default: 0] == 0 else { throw ClientError("sidebarTools.wait") }
        let path = directory(model)
        invalidateVerification(model)
        if FileManager.default.fileExists(atPath: path.path) { try FileManager.default.removeItem(at: path) }
    }
    /// Call only from a user action. Each range is persisted before the next request, including across relaunch.
    public func download(_ model: PhoneModel, policy: DownloadPolicy = DownloadPolicy(),
                         wifi: @escaping @Sendable () async -> Bool,
                         transport supplied: (any HTTPTransport)? = nil,
                         details: @escaping @Sendable (ModelDownloadProgress) async -> Void = { _ in },
                         progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws {
        try checkPermission(model)
        guard !active.contains(model.id), leases[model.id, default: 0] == 0 else { throw ClientError("sidebarTools.wait") }
        active.insert(model.id)
        defer { active.remove(model.id); reservations.removeValue(forKey: model.id) }
        let transport = supplied ?? ModelDownloadTransport(policy: policy)
        try FileManager.default.createDirectory(at: directory(model), withIntermediateDirectories: true)
        let identity = try phoneModelPin(model)
        for file in model.files where try !verify(directory(model).appendingPathComponent(file.path),file: file,identity: identity) { try recoverRanges(partial(model,file),total: file.size) }
        reservations[model.id] = try model.files.filter {
            try !verify(directory(model).appendingPathComponent($0.path), file: $0, identity: identity)
        }
        if reservations[model.id]?.isEmpty == false { try checkDownloadSpace() }
        let meter = DownloadMeter(total: model.total_bytes) { value in
            await details(value); await progress(value.fraction)
        }
        for file in model.files {
            let destination = directory(model).appendingPathComponent(file.path)
            await meter.seed(file.path,bytes: (try verify(destination,file: file,identity: identity)) ? file.size : size(partial(model,file)))
        }
        await meter.update("",bytes: 0)
        var completed: Int64 = 0
        for f in model.files {
            try Task.checkCancellation(); try checkPermission(model)
            let destination = directory(model).appendingPathComponent(f.path)
            if try verify(destination,file: f,identity: identity) { completed += f.size; continue }
            let part = partial(model, f)
            try FileManager.default.createDirectory(at: part.deletingLastPathComponent(), withIntermediateDirectories: true)
            if size(part) > f.size { try FileManager.default.removeItem(at: part) }
            if let asset = f.bundled {
                guard ModelAssetSource.validBundled(asset),f.url == nil,let bundledRoot else { throw ClientError("mobile.local.catalogue_failed") }
                let source = bundledRoot.appendingPathComponent(asset)
                if FileManager.default.fileExists(atPath: part.path) { try FileManager.default.removeItem(at: part) }
                try FileManager.default.copyItem(at: source,to: part)
            }
            if f.bundled == nil && f.size-size(part) >= max(16_777_216,transport.preferredRangeBytes)*2 {
                do { try await downloadParallel(model,file: f,part: part,policy: policy,wifi: wifi,transport: transport,meter: meter) }
                catch is ModelRangeUnsupported {
                    try? FileManager.default.removeItem(at: part.appendingPathExtension("ranges"))
                    let whole = try await downloadRange(model,file: f,offset: 0,end: f.size-1,transport: transport,meter: meter,base: 0)
                    if FileManager.default.fileExists(atPath: part.path) { try FileManager.default.removeItem(at: part) }
                    try FileManager.default.moveItem(at: whole.file,to: part)
                    await meter.replaceFile(f.path,rangePrefix: f.sha256+":",bytes: f.size)
                }
            }
            while size(part) < f.size {
                try Task.checkCancellation(); try checkPermission(model)
                let onWifi = await wifi()
                guard policy.allowMobileData || onWifi else { throw ClientError("mobile.local.wifi_required") }
                let offset = size(part)
                try checkDownloadSpace()
                let end = min(offset + transport.preferredRangeBytes - 1, f.size - 1)
                let reply: DownloadReply
                do { reply = try await downloadRange(model,file: f,offset: offset,end: end,transport: transport,meter: meter,base: offset) }
                catch is ModelRangeUnsupported { reply = try await downloadRange(model,file: f,offset: 0,end: f.size-1,transport: transport,meter: meter,base: 0) }
                defer { try? FileManager.default.removeItem(at: reply.file) }
                try Task.checkCancellation(); try checkPermission(model)
                if reply.status == 200 {
                    if FileManager.default.fileExists(atPath: part.path) { try FileManager.default.removeItem(at: part) }
                    try FileManager.default.moveItem(at: reply.file,to: part)
                } else {
                    try append(reply.file,to: part)
                }
                await progress(Double(completed + size(part)) / Double(model.total_bytes))
            }
            await meter.update("",bytes: 0,stage: "verifying")
            guard try verify(part,file: f,identity: identity) else { try? FileManager.default.removeItem(at: part); throw ClientError("mobile.local.checksum_failed") }
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
            try FileManager.default.moveItem(at: part, to: destination)
            verified.removeValue(forKey: part)
            verified[destination] = try stamp(destination,identity: identity)
            completed += f.size
            reservations[model.id]?.removeAll { $0.path == f.path }
        }
        try Task.checkCancellation(); try checkPermission(model)
        try JSONEncoder().encode(Dictionary(uniqueKeysWithValues: model.files.map { ($0.path, $0.sha256) })).write(to: directory(model).appendingPathComponent("verified.json"), options: .atomic)
        await progress(1)
    }
    private let connections = ModelDownloadPermits(limit: 4)
    private func downloadParallel(_ model: PhoneModel,file: PhoneModel.File,part: URL,policy: DownloadPolicy,wifi: @escaping @Sendable () async -> Bool,transport: any HTTPTransport,meter: DownloadMeter) async throws {
        let chunkSize = max(16_777_216,transport.preferredRangeBytes)
        let chunks = part.appendingPathExtension("ranges")
        try FileManager.default.createDirectory(at: chunks,withIntermediateDirectories: true)
        // Hash-named parent partials bind these receipts to the immutable catalogue file.
        var width = 2, previousRate = 0.0
        while size(part) < file.size {
            try Task.checkCancellation(); try checkPermission(model)
            let onWifi = await wifi()
            guard policy.allowMobileData || onWifi else { throw ClientError("mobile.local.wifi_required") }
            try checkDownloadSpace()
            let start = size(part), before = ProcessInfo.processInfo.systemUptime
            let ranges = (0..<width).compactMap { index -> (Int64,Int64)? in
                let lower = start+Int64(index)*chunkSize
                return lower < file.size ? (lower,min(file.size-1,lower+chunkSize-1)) : nil
            }
            for (lower,upper) in ranges {
                let saved = chunks.appendingPathComponent("\(lower)-\(upper)")
                if size(saved) == upper-lower+1 { await meter.seed(file.sha256+":\(lower)",bytes: size(saved)) }
            }
            let replies = try await withThrowingTaskGroup(of: Int.self) { group in
                for (lower,upper) in ranges {
                    group.addTask {
                        try await self.persistRange(model,file: file,lower: lower,upper: upper,chunks: chunks,transport: transport,meter: meter)
                    }
                }
                var statuses: [Int] = []
                for try await status in group { statuses.append(status) }
                return statuses
            }
            // A server ignoring ranges is supported by accepting one validated whole file.
            if replies.contains(200) {
                if let whole = ranges.map({ chunks.appendingPathComponent("\($0.0)-\($0.1)") }).first(where: { size($0) == file.size }) {
                    if FileManager.default.fileExists(atPath: part.path) { try FileManager.default.removeItem(at: part) }
                    try FileManager.default.moveItem(at: whole,to: part)
                    try? FileManager.default.removeItem(at: chunks)
                    await meter.replaceFile(file.path,rangePrefix: file.sha256+":",bytes: file.size)
                    await meter.update("",bytes: 0)
                    return
                }
                throw ClientError("mobile.local.download_failed")
            }
            for (lower,upper) in ranges {
                let saved = chunks.appendingPathComponent("\(lower)-\(upper)")
                try append(saved,to: part)
                try FileManager.default.removeItem(at: saved)
            }
            let rate = Double(size(part)-start)/max(0.001,ProcessInfo.processInfo.systemUptime-before)
            width = previousRate > 0 && rate < previousRate*0.85 ? 2 : 4
            previousRate = rate
        }
        try? FileManager.default.removeItem(at: chunks)
    }
    private func persistRange(_ model: PhoneModel,file: PhoneModel.File,lower: Int64,upper: Int64,chunks: URL,transport: any HTTPTransport,meter: DownloadMeter) async throws -> Int {
        let destination = chunks.appendingPathComponent("\(lower)-\(upper)")
        if size(destination) == upper-lower+1 { return 206 }
        let reply = try await downloadRange(model,file: file,offset: lower,end: upper,transport: transport,meter: meter,base: 0,meterKey: file.sha256+":\(lower)")
        defer { try? FileManager.default.removeItem(at: reply.file) }
        try Task.checkCancellation(); try checkPermission(model)
        if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
        try FileManager.default.moveItem(at: reply.file,to: destination)
        return reply.status
    }
    private func append(_ source: URL,to destination: URL,offset: UInt64 = 0) throws {
        if !FileManager.default.fileExists(atPath: destination.path) {
            guard FileManager.default.createFile(atPath: destination.path,contents: nil) else { throw ClientError("mobile.local.storage_required") }
        }
        let writer = try FileHandle(forWritingTo: destination); defer { try? writer.close() }
        try writer.seekToEnd()
        let reader = try FileHandle(forReadingFrom: source); defer { try? reader.close() }
        try reader.seek(toOffset: offset)
        while let bytes = try reader.read(upToCount: 1_048_576), !bytes.isEmpty { try writer.write(contentsOf: bytes) }
        try writer.synchronize()
    }
    private func downloadRange(_ model: PhoneModel,file: PhoneModel.File,offset: Int64,end: Int64,transport: any HTTPTransport,meter: DownloadMeter,base: Int64,meterKey: String? = nil) async throws -> DownloadReply {
        var lastFailure: (any Error)?
        var permanent = Set<String>()
        for attempt in 0..<5 {
            var retryAfter: Int?
            let hosts = ["huggingface.co","hf-mirror.com"]
            let sources = sourceRates.count >= hosts.count ? hosts.sorted { sourceRates[$0,default: 0] > sourceRates[$1,default: 0] } : (offset >= max(16_777_216,transport.preferredRangeBytes) ? Array(hosts.reversed()) : hosts)
            for host in sources where !permanent.contains(host) {
                try Task.checkCancellation(); try checkPermission(model)
                if let until = sourceCooldowns[host],until > Date() { try await Task.sleep(for: .seconds(until.timeIntervalSinceNow)) }
                try Task.checkCancellation(); try checkPermission(model)
                let url: URL
                if let fixed = file.url {
                    guard ModelAssetSource.validURL(fixed),var components = URLComponents(string: fixed) else { throw ClientError("mobile.local.catalogue_failed") }
                    if components.host == "huggingface.co" { components.host = host }
                    guard let selected = components.url else { throw ClientError("mobile.local.catalogue_failed") }; url = selected
                } else { url = URL(string: "https://\(host)/\(model.repo)/resolve/\(model.revision)/\(file.path)")! }
                let scoped = NSMutableURLRequest(url: url)
                scoped.setValue("bytes=\(offset)-\(end)",forHTTPHeaderField: "Range")
                scoped.setValue("identity",forHTTPHeaderField: "Accept-Encoding")
                URLProtocol.setProperty(model.id,forKey: "VoiceStudioModel",in: scoped)
                let request = scoped as URLRequest
                let before = ProcessInfo.processInfo.systemUptime
                do {
                    try await connections.acquire()
                    let reply: DownloadReply
                    do {
                        reply = try await transport.download(request,progress: { count in await meter.update(meterKey ?? file.path,bytes: base+min(count,end-offset+1)) })
                        await connections.release()
                    } catch { await connections.release(); throw error }
                    let count = size(reply.file)
                    let elapsed = ProcessInfo.processInfo.systemUptime-before
                    if elapsed >= 0.1 && (reply.status == 200 || reply.status == 206) { sourceRates[host] = Double(count)/elapsed }
                    diagnostic(model,event: "response",host: host,status: reply.status)
                    if reply.status == 200 && count == file.size { return reply }
                    if reply.status == 206 && count == end-offset+1 && reply.headers["content-range"] == "bytes \(offset)-\(end)/\(file.size)" { return reply }
                    try? FileManager.default.removeItem(at: reply.file)
                    if reply.status == 429 || reply.status == 408 || reply.status == 403 || (500...599).contains(reply.status) {
                        if let raw = reply.headers["retry-after"] {
                            retryAfter = Int(raw) ?? HTTPDate.secondsUntil(raw)
                        }
                        lastFailure = URLError(.badServerResponse)
                        if reply.status == 429 { sourceCooldowns[host] = Date().addingTimeInterval(Double(min(3600,max(1,retryAfter ?? 1)))) ; break }
                    } else { throw ClientError("mobile.local.download_failed") }
                } catch is ModelRangeUnsupported { throw ModelRangeUnsupported() }
                catch is CancellationError { throw CancellationError() }
                catch let error as ClientError { lastFailure = error; permanent.insert(host) }
                catch { if Task.isCancelled { throw CancellationError() }; lastFailure = error }
            }
            // Test/application-level validation failures are permanent; network failures are retried.
            if permanent.count == hosts.count { throw lastFailure ?? ClientError("mobile.local.download_failed") }
            if attempt < 4 {
                let seconds = min(3600,max(1,retryAfter ?? ((1 << attempt)+Int.random(in: 0...1))))
                for remaining in stride(from: seconds,through: 1,by: -1) {
                    await meter.update(meterKey ?? file.path,bytes: base,stage: "retrying",retry: remaining)
                    try await Task.sleep(for: .seconds(1))
                }
            }
        }
        throw lastFailure ?? ClientError("mobile.local.download_failed")
    }
    private func diagnostic(_ model: PhoneModel,event: String,host: String,status: Int) {
        // Only local, bounded status metadata; never URLs or credentials.
        let log = directory(model).appendingPathComponent(".download-events")
        let previous = (try? String(contentsOf: log,encoding: .utf8))?.split(separator: "\n").suffix(31).map(String.init) ?? []
        let records = previous+["\(Date().timeIntervalSince1970) \(event) \(host) \(status)"]
        try? Data(records.joined(separator: "\n").utf8).write(to: log,options: .atomic)
    }
    private func recoverRanges(_ part: URL,total: Int64) throws {
        let directory = part.appendingPathExtension("ranges")
        while size(part) < total {
            let offset = size(part)
            let files = (try? FileManager.default.contentsOfDirectory(at: directory,includingPropertiesForKeys: nil)) ?? []
            guard let saved = files.first(where: { file in
                let bounds = file.lastPathComponent.split(separator: "-").compactMap { Int64($0) }
                return bounds.count == 2 && bounds[0] <= offset && bounds[1] >= offset && bounds[1] < total && size(file) == bounds[1]-bounds[0]+1
            }),let lower = Int64(saved.lastPathComponent.split(separator: "-")[0]) else { return }
            guard try available(root) >= size(saved)-(offset-lower) else { throw ClientError("mobile.local.storage_required") }
            try append(saved,to: part,offset: UInt64(offset-lower))
            try FileManager.default.removeItem(at: saved)
        }
    }
    private func savedRangeBytes(_ part: URL,total: Int64) -> Int64 {
        let chunks = (try? FileManager.default.contentsOfDirectory(at: part.appendingPathExtension("ranges"),includingPropertiesForKeys: nil)) ?? []
        return chunks.reduce(0) { sum,url in
            let bounds = url.lastPathComponent.split(separator: "-").compactMap { Int64($0) }
            guard bounds.count == 2,bounds[0] >= size(part),bounds[1] < total,size(url) == bounds[1]-bounds[0]+1 else { return sum }
            return sum+size(url)
        }
    }
    private func checkDownloadSpace() throws {
        // Already persisted ranges occupy real disk space, so reserve only bytes still missing.
        let remaining = reservations.reduce(Int64(0)) { total, entry in
            total + entry.value.reduce(Int64(0)) { subtotal, file in
                let part = root.appendingPathComponent(entry.key).appendingPathComponent(".partial/" + file.sha256)
                return subtotal + max(0, file.size - size(part) - savedRangeBytes(part,total: file.size))
            }
        }
        if remaining > 0 {
            guard try available(root) >= remaining + 16_777_216 else { throw ClientError("mobile.local.storage_required") }
        }
    }
    private func directory(_ model: PhoneModel) -> URL { root.appendingPathComponent(model.id, isDirectory: true) }
    private func partial(_ model: PhoneModel, _ file: PhoneModel.File) -> URL { directory(model).appendingPathComponent(".partial/" + file.sha256) }
    private func size(_ url: URL) -> Int64 { (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.int64Value ?? 0 }
}

private enum HTTPDate {
    static func secondsUntil(_ raw: String) -> Int? {
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return formatter.date(from: raw).map { Int(ceil($0.timeIntervalSinceNow)) }
    }
}
