package com.voicestudio.mobile

import android.app.Activity
import android.app.ActivityManager
import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PhoneCloneUiTest {
    @get:Rule val compose=createAndroidComposeRule<ComponentActivity>()
    private val decodeGate=CompletableDeferred<Unit>()
    private val transcriptGate=CompletableDeferred<Unit>()
    private val cloneGate=CompletableDeferred<Unit>()
    private var cancelled=false
    private lateinit var vm: StudioViewModel
    private lateinit var installed: List<File>
    private val audio=LocalAudio(FloatArray(12000) {.1f},24000)

    @Before fun prepare()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        shadowOf(app.getSystemService(ActivityManager::class.java)).setMemoryInfo(
            ActivityManager.MemoryInfo().apply {totalMem=16_000_000_000L})
        val models=app.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText())}.models
        val selected=listOf("asr","clone").map {cap ->models.first {it.capability==cap}}
        val runtime=PhoneRuntime.get(app)
        installed=selected.map {model ->
            val directory=File(app.noBackupFilesDir,"models/${model.id}").apply {mkdirs()}
            // Structural fixtures only: no network requests or native inference.
            model.files.forEach {item ->
                val file=File(directory,item.path).apply {parentFile!!.mkdirs()}
                java.io.RandomAccessFile(file,"rw").use {it.setLength(item.size)}
            }
            File(directory,".verified").writeText(model.revision+model.files.joinToString {it.path+it.sha256+it.size})
            runtime.licence.record(model,true)
            directory
        }
        val engine=object:LocalEngine {
            override suspend fun transcribe(model:PhoneModel,audio:LocalAudio,progress:(Float)->Unit):String {
                progress(.5f);transcriptGate.await();return "Reference transcript"
            }
            override suspend fun clone(model:PhoneModel,text:String,reference:LocalAudio,referenceText:String,progress:(Float)->Unit):LocalAudio {
                assertEquals("Reference transcript",referenceText)
                progress(.3f)
                try {cloneGate.await()} catch(e:CancellationException) {cancelled=true;throw e}
                return audio
            }
            override suspend fun synthesize(model:PhoneModel,text:String,speaker:Int,speed:Float,language:String?,progress:(Float)->Unit):LocalAudio=error("Not used")
            override fun streamTranscribe(model:PhoneModel,audio:Flow<LocalAudio>):Flow<Transcript> = emptyFlow()
            override suspend fun translate(model:PhoneModel,text:String,targetLanguage:String,progress:(Float)->Unit):String=error("Not used")
        }
        val decoder=object:AudioDecoder {
            override suspend fun decode(source:File,output:File,maxSeconds:Int,progress:(Float)->Unit):PcmFile {
                assertTrue(source.isFile);assertEquals("selected recording",source.readText());assertEquals(30,maxSeconds)
                decodeGate.await();writePcm(audio,output);progress(1f);return PcmFile(output)
            }
        }
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences(consent=false,phoneModels=mapOf("clone" to selected[1].id,"clone-asr" to selected[0].id))}
        vm=StudioViewModel(app,store,engine,decoder)
        compose.setContent {StudioApp(vm)}
        await {!vm.state.value.loading&&!vm.phone.state.value.loading&&selected.all {vm.phone.state.value.canRun(it)}}
        compose.onNodeWithTag("home.voices").performScrollTo().performClick()
        compose.onNodeWithText("Voice name").performScrollTo().performTextInput("My clone")
        val l=com.voicestudio.mobile.l10n.L10n.load(app,"en")
        compose.onNodeWithContentDescription(l.t("mobile.simple.voice_consent")).performScrollTo().performClick()
        compose.onNodeWithText(l.t("mobile.voices.pick_file")).performScrollTo().performClick()
        val request=shadowOf(compose.activity).nextStartedActivityForResult
        val source=File(app.cacheDir,"selected.wav").apply {writeText("selected recording")}
        compose.runOnIdle {shadowOf(compose.activity).receiveResult(request.intent,Activity.RESULT_OK,Intent().setData(Uri.fromFile(source)))}
        await {compose.onAllNodesWithText(l.t("mobile.android.sample_ready")).fetchSemanticsNodes().isNotEmpty()}
        compose.onNodeWithText(l.t("mobile.android.sample_ready")).assertExists()
        compose.onNode(hasText("Clone a voice") and hasClickAction()).assertIsDisplayed().assertIsEnabled().performClick()
        await {vm.phone.state.value.progress==0f}
    }
    private fun await(predicate:()->Boolean) {compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();predicate()}}
    private fun assertProgress(value:Float) {
        compose.onNodeWithContentDescription("Progress").assertIsDisplayed().assert(
            SemanticsMatcher.expectValue(SemanticsProperties.ProgressBarRangeInfo,ProgressBarRangeInfo(value,0f..1f)))
        compose.onNodeWithText("Choose audio file").assertIsNotEnabled()
        compose.onNodeWithText("Record").assertIsNotEnabled()
        compose.onNode(hasText("Clone a voice") and hasClickAction()).assertIsNotEnabled()
        compose.onNodeWithText("Cancel").assertIsDisplayed()
    }
    @Test fun selectedRecordingShowsEveryStageAndPublishesAPlayableVoice() {
        assertProgress(0f)
        decodeGate.complete(Unit);await {vm.phone.state.value.progress==.375f};assertProgress(.375f)
        transcriptGate.complete(Unit);await {vm.phone.state.value.progress==.65f};assertProgress(.65f)
        cloneGate.complete(Unit);await {vm.phone.state.value.progress==null&&vm.phone.state.value.voices.any {it.name=="My clone"}}
        val voice=vm.phone.state.value.voices.single {it.name=="My clone"}
        val wav=vm.phone.voices.preview(voice).readBytes()
        assertEquals("RIFF",wav.take(4).toByteArray().toString(Charsets.US_ASCII))
        assertTrue(wav.toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        assertEquals("Reference transcript",voice.transcript)
        compose.onNodeWithText("My clone").assertExists()
        assertFalse(cancelled)
    }
    @Test fun cancelStopsInferenceAndNeverPublishesAPartialVoice() {
        decodeGate.complete(Unit);transcriptGate.complete(Unit);await {vm.phone.state.value.progress==.65f};assertProgress(.65f)
        compose.onNodeWithText("Cancel").performClick()
        await {cancelled&&vm.phone.state.value.progress==null}
        assertTrue(runBlocking {vm.phone.voices.list()}.isEmpty())
        val app=ApplicationProvider.getApplicationContext<Application>()
        assertTrue(File(app.noBackupFilesDir,"voices").listFiles().orEmpty().isEmpty())
        compose.onNodeWithTag("home.voices").assertExists()
    }
    @Test fun computerActivityShowsIndeterminateProgressAndReenablesInputsAfterwards() {
        vm.phone.cancelInference();await {vm.phone.state.value.progress==null}
        compose.runOnIdle {vm.run {awaitCancellation()}}
        await {vm.state.value.busy}
        compose.onNodeWithContentDescription("Progress").assertIsDisplayed().assert(
            SemanticsMatcher.expectValue(SemanticsProperties.ProgressBarRangeInfo,ProgressBarRangeInfo.Indeterminate))
        compose.onNodeWithText("Choose audio file").assertIsNotEnabled()
        compose.onNodeWithText("Record").assertIsNotEnabled()
        compose.onNodeWithText("Cancel").assertIsDisplayed()
        vm.cancel();await {!vm.state.value.busy}
        compose.onNodeWithContentDescription("Progress").assertDoesNotExist()
        compose.onNodeWithText("Choose audio file").assertIsEnabled()
        compose.onNodeWithText("Record").assertIsEnabled()
    }
    @After fun cleanup() {
        if(::vm.isInitialized) vm.viewModelScope.cancel()
        if(::installed.isInitialized) installed.forEach {it.deleteRecursively()}
    }
}
