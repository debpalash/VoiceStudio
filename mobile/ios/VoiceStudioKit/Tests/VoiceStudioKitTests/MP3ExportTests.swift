import Foundation
import Testing
@preconcurrency import AVFoundation
@testable import VoiceStudioKit

private func mp3Fixture(sampleRate: Double = 24000,channels: AVAudioChannelCount = 1) throws -> (URL,URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("mp3-tests-"+UUID().uuidString)
    try FileManager.default.createDirectory(at: root,withIntermediateDirectories: true)
    let source = root.appendingPathComponent("human.wav")
    let format = try #require(AVAudioFormat(commonFormat: .pcmFormatFloat32,sampleRate: sampleRate,channels: channels,interleaved: false))
    let frames = Int(sampleRate*2)
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format,frameCapacity: AVAudioFrameCount(frames))); buffer.frameLength = AVAudioFrameCount(frames)
    for channelIndex in 0..<Int(channels) {
        let channel = try #require(buffer.floatChannelData?[channelIndex])
        for index in 0..<frames { channel[index] = Float(sin(Double(index)*2*Double.pi*220/sampleRate)*0.3) }
    }
    let writer = try AVAudioFile(forWriting: source,settings: format.settings,commonFormat: .pcmFormatFloat32,interleaved: false)
    try writer.write(from: buffer); return (root,source)
}
@Test func offlineMP3IsPlayableAndPreservesHumanOrSyntheticProvenance() async throws {
    let (root,source) = try mp3Fixture(); defer { try? FileManager.default.removeItem(at: root) }
    for synthetic in [false,true] {
        let file = root.appendingPathComponent(synthetic ? "synthetic.mp3" : "human.mp3")
        let result = try await PhoneAudioExport.mp3(source: source,output: file,synthetic: synthetic)
        #expect(result == file)
        let data = try Data(contentsOf: file)
        #expect(data.starts(with: Data("ID3".utf8)))
        #expect((data.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil) == synthetic)
        let asset = AVURLAsset(url: file)
        #expect(try await asset.loadTracks(withMediaType: .audio).count == 1)
        #expect(abs(try await asset.load(.duration).seconds-2) < 0.15)
        let decoded = try await AVPhoneAudioDecoder().decode(source: file,output: root.appendingPathComponent(UUID().uuidString+".pcm"),maxSeconds: 5,progress: { _ in })
        #expect(try decoded.frames >= 45000)
        #expect(try decoded.read(start: 0,count: 24000).samples.contains { abs($0) > 0.1 })
        // Replacement also goes through the same atomic publication path.
        _ = try await PhoneAudioExport.mp3(source: source,output: file,synthetic: synthetic)
    }
    let exported = try await BookExport.encode(wave: source,format: "mp3",synthetic: false)
    #expect(try Data(contentsOf: exported).range(of: Data("VoiceStudio synthetic audio".utf8)) == nil)
    #expect(try await BookExport.encode(wave: source,format: "wav") == source)
}
@Test func cancellingMP3ExportPreservesPreviousOutputAndRemovesScratch() async throws {
    let (root,source) = try mp3Fixture(); defer { try? FileManager.default.removeItem(at: root) }
    let output = root.appendingPathComponent("result.mp3"), previous = Data("previous complete result".utf8)
    try previous.write(to: output)
    let (events,continuation) = AsyncStream<Void>.makeStream()
    let work = Task {
        try await PhoneAudioExport.mp3(source: source,output: output,progress: { value in
            if value > 0.2 { continuation.yield(()); try? await Task.sleep(for: .seconds(30)) }
        })
    }
    for await _ in events { work.cancel(); break }; continuation.finish()
    await #expect(throws: CancellationError.self) { try await work.value }
    #expect(try Data(contentsOf: output) == previous)
    #expect(try FileManager.default.contentsOfDirectory(at: root,includingPropertiesForKeys: nil).allSatisfy { !$0.lastPathComponent.hasPrefix(".mp3-") })
}

@Test func nativeM4AResamplesMonoAndStereoSpeechFromModelSampleRates() async throws {
    for (rate,channels): (Double,AVAudioChannelCount) in [(22050,1),(44100,2)] {
        let (root,source) = try mp3Fixture(sampleRate: rate,channels: channels)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = try await BookExport.encode(wave: source,format: "m4a",synthetic: false)
        #expect(try Data(contentsOf: file).range(of: Data("VoiceStudio synthetic audio".utf8)) == nil)
        let asset = AVURLAsset(url: file)
        #expect(abs(try await asset.load(.duration).seconds-2) < 0.2)
        let track = try #require(try await asset.loadTracks(withMediaType: .audio).first)
        let description = try #require(try await track.load(.formatDescriptions).first)
        let stream = try #require(CMAudioFormatDescriptionGetStreamBasicDescription(description))
        #expect(stream.pointee.mSampleRate == 24000); #expect(stream.pointee.mChannelsPerFrame == 1)
        let decoded = try await AVPhoneAudioDecoder().decode(source: file,output: root.appendingPathComponent("check.pcm"),maxSeconds: 5,progress: { _ in })
        #expect(try decoded.read(start: 0,count: 24000).samples.contains { abs($0) > 0.1 })
    }
}
