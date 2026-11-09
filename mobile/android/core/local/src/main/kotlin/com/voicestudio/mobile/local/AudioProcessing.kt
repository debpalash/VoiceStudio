package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import kotlin.math.*

data class PhoneAudioDocument(val pcm: PcmFile,val frames: Long,val synthetic: Boolean,val peaks: List<Float> = emptyList()) {val duration get()=frames/24000.0}
data class AudioEditOptions(val start: Double=0.0,val end: Double?=null,val fadeIn: Double=0.0,val fadeOut: Double=0.0,val gainDB: Double=0.0,val normalize: Boolean=false,val targetDB: Double = -2.0,val noiseGate: Boolean=false,val gateDB: Double = -40.0)
data class AudioEditResult(val file: File,val duration: Double,val peak: Double,val synthetic: Boolean)
/** Two bounded passes analyze the actual signal and apply processing without replacing the source. */
object PhoneAudioProcessing {
    suspend fun open(source: File,workspace: File,decoder: AudioDecoder=AndroidAudioDecoder(),progress: (Float)->Unit = {}): PhoneAudioDocument = withContext(Dispatchers.IO) {
        workspace.mkdirs();val output=File(workspace,"${UUID.randomUUID()}.pcm")
        try {
            val pcm=decoder.decode(source,output,14400,progress);val frames=pcm.frames
            require(frames in 1..345600000)
            val synthetic=containsSyntheticMarker(source);val peaks=FloatArray(256)
            var offset=0L
            while(offset<frames) {ensureActive();val samples=pcm.read(offset,minOf(8192,frames-offset).toInt()).samples
                samples.forEachIndexed {index,value ->require(value.isFinite());val bin=minOf(255,((offset+index)*256/frames).toInt());peaks[bin]=max(peaks[bin],abs(value))};offset+=samples.size}
            PhoneAudioDocument(pcm,frames,synthetic,peaks.toList())
        } catch(e:Throwable) {output.delete();throw e}
    }
    suspend fun containsSyntheticMarker(source: File): Boolean = withContext(Dispatchers.IO) {
        val marker="VoiceStudio synthetic audio".toByteArray(Charsets.US_ASCII)
        source.inputStream().use {input ->
            val bytes=ByteArray(65536);var tail=ByteArray(0)
            while(true) {ensureActive();val count=input.read(bytes);if(count<0) return@withContext false
                val joined=tail+bytes.copyOf(count)
                if((0..joined.size-marker.size).any {start ->marker.indices.all {joined[start+it]==marker[it]}}) return@withContext true
                tail=joined.takeLast(marker.size-1).toByteArray()
            }
        }
        @Suppress("UNREACHABLE_CODE") false
    }
    suspend fun process(document: PhoneAudioDocument,options: AudioEditOptions,workspace: File,progress: (Float)->Unit = {}): AudioEditResult = withContext(Dispatchers.IO) {
        val end=options.end ?: document.duration
        if(document.frames !in 1..345600000||!options.start.isFinite()||!end.isFinite()||options.start<0||end>document.duration+.000001||end<=options.start||!options.fadeIn.isFinite()||!options.fadeOut.isFinite()||options.fadeIn<0||options.fadeOut<0||options.fadeIn>end-options.start||options.fadeOut>end-options.start||!options.gainDB.isFinite()||options.gainDB !in -24.0..24.0||!options.targetDB.isFinite()||options.targetDB !in -24.0..-1.0||!options.gateDB.isFinite()||options.gateDB !in -80.0..-10.0||document.pcm.frames!=document.frames) throw ClientFailure("mobile.processing.invalid")
        val first=(options.start*24000).toLong();val last=if(end>=document.duration) document.frames else minOf(document.frames,(end*24000).toLong());val count=last-first
        if(count<=0) throw ClientFailure("mobile.processing.invalid")
        val directory=File(workspace,"audio-edit-${UUID.randomUUID()}").apply {mkdirs()};val pcm=PcmFile(File(directory,"edited.pcm"));val output=File(directory,"edited.wav")
        try {
            var state=Processor(options,count);var peak=0.0;var offset=0L
            while(offset<count) {ensureActive();val samples=document.pcm.read(first+offset,minOf(8192,count-offset).toInt()).samples
                samples.forEachIndexed {index,value ->require(value.isFinite());peak=max(peak,abs(state.sample(value.toDouble(),offset+index)))};offset+=samples.size;progress((offset.toDouble()/count*.45).toFloat())}
            // The desktop −50 dBFS floor prevents normalization from turning silence into hiss.
            val factor=if(options.normalize&&peak>10.0.pow(-50.0/20)) 10.0.pow(options.targetDB/20)/peak else 1.0
            state=Processor(options,count);offset=0
            FileOutputStream(pcm.file).use {out ->while(offset<count) {ensureActive();val samples=document.pcm.read(first+offset,minOf(8192,count-offset).toInt()).samples
                val bytes=ByteBuffer.allocate(samples.size*4).order(ByteOrder.LITTLE_ENDIAN)
                samples.forEachIndexed {index,value ->bytes.putFloat((state.sample(value.toDouble(),offset+index)*factor).coerceIn(-1.0,1.0).toFloat())};out.write(bytes.array());offset+=samples.size;progress((.45+offset.toDouble()/count*.45).toFloat())};out.fd.sync()}
            ensureActive();if(document.synthetic) markSynthetic(pcm,output) else writeNaturalWave(pcm,output)
            ensureActive();pcm.file.delete();progress(1f)
            AudioEditResult(output,count/24000.0,minOf(1.0,peak*factor),document.synthetic)
        } catch(e:Throwable) {directory.deleteRecursively();throw e}
    }
    suspend fun writeNaturalWave(pcm: PcmFile,output: File) = withContext(Dispatchers.IO) {
        require(pcm.frames in 1..345600000)
        val header=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray());header.putInt((36+pcm.frames*2).toInt());header.put("WAVEfmt ".toByteArray());header.putInt(16);header.putShort(1);header.putShort(1);header.putInt(24000);header.putInt(48000);header.putShort(2);header.putShort(16);header.put("data".toByteArray());header.putInt((pcm.frames*2).toInt())
        FileOutputStream(output).use {out ->out.write(header.array());var offset=0L
            while(offset<pcm.frames) {ensureActive();val samples=pcm.read(offset,minOf(8192,pcm.frames-offset).toInt()).samples;val bytes=ByteBuffer.allocate(samples.size*2).order(ByteOrder.LITTLE_ENDIAN)
                samples.forEach {require(it.isFinite());bytes.putShort((it.coerceIn(-1f,1f)*32767).roundToInt().toShort())};out.write(bytes.array());offset+=samples.size};out.fd.sync()}
    }
    private class Processor(val options: AudioEditOptions,val count: Long) {
        val gain=10.0.pow(options.gainDB/20);val threshold=10.0.pow(options.gateDB/20);val fadeIn=(options.fadeIn*24000).toLong();val fadeOut=(options.fadeOut*24000).toLong();val attack=exp(-1.0/(.001*24000));val release=exp(-1.0/(.2*24000));var envelope=0.0
        fun sample(value: Double,index: Long): Double {
            var scale=gain
            if(options.noiseGate) {val level=abs(value);val coefficient=if(level>envelope) attack else release;envelope=coefficient*envelope+(1-coefficient)*level;scale*=if(envelope>=threshold) 1.0 else (envelope/threshold).pow(9)}
            if(fadeIn>0) scale*=minOf(1.0,index.toDouble()/maxOf(1,fadeIn-1));if(fadeOut>0) scale*=minOf(1.0,(count-1-index).toDouble()/maxOf(1,fadeOut-1));return value*scale
        }
    }
}
