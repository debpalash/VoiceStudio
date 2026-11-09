package com.voicestudio.mobile

import android.app.ActivityManager
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import kotlin.math.PI
import kotlin.math.sin

@RunWith(AndroidJUnit4::class)
class PhoneDubInferenceTest {
    @Test fun realModelsDubAnAudioClipWithoutAComputer()=runBlocking {
        val args=InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("dubInferenceSmoke")=="true")
        val app=InstrumentationRegistry.getInstrumentation().targetContext
        val catalog=app.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText())}
        val tts=catalog.models.filter {it.capability=="tts"}.minBy {it.totalBytes}
        val models=listOf(catalog.models.filter {it.capability=="asr"}.minBy {it.totalBytes},catalog.models.first {it.capability=="translate"},catalog.models.first {it.capability=="clone"})
        val ram=ActivityManager.MemoryInfo().also {app.getSystemService(ActivityManager::class.java).getMemoryInfo(it)}.totalMem
        assumeTrue("Use a device with the declared model RAM",(models+tts).all {it.fits(ram)})
        val root=File(app.noBackupFilesDir,"dub-smoke").apply {mkdirs()}
        val licence=LicenceGate(File(root,"licences.json"),BuildConfig.VERSION_NAME)
        for(model in models+tts) if(model.gated) {
            assertEquals("Review the exact bundled terms first","true",args.getString("acceptModelLicencesForSmoke"));licence.record(model,true)
        }
        val downloads=ModelDownloads(File(root,"models"),licence,ram) {true}
        if(args.getString("downloadSmokeModels")=="true") for(model in models+tts) downloads.download(model,false)
        for(model in models+tts) assertTrue("Explicitly provision models first",downloads.installed(model))
        val engine=PhoneLocalEngine(downloads)
        val source=File(root,"source.wav")
        markSynthetic(engine.synthesize(tts,"The quick brown fox jumps over the lazy dog."),source)
        val jobs=PhoneDubStore(File(root,"jobs"))
        val request=jobs.create(source,"Device smoke",listOf(DubTarget("fr","French")),models,true)
        try {
            val result=PhoneDubPipeline(jobs,engine,authorize={models.forEach(licence::requireAccepted)}).run(request.id,models)
            assertTrue(result.done);assertEquals(1,result.outputs.size)
            assertTrue(result.segments.all {it.speech.start>=0&&it.speech.end>it.speech.start&&it.translations["fr"]?.isNotBlank()==true})
            // The translation must actually be French, not the source echoed back (the
            // prompt once named the source language "auto" and the model returned the input).
            val source=result.segments.joinToString(" ") {it.speech.text}.lowercase()
            val french=result.segments.mapNotNull {it.translations["fr"]}.joinToString(" ").lowercase()
            val words=Regex("[\\p{L}']+").findAll(french).map {it.value}.toSet()
            assertTrue("Not translated: $french",french!=source && setOf("le","la","les","des","du","un","une","et","est","par","pour","vous","comment").any {it in words})
            val output=jobs.file(request.id,result.outputs.single().file)
            val pcm=AndroidAudioDecoder().decode(output,File(root,"verification.pcm"))
            assertTrue(pcm.frames>2400)
            assertTrue(pcm.read(0,minOf(pcm.frames,24000*30L).toInt()).samples.any {kotlin.math.abs(it)>.001f})
            assertTrue(output.readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        } finally {jobs.delete(request.id);source.delete();File(root,"verification.pcm").delete()}
    }
    @Test fun platformCodecsMuxVideoAndSyntheticAudioWithOrientation()=runBlocking {
        val app=InstrumentationRegistry.getInstrumentation().targetContext
        val root=File(app.cacheDir,"codec-smoke-${java.util.UUID.randomUUID()}").apply {mkdirs()}
        try {
            val source=File(root,"video.mp4")
            withContext(Dispatchers.IO) {makeVideo(source)}
            val pcmFile=File(root,"track.pcm")
            writePcm(LocalAudio(FloatArray(24000) {(.3*sin(2*PI*440*it/24000)).toFloat()},24000),pcmFile)
            val pcm=PcmFile(pcmFile)
            val wave=File(root,"marked.wav");markSynthetic(pcm,wave)
            val output=AndroidDubMedia().finish(source,pcm,wave,File(root,"result"),false)
            val extractor=MediaExtractor()
            try {
                extractor.setDataSource(output.path)
                val types=(0 until extractor.trackCount).map {extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME).orEmpty()}
                assertTrue(types.any {it.startsWith("video/")});assertTrue(types.any {it.startsWith("audio/")});assertTrue(types.any {it.startsWith("application/")})
            } finally {extractor.release()}
            val metadata=MediaMetadataRetriever()
            try {
                metadata.setDataSource(output.path)
                assertEquals("90",metadata.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION))
                val duration=metadata.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)!!.toLong()
                assertTrue("Unexpected duration $duration",duration in 850..1200)
            } finally {metadata.release()}
            val decoded=AndroidAudioDecoder().decode(output,File(root,"decoded.pcm"))
            assertTrue(decoded.read(0,minOf(decoded.frames,24000).toInt()).samples.any {kotlin.math.abs(it)>.01f})
        } finally {root.deleteRecursively()}
    }
    private fun makeVideo(file: File) {
        val width=320;val height=240;val frames=30
        val codec=MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        val muxer=MediaMuxer(file.path,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        var codecStarted=false;var muxStarted=false
        try {
            val format=MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC,width,height)
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
            format.setInteger(MediaFormat.KEY_FRAME_RATE,30);format.setInteger(MediaFormat.KEY_BIT_RATE,200000);format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,1)
            codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();codecStarted=true
            muxer.setOrientationHint(90)
            var sent=0;var inputDone=false;var outputDone=false;var track=-1
            val info=MediaCodec.BufferInfo()
            while(!outputDone) {
                if(!inputDone) {
                    val index=codec.dequeueInputBuffer(10000)
                    if(index>=0) {
                        if(sent==frames) {codec.queueInputBuffer(index,0,0,sent*1_000_000L/30,MediaCodec.BUFFER_FLAG_END_OF_STREAM);inputDone=true}
                        else {
                            val image=codec.getInputImage(index)
                            if(image!=null) {
                                image.planes.forEachIndexed {planeIndex,plane ->
                                    val w=if(planeIndex==0) width else width/2;val h=if(planeIndex==0) height else height/2
                                    for(y in 0 until h) for(x in 0 until w) plane.buffer.put(y*plane.rowStride+x*plane.pixelStride,(if(planeIndex==0) 80+sent else 128).toByte())
                                }
                                image.close()
                            } else {
                                val buffer=requireNotNull(codec.getInputBuffer(index));buffer.clear()
                                repeat(width*height) {buffer.put((80+sent).toByte())};repeat(width*height/2) {buffer.put(128.toByte())}
                            }
                            codec.queueInputBuffer(index,0,width*height*3/2,sent*1_000_000L/30,0);sent++
                        }
                    }
                }
                when(val index=codec.dequeueOutputBuffer(info,10000)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {track=muxer.addTrack(codec.outputFormat);muxer.start();muxStarted=true}
                    else -> if(index>=0) {
                        try {
                            if(info.size>0&&info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0) {
                                val data=requireNotNull(codec.getOutputBuffer(index));data.position(info.offset);data.limit(info.offset+info.size);muxer.writeSampleData(track,data,info)
                            }
                            outputDone=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                        } finally {codec.releaseOutputBuffer(index,false)}
                    }
                }
            }
        } finally {try {if(codecStarted) codec.stop()} finally {codec.release();try {if(muxStarted) muxer.stop()} finally {muxer.release()}}}
    }
}
