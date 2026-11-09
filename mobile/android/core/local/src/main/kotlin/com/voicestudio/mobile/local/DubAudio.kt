package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.*

/** Pad short speech; compress longer speech with waveform overlap-add.
 * The search aligns waveform phase, preserving pitch instead of speeding PCM up. */
suspend fun fitTiming(audio: LocalAudio, frames: Int): LocalAudio = withContext(Dispatchers.Default) {
    require(frames>0&&audio.sampleRate==24000&&audio.samples.isNotEmpty()&&audio.samples.all {it.isFinite()})
    val source=audio.samples
    if(source.size<=frames) return@withContext LocalAudio(source.copyOf(frames),audio.sampleRate)
    val window=960;val hop=480;val search=240
    val output=FloatArray(frames)
    if(frames<window||source.size<window) {
        for(index in output.indices) {
            if(index%4096==0) currentCoroutineContext().ensureActive()
            val position=index.toDouble()*(source.size-1)/maxOf(1,frames-1)
            val a=position.toInt();val b=minOf(a+1,source.lastIndex)
            output[index]=source[a]+(source[b]-source[a])*(position-a).toFloat()
        }
        return@withContext LocalAudio(output,audio.sampleRate)
    }
    source.copyInto(output,0,0,window)
    var destination=hop
    var previous=0
    while(destination<frames) {
        currentCoroutineContext().ensureActive()
        val expected=(destination.toDouble()*(source.size-window)/maxOf(1,frames-window)).toInt().coerceAtMost(source.size-window)
        val overlap=minOf(hop,frames-destination)
        var best=expected;var bestScore=Double.NEGATIVE_INFINITY
        val lower=maxOf(previous+1,expected-search).coerceAtMost(source.size-window)
        val upper=minOf(source.size-window,expected+search)
        for(candidate in lower..upper step 4) {
            var dot=0.0;var energy=1e-12
            for(index in 0 until overlap step 4) {
                val sample=source[candidate+index].toDouble();dot+=output[destination+index]*sample;energy+=sample*sample
            }
            val score=dot/sqrt(energy)
            if(score>bestScore) {bestScore=score;best=candidate}
        }
        val count=minOf(window,frames-destination,source.size-best)
        for(index in 0 until count) {
            val weight=if(index<overlap) index.toFloat()/overlap else 1f
            output[destination+index]=output[destination+index]*(1-weight)+source[best+index]*weight
        }
        previous=best;destination+=hop
    }
    LocalAudio(output,audio.sampleRate)
}

/** Rebuilt from immutable segment files on resume, so a segment is never mixed twice. */
suspend fun mixTimeline(file: File, frames: Long, segments: List<Pair<Long,PcmFile>>, gains: List<Double> = emptyList()): PcmFile = withContext(Dispatchers.IO) {
    require(frames in 1..345_600_000L)
    file.parentFile!!.mkdirs();file.delete()
    RandomAccessFile(file,"rw").use {out ->
        out.setLength(frames*4)
        require(gains.isEmpty()||gains.size==segments.size)
        for((piece,value) in segments.withIndex()) {
            val (start,segment)=value
            val gain=if(gains.isEmpty()) 1.0 else gains[piece]
            require(gain.isFinite()&&gain in 0.0..2.0)
            if(gain==0.0) continue
            require(start>=0&&start<frames&&segment.sampleRate==24000)
            var offset=0L
            val count=minOf(segment.frames,frames-start)
            while(offset<count) {
                currentCoroutineContext().ensureActive()
                val size=minOf(8192,count-offset).toInt()
                val samples=segment.read(offset,size).samples
                val previous=ByteArray(size*4);out.seek((start+offset)*4);out.readFully(previous)
                val input=ByteBuffer.wrap(previous).order(ByteOrder.LITTLE_ENDIAN)
                val result=ByteBuffer.allocate(previous.size).order(ByteOrder.LITTLE_ENDIAN)
                samples.forEach {sample ->result.putFloat((input.float+sample*gain.toFloat()).coerceIn(-1f,1f))}
                out.seek((start+offset)*4);out.write(result.array());offset+=size
            }
        }
        out.fd.sync()
    }
    PcmFile(file)
}

/** Streaming provenance chokepoint for long generated tracks, without a huge array. */
suspend fun markSynthetic(audio: PcmFile, file: File) = withContext(Dispatchers.IO) {
    require(audio.frames>0&&audio.frames*2<Int.MAX_VALUE-128)
    val marker="VoiceStudio synthetic audio\u0000".toByteArray(Charsets.US_ASCII)
    val padded=marker.size+marker.size%2
    val dataBytes=(audio.frames*2).toInt()
    val header=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
    header.put("RIFF".toByteArray());header.putInt(36+dataBytes+20+padded);header.put("WAVEfmt ".toByteArray());header.putInt(16)
    header.putShort(1);header.putShort(1);header.putInt(audio.sampleRate);header.putInt(audio.sampleRate*2);header.putShort(2);header.putShort(16)
    header.put("data".toByteArray());header.putInt(dataBytes)
    file.parentFile!!.mkdirs()
    FileOutputStream(file).use {out ->
        out.write(header.array());var position=0L
        while(position<audio.frames) {
            currentCoroutineContext().ensureActive()
            val count=minOf(8192,audio.frames-position).toInt()
            val bytes=ByteBuffer.allocate(count*2).order(ByteOrder.LITTLE_ENDIAN)
            audio.read(position,count).samples.forEach {sample ->require(sample.isFinite());bytes.putShort((sample.coerceIn(-1f,1f)*32767).toInt().toShort())}
            out.write(bytes.array());position+=count
        }
        val tail=ByteBuffer.allocate(20+padded).order(ByteOrder.LITTLE_ENDIAN)
        tail.put("LIST".toByteArray());tail.putInt(12+padded);tail.put("INFOICMT".toByteArray());tail.putInt(marker.size);tail.put(marker)
        out.write(tail.array());out.fd.sync()
    }
}
