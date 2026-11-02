import Foundation
import CryptoKit
import Testing
@testable import VoiceStudioKit

private actor GalleryTransport: HTTPTransport {
    var replies: [HTTPReply]
    var requests = 0
    init(_ replies: [HTTPReply]) { self.replies = replies }
    func send(_ request: URLRequest) throws -> HTTPReply {
        #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
        requests += 1
        guard !replies.isEmpty else { throw ClientError("mobile.errors.invalid_response") }
        return replies.removeFirst()
    }
}
private func gallerySeed() throws -> Data { try Data(contentsOf: repository.appendingPathComponent("mobile/contract/community-gallery.json")) }

@Test func communityGalleryHasRealNamedVoicesWithoutNetworkAndCachesRefresh() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let seed = try gallerySeed(), transport = GalleryTransport([HTTPReply(data: seed)])
    let client = try CommunityGallery(root: root,transport: transport)
    let bundled = try await client.cached(seed: seed)
    #expect(bundled.items.count == 50)
    #expect(bundled.items.contains { $0.name == "The Librarian" && $0.instruct != nil })
    #expect(bundled.items.contains { $0.name == "Thalia" && $0.audio?.ref_text.isEmpty == false })
    #expect(await transport.requests == 0)
    _ = try await client.refresh()
    #expect(await transport.requests == 1)
    let offline = try CommunityGallery(root: root,transport: GalleryTransport([]))
    #expect(try await offline.cached(seed: Data()).items.count == 50)
    await #expect(throws: (any Error).self) { try await offline.refresh() }
    #expect(try await offline.cached(seed: Data()).items.count == 50)
}
@Test func communityGalleryRejectsUnsafeManifestAndRedirectBeforeSending() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let seed = try gallerySeed()
    let unsafe = Data(String(decoding: seed,as: UTF8.self).replacingOccurrences(of: "https://github.com/debpalash/omnivoice-gallery/releases",with: "http://127.0.0.1/releases").utf8)
    #expect(throws: (any Error).self) { try CommunityCatalogue.decode(unsafe) }
    let transport = GalleryTransport([HTTPReply(data: Data(),status: 302,headers: ["Location":"https://untrusted.example/voice"] )])
    let client = try CommunityGallery(root: root,transport: transport)
    await #expect(throws: (any Error).self) { try await client.refresh() }
    #expect(await transport.requests == 1)
}
@Test func communityRecordingChecksHashBeforeCachingAndPermissionBeforeImport() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let wave = try LocalAudio(samples: Array(repeating: Float(0.2),count: 4800),sampleRate: 24000).markSynthetic()
    let hash = SHA256.hash(data: wave).map { String(format: "%02x",$0) }.joined()
    let item = GalleryEntry(id: "test",type: "voice",name: "Test",language: "English",author: "creator",license: "CC-BY-4.0",instruct: nil,sample_script: nil,audio: GalleryRecording(url: "https://github.com/debpalash/omnivoice-gallery/releases/download/voices-v1/test.wav",sha256: hash,ref_text: "Actual reference words"))
    let transport = GalleryTransport([HTTPReply(data: Data("bad".utf8)),HTTPReply(data: wave),HTTPReply(data: wave)])
    let client = try CommunityGallery(root: root,transport: transport)
    await #expect(throws: (any Error).self) { try await client.recording(item) }
    #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent(hash+".wav").path))
    let file = try await client.recording(item)
    _ = try await client.recording(item)
    #expect(await transport.requests == 2)
    try Data("tampered".utf8).write(to: file)
    _ = try await client.recording(item)
    #expect(await transport.requests == 3)
    let store = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    await #expect(throws: (any Error).self) { try await store.importCommunity(item,source: file,consent: false) }
    #expect(try await store.list().isEmpty)
    let voice = try await store.importCommunity(item,source: file,consent: true)
    #expect(voice.transcript == "Actual reference words")
    #expect(voice.sourceAttribution?["license"] == "CC-BY-4.0")
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(await store.preview(voice)))
}
