package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID
import kotlin.math.abs

/** At most one bounded cue is loaded; previews never read an entire source movie into RAM. */
data class DubPreview(val file: File,val peaks: List<Float>)
suspend fun PhoneDubStore.preview(id: String,segment: DubSegmentState,target: String,generated: Boolean): DubPreview = withContext(Dispatchers.IO) {
    val duration=segment.speech.end-segment.speech.start
    require(duration.isFinite()&&duration>0&&duration<=30)
    val audio=if(generated) {
        val render=checkpoint(id).renders.firstOrNull {it.segmentID==segment.id&&it.target==target&&it.scope==dubSegmentScope(segment,target)} ?: throw ClientFailure("mobile.editing.preview_unavailable")
        val pcm=render.file?.let {file(id,it)} ?: throw ClientFailure("mobile.editing.preview_unavailable")
        if(!pcm.isFile||pcm.length()!=render.frames*4||sha256(pcm)!=render.sha256) throw ClientFailure("mobile.editing.preview_unavailable")
        require(segment.gain.isFinite()&&segment.gain in 0.0..2.0)
        val dry=PcmFile(pcm).read(0,render.frames.toInt())
        LocalAudio(FloatArray(dry.samples.size) {if(segment.muted) 0f else (dry.samples[it]*segment.gain).toFloat().coerceIn(-1f,1f)},dry.sampleRate)
    } else PcmFile(file(id,"original.pcm")).clip(segment.speech.start,segment.speech.end)
    val output=file(id,"preview-${UUID.randomUUID()}.wav")
    try {
        if(generated) markSynthetic(audio,output) else writeReferenceWave(audio,output)
        val peaks=List(128) {bin ->
            val start=audio.samples.size*bin/128;val end=audio.samples.size*(bin+1)/128
            var maximum=0f;for(index in start until end) maximum=maxOf(maximum,abs(audio.samples[index]));maximum.coerceIn(0f,1f)
        }
        DubPreview(output,peaks)
    } catch(e:Throwable) {output.delete();throw e}
}
