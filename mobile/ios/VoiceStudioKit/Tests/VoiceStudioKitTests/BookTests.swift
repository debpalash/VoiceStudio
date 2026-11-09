import Foundation
import Testing
@preconcurrency import AVFoundation
import CoreText
import PDFKit
@testable import VoiceStudioKit

private func bookRoot() throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("book-tests-"+UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true); return root
}
private func crc(_ data: Data) -> UInt32 {
    var value = UInt32.max
    for byte in data { value ^= UInt32(byte); for _ in 0..<8 { value = (value >> 1) ^ (value & 1 == 1 ? 0xedb88320 : 0) } }
    return value ^ UInt32.max
}
private func bookZip(_ entries: [(String,String)]) -> Data {
    var bytes = Data(), central = Data()
    func append<T: FixedWidthInteger>(_ value: T,to data: inout Data) { var little = value.littleEndian; withUnsafeBytes(of: &little) { data.append(contentsOf: $0) } }
    for (name,value) in entries {
        let name = Data(name.utf8), data = Data(value.utf8), offset = bytes.count, checksum = crc(data)
        append(UInt32(0x04034b50),to: &bytes); append(UInt16(20),to: &bytes)
        for _ in 0..<4 { append(UInt16(0),to: &bytes) }
        append(checksum,to: &bytes); append(UInt32(data.count),to: &bytes); append(UInt32(data.count),to: &bytes); append(UInt16(name.count),to: &bytes); append(UInt16(0),to: &bytes)
        bytes.append(name); bytes.append(data)
        append(UInt32(0x02014b50),to: &central); append(UInt16(20),to: &central); append(UInt16(20),to: &central)
        for _ in 0..<4 { append(UInt16(0),to: &central) }
        append(checksum,to: &central); append(UInt32(data.count),to: &central); append(UInt32(data.count),to: &central); append(UInt16(name.count),to: &central)
        for _ in 0..<4 { append(UInt16(0),to: &central) }
        append(UInt32(0),to: &central); append(UInt32(offset),to: &central); central.append(name)
    }
    let start = bytes.count; bytes.append(central); append(UInt32(0x06054b50),to: &bytes)
    append(UInt16(0),to: &bytes); append(UInt16(0),to: &bytes); append(UInt16(entries.count),to: &bytes); append(UInt16(entries.count),to: &bytes)
    append(UInt32(central.count),to: &bytes); append(UInt32(start),to: &bytes); append(UInt16(0),to: &bytes); return bytes
}
private func epubEntries() -> [(String,String)] { [
    ("META-INF/container.xml","<container><rootfiles><rootfile full-path=\"OEBPS/book.opf\"/></rootfiles></container>"),
    ("OEBPS/book.opf","<package><manifest><item id=\"a\" href=\"one.xhtml\"/><item id=\"b\" href=\"two.xhtml\"/></manifest><spine><itemref idref=\"b\"/><itemref idref=\"a\"/></spine></package>"),
    ("OEBPS/one.xhtml","<html><head><title>Hidden title</title></head><body><h1>First</h1><p>First body</p></body></html>"),
    ("OEBPS/two.xhtml","<html><body><h1>Second</h1><p>Second body</p><script>do not speak</script></body></html>")
] }
@Test func epubUsesSpineAndNeverSpeaksScriptsOrExtractsPaths() throws {
    let text = try BookImport.epub(bookZip(epubEntries()))
    #expect(text.hasPrefix("# Second")); #expect(text.contains("# First")); #expect(!text.contains("Hidden title")); #expect(!text.contains("do not speak"))
    #expect(try ProductionPlan(script: text).chapters == ["Second","First"])
    for name in ["../escape","/absolute","OEBPS/../../escape","OEBPS\\escape","C:/escape"] {
        #expect(throws: (any Error).self) { try BookImport.epub(bookZip(epubEntries()+[(name,"unsafe")])) }
    }
    #expect(throws: (any Error).self) { try BookImport.epub(bookZip(epubEntries()+[("OEBPS/./one.xhtml","duplicate")])) }
    var entity = epubEntries(); entity[2].1 = "<!DOCTYPE html [<!ENTITY x SYSTEM \"file:///etc/passwd\">]><html><body>&x;</body></html>"
    #expect(throws: (any Error).self) { try BookImport.epub(bookZip(entity)) }
    var corrupted = bookZip(epubEntries())
    // Corrupt member content, not its local-header filename (the central directory is authoritative).
    let firstContent = 30+epubEntries()[0].0.utf8.count
    corrupted[firstContent+3] ^= 1
    #expect(throws: (any Error).self) { try BookImport.epub(corrupted) }
    #expect(throws: (any Error).self) { try BookImport.epub(Data(bookZip(epubEntries()).dropLast())) }
}
@Test func bookPDFImportExtractsLocalTextAndRejectsImageOnlyPages() async throws {
    let root = try bookRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let pdf = root.appendingPathComponent("text.pdf"); var rect = CGRect(x: 0,y: 0,width: 300,height: 300)
    let context = try #require(CGContext(pdf as CFURL,mediaBox: &rect,nil)); context.beginPDFPage(nil)
    context.textPosition = CGPoint(x: 20,y: 150)
    CTLineDraw(CTLineCreateWithAttributedString(NSAttributedString(string: "A readable local book",attributes: [NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString,12,nil)])),context)
    context.endPDFPage(); context.closePDF()
    #expect(try await BookImport.read(pdf).contains("A readable local book"))
    let empty = root.appendingPathComponent("empty.pdf"); let blank = try #require(CGContext(empty as CFURL,mediaBox: &rect,nil)); blank.beginPDFPage(nil); blank.endPDFPage(); blank.closePDF()
    await #expect(throws: (any Error).self) { try await BookImport.read(empty) }
}
@Test func narrationControlsAreBoundedLiteralAndPitchPreserving() throws {
    let plan = try ProductionPlan(script: "[voice:Alice] [speed:1.5] Dr. Wu [pause 500ms] meets Dr. Watson.\n[pause:1]\nNext",pronunciations: ["Dr.":"Doctor","Wu":"Woo","Doctor":"Wrong"],speed: 0.75)
    #expect(plan.segments[0].text == "Doctor Woo"); #expect(plan.segments[0].speed == 1.5)
    #expect(plan.segments[1].pause == 0.5); #expect(plan.segments[2].text == "meets Doctor Watson.")
    #expect(plan.segments.last?.speed == 0.75); #expect(plan.segments.last?.role == "Alice")
    #expect(throws: (any Error).self) { try ProductionPlan(script: "Hello [pause:31]") }
    #expect(throws: (any Error).self) { try ProductionPlan(script: "[speed:NaN] Hello") }
    #expect(throws: (any Error).self) { try ProductionPlan(script: "Hello",pronunciations: ["":"bad"]) }
    let samples = (0..<24000).map { Float(sin(Double($0)*2*Double.pi*220/24000)*0.5) }
    for rate: Float in [0.5,0.75,1.25,2] {
        let stretched = try NarrationAudio.speed(samples,rate: rate)
        #expect(stretched.count == Int((24000/Double(rate)).rounded())); #expect(stretched.allSatisfy { $0.isFinite })
        let crossings = zip(stretched,stretched.dropFirst()).filter { $0 <= 0 && $1 > 0 }.count
        let frequency = Double(crossings)*24000/Double(stretched.count)
        #expect(abs(frequency-220) < 25)
    }
}
private actor BookEngine {
    var texts: [String] = []
    func render(_ segment: ProductionSegment,_ voice: String) -> LocalAudio { texts.append(segment.text+voice); return LocalAudio(samples: Array(repeating: 0.2,count: 2400),sampleRate: 24000) }
}
@Test func editingOneLineOrPronunciationOnlyRegeneratesAffectedAudio() async throws {
    let root = try bookRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let store = try ProductionStore(root: root), engine = BookEngine()
    var project = ProductionProject(kind: "audiobook",title: "Book",script: "First\nSecond\n# Third\nThird")
    _ = try await store.render(project,configuration: "model1",synthesize: { segment,selection in await engine.render(segment,selection) })
    #expect(await engine.texts.count == 3)
    project.script = "First\nChanged\n# Third\nThird"
    _ = try await store.render(project,configuration: "model1",synthesize: { segment,selection in await engine.render(segment,selection) })
    #expect(await engine.texts.count == 4)
    project.pronunciations = ["Third":"Thurd"]
    _ = try await store.render(project,configuration: "model1",synthesize: { segment,selection in await engine.render(segment,selection) })
    #expect(await engine.texts.count == 5)
    #expect(try await store.chapterCues(project,configuration: "model1").map(\.start) == [0,0.7])
    project.script = "First [pause 1s]\n# Next\nThird"
    _ = try await store.render(project,configuration: "model1",synthesize: { segment,selection in await engine.render(segment,selection) })
    #expect(await engine.texts.count == 5) // Pause synthesizes nothing; the speech is cached.
    #expect(try await store.chapterCues(project,configuration: "model1").map(\.start) == [0,1.1])
}
@Test func nativeAACExportsArePlayableMarkedAndChaptered() async throws {
    let root = try bookRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let wave = root.appendingPathComponent("audio.wav")
    try LocalAudio(samples: (0..<48000).map { Float(sin(Double($0)*0.05)*0.3) },sampleRate: 24000).markSynthetic().write(to: wave)
    for format in ["m4a","m4b"] {
        let file = try await BookExport.encode(wave: wave,format: format,chapters: [BookChapter(title: "One",start: 0),BookChapter(title: "Two",start: 1)])
        let data = try Data(contentsOf: file)
        #expect(data.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
        #expect((data.range(of: Data("chpl".utf8)) != nil) == (format == "m4b"))
        let asset = AVURLAsset(url: file)
        #expect(try await asset.loadTracks(withMediaType: .audio).count == 1)
        #expect(abs(try await asset.load(.duration).seconds-2) < 0.2)
        let locales = try await asset.load(.availableChapterLocales)
        if format == "m4b" {
            let locale = try #require(locales.first)
            let groups = try await asset.loadChapterMetadataGroups(withTitleLocale: locale,containingItemsWithCommonKeys: [.commonKeyTitle])
            #expect(groups.count == 2); #expect(abs(groups[1].timeRange.start.seconds-1) < 0.01)
            #expect(try await groups[0].items.first?.load(.stringValue) == "One")
            #expect(try await groups[1].items.first?.load(.stringValue) == "Two")
        } else { #expect(locales.isEmpty) }
        let decoded = try await AVPhoneAudioDecoder().decode(source: file,output: root.appendingPathComponent(format+".pcm"),maxSeconds: 5,progress: { _ in })
        #expect(try decoded.read(start: 0,count: min(24000,decoded.frames)).samples.contains { abs($0) > 0.01 })
    }
}
