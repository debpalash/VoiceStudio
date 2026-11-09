package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.Rule
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.*

class AudioProcessingTest {
    @get:Rule val temporary=TemporaryFolder()
    private fun document(samples: FloatArray,synthetic: Boolean=false): PhoneAudioDocument {
        val pcm=PcmFile(File(temporary.root,"source.pcm"));writePcm(LocalAudio(samples,24000),pcm.file)
        return PhoneAudioDocument(pcm,samples.size.toLong(),synthetic)
    }
    private fun samples(file: File): FloatArray {
        val bytes=file.readBytes();var offset=12
        while(offset+8<=bytes.size) {
            val count=ByteBuffer.wrap(bytes,offset+4,4).order(ByteOrder.LITTLE_ENDIAN).int
            require(count>=0&&count<=bytes.size-offset-8)
            if(String(bytes,offset,4,Charsets.US_ASCII)=="data") {
                val buffer=ByteBuffer.wrap(bytes,offset+8,count).order(ByteOrder.LITTLE_ENDIAN)
                return FloatArray(count/2) {buffer.short/32767f}
            };offset+=8+count+count%2
        };error("Missing PCM")
    }
    @Test fun trimFadesAndGainProcessRealSamplesAndKeepTheSource()=runTest {
        val input=document(FloatArray(96000) {.2f});val hash=sha256(input.pcm.file)
        val result=PhoneAudioProcessing.process(input,AudioEditOptions(start=.5,end=1.5,fadeIn=.1,fadeOut=.1,gainDB=20*log10(2.0)),temporary.root)
        val actual=samples(result.file)
        assertEquals(24000,actual.size);assertEquals(0f,actual.first(),0f);assertEquals(0f,actual.last(),0f)
        assertEquals(.4f,actual[6000],.00004f);assertEquals(actual[8191],actual[8192],.00004f)
        assertEquals(hash,sha256(input.pcm.file));assertFalse(result.synthetic);assertFalse(PhoneAudioProcessing.containsSyntheticMarker(result.file))
    }
    @Test fun normalizeMeasuresActualPeakAfterGainAndNeverAmplifiesSilence()=runTest {
        val input=document(FloatArray(24000) {.1f}.also {it[19000] = -.5f})
        val result=PhoneAudioProcessing.process(input,AudioEditOptions(gainDB=12.0,normalize=true),temporary.root);val actual=samples(result.file)
        assertEquals(10.0.pow(-2.0/20),actual.maxOf {abs(it)}.toDouble(),.00004)
        assertEquals(10.0.pow(-2.0/20)*.2,actual[100].toDouble(),.00004)
        val quiet=document(FloatArray(24000) {.0001f})
        val quietResult=PhoneAudioProcessing.process(quiet,AudioEditOptions(normalize=true),temporary.root)
        assertTrue(samples(quietResult.file).all {abs(it)<=.00012f})
    }
    @Test fun gateReleaseProtectsSpeechTailsAndAttenuatesQuietNoise()=runTest {
        val input=document(FloatArray(96000) {if(it<24000) .25f else .001f})
        val result=PhoneAudioProcessing.process(input,AudioEditOptions(noiseGate=true),temporary.root);val actual=samples(result.file)
        assertEquals(.25f,actual[12000],.00004f);assertTrue(actual[24500]>.0009f);assertEquals(0f,actual[90000],0f)
    }
    @Test fun processingKeepsGeneratedMarkersAndOrdinaryRecordingsUnmarked()=runTest {
        val input=document(FloatArray(24000) {.2f},true)
        val generated=PhoneAudioProcessing.process(input,AudioEditOptions(),temporary.root)
        assertTrue(generated.synthetic);assertTrue(PhoneAudioProcessing.containsSyntheticMarker(generated.file))
        val split=File(temporary.root,"split-marker").apply {writeBytes(ByteArray(65530)+"VoiceStudio synthetic audio".toByteArray())}
        assertTrue(PhoneAudioProcessing.containsSyntheticMarker(split))
        val natural=File(temporary.root,"natural.wav");PhoneAudioProcessing.writeNaturalWave(input.pcm,natural)
        assertFalse(PhoneAudioProcessing.containsSyntheticMarker(natural))
        val decoder=object: AudioDecoder {override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {writePcm(LocalAudio(FloatArray(24000) {.2f},24000),output);return PcmFile(output)}}
        assertTrue(PhoneAudioProcessing.open(generated.file,temporary.root,decoder).synthetic)
        assertFalse(PhoneAudioProcessing.open(natural,temporary.root,decoder).synthetic)
        assertEquals(256,PhoneAudioProcessing.open(natural,temporary.root,decoder).peaks.size)
    }
    @Test fun invalidEditsAndCancellationKeepSourceAndDoNotPublishPartialResults()=runTest {
        val input=document(FloatArray(240000) {.2f});val hash=sha256(input.pcm.file)
        try {PhoneAudioProcessing.process(input,AudioEditOptions(end=20.0),temporary.root);fail()} catch(e:ClientFailure) {assertEquals("mobile.processing.invalid",e.key)}
        val before=temporary.root.listFiles().orEmpty().map {it.name}.toSet()
        try {PhoneAudioProcessing.process(input,AudioEditOptions(),temporary.root) {if(it>.2f) throw CancellationException("cancel")};fail()} catch(_:CancellationException) {}
        assertEquals(hash,sha256(input.pcm.file));assertEquals(before,temporary.root.listFiles().orEmpty().map {it.name}.toSet())
    }
    @Test fun fullLengthNormalizationIncludesLastFrameDespiteTimestampRoundoff()=runTest {
        val input=document(FloatArray(3003).also {it[3002]=.5f})
        for(explicitEnd in listOf(false,true)) {
            val options=AudioEditOptions(normalize=true,end=if(explicitEnd) input.duration else null)
            val result=PhoneAudioProcessing.process(input,options,temporary.root);val actual=samples(result.file)
            assertEquals(3003,actual.size);assertEquals(10.0.pow(-2.0/20),actual.last().toDouble(),.00004)
        }
    }

}
