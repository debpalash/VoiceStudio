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
public actor ModelStore {
    public let root: URL
    private var acceptances: [String: [LicenceAcceptance]] = [:]
    private var active = Set<String>()
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
    private let ram: UInt64
    private let available: @Sendable (URL) throws -> Int64
    public init(root: URL, ram: UInt64 = ProcessInfo.processInfo.physicalMemory,
                bundledRoot: URL? = nil,
                fileHash: @escaping @Sendable (URL) throws -> String = { try ModelHash.file($0) },
                available: @escaping @Sendable (URL) throws -> Int64 = { url in
                    let values = try url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey])
                    if let capacity = values.volumeAvailableCapacityForImportantUsage, capacity > 0 { return capacity }
                    if let capacity = values.volumeAvailableCapacity, capacity > 0 { return Int64(capacity) }
                    return (try FileManager.default.attributesOfFileSystem(forPath: url.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
                }) throws {
        self.bundledRoot = bundledRoot ?? Bundle.main.resourceURL?.appendingPathComponent("Catalogs")
        self.root = root; self.ram = ram; self.available = available; self.fileHash = fileHash
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
        guard model.fits(ram: ram) else { throw ClientError("mobile.local.ram_required", params: ["ram": String(model.min_ram_gb)]) }
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
                         progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws {
        guard model.fits(ram: ram) else { throw ClientError("mobile.local.ram_required", params: ["ram": String(model.min_ram_gb)]) }
        try checkPermission(model)
        guard !active.contains(model.id), leases[model.id, default: 0] == 0 else { throw ClientError("sidebarTools.wait") }
        active.insert(model.id)
        defer { active.remove(model.id); reservations.removeValue(forKey: model.id) }
        let transport = supplied ?? ModelDownloadTransport(policy: policy)
        try FileManager.default.createDirectory(at: directory(model), withIntermediateDirectories: true)
        let identity = try phoneModelPin(model)
        reservations[model.id] = try model.files.filter {
            try !verify(directory(model).appendingPathComponent($0.path), file: $0, identity: identity)
        }
        if reservations[model.id]?.isEmpty == false { try checkDownloadSpace() }
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
            while size(part) < f.size {
                try Task.checkCancellation(); try checkPermission(model)
                let onWifi = await wifi()
                guard policy.allowMobileData || onWifi else { throw ClientError("mobile.local.wifi_required") }
                let offset = size(part)
                try checkDownloadSpace()
                var lastFailure: (any Error)?
                var received = false
                for host in ["huggingface.co", "hf-mirror.com"] {
                    do {
                        let url: URL
                        if let fixed = f.url {
                            guard ModelAssetSource.validURL(fixed),var components = URLComponents(string: fixed) else { throw ClientError("mobile.local.catalogue_failed") }
                            if components.host == "huggingface.co" { components.host = host }
                            guard let selected = components.url else { throw ClientError("mobile.local.catalogue_failed") }; url = selected
                        } else { url = URL(string: "https://\(host)/\(model.repo)/resolve/\(model.revision)/\(f.path)")! }
                        var request = URLRequest(url: url)
                        let end = min(offset + 4_194_303, f.size - 1)
                        request.setValue("bytes=\(offset)-\(end)", forHTTPHeaderField: "Range")
                        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
                        let reply = try await transport.download(request)
                        defer { try? FileManager.default.removeItem(at: reply.file) }
                        try Task.checkCancellation(); try checkPermission(model)
                        let count = size(reply.file)
                        if reply.status == 200 {
                            guard count == f.size else { throw ClientError("mobile.local.download_failed") }
                            if FileManager.default.fileExists(atPath: part.path) { try FileManager.default.removeItem(at: part) }
                            try FileManager.default.moveItem(at: reply.file, to: part)
                        } else {
                            guard reply.status == 206, count == end - offset + 1,
                                  reply.headers["content-range"] == "bytes \(offset)-\(end)/\(f.size)" else { throw ClientError("mobile.local.download_failed") }
                            if !FileManager.default.fileExists(atPath: part.path) { FileManager.default.createFile(atPath: part.path, contents: nil) }
                            let writer = try FileHandle(forWritingTo: part); defer { try? writer.close() }
                            try writer.seekToEnd()
                            let reader = try FileHandle(forReadingFrom: reply.file); defer { try? reader.close() }
                            while let bytes = try reader.read(upToCount: 1_048_576), !bytes.isEmpty { try writer.write(contentsOf: bytes) }
                            try writer.synchronize()
                        }
                        received = true; break
                    } catch is CancellationError { throw CancellationError() }
                    catch { if Task.isCancelled { throw CancellationError() }; lastFailure = error }
                }
                guard received else { throw lastFailure ?? ClientError("mobile.local.download_failed") }
                await progress(Double(completed + size(part)) / Double(model.total_bytes))
            }
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
    private func checkDownloadSpace() throws {
        // Already persisted ranges occupy real disk space, so reserve only bytes still missing.
        let remaining = reservations.reduce(Int64(0)) { total, entry in
            total + entry.value.reduce(Int64(0)) { subtotal, file in
                let part = root.appendingPathComponent(entry.key).appendingPathComponent(".partial/" + file.sha256)
                return subtotal + max(0, file.size - size(part))
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
