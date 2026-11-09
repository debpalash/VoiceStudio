package com.voicestudio.mobile.local

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import java.io.*
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.*

/** Disk-backed mono float PCM. Long media never becomes one giant heap buffer. */
data class PcmFile(val file: File, val sampleRate: Int = 24000) {
    val frames: Long get() = file.length()/4
    val duration: Double get() = frames.toDouble()/sampleRate
    fun read(start: Long, count: Int): LocalAudio {
        require(start>=0&&count>=0&&start+count<=frames)
        val bytes=ByteArray(Math.multiplyExact(count,4))
        RandomAccessFile(file,"r").use {it.seek(start*4);it.readFully(bytes)}
        val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        return LocalAudio(FloatArray(count) {buffer.get()},sampleRate)
    }
    fun clip(start: Double, end: Double): LocalAudio {
        require(start.isFinite()&&end.isFinite()&&start>=0&&end>start)
        val first=(start*sampleRate).toLong().coerceAtMost(frames)
        val last=(end*sampleRate).toLong().coerceAtMost(frames)
        return read(first,(last-first).toInt())
    }
}
fun writePcm(audio: LocalAudio, file: File) {
    require(audio.sampleRate>0&&audio.samples.isNotEmpty()&&audio.samples.all {it.isFinite()})
    file.parentFile?.mkdirs()
    FileOutputStream(file).use {out ->
        val bytes=ByteBuffer.allocate(16384).order(ByteOrder.LITTLE_ENDIAN)
        audio.samples.forEach {sample ->
            if(bytes.remaining()<4) {out.write(bytes.array(),0,bytes.position());bytes.clear()}
            bytes.putFloat(sample.coerceIn(-1f,1f))
        }
        out.write(bytes.array(),0,bytes.position());out.fd.sync()
    }
}
/** Linear, phase-continuous sample-rate conversion across decoder buffers. */
internal class MonoResampler(private val inputRate: Int, private val outputRate: Int) {
    private var consumed=0L
    private var next=0.0
    private var previous=0f
    fun accept(samples: FloatArray): FloatArray {
        require(inputRate>0&&outputRate>0)
        if(samples.isEmpty()) return FloatArray(0)
        val result=ArrayList<Float>((samples.size.toLong()*outputRate/inputRate+2).toInt())
        val end=consumed+samples.size-1
        while(next<=end) {
            val low=floor(next).toLong();val high=ceil(next).toLong()
            if(high>end) break
            val a=if(low<consumed) previous else samples[(low-consumed).toInt()]
            val b=if(high<consumed) previous else samples[(high-consumed).toInt()]
            result+=(a+(b-a)*(next-low).toFloat()).coerceIn(-1f,1f)
            next+=inputRate.toDouble()/outputRate
        }
        consumed+=samples.size;previous=samples.last()
        return result.toFloatArray()
    }
}

