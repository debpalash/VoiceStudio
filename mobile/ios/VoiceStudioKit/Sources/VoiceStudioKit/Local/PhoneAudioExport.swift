import Foundation
import VSNative

/// Offline streaming MP3 export. Callers explicitly preserve the source's synthetic provenance.
public enum PhoneAudioExport {
    @discardableResult public static func mp3(source: URL,output: URL,synthetic: Bool = true,
                    progress: @escaping @Sendable (Double) async -> Void = { _ in }) async throws -> URL {
        guard source.isFileURL, output.isFileURL, source.standardizedFileURL != output.standardizedFileURL else { throw ClientError("mobile.export.failed") }
        let scratch = output.deletingLastPathComponent().appendingPathComponent(".mp3-"+UUID().uuidString,isDirectory: true)
        try FileManager.default.createDirectory(at: scratch,withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: scratch) }
        let pcm = try await AVPhoneAudioDecoder().decode(source: source,output: scratch.appendingPathComponent("decoded.pcm"),maxSeconds: 14400,progress: { await progress($0*0.2) })
        let pending = scratch.appendingPathComponent("audio.mp3")
        let work = Task.detached(priority: .userInitiated) {
            let frames = try pcm.frames
            guard let encoder = vs_mp3_create(pending.path,24000,UInt64(frames),synthetic ? 1 : 0) else { throw ClientError("mobile.export.failed") }
            defer { vs_mp3_free(encoder) }
            var offset = 0
            while offset < frames {
                try Task.checkCancellation()
                let samples = try pcm.read(start: offset,count: min(8192,frames-offset)).samples
                guard samples.withUnsafeBufferPointer({ vs_mp3_write(encoder,$0.baseAddress,Int32($0.count)) }) == 0 else { throw ClientError("mobile.export.failed") }
                offset += samples.count; await progress(0.2+Double(offset)/Double(frames)*0.8)
            }
            try Task.checkCancellation()
            guard vs_mp3_finish(encoder) == 0 else { throw ClientError("mobile.export.failed") }
        }
        try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
        try Task.checkCancellation()
        // Same-directory scratch keeps publication atomic; cancellation never publishes partial audio.
        if FileManager.default.fileExists(atPath: output.path) { _ = try FileManager.default.replaceItemAt(output,withItemAt: pending) }
        else { try FileManager.default.moveItem(at: pending,to: output) }
        return output
    }
}
