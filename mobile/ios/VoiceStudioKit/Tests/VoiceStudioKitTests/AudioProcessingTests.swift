import Foundation
import Testing
@testable import VoiceStudioKit

private func processingRoot() throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("audio-processing-"+UUID().uuidString)
    try PrivateFiles.directory(root); return root
}
private func processingDocument(_ root: URL,_ samples: [Float],synthetic: Bool = false) throws -> PhoneAudioDocument {
    let pcm = PCMFile(root.appendingPathComponent("source.pcm")); try PCMFile.write(PCMChunk(samples: samples,sampleRate: 24000),to: pcm.url)
    return PhoneAudioDocument(pcm: pcm,frames: samples.count,synthetic: synthetic)
}
private func waveSamples(_ file: URL) throws -> [Float] {
    let data = try Data(contentsOf: file); var offset = 12
    while offset+8 <= data.count {
        let count = data.withUnsafeBytes { Int(UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: offset+4,as: UInt32.self))) }
        guard count <= data.count-offset-8 else { throw ClientError("mobile.errors.audio") }
        if String(decoding: data[offset..<offset+4],as: UTF8.self) == "data" {
            return stride(from: offset+8,to: offset+8+count,by: 2).map { index in data.withUnsafeBytes { Float(Int16(littleEndian: $0.loadUnaligned(fromByteOffset: index,as: Int16.self)))/32767 } }
        }
        offset += 8+count+count%2
    }
    throw ClientError("mobile.errors.audio")
}
@Test func phoneAudioTrimFadesAndGainProcessRealSamplesWithoutOverwritingSource() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let document = try processingDocument(root,Array(repeating: 0.2,count: 96000)), sourceHash = try ModelHash.file(document.pcm.url)
    var options = PhoneAudioEditOptions(); options.start = 0.5; options.end = 1.5; options.fadeIn = 0.1; options.fadeOut = 0.1; options.gainDB = 20*log10(2)
    let result = try await PhoneAudioProcessing.process(document,options: options,workspace: root)
    let samples = try waveSamples(result.file)
    #expect(result.duration == 1); #expect(samples.count == 24000); #expect(samples.first == 0); #expect(samples.last == 0)
    #expect(abs(samples[6000]-0.4) < 0.00004); #expect(abs(samples[8191]-samples[8192]) < 0.00004)
    #expect(try ModelHash.file(document.pcm.url) == sourceHash); #expect(!result.synthetic); #expect(try !PhoneAudioProcessing.containsSyntheticMarker(result.file))
}
@Test func phoneAudioNormalizationMeasuresTheActualPeakAndKeepsSilenceQuiet() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    var samples = Array(repeating: Float(0.1),count: 24000); samples[19000] = -0.5
    let document = try processingDocument(root,samples)
    var options = PhoneAudioEditOptions(); options.gainDB = 12; options.normalize = true; options.targetDB = -2
    let result = try await PhoneAudioProcessing.process(document,options: options,workspace: root), actual = try waveSamples(result.file)
    #expect(abs(Double(actual.map(abs).max()!)-pow(10,-2.0/20)) < 0.00004)
    #expect(abs(Double(actual[100])-pow(10,-2.0/20)*0.2) < 0.00004)
    let quiet = try processingDocument(root,Array(repeating: 0.0001,count: 24000)); options.gainDB = 0
    let quietResult = try await PhoneAudioProcessing.process(quiet,options: options,workspace: root)
    #expect(try waveSamples(quietResult.file).allSatisfy { abs($0) <= 0.00012 })
}
@Test func phoneAudioNoiseGateHasReleaseAndDoesNotAttenuateSustainedSpeech() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let samples = Array(repeating: Float(0.25),count: 24000)+Array(repeating: Float(0.001),count: 72000)
    let document = try processingDocument(root,samples)
    var options = PhoneAudioEditOptions(); options.noiseGate = true
    let result = try await PhoneAudioProcessing.process(document,options: options,workspace: root), actual = try waveSamples(result.file)
    #expect(abs(actual[12000]-0.25) < 0.00004); #expect(actual[24500] > 0.0009); #expect(actual[90000] == 0)
}
@Test func phoneAudioProcessingPreservesSyntheticProvenanceAndRejectsInvalidEdits() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let document = try processingDocument(root,Array(repeating: 0.2,count: 24000),synthetic: true)
    let result = try await PhoneAudioProcessing.process(document,options: PhoneAudioEditOptions(),workspace: root)
    #expect(result.synthetic); #expect(try PhoneAudioProcessing.containsSyntheticMarker(result.file))
    var invalid = PhoneAudioEditOptions(); invalid.end = 2
    await #expect(throws: (any Error).self) { try await PhoneAudioProcessing.process(document,options: invalid,workspace: root) }
    #expect(try document.pcm.frames == 24000)
    let split = root.appendingPathComponent("split-marker"); var bytes = Data(repeating: 0,count: 65530); bytes.append(Data("VoiceStudio synthetic audio".utf8)); try bytes.write(to: split)
    #expect(try PhoneAudioProcessing.containsSyntheticMarker(split))
    let natural = root.appendingPathComponent("natural.wav"); try PhoneAudioProcessing.writeNaturalWave(document.pcm,to: natural)
    #expect(try !PhoneAudioProcessing.containsSyntheticMarker(natural))
    let opened = try await PhoneAudioProcessing.open(source: result.file,workspace: root)
    #expect(opened.synthetic); #expect(opened.peaks.count == 256); #expect(opened.frames == 24000)
    let human = try await PhoneAudioProcessing.open(source: natural,workspace: root)
    #expect(!human.synthetic)
}
@Test func phoneAudioProcessingCancellationKeepsItsSource() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    let document = try processingDocument(root,Array(repeating: 0.2,count: 240000))
    let hash = try ModelHash.file(document.pcm.url)
    let task = Task { try await PhoneAudioProcessing.process(document,options: PhoneAudioEditOptions(),workspace: root) }; task.cancel()
    await #expect(throws: CancellationError.self) { try await task.value }
    #expect(try ModelHash.file(document.pcm.url) == hash)
}

@Test func phoneAudioFullLengthNormalizationIncludesTheLastFrame() async throws {
    let root = try processingRoot(); defer { try? FileManager.default.removeItem(at: root) }
    var samples = Array(repeating: Float(0),count: 3003); samples[3002] = 0.5
    let document = try processingDocument(root,samples)
    for explicitEnd in [false,true] {
        var options = PhoneAudioEditOptions(); options.normalize = true
        if explicitEnd { options.end = document.duration }
        let result = try await PhoneAudioProcessing.process(document,options: options,workspace: root), actual = try waveSamples(result.file)
        #expect(actual.count == 3003); #expect(abs(Double(actual.last!)-pow(10,-2.0/20)) < 0.00004)
    }
}
