package com.voicestudio.mobile.features.dictate

import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PhoneDictationTest {
    private val model=PhoneModel("stream","asr-stream","sherpa-onnx","test/model","a".repeat(40),emptyList(),0,listOf("en"),2.0,
        ModelLicence("MIT","commercial-ok",false,"holder","holder","b".repeat(64)),JsonObject(emptyMap()))
    private open class Engine : LocalEngine {
        override suspend fun synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,language: String?,progress: (Float)->Unit): LocalAudio = error("unused")
        override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio = error("unused")
        override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit): String = error("unused")
        override suspend fun translate(model: PhoneModel,text: String,targetLanguage: String,progress: (Float)->Unit): String = error("unused")
        override fun streamTranscribe(model: PhoneModel,audio: Flow<LocalAudio>) = flow {
            audio.collect {assertEquals(16000,it.sampleRate);assertEquals(2,it.samples.size);emit(Transcript("partial",false,0f))}
            emit(Transcript("complete",true,1f))
        }
    }
    @Test fun microphoneEndsGracefullyAndKeepsFinalTranscript() = runTest {
        val failures=mutableListOf<Throwable>()
        val saved=mutableListOf<String>()
        val controller=PhoneDictation(Engine(),this,{{error ->failures+=error}},DictationAudioSource {_,send ->send(byteArrayOf(0,0,0,64),4)},completed={saved+=it})
        controller.start(model);advanceUntilIdle()
        assertEquals("complete",controller.text.value);assertFalse(controller.active.value);assertTrue(failures.isEmpty())
        controller.close();advanceUntilIdle();assertEquals(listOf("complete"),saved)
    }
    @Test fun lifecycleCloseCancelsCaptureAndReleasesSource() = runTest {
        var released=false
        val source=DictationAudioSource {_,_ ->try {awaitCancellation()} finally {released=true}}
        val controller=PhoneDictation(Engine(),this,{{error ->throw error}},source)
        controller.start(model);runCurrent();assertTrue(controller.active.value)
        controller.close();advanceUntilIdle();assertTrue(released);assertFalse(controller.active.value)
    }
    @Test fun permissionOwnerSurvivesLocalStartAndExplicitLaterStartIsFresh() = runTest {
        for (original in listOf<String?>(null,"grant-a")) {
            var current=original
            var observations=0
            val reports=mutableListOf<String>()
            val feedback=mutableListOf<String?>()
            val observe:()->(Throwable)->Unit={
                observations++
                val owner=current
                {_:Throwable ->
                    feedback+=owner
                    if(owner!=null && owner==current) reports+=owner
                }
            }
            val originalFailure=observe()
            current="grant-b"
            val engine=object:Engine() {
                override fun streamTranscribe(model:PhoneModel,audio:Flow<LocalAudio>):Flow<Transcript> = flow {
                    throw com.voicestudio.mobile.network.ClientFailure("mobile.local.runtime_failed")
                }
            }
            val controller=PhoneDictation(engine,this,observe,DictationAudioSource {_,_ ->})
            try {
                controller.start(model,originalFailure);advanceUntilIdle()
                assertEquals(1,observations)
                assertEquals(listOf(original),feedback)
                assertTrue(reports.isEmpty())
                assertFalse(controller.active.value)
                controller.start(model);advanceUntilIdle()
                assertEquals(2,observations)
                assertEquals(listOf(original,"grant-b"),feedback)
                assertEquals(listOf("grant-b"),reports)
            } finally {controller.close()}
        }
    }

}
