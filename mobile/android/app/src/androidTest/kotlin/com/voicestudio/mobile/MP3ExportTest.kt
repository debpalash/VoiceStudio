package com.voicestudio.mobile

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import android.media.MediaExtractor
import android.media.MediaFormat
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import kotlin.math.*

/** Actual ART + source-built JNI + platform decoder; no downloaded model is needed. */
@RunWith(AndroidJUnit4::class)
class MP3ExportTest {
    private fun fixture(): Pair<File,File> {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val root=File(context.cacheDir,"mp3-tests-${UUID.randomUUID()}").apply {mkdirs()};val wave=File(root,"human.wav")
        val bytes=ByteBuffer.allocate(44+48000*2).order(ByteOrder.LITTLE_ENDIAN)
        bytes.put("RIFF".toByteArray());bytes.putInt(36+48000*2);bytes.put("WAVEfmt ".toByteArray());bytes.putInt(16)
        bytes.putShort(1);bytes.putShort(1);bytes.putInt(24000);bytes.putInt(48000);bytes.putShort(2);bytes.putShort(16)
        bytes.put("data".toByteArray());bytes.putInt(48000*2)
        repeat(48000) {bytes.putShort((sin(it*2*PI*220/24000)*10000).toInt().toShort())}
        wave.writeBytes(bytes.array());return root to wave
    }
    @Test fun offlineMp3IsPlayableWithExactDurationAndSourceProvenance()=runBlocking {
        val(root,wave)=fixture()
        try {
            for(synthetic in listOf(false,true)) {
                val file=File(root,if(synthetic) "synthetic.mp3" else "human-\uD83D\uDCDA.mp3")
                assertEquals(file,PhoneAudioExport.mp3(wave,file,synthetic))
                val bytes=file.readBytes().toString(Charsets.ISO_8859_1)
                assertTrue(bytes.startsWith("ID3"));assertEquals(synthetic,bytes.contains("VoiceStudio synthetic audio"))
                val reader=MediaExtractor()
                try {reader.setDataSource(file.path);val format=reader.getTrackFormat(0);assertEquals("audio/mpeg",format.getString(MediaFormat.KEY_MIME));assertEquals(24000,format.getInteger(MediaFormat.KEY_SAMPLE_RATE));assertEquals(1,format.getInteger(MediaFormat.KEY_CHANNEL_COUNT))} finally {reader.release()}
                val decoded=AndroidAudioDecoder().decode(file,File(root,"$synthetic.pcm"),5) {}
                assertTrue(decoded.frames>=45000);assertTrue(decoded.read(0,24000).samples.any {abs(it)>.1f})
                // Existing completed outputs are atomically replaceable.
                PhoneAudioExport.mp3(wave,file,synthetic)
            }
            val exported=BookExport.encode(wave,"mp3",synthetic=false)
            assertFalse(exported.readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
            assertEquals(wave,BookExport.encode(wave,"wav"))
        } finally {root.deleteRecursively()}
    }
    @Test fun cancellationNeverReplacesCompletedAudioOrLeavesScratch()=runBlocking {
        val(root,wave)=fixture();val output=File(root,"result.mp3");val previous="previous complete result".toByteArray();output.writeBytes(previous)
        val entered=CompletableDeferred<Unit>();val release=CompletableDeferred<Unit>()
        try {
            val work=async(Dispatchers.IO) {PhoneAudioExport.mp3(wave,output,progress={value->if(value>.2f) {entered.complete(Unit);runBlocking {release.await()}}})}
            withTimeout(15000) {entered.await()};work.cancel();release.complete(Unit)
            try {work.await();fail("Expected cancellation")} catch(_:CancellationException) {}
            assertArrayEquals(previous,output.readBytes());assertFalse(root.listFiles().orEmpty().any {it.name.startsWith(".mp3-")})
        } finally {release.complete(Unit);root.deleteRecursively()}
    }
}
