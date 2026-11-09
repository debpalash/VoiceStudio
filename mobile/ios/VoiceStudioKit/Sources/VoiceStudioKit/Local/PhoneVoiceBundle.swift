import Foundation

/// Imports an explicitly requested stored .omnivoice archive. No inference or model download.
public enum PhoneVoiceBundle {
    public static func install(_ source: URL,store: PhoneVoiceStore,attribution: [String:String],consent: Bool,decoder: any PhoneAudioDecoder = AVPhoneAudioDecoder()) async throws -> PhoneVoice {
        guard consent else { throw ClientError("mobile.handoff.voices_required") }
        let entries = try ProjectArchive.entries(source)
        let allowed = Set(["wav","flac","mp3","m4a","ogg","opus","webm","caf","aiff","aif"])
        guard entries.count <= 4,entries.reduce(0,{ $0+$1.size }) <= 64*1024*1024,
              entries.allSatisfy({ $0.size <= 32*1024*1024 && ($0.path == "metadata.json" || ((($0.path as NSString).deletingPathExtension == "ref_audio" || ($0.path as NSString).deletingPathExtension == "locked_audio") && allowed.contains(($0.path as NSString).pathExtension.lowercased()))) }),
              (entries.first(where: { $0.path == "metadata.json" })?.size ?? Int.max) <= 65536,
              entries.filter({ ($0.path as NSString).deletingPathExtension == "ref_audio" }).count == 1 else { throw ClientError("mobile.galleryCopy.unavailable") }
        let workspace = FileManager.default.temporaryDirectory.appendingPathComponent("voice-bundle-"+UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: workspace) }
        let files = try await ProjectArchive.extract(source,to: workspace)
        guard let metadataFile = files["metadata.json"],let reference = files.first(where: { ($0.key as NSString).deletingPathExtension == "ref_audio" })?.value else { throw ClientError("mobile.galleryCopy.unavailable") }
        var metadata = try JSONDecoder().decode([String:JSONValue].self,from: Data(contentsOf: metadataFile))
        guard metadata["bundle_version"]?.number == 1,let transcript = metadata["ref_text"]?.string,!transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,transcript.utf16.count <= 4000,let name = metadata["profile_name"]?.string,!name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,name.count <= 200 else { throw ClientError("mobile.galleryCopy.unavailable") }
        metadata["sourceAttribution"] = .object(attribution.mapValues(JSONValue.string))
        let pcm = try await decoder.decode(source: reference,output: workspace.appendingPathComponent("reference.pcm"),maxSeconds: 30,progress: { _ in })
        guard (2400...720000).contains(try pcm.frames) else { throw ClientError("mobile.galleryCopy.unavailable") }
        let marked = try PhoneAudioProcessing.containsSyntheticMarker(reference)
        let synthetic = metadata["kind"]?.string == "design" || marked
        return try await store.installHandoff(name: name,transcript: transcript,reference: pcm.read(start: 0,count: pcm.frames),metadata: metadata,synthetic: synthetic,consent: consent)
    }
}
