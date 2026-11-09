import Foundation

public struct PhoneDubSubtitleStyle: Sendable, Equatable {
    public var fontSize = 36
    public var color = "FFFFFF"
    public var top = false
    public var dual = false
    public init() {}
    public var valid: Bool { (18...72).contains(fontSize) && ["FFFFFF","FFFF00","00FFFF"].contains(color) }
}
public enum PhoneDubASS {
    public static func export(_ segments: [PhoneDubSegment],target: String,style: PhoneDubSubtitleStyle = .init()) throws -> String {
        guard style.valid, !segments.isEmpty, segments.count <= 10000 else { throw ClientError("mobile.dub_advanced.invalid_export") }
        let rgb = style.color, bgr = String(rgb.suffix(2))+String(rgb.dropFirst(2).prefix(2))+String(rgb.prefix(2))
        let header = """
        [Script Info]
        ScriptType: v4.00+
        PlayResX: 1920
        PlayResY: 1080
        WrapStyle: 0
        ScaledBorderAndShadow: yes

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Default,Arial,\(style.fontSize),&H00\(bgr),&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,\(style.top ? 8 : 2),64,64,64,1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        """
        let lines = try segments.map { cue -> String in
            guard cue.speech.end > cue.speech.start, let text = cue.translations[target], !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ClientError("mobile.editing.subtitle_missing") }
            let speaker = (cue.speaker ?? "").replacingOccurrences(of: ",",with: " ").replacingOccurrences(of: "\n",with: " ").replacingOccurrences(of: "\r",with: " ")
            let content = escape(text)+(style.dual ? "\\N{\\i1}"+escape(cue.speech.text)+"{\\i0}" : "")
            return "Dialogue: 0,\(try stamp(cue.speech.start)),\(try stamp(cue.speech.end)),Default,\(speaker),0,0,0,,\(content)"
        }
        return header+"\n"+lines.joined(separator: "\n")+"\n"
    }
    private static func escape(_ text: String) -> String {
        text.replacingOccurrences(of: "\\",with: "\\\\").replacingOccurrences(of: "{",with: "\\{").replacingOccurrences(of: "}",with: "\\}").replacingOccurrences(of: "\r\n",with: "\\N").replacingOccurrences(of: "\n",with: "\\N").replacingOccurrences(of: "\r",with: "\\N")
    }
    private static func stamp(_ value: Double) throws -> String {
        guard value.isFinite, value >= 0, value <= 14400 else { throw ClientError("mobile.dub_advanced.invalid_export") }
        let cs = Int((value*100).rounded()); return String(format: "%d:%02d:%02d.%02d",cs/360000,cs/6000%60,cs/100%60,cs%100)
    }
}
public enum PhoneDubPackage: String, Sendable { case clips, stems }
public extension PhoneDubStore {
    /// Export a verified saved generation under the same job lock as rendering and deletion.
    func exportPackage(_ id: UUID,target: String,kind: PhoneDubPackage,workspace: URL) async throws -> URL {
        try await acquire(id)
        do { let result = try await writePackage(id,target: target,kind: kind,workspace: workspace); release(id); return result }
        catch { release(id); throw error }
    }
    private func writePackage(_ id: UUID,target: String,kind: PhoneDubPackage,workspace: URL) async throws -> URL {
        let request = try request(id), cp = try checkpoint(id)
        guard cp.done, request.targets.contains(where: { $0.code == target }), !cp.segments.isEmpty else { throw ClientError("mobile.dub_advanced.generate_first") }
        var byIndex: [Int:PhoneDubRender] = [:]
        for render in cp.renders where render.target == target { byIndex[render.index] = render }
        var pieces: [(Int,PCMFile)] = []
        for (index,cue) in cp.segments.enumerated() {
            guard let render = byIndex[index], render.segmentID == cue.id, render.scope == phoneDubSegmentScope(cue,target: target), let name = render.file else { throw ClientError("mobile.dub_advanced.generate_first") }
            let pcm = PCMFile(try file(id,name))
            guard try pcm.frames == render.frames, try ModelHash.file(pcm.url) == render.hash else { throw ClientError("mobile.simple.media_failed") }
            pieces.append((Int(cue.speech.start*24000),pcm))
        }
        let sampleCount = kind == .stems ? cp.frames*2 : byIndex.values.reduce(0) { $0+$1.frames }
        guard sampleCount <= 500_000_000, kind != .stems || cp.frames*2 <= 536_870_000 else { throw ClientError("mobile.dub_advanced.export_limit") }
        let required = Int64(sampleCount)*4+16_000_000
        let volume = try directory(id).resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage ?? Int64.max
        guard volume >= required else { throw ClientError("mobile.local.storage_required") }
        let root = workspace.appendingPathComponent("dub-package-"+UUID().uuidString)
        try PrivateFiles.directory(root)
        do {
            var entries: [ProjectArchiveFile] = []
            if kind == .clips {
                for (index,piece) in pieces.enumerated() {
                    try Task.checkCancellation()
                    let cue = cp.segments[index], dry = try piece.1.read(start: 0,count: piece.1.frames), gain = cue.muted == true ? 0 : (cue.gain ?? 1)
                    let output = root.appendingPathComponent(String(format: "%05d.wav",index+1))
                    let audio = LocalAudio(samples: dry.samples.map { min(1,max(-1,$0*Float(gain))) },sampleRate: 24000)
                    try audio.markSynthetic().write(to: output,options: .atomic)
                    entries.append(ProjectArchiveFile(path: "segments/"+output.lastPathComponent,file: output))
                }
            } else {
                let mixed = try mixTimeline(to: root.appendingPathComponent("mix.pcm"),frames: cp.frames,segments: pieces,gains: cp.segments.map { $0.muted == true ? 0 : ($0.gain ?? 1) })
                let dubbed = root.appendingPathComponent("dubbed.wav"); try mixed.markSynthetic(to: dubbed)
                let original = root.appendingPathComponent("original.wav"), pcm = PCMFile(try file(id,"original.pcm"))
                guard try pcm.frames == cp.frames, try ModelHash.file(pcm.url) == cp.originalHash else { throw ClientError("mobile.simple.media_failed") }
                if try PhoneAudioProcessing.containsSyntheticMarker(file(id,request.sourceFile)) { try pcm.markSynthetic(to: original) }
                else { try PhoneAudioProcessing.writeNaturalWave(pcm,to: original) }
                entries += [ProjectArchiveFile(path: "dubbed.wav",file: dubbed),ProjectArchiveFile(path: "original.wav",file: original)]
                try FileManager.default.removeItem(at: mixed.url)
            }
            for ext in ["srt","vtt","ass"] {
                let text = try ext == "ass" ? PhoneDubASS.export(cp.segments,target: target) : PhoneDubSubtitles.export(cp.segments,target: target,vtt: ext == "vtt")
                let output = root.appendingPathComponent("subtitles."+ext); try text.write(to: output,atomically: true,encoding: .utf8)
                entries.append(ProjectArchiveFile(path: output.lastPathComponent,file: output))
            }
            struct Manifest: Encodable { let format: String; let version: Int; let kind: String; let sourceHash: String; let language: String; let modelPins: [String:String]; let segments: [PhoneDubSegment]; let originalIsIsolatedBackground: Bool }
            // Packages contain shareable outputs; voice conditioning stays private.
            let publicSegments = cp.segments.map { cue in
                var value = cue
                value.voiceID = nil; value.referenceFile = nil; value.referenceText = nil
                value.referenceOrigin = nil; value.referenceNeedsTranscription = nil
                return value
            }
            let manifest = root.appendingPathComponent("manifest.json")
            try PrivateFiles.write(Manifest(format: "voicestudio-dub-export",version: 1,kind: kind.rawValue,sourceHash: request.sourceHash,language: target,modelPins: request.modelPins,segments: publicSegments,originalIsIsolatedBackground: false),to: manifest)
            entries.append(ProjectArchiveFile(path: "manifest.json",file: manifest))
            let output = workspace.appendingPathComponent("dub-"+kind.rawValue+"-"+UUID().uuidString+".zip")
            try await ProjectArchive.write(entries: entries,to: output)
            try? FileManager.default.removeItem(at: root); return output
        } catch { try? FileManager.default.removeItem(at: root); throw error }
    }
}
