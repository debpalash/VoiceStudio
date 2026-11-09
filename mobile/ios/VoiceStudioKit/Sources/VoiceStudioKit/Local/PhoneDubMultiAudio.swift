import Foundation
@preconcurrency import AVFoundation

public struct PhoneDubMuxTrack: Sendable {
    public let file: URL
    public let language: String
    public let name: String
    public init(file: URL,language: String,name: String) { self.file = file; self.language = language; self.name = name }
}
/// Copies compressed source video and encoded language tracks; no new voice inference or video re-encode.
public enum PhoneDubMultiAudio {
    public static func export(source: URL,tracks: [PhoneDubMuxTrack],to output: URL) async throws -> URL {
        guard !tracks.isEmpty, tracks.count <= 20, Set(tracks.map(\.language)).count == tracks.count else { throw ClientError("mobile.dub_advanced.invalid_export") }
        let work = Task.detached(priority: .userInitiated) {
            let pending = output.deletingLastPathComponent().appendingPathComponent(UUID().uuidString+".mp4")
            defer { try? FileManager.default.removeItem(at: pending) }
            let sourceAsset = AVURLAsset(url: source), duration = try await sourceAsset.load(.duration)
            guard let video = try await sourceAsset.loadTracks(withMediaType: .video).first else { throw ClientError("mobile.dub_advanced.video_required") }
            let writer = try AVAssetWriter(outputURL: pending,fileType: .mp4)
            var readers: [AVAssetReader] = [], outputs: [AVAssetReaderTrackOutput] = [], inputs: [AVAssetWriterInput] = []
            func append(_ asset: AVAsset,_ track: AVAssetTrack,_ type: AVMediaType,_ language: String?,_ title: String?) async throws {
                let reader = try AVAssetReader(asset: asset), stream = AVAssetReaderTrackOutput(track: track,outputSettings: nil)
                let formats = try await track.load(.formatDescriptions)
                let input = AVAssetWriterInput(mediaType: type,outputSettings: nil,sourceFormatHint: formats.first)
                if type == .video { input.transform = try await track.load(.preferredTransform) }
                if let language {
                    let code = Locale.Language(identifier: language).languageCode?.identifier(.alpha3) ?? "und"
                    if code.utf8.count == 3, code.utf8.allSatisfy({ (97...122).contains($0) }) { input.languageCode = code }
                    if language.range(of: "^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",options: .regularExpression) != nil { input.extendedLanguageTag = language }
                }
                if let title { let item = AVMutableMetadataItem(); item.identifier = .commonIdentifierTitle; item.value = title as NSString; input.metadata = [item] }
                guard reader.canAdd(stream), writer.canAdd(input) else { throw ClientError("mobile.simple.media_failed") }
                reader.add(stream); writer.add(input); readers.append(reader); outputs.append(stream); inputs.append(input)
            }
            try await append(sourceAsset,video,.video,nil,nil)
            for track in tracks {
                let asset = AVURLAsset(url: track.file)
                guard let audio = try await asset.loadTracks(withMediaType: .audio).first else { throw ClientError("mobile.simple.media_failed") }
                try await append(asset,audio,.audio,track.language.replacingOccurrences(of: "_",with: "-"),track.name)
            }
            let group = AVAssetWriterInputGroup(inputs: Array(inputs.dropFirst()),defaultInput: inputs[1])
            guard writer.canAdd(group) else { throw ClientError("mobile.simple.media_failed") }; writer.add(group)
            let provenance = AVMutableMetadataItem(); provenance.identifier = .commonIdentifierDescription; provenance.value = "VoiceStudio synthetic audio" as NSString; writer.metadata = [provenance]
            defer { readers.forEach { $0.cancelReading() }; if writer.status == .writing { writer.cancelWriting() } }
            guard writer.startWriting(), readers.allSatisfy({ $0.startReading() }) else { throw ClientError("mobile.simple.media_failed") }
            writer.startSession(atSourceTime: .zero); var completed = [Bool](repeating: false,count: readers.count)
            while completed.contains(false) {
                try Task.checkCancellation(); guard writer.status == .writing else { throw ClientError("mobile.simple.media_failed") }
                var progressed = false
                for index in inputs.indices where !completed[index] && inputs[index].isReadyForMoreMediaData {
                    if let sample = outputs[index].copyNextSampleBuffer() { guard inputs[index].append(sample) else { throw ClientError("mobile.simple.media_failed") } }
                    else { guard readers[index].status == .completed else { throw ClientError("mobile.simple.media_failed") }; inputs[index].markAsFinished(); completed[index] = true }
                    progressed = true
                }
                if !progressed { try await Task.sleep(for: .milliseconds(5)) }
            }
            writer.endSession(atSourceTime: duration); await writer.finishWriting(); try Task.checkCancellation()
            guard writer.status == .completed else { throw ClientError("mobile.simple.media_failed") }
            guard !FileManager.default.fileExists(atPath: output.path) else { throw ClientError("mobile.dub_advanced.invalid_export") }
            try FileManager.default.moveItem(at: pending,to: output); return output
        }
        return try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
    }
}
public extension PhoneDubStore {
    func exportMultiAudio(_ id: UUID,defaultLanguage: String,workspace: URL) async throws -> URL {
        try await acquire(id)
        do {
            let request = try request(id), cp = try checkpoint(id)
            guard cp.done, cp.outputs.contains(where: { $0.language == defaultLanguage }), cp.outputs.allSatisfy({ $0.file.hasSuffix(".mp4") }) else { throw ClientError("mobile.dub_advanced.video_required") }
            let order = cp.outputs.sorted { ($0.language == defaultLanguage ? 0 : 1) < ($1.language == defaultLanguage ? 0 : 1) }
            let tracks = try order.map { PhoneDubMuxTrack(file: try verifiedOutput(id,$0),language: $0.language,name: $0.name) }
            let source = try file(id,request.sourceFile)
            guard try ModelHash.file(source) == request.sourceHash else { throw ClientError("mobile.simple.media_failed") }
            try PrivateFiles.directory(workspace)
            let output = workspace.appendingPathComponent("dub-multitrack-"+UUID().uuidString+".mp4")
            let value = try await PhoneDubMultiAudio.export(source: source,tracks: tracks,to: output); release(id); return value
        } catch { release(id); throw error }
    }
}
