import Foundation

public extension APIClient {
    /// Called only after the user confirms copying an already imported computer voice.
    func downloadPhoneVoiceBundle(_ profileID: String) async throws -> URL {
        let result = try await download(request("/marketplace/export/\(try Self.component(profileID))?stored=true",method: "POST"), maxBytes: 64*1024*1024)
        let bytes = try result.file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard bytes > 0,bytes <= 64*1024*1024 else { try? FileManager.default.removeItem(at: result.file); throw ClientError("mobile.galleryCopy.unavailable") }
        return result.file
    }
}
