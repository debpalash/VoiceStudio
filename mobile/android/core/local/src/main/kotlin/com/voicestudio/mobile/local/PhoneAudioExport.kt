package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

internal object NativeMp3Bridge {
    init {System.loadLibrary("vs_mp3")}
    external fun create(path: ByteArray,frames: Long,synthetic: Boolean): Long
    external fun write(handle: Long,samples: FloatArray): Int
    external fun finish(handle: Long): Int
    external fun close(handle: Long)
}

/** Offline bounded export; human recordings remain human when synthetic is false. */
object PhoneAudioExport {
    suspend fun mp3(source: File,output: File,synthetic: Boolean=true,progress: (Float)->Unit={}): File=withContext(Dispatchers.IO) {
        require(source.canonicalFile!=output.canonicalFile)
        val scratch=File(output.parentFile,".mp3-${UUID.randomUUID()}").apply {check(mkdirs())}
        try {
            val audio=AndroidAudioDecoder().decode(source,File(scratch,"decoded.pcm"),14400) {progress(it*.2f)}
            encode(audio,File(scratch,"audio.mp3"),synthetic) {progress(.2f+it*.8f)}
            currentCoroutineContext().ensureActive()
            Files.move(File(scratch,"audio.mp3").toPath(),output.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
            output
        } finally {scratch.deleteRecursively()}
    }
    internal suspend fun encode(audio: PcmFile,pending: File,synthetic: Boolean,progress: (Float)->Unit={}) {
        require(audio.sampleRate==24000)
        val encoder=NativeMp3Bridge.create(pending.absolutePath.toByteArray(Charsets.UTF_8),audio.frames,synthetic)
        if(encoder==0L) throw ClientFailure("mobile.export.failed")
        try {
            var offset=0L
            while(offset<audio.frames) {
                currentCoroutineContext().ensureActive()
                val samples=audio.read(offset,minOf(8192L,audio.frames-offset).toInt()).samples
                if(NativeMp3Bridge.write(encoder,samples)!=0) throw ClientFailure("mobile.export.failed")
                offset+=samples.size;progress(offset.toFloat()/audio.frames)
            }
            currentCoroutineContext().ensureActive()
            if(NativeMp3Bridge.finish(encoder)!=0) throw ClientFailure("mobile.export.failed")
        } finally {NativeMp3Bridge.close(encoder)}
    }
}
