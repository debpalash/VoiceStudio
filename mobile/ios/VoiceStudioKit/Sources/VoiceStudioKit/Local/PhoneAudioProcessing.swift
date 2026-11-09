import Foundation

public struct PhoneAudioDocument: Sendable {
    public let pcm: PCMFile
    public let frames: Int
    public let synthetic: Bool
    public let peaks: [Float]
    public var duration: Double { Double(frames)/24000 }
    public init(pcm: PCMFile,frames: Int,synthetic: Bool,peaks: [Float] = []) { self.pcm = pcm; self.frames = frames; self.synthetic = synthetic; self.peaks = peaks }
}
public struct PhoneAudioEditOptions: Sendable, Equatable {
    public var start: Double = 0
    public var end: Double?
    public var fadeIn: Double = 0
    public var fadeOut: Double = 0
    public var gainDB: Double = 0
    public var normalize = false
    public var targetDB: Double = -2
    public var noiseGate = false
    public var gateDB: Double = -40
    public init() {}
}
public struct PhoneAudioEditResult: Sendable {
    public let file: URL
    public let duration: Double
    public let peak: Double
    public let synthetic: Bool
}
/// Speech editing stays on disk at 24 kHz mono. Processing never changes or replaces the imported file.
public enum PhoneAudioProcessing {
    public static func open(source: URL,workspace: URL,decoder: any PhoneAudioDecoder = AVPhoneAudioDecoder(),progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws -> PhoneAudioDocument {
        try PrivateFiles.directory(workspace)
        let output = workspace.appendingPathComponent(UUID().uuidString+".pcm")
        do {
            let pcm = try await decoder.decode(source: source,output: output,maxSeconds: 14400,progress: progress)
            let work = Task.detached(priority: .userInitiated) {
                let frames = try pcm.frames
                guard frames > 0, frames <= 345600000 else { throw ClientError("mobile.simple.audio_long") }
                let access = source.startAccessingSecurityScopedResource(); defer { if access { source.stopAccessingSecurityScopedResource() } }
                let synthetic = try containsSyntheticMarker(source)
                var peaks = [Float](repeating: 0,count: 256)
                for offset in stride(from: 0,to: frames,by: 8192) {
                    try Task.checkCancellation()
                    for (index,sample) in try pcm.read(start: offset,count: min(8192,frames-offset)).samples.enumerated() {
                        let bin = min(255,(offset+index)*256/frames); peaks[bin] = max(peaks[bin],abs(sample))
                    }
                }
                return PhoneAudioDocument(pcm: pcm,frames: frames,synthetic: synthetic,peaks: peaks)
            }
            return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
        } catch { try? FileManager.default.removeItem(at: output); throw error }
    }
    /// All recognized VoiceStudio encoders store this ASCII marker in bounded metadata blocks.
    public static func containsSyntheticMarker(_ source: URL) throws -> Bool {
        let marker = Data("VoiceStudio synthetic audio".utf8), input = try FileHandle(forReadingFrom: source)
        defer { try? input.close() }
        var tail = Data()
        while let chunk = try input.read(upToCount: 65536), !chunk.isEmpty {
            try Task.checkCancellation(); var bytes = tail; bytes.append(chunk)
            if bytes.range(of: marker) != nil { return true }; tail = bytes.suffix(marker.count-1)
        }
        return false
    }
    public static func process(_ document: PhoneAudioDocument,options: PhoneAudioEditOptions,workspace: URL,progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws -> PhoneAudioEditResult {
        let work = Task.detached(priority: .userInitiated) { try await render(document,options: options,workspace: workspace,progress: progress) }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
    private static func render(_ document: PhoneAudioDocument,options: PhoneAudioEditOptions,workspace: URL,progress: @escaping @Sendable (Double) async -> Void) async throws -> PhoneAudioEditResult {
        let end = options.end ?? document.duration
        guard document.frames > 0, document.frames <= 345600000, options.start.isFinite, end.isFinite, options.start >= 0, end <= document.duration+0.000001, end > options.start,
              options.fadeIn.isFinite, options.fadeOut.isFinite, options.fadeIn >= 0, options.fadeOut >= 0, options.fadeIn <= end-options.start, options.fadeOut <= end-options.start,
              options.gainDB.isFinite, (-24...24).contains(options.gainDB), options.targetDB.isFinite, (-24...(-1)).contains(options.targetDB), options.gateDB.isFinite, (-80...(-10)).contains(options.gateDB),
              try document.pcm.frames == document.frames else { throw ClientError("mobile.processing.invalid") }
        let first = Int(options.start*24000)
        let last = end >= document.duration ? document.frames : min(document.frames,Int(end*24000))
        let count = last-first
        guard count > 0 else { throw ClientError("mobile.processing.invalid") }
        let directory = workspace.appendingPathComponent("audio-edit-"+UUID().uuidString)
        try PrivateFiles.directory(directory)
        let output = directory.appendingPathComponent("edited.wav"), pcm = PCMFile(directory.appendingPathComponent("edited.pcm"))
        do {
            var state = Processor(options,count: count), peak = 0.0
            // Peak analysis observes the actual gated, faded, gained signal before any clipping.
            for offset in stride(from: 0,to: count,by: 8192) {
                try Task.checkCancellation()
                let block = try document.pcm.read(start: first+offset,count: min(8192,count-offset))
                for (index,sample) in block.samples.enumerated() { peak = max(peak,abs(state.sample(Double(sample),at: offset+index))) }
                await progress(Double(offset+block.samples.count)/Double(count)*0.45)
            }
            // Match desktop normalize_audio: never amplify a signal at or below −50 dBFS.
            let factor = options.normalize && peak > pow(10,-50.0/20) ? pow(10,options.targetDB/20)/peak : 1
            state = Processor(options,count: count)
            let handle = try FileHandle(forWritingTo: PrivateFiles.empty(pcm.url)); defer { try? handle.close() }
            for offset in stride(from: 0,to: count,by: 8192) {
                try Task.checkCancellation()
                let block = try document.pcm.read(start: first+offset,count: min(8192,count-offset)); var data = Data(capacity: block.samples.count*4)
                for (index,sample) in block.samples.enumerated() {
                    var value = Float(max(-1,min(1,state.sample(Double(sample),at: offset+index)*factor))).bitPattern.littleEndian
                    withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
                }
                try handle.write(contentsOf: data); await progress(0.45+Double(offset+block.samples.count)/Double(count)*0.45)
            }
            try handle.synchronize(); try Task.checkCancellation()
            if document.synthetic { try pcm.markSynthetic(to: output) } else { try writeNaturalWave(pcm,to: output) }
            try Task.checkCancellation(); try FileManager.default.removeItem(at: pcm.url); await progress(1)
            return PhoneAudioEditResult(file: output,duration: Double(count)/24000,peak: min(1,peak*factor),synthetic: document.synthetic)
        } catch { try? FileManager.default.removeItem(at: directory); throw error }
    }
    /// Streaming PCM16 for natural recordings; no synthetic label is added to an ordinary edit.
    public static func writeNaturalWave(_ pcm: PCMFile,to output: URL) throws {
        let frames = try pcm.frames
        guard frames > 0, frames <= 345600000 else { throw ClientError("mobile.simple.audio_long") }
        var header = Data()
        func word<T: FixedWidthInteger>(_ value: T) { var little = value.littleEndian; withUnsafeBytes(of: &little) { header.append(contentsOf: $0) } }
        header.append(Data("RIFF".utf8)); word(UInt32(36+frames*2)); header.append(Data("WAVEfmt ".utf8)); word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(24000)); word(UInt32(48000)); word(UInt16(2)); word(UInt16(16)); header.append(Data("data".utf8)); word(UInt32(frames*2))
        let handle = try FileHandle(forWritingTo: PrivateFiles.empty(output)); defer { try? handle.close() }; try handle.write(contentsOf: header)
        for offset in stride(from: 0,to: frames,by: 8192) {
            try Task.checkCancellation(); var data = Data()
            for sample in try pcm.read(start: offset,count: min(8192,frames-offset)).samples { var value = Int16((max(-1,min(1,sample))*32767).rounded()).littleEndian; withUnsafeBytes(of: &value) { data.append(contentsOf: $0) } }
            try handle.write(contentsOf: data)
        }
        try handle.synchronize()
    }
    private struct Processor {
        let options: PhoneAudioEditOptions
        let count: Int
        let gain: Double
        let threshold: Double
        let fadeIn: Int
        let fadeOut: Int
        let attack = exp(-1.0/(0.001*24000))
        let release = exp(-1.0/(0.2*24000))
        var envelope = 0.0
        init(_ options: PhoneAudioEditOptions,count: Int) { self.options = options; self.count = count; gain = pow(10,options.gainDB/20); threshold = pow(10,options.gateDB/20); fadeIn = Int(options.fadeIn*24000); fadeOut = Int(options.fadeOut*24000) }
        mutating func sample(_ value: Double,at index: Int) -> Double {
            var scale = gain
            if options.noiseGate {
                let level = abs(value), coefficient = level > envelope ? attack : release
                envelope = coefficient*envelope+(1-coefficient)*level
                scale *= envelope >= threshold ? 1 : pow(envelope/threshold,9)
            }
            if fadeIn > 0 { scale *= min(1,Double(index)/Double(max(1,fadeIn-1))) }
            if fadeOut > 0 { scale *= min(1,Double(count-1-index)/Double(max(1,fadeOut-1))) }
            return value*scale
        }
    }
}
