#if os(iOS) && canImport(VoiceStudio)
import AVFoundation
import Foundation
import Testing
@testable import VoiceStudio

@Test @MainActor func phoneDubPlaybackRestoresPlaybackAfterDictation() throws {
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.record, mode: .measurement)
    defer { try? session.setActive(false) }
    let player = try PhoneDubPlayback.startPlayback(url: URL(fileURLWithPath: "/tmp/dub.mp4"))
    defer { player.pause() }
    #expect(session.category == .playback)
    #expect(session.mode == .default)
    #expect(player.currentItem != nil)
}

@Test @MainActor func phoneDictationConverterKeepsRightStereoChannel() throws {
    let input = try #require(AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 2, interleaved: false))
    let output = try #require(AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true))
    let converter = try #require(AVAudioConverter(from: input, to: output))
    let pcm = PCMConverter(converter: converter, output: output)
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: input, frameCapacity: 4800))
    buffer.frameLength = 4800
    let channels = try #require(buffer.floatChannelData)
    for frame in 0..<4800 {
        let phase = Double(frame) * 2.0 * Double.pi * 440.0 / input.sampleRate
        channels[0][frame] = 0
        channels[1][frame] = Float(sin(phase) * 0.4)
    }
    let audio = try pcm.convert(buffer)
    #expect(!audio.isEmpty)
    #expect(audio.contains { $0 != 0 })
}
#endif