interface AudioDecoder {
    suspend fun decode(source: File, output: File, maxSeconds: Int = 14400, progress: (Float)->Unit = {}): PcmFile
}
/** MediaCodec output is downmixed and resampled to OmniVoice's 24 kHz mono. */
class AndroidAudioDecoder: AudioDecoder {
    override suspend fun decode(source: File, output: File, maxSeconds: Int, progress: (Float)->Unit): PcmFile = withContext(Dispatchers.IO) {
        val extractor=MediaExtractor()
        var codec: MediaCodec?=null
        var started=false
        output.parentFile?.mkdirs()
        try {
            extractor.setDataSource(source.absolutePath)
            val track=(0 until extractor.trackCount).firstOrNull {extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true}
                ?: throw ClientFailure("mobile.simple.media_failed")
            extractor.selectTrack(track)
            val format=extractor.getTrackFormat(track)
            val durationUs=(0 until extractor.trackCount).map {extractor.getTrackFormat(it)}.mapNotNull {
                if(it.containsKey(MediaFormat.KEY_DURATION)) it.getLong(MediaFormat.KEY_DURATION) else null
            }.maxOrNull() ?: 0L
            if(durationUs>maxSeconds*1_000_000L) throw ClientFailure("mobile.simple.audio_long")
            if(output.parentFile!!.usableSpace<maxOf(16_000_000L,durationUs/1_000_000*96000+16_000_000)) throw ClientFailure("mobile.local.storage_required")
            if(format.getString(MediaFormat.KEY_MIME)=="audio/raw") {
                val result=decodeRaw(extractor,format,output,maxSeconds,durationUs,progress)
                return@withContext result
            }
            val decoder=MediaCodec.createDecoderByType(requireNotNull(format.getString(MediaFormat.KEY_MIME)))
            codec=decoder;decoder.configure(format,null,null,0);decoder.start();started=true
            var channels=format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            var rate=format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            var encoding=AudioFormat.ENCODING_PCM_16BIT
            var resampler: MonoResampler?=null
            var inputDone=false;var outputDone=false;var written=0L
            val info=MediaCodec.BufferInfo()
            FileOutputStream(output).use {out ->
                while(!outputDone) {
                    currentCoroutineContext().ensureActive()
                    if(!inputDone) {
                        val index=decoder.dequeueInputBuffer(10000)
                        if(index>=0) {
                            val buffer=requireNotNull(decoder.getInputBuffer(index));buffer.clear()
                            val count=extractor.readSampleData(buffer,0)
                            if(count<0) {decoder.queueInputBuffer(index,0,0,0,MediaCodec.BUFFER_FLAG_END_OF_STREAM);inputDone=true}
                            else {decoder.queueInputBuffer(index,0,count,extractor.sampleTime,0);extractor.advance()}
                        }
                    }
                    when(val index=decoder.dequeueOutputBuffer(info,10000)) {
                        MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                            val actual=decoder.outputFormat
                            val newRate=actual.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                            if(resampler!=null&&newRate!=rate) throw ClientFailure("mobile.simple.media_failed")
                            rate=newRate;channels=actual.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                            if(actual.containsKey(MediaFormat.KEY_PCM_ENCODING)) encoding=actual.getInteger(MediaFormat.KEY_PCM_ENCODING)
                            if(encoding !in setOf(AudioFormat.ENCODING_PCM_16BIT,AudioFormat.ENCODING_PCM_FLOAT)) throw ClientFailure("mobile.simple.media_failed")
                        }
                        else -> if(index>=0) {
                            try {
                                if(info.size>0) {
                                    val buffer=requireNotNull(decoder.getOutputBuffer(index)).order(ByteOrder.LITTLE_ENDIAN)
                                    buffer.position(info.offset);buffer.limit(info.offset+info.size)
                                    val bytesPerSample=if(encoding==AudioFormat.ENCODING_PCM_FLOAT) 4 else 2
                                    require(channels in 1..32&&rate>0)
                                    val mono=FloatArray(info.size/(bytesPerSample*channels)) {
                                        var sum=0f
                                        repeat(channels) {sum+=if(encoding==AudioFormat.ENCODING_PCM_FLOAT) buffer.float else buffer.short/32768f}
                                        (sum/channels).takeIf {it.isFinite()} ?: 0f
                                    }
                                    val converter=resampler ?: MonoResampler(rate,24000).also {resampler=it}
                                    val samples=converter.accept(mono)
                                    written=appendDecodedPcm(out,samples,info.presentationTimeUs,written,maxSeconds)
                                    if(durationUs>0) progress((info.presentationTimeUs.toFloat()/durationUs).coerceIn(0f,1f))
                                }
                                outputDone=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                            } finally {decoder.releaseOutputBuffer(index,false)}
                        }
                    }
                }
                val tail=(durationUs*24000/1_000_000-written).coerceAtLeast(0)
                if(written+tail>maxSeconds*24000L) throw ClientFailure("mobile.simple.audio_long")
                appendSilence(out,tail);written+=tail
                out.fd.sync()
            }
            if(written<2400) throw ClientFailure("mobile.simple.media_failed")
            progress(1f);PcmFile(output)
        } catch(e:CancellationException) {output.delete();throw e}
        catch(e:ClientFailure) {output.delete();throw e}
        catch(e:Exception) {output.delete();throw ClientFailure("mobile.simple.media_failed")}
        finally {runCatching {if(started) codec?.stop()};runCatching {codec?.release()};runCatching {extractor.release()}}
    }
    private suspend fun appendSilence(out: FileOutputStream,frames: Long) {
        var remaining=frames*4
        val zeros=ByteArray(32768)
        while(remaining>0) {currentCoroutineContext().ensureActive();val count=minOf(zeros.size.toLong(),remaining).toInt();out.write(zeros,0,count);remaining-=count}
    }
    private suspend fun decodeRaw(extractor: MediaExtractor, format: MediaFormat, output: File, maxSeconds: Int, durationUs: Long, progress: (Float)->Unit): PcmFile {
        val channels=format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
        val rate=format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
        val encoding=if(format.containsKey(MediaFormat.KEY_PCM_ENCODING)) format.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
        require(channels in 1..32&&rate>0&&encoding in setOf(AudioFormat.ENCODING_PCM_16BIT,AudioFormat.ENCODING_PCM_FLOAT))
        val converter=MonoResampler(rate,24000)
        var written=0L
        FileOutputStream(output).use {out ->
            while(extractor.sampleTrackIndex>=0) {
                currentCoroutineContext().ensureActive()
                val size=extractor.sampleSize
                require(size in 1..4_194_304)
                val buffer=ByteBuffer.allocate(size.toInt()).order(ByteOrder.LITTLE_ENDIAN)
                val count=extractor.readSampleData(buffer,0)
                if(count<0) break
                buffer.position(0);buffer.limit(count)
                val mono=FloatArray(count/((if(encoding==AudioFormat.ENCODING_PCM_FLOAT) 4 else 2)*channels)) {
                    var sum=0f;repeat(channels) {sum+=if(encoding==AudioFormat.ENCODING_PCM_FLOAT) buffer.float else buffer.short/32768f}
                    (sum/channels).takeIf {it.isFinite()} ?: 0f
                }
                written=appendDecodedPcm(out,converter.accept(mono),extractor.sampleTime,written,maxSeconds)
                if(durationUs>0) progress((extractor.sampleTime.toFloat()/durationUs).coerceIn(0f,1f))
                extractor.advance()
            }
            val tail=(durationUs*24000/1_000_000-written).coerceAtLeast(0)
            if(written+tail>maxSeconds*24000L) throw ClientFailure("mobile.simple.audio_long")
            appendSilence(out,tail);written+=tail
            out.fd.sync()
        }
        if(written<2400) throw ClientFailure("mobile.simple.media_failed")
        progress(1f);return PcmFile(output)
    }

}

