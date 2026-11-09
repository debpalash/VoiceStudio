import Foundation
import Testing
@testable import VoiceStudioKit

private struct BundleDecoder: PhoneAudioDecoder {
    func decode(source: URL,output: URL,maxSeconds: Double,progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        #expect(maxSeconds == 30)
        try PCMFile.write(PCMChunk(samples: Array(repeating: 0.2,count: 4800),sampleRate: 24000),to: output)
        return PCMFile(output)
    }
}
private func voiceBundle(_ root: URL,text: String = "Reference words",extra: Bool = false,designed: Bool = false) async throws -> URL {
    let metadata = root.appendingPathComponent("metadata.json"),reference = root.appendingPathComponent("ref_audio.wav")
    try JSONEncoder().encode(["bundle_version":JSONValue.number(1),"profile_name":.string("Gallery voice"),"ref_text":.string(text),"kind":.string(designed ? "design" : "clone")]).write(to: metadata)
    if designed { try Data("Unmarked decoded fixture".utf8).write(to: reference) } else { try LocalAudio(samples: Array(repeating: 0.2,count: 4800),sampleRate: 24000).markSynthetic().write(to: reference) }
    var files = [ProjectArchiveFile(path: "metadata.json",file: metadata),ProjectArchiveFile(path: "ref_audio.wav",file: reference)]
    if extra { files.append(.init(path: "unexpected.wav",file: reference)) }
    return try await ProjectArchive.write(entries: files,to: root.appendingPathComponent(UUID().uuidString+".omnivoice"))
}
@Test func galleryBundleInstallsFreshLocalVoiceWithVisibleLicenseAndNoInference() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await voiceBundle(root),store = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let attribution = ["source":"community","id":"foreign-id","author":"Original creator","license":"CC BY 4.0"]
    let first = try await PhoneVoiceBundle.install(archive,store: store,attribution: attribution,consent: true,decoder: BundleDecoder())
    let second = try await PhoneVoiceBundle.install(archive,store: store,attribution: attribution,consent: true,decoder: BundleDecoder())
    #expect(first.id != second.id); #expect(first.transcript == "Reference words"); #expect(first.sourceAttribution == attribution)
    #expect(try await store.list().count == 2); #expect(try await store.list().first?.sourceAttribution?["license"] == "CC BY 4.0")
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(await store.preview(first)))
}
@Test func galleryBundleRejectsMissingPermissionTranscriptAndUnexpectedAssets() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneVoiceStore(root: root.appendingPathComponent("voices")),archive = try await voiceBundle(root)
    await #expect(throws: ClientError.self) { try await PhoneVoiceBundle.install(archive,store: store,attribution: [:],consent: false,decoder: BundleDecoder()) }
    for bad in [try await voiceBundle(root,text: " "),try await voiceBundle(root,extra: true)] {
        await #expect(throws: ClientError.self) { try await PhoneVoiceBundle.install(bad,store: store,attribution: [:],consent: true,decoder: BundleDecoder()) }
    }
    #expect(try await store.list().isEmpty)
}

@Test func galleryBundleDesignedReferenceKeepsSyntheticProvenanceWithoutHostMarker() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneVoiceStore(root: root.appendingPathComponent("voices"))
    let archive = try await voiceBundle(root,designed: true)
    let voice = try await PhoneVoiceBundle.install(archive,store: store,attribution: [:],consent: true,decoder: BundleDecoder())
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(await store.preview(voice)))
}
