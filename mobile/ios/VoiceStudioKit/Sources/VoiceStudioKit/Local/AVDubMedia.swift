import Foundation
@preconcurrency import AVFoundation

private final class AudioReadFailure: @unchecked Sendable {
    private let lock = NSLock()
    private var failure: (any Error)?
    var value: (any Error)? { lock.withLock { failure } }
    func set(_ error: any Error) { lock.withLock { failure = error } }
}

/// AVAssetReader extracts directly to 24-kHz mono float PCM in bounded buffers.
public struct AVPhoneAudioDecoder: PhoneAudioDecoder {
    public init() {}
    public func decode(source: URL, output: URL, maxSeconds: Double, progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        guard source.isFileURL, output.isFileURL else { throw ClientError("mobile.simple.media_failed") }
        let work = Task.detached(priority: .userInitiated) {
            let access = source.startAccessingSecurityScopedResource(); defer { if access { source.stopAccessingSecurityScopedResource() } }
            if ["wav", "caf", "aiff", "aif"].contains(source.pathExtension.lowercased()) {
                return try await Self.decodeAudioFile(source: source,output: output,maxSeconds: maxSeconds,progress: progress)
            }
            let asset = AVURLAsset(url: source,options: [AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue])
            let duration = try await asset.load(.duration).seconds
            guard duration.isFinite, duration >= 0.1, duration <= maxSeconds+0.01,
                  let track = try await asset.loadTracks(withMediaType: .audio).first else { throw ClientError("mobile.simple.audio_long") }
            let reader = try AVAssetReader(asset: asset)
            let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 24000, AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false]
            let trackOutput = AVAssetReaderAudioMixOutput(audioTracks: [track], audioSettings: settings); trackOutput.alwaysCopiesSampleData = false
            guard reader.canAdd(trackOutput) else { throw ClientError("mobile.simple.media_failed") }; reader.add(trackOutput)
            let handle = try FileHandle(forWritingTo: PrivateFiles.empty(output)); defer { try? handle.close(); reader.cancelReading() }
            guard reader.startReading() else { throw ClientError("mobile.simple.media_failed") }
            var count = 0
            let totalFrames = min(Int(maxSeconds*24000),Int((duration*24000).rounded()))
            func pad(_ frames: Int) throws {
                var remaining = frames
                while remaining > 0 { try Task.checkCancellation(); let size = min(8192,remaining); try handle.write(contentsOf: Data(count: size*4)); remaining -= size }
            }
            while let buffer = trackOutput.copyNextSampleBuffer() {
                try Task.checkCancellation()
                guard let block = CMSampleBufferGetDataBuffer(buffer) else { throw ClientError("mobile.simple.media_failed") }
                var bytes = Data(count: CMBlockBufferGetDataLength(block))
                let rc = bytes.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block,atOffset: 0,dataLength: $0.count,destination: $0.baseAddress!) }
                guard rc == kCMBlockBufferNoErr, bytes.count.isMultiple(of: 4) else { throw ClientError("mobile.simple.media_failed") }
                let timestamp = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
                guard timestamp.isFinite else { throw ClientError("mobile.simple.media_failed") }
                var position = Int((timestamp*24000).rounded())
                if position >= totalFrames { continue }
                position = count+timelineShift(timestamp: timestamp,written: count)
                if position > count { try pad(position-count); count = position }
                let skip = max(0,count-position)
                let frames = min(bytes.count/4-skip,totalFrames-count)
                if frames > 0 {
                    try handle.write(contentsOf: bytes.subdata(in: (skip*4)..<((skip+frames)*4))); count += frames
                }
                await progress(min(1,Double(count)/Double(totalFrames)))
            }
            guard reader.status == .completed, count >= 2400 else { throw ClientError("mobile.simple.media_failed") }
            if count < totalFrames { try pad(totalFrames-count) }
            try handle.synchronize(); return PCMFile(output)
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    private static func decodeAudioFile(source: URL,output: URL,maxSeconds: Double,progress: @escaping @Sendable (Double) async -> Void) async throws -> PCMFile {
        let input = try AVAudioFile(forReading: source,commonFormat: .pcmFormatFloat32,interleaved: false)
        let duration = Double(input.length)/input.processingFormat.sampleRate
        guard duration >= 0.1, duration <= maxSeconds+0.01,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32,sampleRate: 24000,channels: 1,interleaved: false),
              let converter = AVAudioConverter(from: input.processingFormat,to: format),
              let buffer = AVAudioPCMBuffer(pcmFormat: format,frameCapacity: 8192) else { throw ClientError("mobile.simple.audio_long") }
        converter.downmix = true
        let handle = try FileHandle(forWritingTo: PrivateFiles.empty(output)); defer { try? handle.close() }
        var finished = false, total = 0
        while !finished {
            try Task.checkCancellation()
            var conversionError: NSError?
            let readError = AudioReadFailure()
            let status = converter.convert(to: buffer,error: &conversionError) { count,status in
                if input.framePosition >= input.length { status.pointee = .endOfStream; return nil }
                guard let sourceBuffer = AVAudioPCMBuffer(pcmFormat: input.processingFormat,frameCapacity: count) else { status.pointee = .endOfStream; return nil }
                do { try input.read(into: sourceBuffer,frameCount: count); status.pointee = .haveData; return sourceBuffer }
                catch { readError.set(error); status.pointee = .endOfStream; return nil }
            }
            guard status != .error, conversionError == nil, readError.value == nil, let channel = buffer.floatChannelData?[0] else { throw ClientError("mobile.simple.media_failed") }
            let count = min(Int(buffer.frameLength),max(0,Int(maxSeconds*24000)-total)); total += count
            if count > 0 {
                let bytes = Data(bytes: channel,count: count*4)
                try handle.write(contentsOf: bytes)
            }
            finished = status == .endOfStream
            await progress(min(1,Double(input.framePosition)/Double(max(1,input.length))))
        }
        guard total >= 2400 else { throw ClientError("mobile.simple.media_failed") }
        try handle.synchronize(); return PCMFile(output)
    }

}
public protocol PhoneDubMedia: Sendable {
    func finish(source: URL, markedWave: URL, output: URL, audioOnly: Bool, progress: @escaping @Sendable (Double) async -> Void) async throws -> URL
}
/// Passthrough video + AAC replacement audio. No encoder, media or model request leaves the phone.
public struct AVPhoneDubMedia: PhoneDubMedia {
    public init() {}
    public func finish(source: URL, markedWave: URL, output: URL, audioOnly: Bool, progress: @escaping @Sendable (Double) async -> Void) async throws -> URL {
        guard source.isFileURL, markedWave.isFileURL, output.isFileURL else { throw ClientError("mobile.simple.media_failed") }
        let work = Task.detached(priority: .userInitiated) {
            let original = AVURLAsset(url: source,options: [AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue])
            let videos = try await original.loadTracks(withMediaType: .video)
            if audioOnly || videos.isEmpty {
                let result = output.appendingPathExtension("wav")
                try? FileManager.default.removeItem(at: result); try FileManager.default.copyItem(at: markedWave,to: result)
                try Task.checkCancellation(); await progress(1); return result
            }
            let result = output.appendingPathExtension("mp4")
            try? FileManager.default.removeItem(at: result)
            let writer = try AVAssetWriter(outputURL: result,fileType: .mp4)
            let videoReader = try AVAssetReader(asset: original)
            let sound = AVURLAsset(url: markedWave,options: [AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue])
            guard let audioTrack = try await sound.loadTracks(withMediaType: .audio).first, let videoTrack = videos.first else { throw ClientError("mobile.simple.media_failed") }
            let audioReader = try AVAssetReader(asset: sound)
            let duration = try await original.load(.duration)
            let videoOutput = AVAssetReaderTrackOutput(track: videoTrack,outputSettings: nil)
            let audioOutput = AVAssetReaderTrackOutput(track: audioTrack,outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
            guard videoReader.canAdd(videoOutput), audioReader.canAdd(audioOutput) else { throw ClientError("mobile.simple.media_failed") }
            videoReader.add(videoOutput); audioReader.add(audioOutput)
            let formats = try await videoTrack.load(.formatDescriptions)
            let videoInput = AVAssetWriterInput(mediaType: .video,outputSettings: nil,sourceFormatHint: formats.first)
            videoInput.transform = try await videoTrack.load(.preferredTransform)
            // Let the platform select a supported bitrate for 24-kHz mono AAC.
            // Forcing 96 kb/s can fail with "Cannot Encode Media" (-11861).
            let audioInput = AVAssetWriterInput(mediaType: .audio,outputSettings: [AVFormatIDKey: kAudioFormatMPEG4AAC,AVSampleRateKey: 24000,AVNumberOfChannelsKey: 1])
            guard writer.canAdd(videoInput), writer.canAdd(audioInput) else { throw ClientError("mobile.simple.media_failed") }
            writer.add(videoInput); writer.add(audioInput)
            let provenance = AVMutableMetadataItem(); provenance.identifier = .commonIdentifierDescription; provenance.value = "VoiceStudio synthetic audio" as NSString
            writer.metadata = [provenance]
            defer { videoReader.cancelReading(); audioReader.cancelReading(); if writer.status == .writing { writer.cancelWriting() } }
            guard writer.startWriting(), videoReader.startReading(), audioReader.startReading() else { throw ClientError("mobile.simple.media_failed") }
            writer.startSession(atSourceTime: .zero)
            var videoDone = false, audioDone = false
            while !videoDone || !audioDone {
                try Task.checkCancellation()
                guard writer.status == .writing else { throw ClientError("mobile.simple.media_failed") }
                var advanced = false
                if !videoDone && videoInput.isReadyForMoreMediaData {
                    if let sample = videoOutput.copyNextSampleBuffer() {
                        guard videoInput.append(sample) else { throw ClientError("mobile.simple.media_failed") }
                        let time = CMSampleBufferGetPresentationTimeStamp(sample).seconds
                        await progress(min(0.99,max(0,time/max(0.001,duration.seconds))))
                    } else { guard videoReader.status == .completed else { throw ClientError("mobile.simple.media_failed") }; videoInput.markAsFinished(); videoDone = true }
                    advanced = true
                }
                if !audioDone && audioInput.isReadyForMoreMediaData {
                    if let sample = audioOutput.copyNextSampleBuffer() {
                        guard audioInput.append(sample) else { throw ClientError("mobile.simple.media_failed") }
                    } else { guard audioReader.status == .completed else { throw ClientError("mobile.simple.media_failed") }; audioInput.markAsFinished(); audioDone = true }
                    advanced = true
                }
                if !advanced { try await Task.sleep(for: .milliseconds(5)) }
            }
            writer.endSession(atSourceTime: duration)
            await writer.finishWriting()
            try Task.checkCancellation()
            guard writer.status == .completed else { throw ClientError("mobile.simple.media_failed") }
            await progress(1); return result
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
}

/// Frames to insert (positive) or leading frames to drop (negative) so a buffer stamped `timestamp`
/// seconds lands at its container time given `written` 24 kHz frames already output. Under 10 ms is jitter.
func timelineShift(timestamp: Double,written: Int) -> Int {
    guard timestamp.isFinite, timestamp >= 0 else { return 0 }
    let delta = Int((timestamp*24000).rounded())-written
    return abs(delta) < 240 ? 0 : delta
}
