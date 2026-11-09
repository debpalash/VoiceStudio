import Foundation

public extension PhoneDubStore {
    /// A bounded cue preview. A generated preview is available only for the exact saved render inputs.
    func preview(_ id: UUID,segment: PhoneDubSegment,target: String,generated: Bool) throws -> URL {
        let duration = segment.speech.end-segment.speech.start
        guard duration.isFinite, duration > 0, duration <= 30 else { throw ClientError("mobile.simple.audio_long") }
        let audio: PCMChunk
        if generated {
            guard let render = try checkpoint(id).renders.first(where: { $0.segmentID == segment.id && $0.target == target && $0.scope == phoneDubSegmentScope(segment,target: target) }),
                  let name = render.file else { throw ClientError("mobile.editing.preview_unavailable") }
            let pcm = PCMFile(try file(id,name))
            guard (try? pcm.frames) == render.frames, (try? ModelHash.file(pcm.url)) == render.hash else { throw ClientError("mobile.editing.preview_unavailable") }
            audio = try pcm.read(start: 0,count: render.frames)
        } else { audio = try PCMFile(file(id,"original.pcm")).clip(start: segment.speech.start,end: segment.speech.end) }
        let output = try file(id,"preview-"+UUID().uuidString+".wav")
        if generated {
            let gain = segment.muted == true ? 0 : (segment.gain ?? 1)
            guard gain.isFinite, (0...2).contains(gain) else { throw ClientError("mobile.simple.media_failed") }
            try LocalAudio(samples: audio.samples.map { min(1,max(-1,$0*Float(gain))) },sampleRate: audio.sampleRate).markSynthetic().write(to: output,options: .atomic) }
        else { try Self.referenceWave(audio).write(to: output,options: .atomic) }
        return output
    }
    /// Original recordings carry no synthetic provenance label.
    private static func referenceWave(_ audio: PCMChunk) -> Data {
        var data = Data()
        func word<T: FixedWidthInteger>(_ value: T) { var little = value.littleEndian; withUnsafeBytes(of: &little) { data.append(contentsOf: $0) } }
        data.append(Data("RIFF".utf8)); word(UInt32(36+audio.samples.count*2)); data.append(Data("WAVEfmt ".utf8)); word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(audio.sampleRate)); word(UInt32(audio.sampleRate*2)); word(UInt16(2)); word(UInt16(16)); data.append(Data("data".utf8)); word(UInt32(audio.samples.count*2))
        for sample in audio.samples { word(Int16(max(-1,min(1,sample))*32767)) }; return data
    }
}
