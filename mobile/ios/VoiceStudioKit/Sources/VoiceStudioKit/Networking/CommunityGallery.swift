import Foundation
import CryptoKit

/// Public community catalogue. Created on Gallery entry; never requests anything during app startup.
public struct GalleryRecording: Codable, Sendable {
    public let url: String
    public let sha256: String
    public let ref_text: String
}
public struct GalleryEntry: Codable, Identifiable, Sendable {
    public let id: String
    public let type: String
    public let name: String
    public let language: String
    public let author: String?
    public let license: String?
    public let instruct: String?
    public let sample_script: String?
    public let audio: GalleryRecording?
    public var attribution: [String:String] {
        var result = ["source":"community","id":id,"name":name,"language":language,"url":"https://github.com/debpalash/omnivoice-gallery"]
        result["author"] = author; result["license"] = license
        return result
    }
}
public struct GalleryPack: Codable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let item_ids: [String]
}
public struct CommunityCatalogue: Codable, Sendable {
    public let schema_version: Int
    public let packs: [GalleryPack]
    public let items: [GalleryEntry]
    public static func decode(_ data: Data) throws -> Self {
        guard data.count <= 4*1024*1024 else { throw ClientError("mobile.errors.invalid_response") }
        let value = try JSONDecoder().decode(Self.self,from: data)
        guard value.schema_version == 1, value.items.count <= 2000, Set(value.items.map(\.id)).count == value.items.count else { throw ClientError("mobile.errors.invalid_response") }
        for item in value.items {
            guard item.id.range(of: "^[A-Za-z0-9_-]{1,128}$",options: .regularExpression) != nil,
                  !item.name.isEmpty, item.name.utf16.count <= 200, item.language.count <= 100,
                  (item.author?.count ?? 0) <= 200, (item.license?.count ?? 0) <= 200 else { throw ClientError("mobile.errors.invalid_response") }
            if item.type == "preset" {
                guard let instruction = item.instruct, !instruction.isEmpty, instruction.count <= 1000,
                      let script = item.sample_script, !script.isEmpty, script.utf16.count <= 2000 else { throw ClientError("mobile.errors.invalid_response") }
            } else {
                guard item.type == "voice", let audio = item.audio, let url = URL(string: audio.url), CommunityGallery.allowed(url),
                      audio.sha256.range(of: "^[a-f0-9]{64}$",options: .regularExpression) != nil,
                      !audio.ref_text.isEmpty, audio.ref_text.utf16.count <= 4000 else { throw ClientError("mobile.errors.invalid_response") }
            }
        }
        return value
    }
}
public actor CommunityGallery {
    public static let manifestURL = URL(string: "https://cdn.jsdelivr.net/gh/debpalash/omnivoice-gallery@main/manifest.json")!
    private let root: URL
    private let transport: any HTTPTransport
    public init(root: URL,transport: any HTTPTransport = URLSessionTransport()) throws {
        self.root = root; self.transport = transport; try PrivateFiles.directory(root)
    }
    public func cached(seed: Data) throws -> CommunityCatalogue {
        let file = root.appendingPathComponent("manifest.json")
        if let data = try? Data(contentsOf: file), let value = try? CommunityCatalogue.decode(data) { return value }
        return try CommunityCatalogue.decode(seed)
    }
    public func refresh() async throws -> CommunityCatalogue {
        let data = try await fetch(Self.manifestURL,limit: 4*1024*1024)
        let value = try CommunityCatalogue.decode(data)
        try data.write(to: root.appendingPathComponent("manifest.json"),options: .atomic)
        return value
    }
    public func recording(_ entry: GalleryEntry) async throws -> URL {
        guard entry.type == "voice", let audio = entry.audio, let url = URL(string: audio.url), Self.allowed(url),
              audio.sha256.range(of: "^[a-f0-9]{64}$",options: .regularExpression) != nil else { throw ClientError("mobile.errors.invalid_response") }
        let file = root.appendingPathComponent(audio.sha256+".wav")
        if let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 32*1024*1024,
           (try? ModelHash.file(file)) == audio.sha256 { return file }
        try? FileManager.default.removeItem(at: file)
        let data = try await fetch(url,limit: 32*1024*1024)
        guard SHA256.hash(data: data).map { String(format: "%02x",$0) }.joined() == audio.sha256 else { throw ClientError("mobile.errors.invalid_response") }
        try data.write(to: file,options: .atomic); return file
    }
    internal static func allowed(_ url: URL) -> Bool {
        url.scheme == "https" && url.user == nil && url.password == nil && (url.port == nil || url.port == 443) &&
        ["cdn.jsdelivr.net","github.com","raw.githubusercontent.com","objects.githubusercontent.com","release-assets.githubusercontent.com"].contains(url.host ?? "")
    }
    private func fetch(_ initial: URL,limit: Int) async throws -> Data {
        var url = initial
        for _ in 0..<6 {
            guard Self.allowed(url) else { throw ClientError("mobile.errors.invalid_response") }
            var request = URLRequest(url: url); request.timeoutInterval = 30
            let reply = try await transport.download(request,maxBytes: limit)
            defer { try? FileManager.default.removeItem(at: reply.file) }
            if [301,302,303,307,308].contains(reply.status), let location = reply.headers["location"], let next = URL(string: location,relativeTo: url)?.absoluteURL {
                url = next; continue
            }
            guard reply.status == 200 else { throw ClientError("mobile.errors.invalid_response") }
            return try Data(contentsOf: reply.file)
        }
        throw ClientError("mobile.errors.invalid_response")
    }
}
