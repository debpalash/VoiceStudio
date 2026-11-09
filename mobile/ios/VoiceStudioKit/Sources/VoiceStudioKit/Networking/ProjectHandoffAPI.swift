import Foundation

public struct HandoffRemote: Codable, Sendable, Identifiable {
    public let id: String
    public let title: String
    public let kind: String
    public let createdAt: Double
    public let incoming: Bool
}
public extension APIClient {
    func handoffInbox() async throws -> [HandoffRemote] { try await decode([HandoffRemote].self,"/project-bundles") }
    func sendHandoff(_ file: URL,allowVoices: Bool) async throws {
        guard file.isFileURL, (try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) <= ProjectArchive.maxBytes+8_388_608 else { throw ClientError("mobile.handoff.too_large") }
        let config = URLSessionConfiguration.ephemeral; config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil; config.timeoutIntervalForResource = 1800
        let session = URLSession(configuration: config,delegate: HostSessionDelegate(),delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let upload = try request("/project-bundles/import?allow_voices="+(allowVoices ? "true" : "false"),method: "POST",contentType: "application/zip")
        let (data,response) = try await session.upload(for: upload,fromFile: file)
        guard let response = response as? HTTPURLResponse else { throw ClientError("mobile.errors.invalid_response") }
        _ = try Self.validate(HTTPReply(data: data,status: response.statusCode),authenticated: credentials.session != nil)
    }
    func receiveHandoff(_ id: String) async throws -> URL {
        guard let uuid = UUID(uuidString: id), uuid.uuidString.lowercased() == id.lowercased() else { throw ClientError("mobile.handoff.invalid") }
        let result = try await download(request("/project-bundles/"+id), maxBytes: ProjectArchive.maxBytes+8_388_608)
        guard (try result.file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) <= ProjectArchive.maxBytes+8_388_608 else { try? FileManager.default.removeItem(at: result.file); throw ClientError("mobile.handoff.too_large") }
        return result.file
    }
}
