import Foundation
import Testing
@testable import VoiceStudioKit

private func transcriptSource(_ root: URL) throws -> URL {
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let source = root.appendingPathComponent("input.wav")
    try Data([0]).write(to: source)
    return source
}
private struct TranscriptDecoder: PhoneAudioDecoder {
    func decode(source: URL,output: URL,maxSeconds: Double,progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        try PCMFile.write(PCMChunk(samples: Array(repeating: 0.2,count: 504000),sampleRate: 24000),to: output)
        return PCMFile(output)
    }
}
private actor TranscriptRecognizer {
    var calls = 0
    func recognize(_ audio: PCMChunk) throws -> [SpeechSegment] {
        calls += 1
        if calls == 2 { throw CancellationError() }
        return [SpeechSegment(start: 0,end: 0.5,text: calls == 1 ? "First" : "Second")]
    }
}
@Test func transcriptHistoryPersistsSearchEditsAndIdempotentSessions() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneTranscriptStore(root: root),id = UUID().uuidString.lowercased()
    _ = try await store.saveDictation(id: id,title: "Meeting",text: "  Original words  ")
    _ = try await store.saveDictation(id: id,title: "Duplicate",text: "Different words")
    let restored = try PhoneTranscriptStore(root: root)
    #expect(try await restored.list(query: "ORIGINAL").count == 1)
    try await restored.edit(id,text: "Corrected words")
    #expect(try await restored.record(id).originalText == "Original words")
    #expect(try await restored.list(query: "corrected").first?.text == "Corrected words")
    try await restored.edit(id,text: nil)
    #expect(try await restored.record(id).text == "Original words")
    try await restored.delete(id); #expect(try await restored.list().isEmpty)
}
@Test func transcriptFileResumesCompletedChunksAndPreservesAbsoluteTimes() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let model = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == .asr })
    let store = try PhoneTranscriptStore(root: root),recognizer = TranscriptRecognizer()
    let record = try await store.prepare(source: transcriptSource(root),title: "Interview",model: model,decoder: TranscriptDecoder())
    await #expect(throws: CancellationError.self) { try await store.run(record.id,model: model) { try await recognizer.recognize($0) } }
    #expect(try await store.record(record.id).nextFrame == 480000)
    let restored = try PhoneTranscriptStore(root: root)
    let result = try await restored.run(record.id,model: model) { try await recognizer.recognize($0) }
    #expect(result.complete); #expect(result.originalText == "First\nSecond")
    #expect(result.segments[1].start == 20); #expect(await recognizer.calls == 3)
    #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent(record.id+"/source.pcm").path))
    #expect(try PhoneTranscriptExport.subtitles(result,vtt: true).contains("00:00:20.000 --> 00:00:20.500"))
    try await restored.edit(record.id,text: "Edited")
    let edited = try await restored.record(record.id)
    #expect(throws: (any Error).self) { try PhoneTranscriptExport.subtitles(edited,vtt: true) }
}
@Test func transcriptRejectsCorruptSourceAndEscapesSubtitleMarkup() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let model = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == .asr })
    let store = try PhoneTranscriptStore(root: root)
    let record = try await store.prepare(source: transcriptSource(root),title: "Interview",model: model,decoder: TranscriptDecoder())
    try Data("changed".utf8).write(to: root.appendingPathComponent(record.id+"/source.pcm"))
    await #expect(throws: (any Error).self) { try await store.run(record.id,model: model) { _ in Issue.record("Corrupt input reached recognizer");return [] } }
    let cue = PhoneTranscript(title: "Captions",segments: [SpeechSegment(start: 0,end: 1,text: "<b>Hello</b> & goodbye\n\nnext")])
    let text = try PhoneTranscriptExport.subtitles(cue,vtt: false)
    #expect(text.contains("&lt;b&gt;Hello&lt;/b&gt; &amp; goodbye\nnext"))
}

@Test func transcriptTitlesBoundEmojiWithoutSplittingUnicodeOrLosingWords() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try PhoneTranscriptStore(root: root)
    let title = String(repeating: "👨‍👩‍👧‍👦",count: 120)
    let record = try await store.saveDictation(id: UUID().uuidString.lowercased(),title: title,text: "Spoken words")
    #expect(record.valid); #expect(!record.title.isEmpty); #expect(record.title.utf16.count <= 300)
    #expect(!record.title.contains("�")); #expect(record.originalText == "Spoken words")
    let fallback = try await store.saveDictation(id: UUID().uuidString.lowercased(),title: " \n ",text: "\nFirst words")
    #expect(fallback.title == "First words")
    let model = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == .asr })
    let file = try await store.prepare(source: transcriptSource(root),title: title,model: model,decoder: TranscriptDecoder())
    #expect(file.valid); #expect(file.title.utf16.count <= 300)
}

@Test func transcriptRejectsOversizedSourceBeforeDecoder() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = try transcriptSource(root)
    let writer = try FileHandle(forWritingTo: source)
    try writer.truncate(atOffset: UInt64(PhoneTranscriptStore.maxSourceBytes)+1)
    try writer.close()
    let model = try #require(ModelCatalogue.decode(Data(contentsOf: repository.appendingPathComponent("mobile/contract/models.json"))).models.first { $0.capability == .asr })
    let store = try PhoneTranscriptStore(root: root.appendingPathComponent("history"))
    await #expect(throws: ClientError.self) { try await store.prepare(source: source,title: "Too large",model: model,decoder: TranscriptDecoder()) }
    #expect(try await store.list().isEmpty)
}
