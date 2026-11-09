import Foundation
@preconcurrency import AVFoundation
import VoiceStudioKit

@MainActor final class DictationAudio {
    private var engine: AVAudioEngine?
    private var continuation: AsyncThrowingStream<Data, any Error>.Continuation?
    func start() async throws -> AsyncThrowingStream<Data, any Error> {
        guard await AVAudioApplication.requestRecordPermission() else { throw ClientError("mobile.permissions.denied") }
        try Task.checkCancellation()
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement); try session.setActive(true)
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, let output = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: format, to: output) else { throw ClientError("mobile.errors.audio") }
        let box = PCMConverter(converter: converter, output: output)
        let (stream, continuation) = AsyncThrowingStream<Data, any Error>.makeStream(bufferingPolicy: .bufferingOldest(64))
        self.continuation = continuation
        input.installTap(onBus: 0, bufferSize: 2048, format: format) { buffer, _ in
            do {
                let bytes = try box.convert(buffer)
                if case .dropped = continuation.yield(bytes) { continuation.finish(throwing: ClientError("mobile.errors.audio")) }
            } catch { continuation.finish(throwing: ClientError("mobile.errors.audio")) }
        }
        engine.prepare()
        do { try engine.start(); self.engine = engine }
        catch { input.removeTap(onBus: 0); continuation.finish(); throw ClientError("mobile.errors.audio") }
        return stream
    }
    func stop() {
        engine?.inputNode.removeTap(onBus: 0); engine?.stop(); engine = nil
        continuation?.finish(); continuation = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
/// The converter is confined to AVAudioEngine's serial audio callback.
final class PCMConverter: @unchecked Sendable {
    let converter: AVAudioConverter
    let output: AVAudioFormat
    init(converter: AVAudioConverter, output: AVAudioFormat) { self.converter = converter; self.output = output; converter.downmix = true }
    func convert(_ input: AVAudioPCMBuffer) throws -> Data {
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * 16000 / input.format.sampleRate)) + 32
        guard let result = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: capacity) else { throw ClientError("mobile.errors.audio") }
        let source = ConversionInput(input)
        var error: NSError?
        let status = converter.convert(to: result, error: &error) { _, flag in
            source.next(flag)
        }
        guard status != .error, error == nil, let samples = result.int16ChannelData?[0] else { throw ClientError("mobile.errors.audio") }
        return Data(bytes: samples, count: Int(result.frameLength) * MemoryLayout<Int16>.size)
    }
}

/// AVAudioConverter invokes this input provider serially during convert().
private final class ConversionInput: @unchecked Sendable {
    let buffer: AVAudioPCMBuffer
    private var supplied = false
    init(_ buffer: AVAudioPCMBuffer) { self.buffer = buffer }
    func next(_ flag: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioPCMBuffer? {
        if supplied { flag.pointee = .noDataNow; return nil }
        supplied = true; flag.pointee = .haveData; return buffer
    }
}