/** Write one decoded buffer at its container timestamp. */
internal suspend fun appendDecodedPcm(out: FileOutputStream,samples: FloatArray,timeUs: Long,written: Long,maxSeconds: Int): Long {
    val shift=timelineShift(timeUs,written)
    val gap=shift.coerceAtLeast(0)
    val skip=minOf((-shift).coerceAtLeast(0),samples.size.toLong()).toInt()
    val end=written+gap+samples.size-skip
    if(end>maxSeconds*24000L) throw ClientFailure("mobile.simple.audio_long")
    var remaining=gap*4
    val zeros=ByteArray(32768)
    while(remaining>0) {currentCoroutineContext().ensureActive();val count=minOf(zeros.size.toLong(),remaining).toInt();out.write(zeros,0,count);remaining-=count}
    val bytes=ByteBuffer.allocate((samples.size-skip)*4).order(ByteOrder.LITTLE_ENDIAN)
    for(i in skip until samples.size) bytes.putFloat(samples[i])
    out.write(bytes.array())
    return end
}

/**
 * Frames of 24 kHz silence to insert (positive) or leading frames to drop (negative) so a decoded audio buffer
 * stamped [sampleTimeUs] lands at its container time given [written] frames already output.
 * Jitter under 10 ms is ignored; a missing timestamp changes nothing.
 */
internal fun timelineShift(sampleTimeUs: Long,written: Long): Long {
    if(sampleTimeUs<0) return 0
    val delta=sampleTimeUs*24000/1_000_000-written
    return if(kotlin.math.abs(delta)<240) 0 else delta
}

/** Real recordings have no synthetic marker; generated audio uses markSynthetic. */
internal fun writeReferenceWave(audio: LocalAudio, file: File) {
    val header=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
    header.put("RIFF".toByteArray());header.putInt(36+audio.samples.size*2);header.put("WAVEfmt ".toByteArray());header.putInt(16)
    header.putShort(1);header.putShort(1);header.putInt(audio.sampleRate);header.putInt(audio.sampleRate*2);header.putShort(2);header.putShort(16)
    header.put("data".toByteArray());header.putInt(audio.samples.size*2)
    FileOutputStream(file).use {out ->
        out.write(header.array());val bytes=ByteBuffer.allocate(audio.samples.size*2).order(ByteOrder.LITTLE_ENDIAN)
        audio.samples.forEach {bytes.putShort((it.coerceIn(-1f,1f)*32767).toInt().toShort())};out.write(bytes.array());out.fd.sync()
    }
}
