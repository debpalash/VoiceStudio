package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.test.runTest
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import java.io.File

internal open class FakeLocalEngine: LocalEngine {
    override suspend fun synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,language: String?,progress: (Float)->Unit)=LocalAudio(FloatArray(2400) {.1f},24000)
    override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit)=synthesize(model,text,0,1f,null,progress)
    override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)="Reference transcript"
    override fun streamTranscribe(model: PhoneModel,audio: Flow<LocalAudio>): Flow<Transcript> = emptyFlow()
    override suspend fun translate(model: PhoneModel,text: String,targetLanguage: String,progress: (Float)->Unit)="Translated text"
}
internal fun fixtureModel(capability: String="asr")=PhoneModel("fixture-$capability",capability,when(capability) {"clone"->"omnivoice.cpp";"translate"->"llama.cpp";else->"sherpa-onnx"},"org/model","a".repeat(40),emptyList(),1,listOf("en"),1.0,ModelLicence("mit","commercial-ok",false,"","","b".repeat(64)))
class PhoneVoicesTest {
    @get:Rule val temporary=TemporaryFolder()
    private val audio=LocalAudio(FloatArray(12000) {it/12000f},24000)
    private val decoder=object: AudioDecoder {
        override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {
            assertEquals(30,maxSeconds);writePcm(audio,output);return PcmFile(output)
        }
    }
    @Test fun voicePersistsReferenceTranscriptConsentRenameAndDelete()=runTest {
        val root=File(temporary.root,"voices");val store=PhoneVoiceStore(root)
        val voice=store.create(" My voice ",File("unused"),true,fixtureModel(),fixtureModel("clone"),FakeLocalEngine(),decoder)
        assertEquals("My voice",voice.name);assertEquals("Reference transcript",voice.transcript);assertTrue(voice.consentAt>0)
        assertArrayEquals(audio.samples,store.reference(voice).samples,0f)
        assertTrue(store.preview(voice).readBytes().take(4).toByteArray().contentEquals("RIFF".toByteArray()))
        val restarted=PhoneVoiceStore(root);assertEquals(listOf(voice),restarted.list())
        restarted.rename(voice,"Renamed");assertEquals("Renamed",store.list().single().name)
        restarted.delete(voice);assertTrue(store.list().isEmpty())
    }
    @Test fun declinedConsentCannotDecodeOrCreateAnything()=runTest {
        val root=File(temporary.root,"voices")
        try {PhoneVoiceStore(root).create("Name",File("unused"),false,fixtureModel(),fixtureModel("clone"),FakeLocalEngine(),decoder);fail()} catch(_:IllegalArgumentException) {}
        assertFalse(root.exists())
    }
    @Test fun failedOrCancelledTranscriptionNeverPublishesVoice()=runTest {
        val root=File(temporary.root,"voices");val store=PhoneVoiceStore(root)
        val waiting=CompletableDeferred<Unit>()
        val engine=object: FakeLocalEngine() {
            override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit): String {waiting.complete(Unit);awaitCancellation()}
        }
        val task=launch {store.create("Name",File("unused"),true,fixtureModel(),fixtureModel("clone"),engine,decoder)}
        waiting.await();task.cancelAndJoin();assertTrue(store.list().isEmpty());assertTrue(root.listFiles().orEmpty().isEmpty())
        val silent=object: FakeLocalEngine() {override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=" "}
        try {store.create("Name",File("unused"),true,fixtureModel(),fixtureModel("clone"),silent,decoder);fail()} catch(e:com.voicestudio.mobile.network.ClientFailure) {assertEquals("mobile.simple.no_speech",e.key)}
        assertTrue(store.list().isEmpty())
    }
    @Test fun oneUnreadableVoiceDoesNotHideHealthyVoices()=runTest {
        val root=File(temporary.root,"voices");val store=PhoneVoiceStore(root)
        val voice=store.create("Healthy",File("unused"),true,fixtureModel(),fixtureModel("clone"),FakeLocalEngine(),decoder)
        File(root,"broken/voice.json").apply {parentFile!!.mkdirs();writeText("not json")}
        File(root,"bad-metadata/voice.json").apply {parentFile!!.mkdirs();writeText(LocalJson.encodeToString(PhoneVoice.serializer(),voice.copy(name="")))}
        assertEquals(listOf(voice),store.list())
    }
    @Test fun pathsCannotEscapePrivateVoiceDirectory()=runTest {
        val store=PhoneVoiceStore(File(temporary.root,"voices"))
        try {store.reference(PhoneVoice("../escape","N","T",1,1));fail()} catch(_:IllegalArgumentException) {}
    }
    @Test fun cloneEngineIsRequiredAndFailedPreviewNeverPublishes()=runTest {
        val root=File(temporary.root,"voices");val store=PhoneVoiceStore(root);var clones=0
        val engine=object: FakeLocalEngine() {
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                assertEquals("clone",model.capability);assertEquals("Reference transcript",text);assertEquals(text,referenceText)
                clones++;if(clones==1) throw CancellationException("Interrupted preview")
                return LocalAudio(FloatArray(4800) {.4f},24000)
            }
        }
        try {store.create("Voice",File("unused"),true,fixtureModel(),fixtureModel(),engine,decoder);fail()} catch(_:IllegalArgumentException) {}
        assertEquals(0,clones);assertTrue(store.list().isEmpty())
        try {store.create("Voice",File("unused"),true,fixtureModel(),fixtureModel("clone"),engine,decoder);fail()} catch(_:CancellationException) {}
        assertEquals(1,clones);assertTrue(store.list().isEmpty());assertTrue(root.listFiles().orEmpty().isEmpty())
        val voice=store.create("Voice",File("unused"),true,fixtureModel(),fixtureModel("clone"),engine,decoder)
        assertEquals(2,clones);assertEquals("preview.wav",store.preview(voice).name);assertEquals(fixtureModel("clone").repo,voice.modelRepo)
        assertTrue(store.preview(voice).readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        assertFalse(File(root,voice.id+"/reference.wav").readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
    }
    @Test fun resamplingKeepsPhaseAcrossOddBufferSizes() {
        val input=FloatArray(44100) {kotlin.math.sin(it*.05).toFloat()}
        val once=MonoResampler(44100,24000).accept(input)
        val converter=MonoResampler(44100,24000)
        val chunks=input.toList().chunked(113).flatMap {converter.accept(it.toFloatArray()).toList()}.toFloatArray()
        assertEquals(24000,once.size);assertArrayEquals(once,chunks,.00001f)
    }
}
