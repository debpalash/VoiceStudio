import Foundation
import Testing
@testable import VoiceStudioKit

@Test func phoneSubtitleSRTAndVTTRoundTripTimingAndMultilineText() throws {
    let input = "\u{FEFF}1\r\n00:00:01,125 --> 00:00:02,900\r\n<b>Hello</b> &amp; goodbye\r\nSecond line\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nNext line\r\n"
    let cues = try PhoneDubSubtitles.parse(input)
    #expect(cues[0].start == 1.125); #expect(cues[0].text == "Hello & goodbye\nSecond line")
    let segments = cues.map { PhoneDubSegment(speech: SpeechSegment(start: $0.start,end: $0.end,text: "Source"),translations: ["fr":$0.text]) }
    for vtt in [false,true] { #expect(try PhoneDubSubtitles.parse(PhoneDubSubtitles.export(segments,target: "fr",vtt: vtt)) == cues) }
    #expect(try PhoneDubSubtitles.parse("WEBVTT\n\nNOTE comment\nignore me\n\ncue-id\n00:01.125 --> 00:02.900 align:start\nHello &amp; goodbye\nSecond line\n") == Array(cues.prefix(1)))
}
@Test func phoneSubtitleRejectsMalformedAndUnboundedCues() throws {
    for input in ["1\n00:00:02,000 --> 00:00:01,000\nBad","1\n00:00:00,000 --> 00:00:31,000\nToo long","1\n00:99:00,000 --> 01:00:00,000\nBad","WEBVTT\n\nnot a cue"] {
        #expect(throws: (any Error).self) { try PhoneDubSubtitles.parse(input) }
    }
}
@Test func phoneDubStructuralEditsKeepCustomVoicesAndRejectConflictingReferences() throws {
    let segments = [PhoneDubSegment(speech: SpeechSegment(start: 0,end: 2,text: "First source sentence"),referenceText: "Exact reference",translations: ["fr":"First translated sentence"],referenceFile: "custom.pcm"),PhoneDubSegment(speech: SpeechSegment(start: 2,end: 4,text: "Second source sentence"),referenceText: "Exact reference",translations: ["fr":"Second translated sentence"],referenceFile: "custom.pcm")]
    let split = try PhoneDubEditing.split(segments,index: 0,at: 1)
    #expect(split.count == 3); #expect(split[0].id == segments[0].id); #expect(split[1].id != split[0].id)
    #expect(split[1].referenceFile == "custom.pcm"); #expect(split[1].referenceText == "Exact reference")
    let merged = try PhoneDubEditing.merge(split,index: 0)
    #expect(merged.count == 2); #expect(merged[0].speech.start == 0); #expect(merged[0].speech.end == 2)
    var conflicting = segments; conflicting[1].referenceFile = "other.pcm"
    #expect(throws: (any Error).self) { try PhoneDubEditing.merge(conflicting,index: 0) }
    let imported = try PhoneDubEditing.importTranslation(segments,cues: [PhoneDubSubtitleCue(start: 0,end: 2,text: "New translation")],target: "fr",duration: 4)
    #expect(imported[0].id == segments[0].id); #expect(imported[0].referenceFile == "custom.pcm")
    #expect(throws: (any Error).self) { try PhoneDubEditing.importTranslation(conflicting,cues: [PhoneDubSubtitleCue(start: 1,end: 3,text: "New translation")],target: "fr",duration: 4) }
    #expect(throws: (any Error).self) { try PhoneDubEditing.importTranslation(segments,cues: [PhoneDubSubtitleCue(start: 5,end: 6,text: "No source")],target: "fr",duration: 8) }
}

@Test func phoneSubtitleEscapesLiteralMarkupAndCachePruningProtectsPublishedRenders() throws {
    let cue = PhoneDubSegment(speech: SpeechSegment(start: 0,end: 1,text: "Source"),translations: ["fr":"Say <hello> & goodbye and &lt;literal&gt; &amp;"])
    for vtt in [false,true] { #expect(try PhoneDubSubtitles.parse(PhoneDubSubtitles.export([cue],target: "fr",vtt: vtt))[0].text == "Say <hello> & goodbye and &lt;literal&gt; &amp;") }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("dub-cache-"+UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("original.pcm"); try Data(repeating: 0,count: 40).write(to: source)
    let keep = root.appendingPathComponent("cache-"+String(repeating: "a",count: 64)+".pcm"); try Data(repeating: 0,count: 40).write(to: keep)
    let old = root.appendingPathComponent("cache-"+String(repeating: "b",count: 64)+".pcm"); try Data(repeating: 0,count: 8).write(to: old)
    let latest = root.appendingPathComponent("cache-"+String(repeating: "c",count: 64)+".pcm"); try Data(repeating: 0,count: 8).write(to: latest)
    try FileManager.default.setAttributes([.modificationDate:Date(timeIntervalSince1970: 1000)],ofItemAtPath: old.path)
    try FileManager.default.setAttributes([.modificationDate:Date(timeIntervalSince1970: 2000)],ofItemAtPath: latest.path)
    let metadata = old.deletingPathExtension().appendingPathExtension("json"); try Data("metadata".utf8).write(to: metadata)
    try prunePhoneDubCaches(directory: root,referenced: [keep.lastPathComponent],maxBytes: 8,maxFiles: 1)
    #expect(FileManager.default.fileExists(atPath: source.path)); #expect(FileManager.default.fileExists(atPath: keep.path)); #expect(FileManager.default.fileExists(atPath: latest.path))
    #expect(!FileManager.default.fileExists(atPath: old.path)); #expect(!FileManager.default.fileExists(atPath: metadata.path))
}

@Test func phonePCMFrameCountRefreshesAfterAtomicReplacement() throws {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("pcm-size-"+UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: url) }
    let pcm = PCMFile(url)
    try PCMFile.write(PCMChunk(samples: [0.1,0.2],sampleRate: 24000),to: url)
    #expect(try pcm.frames == 2)
    try PCMFile.write(PCMChunk(samples: [0.1,0.2,0.3,0.4],sampleRate: 24000),to: url)
    #expect(try pcm.frames == 4)
    try Data([1]).write(to: url,options: .atomic)
    #expect(throws: (any Error).self) { try pcm.frames }
}
