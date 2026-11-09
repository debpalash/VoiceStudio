package com.voicestudio.mobile.local

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Android's platform codecs only: no ffmpeg executable or network media source. */
class AndroidDubMedia: DubMedia {
    override suspend fun finish(source: File,audio: PcmFile,markedWave: File,output: File,audioOnly: Boolean,progress: (Float)->Unit): File = withContext(Dispatchers.IO) {
        val extractor=MediaExtractor()
        val hasVideo=try {
            extractor.setDataSource(source.absolutePath)
            (0 until extractor.trackCount).any {extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/")==true}
        } finally {extractor.release()}
        if(audioOnly||!hasVideo) {
            val result=File(output.path+".wav")
            Files.move(markedWave.toPath(),result.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
            progress(1f);return@withContext result
        }
        val encoded=File(output.path+".aac.m4a")
        val pending=File(output.path+".pending.mp4")
        try {
            encodeAac(audio,encoded) {progress(it*.6f)}
            mux(source,encoded,pending,audio.duration) {progress(.6f+it*.4f)}
            currentCoroutineContext().ensureActive()
            val result=File(output.path+".mp4")
            Files.move(pending.toPath(),result.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
            result
        } catch(e:CancellationException) {throw e}
        catch(e:ClientFailure) {throw e}
        catch(e:Exception) {throw ClientFailure("mobile.simple.media_failed")}
        finally {encoded.delete();pending.delete()}
    }
    private suspend fun encodeAac(audio: PcmFile,output: File,progress: (Float)->Unit) {
        val codec=MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
        var muxer: MediaMuxer?=null;var codecStarted=false;var muxStarted=false;var completed=false
        try {
            val format=MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC,audio.sampleRate,1)
            format.setInteger(MediaFormat.KEY_AAC_PROFILE,MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            format.setInteger(MediaFormat.KEY_BIT_RATE,96000)
            codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();codecStarted=true
            val writer=MediaMuxer(output.path,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);muxer=writer
            var track=-1;var inputDone=false;var outputDone=false;var sent=0L
            val info=MediaCodec.BufferInfo()
            while(!outputDone) {
                currentCoroutineContext().ensureActive()
                if(!inputDone) {
                    val index=codec.dequeueInputBuffer(10000)
                    if(index>=0) {
                        val buffer=requireNotNull(codec.getInputBuffer(index)).order(ByteOrder.LITTLE_ENDIAN);buffer.clear()
                        val count=minOf(buffer.remaining()/2L,audio.frames-sent).toInt()
                        val pts=sent*1_000_000/audio.sampleRate
                        if(count<=0) {codec.queueInputBuffer(index,0,0,pts,MediaCodec.BUFFER_FLAG_END_OF_STREAM);inputDone=true}
                        else {
                            audio.read(sent,count).samples.forEach {buffer.putShort((it.coerceIn(-1f,1f)*32767).toInt().toShort())}
                            codec.queueInputBuffer(index,0,count*2,pts,0);sent+=count;progress(sent.toFloat()/audio.frames)
                        }
                    }
                }
                when(val index=codec.dequeueOutputBuffer(info,10000)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        check(!muxStarted);track=writer.addTrack(codec.outputFormat);writer.start();muxStarted=true
                    }
                    else -> if(index>=0) {
                        try {
                            if(info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0&&info.size>0) {
                                check(muxStarted)
                                val buffer=requireNotNull(codec.getOutputBuffer(index));buffer.position(info.offset);buffer.limit(info.offset+info.size)
                                writer.writeSampleData(track,buffer,info)
                            }
                            outputDone=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                        } finally {codec.releaseOutputBuffer(index,false)}
                    }
                }
            }
            completed=true
        } finally {
            runCatching {if(codecStarted) codec.stop()};runCatching {codec.release()}
            try {if(muxStarted) {if(completed) muxer?.stop() else runCatching {muxer?.stop()}}} finally {runCatching {muxer?.release()}}
        }
    }
    private suspend fun mux(video: File,audio: File,output: File,duration: Double,progress: (Float)->Unit) {
        val picture=MediaExtractor();val soundtrack=MediaExtractor()
        var writer: MediaMuxer?=null;var started=false;var completed=false
        try {
            picture.setDataSource(video.path);soundtrack.setDataSource(audio.path)
            val videoTrack=(0 until picture.trackCount).first {picture.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/")==true}
            val audioTrack=(0 until soundtrack.trackCount).first {soundtrack.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true}
            picture.selectTrack(videoTrack);soundtrack.selectTrack(audioTrack)
            val muxer=MediaMuxer(output.path,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);writer=muxer
            val retriever=MediaMetadataRetriever()
            try {
                retriever.setDataSource(video.path)
                retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull()?.takeIf {it in listOf(0,90,180,270)}?.let(muxer::setOrientationHint)
            } finally {retriever.release()}
            val videoOutput=muxer.addTrack(picture.getTrackFormat(videoTrack))
            val audioOutput=muxer.addTrack(soundtrack.getTrackFormat(audioTrack))
            // MP4 metadata tracks (application/*) are supported from API 26.
            // This carries provenance after AAC encoding, where RIFF INFO is lost.
            val provenance=muxer.addTrack(MediaFormat().apply {setString(MediaFormat.KEY_MIME,"application/voicestudio.synthetic-audio")})
            muxer.start();started=true
            val marker="VoiceStudio synthetic audio".toByteArray(Charsets.US_ASCII)
            val markerInfo=MediaCodec.BufferInfo().apply {set(0,marker.size,0,0)}
            muxer.writeSampleData(provenance,ByteBuffer.wrap(marker),markerInfo)
            val streams=listOf(picture to videoOutput,soundtrack to audioOutput)
            var buffer=ByteBuffer.allocate(1_048_576)
            val info=MediaCodec.BufferInfo()
            var lastProgress=-1
            // Interleave timestamps instead of writing the entire video first.
            while(true) {
                currentCoroutineContext().ensureActive()
                val stream=streams.filter {it.first.sampleTrackIndex>=0}.minByOrNull {it.first.sampleTime} ?: break
                val reader=stream.first
                val size=reader.sampleSize
                if(size !in 1..67_108_864) throw ClientFailure("mobile.simple.media_failed")
                if(size>buffer.capacity()) buffer=ByteBuffer.allocate(size.toInt())
                buffer.clear();val count=reader.readSampleData(buffer,0)
                if(count<0) {reader.advance();continue}
                if(reader.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED!=0) throw ClientFailure("mobile.simple.media_failed")
                val flags=if(reader.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC!=0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
                info.set(0,count,reader.sampleTime.coerceAtLeast(0),flags)
                buffer.position(0);buffer.limit(count);muxer.writeSampleData(stream.second,buffer,info)
                val percent=(info.presentationTimeUs/maxOf(1.0,duration*1_000_000)*100).toInt().coerceIn(0,100)
                if(percent!=lastProgress) {lastProgress=percent;progress(percent/100f)}
                reader.advance()
            }
            completed=true
        } finally {
            try {if(started) {if(completed) writer?.stop() else runCatching {writer?.stop()}}}
            finally {runCatching {writer?.release()};runCatching {picture.release()};runCatching {soundtrack.release()}}
        }
    }
}
