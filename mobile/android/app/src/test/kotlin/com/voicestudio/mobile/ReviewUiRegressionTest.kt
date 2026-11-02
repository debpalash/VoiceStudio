package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.runtime.*
import androidx.compose.material3.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.features.connect.ConnectScreen
import com.voicestudio.mobile.features.speak.SpeakScreen
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class ReviewUiRegressionTest {
    @get:Rule val compose=createComposeRule()
    private val app get()=ApplicationProvider.getApplicationContext<Application>()
    private fun vm(): StudioViewModel {
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        runBlocking {store.update {Preferences(consent=false)}}
        return StudioViewModel(app,store)
    }
    private fun await(condition: ()->Boolean) {compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();condition()}}
    private fun content(block: @Composable ()->Unit) {
        compose.setContent {CompositionLocalProvider(LocalL10n provides L10n.load(app,"en")) {VoiceStudioTheme {block()}}}
    }
    @Test fun homeShowsRealEmptyLibrariesAndRoutesEveryCardOffline() {
        val vm=vm()
        var destination=""
        try {
            content {HomeScreen(vm) {destination=it}}
            await {!vm.phone.state.value.loading}
            compose.onNodeWithText("VoiceStudio").assertExists()
            for(route in listOf("speak","voices","dub","dictate","models","connect")) {
                compose.onNodeWithTag("home.$route").performScrollTo().performClick()
                assertEquals(if(route=="voices") "clone" else route,destination)
            }
            compose.onNodeWithText("Your generations will appear here.").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("No projects yet").performScrollTo().assertIsDisplayed()
            assertNull(vm.api)
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun languageSelectionOnlyAppliesConfirmedDraft() {
        var selected by mutableStateOf(emptyList<String>())
        content {MultiPicker("mobile.dub.target_languages",selected,listOf("fr" to "French")) {selected=it}}
        compose.onNodeWithText("Target languages").performClick()
        compose.onNodeWithText("French").performClick()
        assertTrue(selected.isEmpty())
        compose.onNodeWithText("Cancel").performClick()
        assertTrue(selected.isEmpty())
        compose.onNodeWithText("Target languages").performClick()
        compose.onNodeWithText("French").performClick()
        compose.onNodeWithText("Confirm").performClick()
        assertEquals(listOf("fr"),selected)
    }
    @Test fun successfulQrScanExposesAddressPinAndConnect() {
        shadowOf(app).grantPermissions(android.Manifest.permission.CAMERA)
        val vm=vm()
        try {
            content {ConnectScreen(vm,scanContent={ code,_,_ -> Button(onClick={code("http://192.168.1.5:8000/?pin=123456")}) {Text("Fixture QR")} })}
            compose.onNodeWithText("Scan QR code").performClick()
            compose.onNodeWithText("Fixture QR").performClick()
            compose.onNodeWithText("Server address").assertExists()
            compose.onNodeWithText("http://192.168.1.5:8000/").assertExists()
            compose.onNode(hasText("Connect") and hasClickAction()).performScrollTo().assertIsEnabled()
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun speechOffersAllEnginesAndRequiresSavedVoiceForReferenceOnlyModels() {
        shadowOf(app.getSystemService(android.app.ActivityManager::class.java)).setMemoryInfo(android.app.ActivityManager.MemoryInfo().apply {totalMem=16_000_000_000})
        val vm=vm()
        try {
            content {SpeakScreen(vm)}
            await {!vm.phone.state.value.loading&&vm.phone.state.value.models.isNotEmpty()}
            compose.waitUntil(5000) {compose.onAllNodesWithText("Kitten").fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithContentDescription("Model, currently Kitten").performScrollTo().performClick()
            for(name in listOf("Kitten","Kokoro","OmniVoice","Piper","Supertonic 3","Pocket TTS","ZipVoice")) {
                compose.onNodeWithText("Search").performTextReplacement(name)
                compose.onAllNodes(hasText(name) and hasClickAction() and !hasSetTextAction()).onLast().assertExists()
            }
            compose.onNodeWithText("Search").performTextReplacement("Pocket TTS")
            compose.onNode(hasText("Pocket TTS") and hasClickAction() and !hasSetTextAction()).performScrollTo().performClick()
            compose.onNodeWithText("Choose a saved voice to use this model. You can import one from Gallery.").performScrollTo().assertExists()
            assertTrue(vm.phone.state.value.installed.isEmpty())
            assertNull(vm.phone.state.value.progress)
            compose.onNodeWithText("Voice 1").assertDoesNotExist()
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun omniSpeechDoesNotOfferUnconditionedDefaultVoice() {
        shadowOf(app.getSystemService(android.app.ActivityManager::class.java)).setMemoryInfo(android.app.ActivityManager.MemoryInfo().apply {totalMem=16_000_000_000})
        val vm=vm()
        try {
            content {SpeakScreen(vm)}
            await {!vm.phone.state.value.loading&&vm.phone.state.value.models.isNotEmpty()}
            compose.waitUntil(5000) {compose.onAllNodesWithText("Kitten").fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithContentDescription("Model, currently Kitten").performScrollTo().performClick()
            compose.onNodeWithText("Search").performTextReplacement("OmniVoice")
            compose.onNode(hasText("OmniVoice") and hasClickAction() and !hasSetTextAction()).performClick()
            compose.onNodeWithText("Choose a saved voice to use this model. You can import one from Gallery.").performScrollTo().assertExists()
            compose.onNodeWithText("Default voice").assertDoesNotExist()
            compose.onNodeWithContentDescription("Voice, currently Choose a voice").assertExists()
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun modelSwitchDropsOutOfRangePresetImmediately() {
        shadowOf(app.getSystemService(android.app.ActivityManager::class.java)).setMemoryInfo(android.app.ActivityManager.MemoryInfo().apply {totalMem=16_000_000_000})
        val vm=vm()
        try {
            content {SpeakScreen(vm)}
            await {!vm.phone.state.value.loading&&vm.phone.state.value.models.isNotEmpty()}
            val kokoro=vm.phone.state.value.models.first {it.id.contains("kokoro")}
            val kitten=vm.phone.state.value.models.first {it.id.contains("kitten")}
            vm.preference {it.copy(phoneModels=it.phoneModels+("speak" to kokoro.id))}
            await {vm.preferences.value.phoneModels["speak"]==kokoro.id}
            // The picker must receive the screen's collected preference before
            // opening it; the view-model flow can update a frame earlier.
            compose.waitUntil(5000) {compose.onAllNodesWithText("Kokoro").fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithContentDescription("Voice, currently Voice 1").performScrollTo().performClick()
            compose.onNodeWithText("Search").performTextInput("103")
            compose.onNode(hasText("Voice 103") and !hasSetTextAction()).performClick()
            compose.onNodeWithText("Voice 103").assertExists()
            vm.preference {it.copy(phoneModels=it.phoneModels+("speak" to kitten.id))}
            await {vm.preferences.value.phoneModels["speak"]==kitten.id}
            // Preferences are collected by the screen before its selection-reset
            // effect renders the replacement preset; wait for that visible state.
            compose.waitUntil(5000) {compose.onAllNodesWithText("Voice 1").fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("Voice 1").assertExists()
            compose.onNodeWithText("Voice 103").assertDoesNotExist()
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun errorsKeepShortTitleAndRecoveryCause() {
        val vm=vm();val l=L10n.load(app,"en")
        try {
            compose.setContent {StudioApp(vm)}
            await {!vm.state.value.loading}
            vm.fail(com.voicestudio.mobile.network.ClientFailure("mobile.local.ram_required",mapOf("ram" to "6")))
            compose.onNodeWithText(l.t("mobile.simple.models_error")).assertExists()
            compose.onNodeWithText(l.t("mobile.local.ram_required",mapOf("ram" to "6"))).assertExists()
            vm.fail(com.voicestudio.mobile.network.ClientFailure("mobile.connect.error_pin"))
            compose.onNodeWithText(l.t("mobile.connect.error_pin")).assertExists()
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun bundledNativeNoticesCoverPinnedRuntimesAndSherpaDependencies() {
        val names=app.assets.list("licenses").orEmpty().toSet()
        val required=setOf("sherpa-onnx","onnxruntime","espeak-ng-for-piper","piper-phonemize","kaldi-native-fbank","kissfft",
            "kaldi-decoder","kaldifst","openfst","simple-sentencepiece","json","hclust-cpp","omnivoice.cpp","omnivoice-ggml","llama.cpp","llama-ggml","eigen")
        required.forEach {assertTrue("Missing notice: $it", "native-$it.txt" in names)}
        val corresponding=app.assets.open("licenses/native-Corresponding-source.txt").bufferedReader().use {it.readText()}
        assertTrue(corresponding.contains("GPL-3.0"));assertTrue(corresponding.contains("ed530aa113046142eb5115cf2fc9157854d0ffe1"))
    }
    @Test fun nativeBuildUsesOneFetchAndRecordingStopsUnderDecoderLimit() {
        val root=File(requireNotNull(System.getProperty("androidRoot")))
        val appBuild=File(root,"app/build.gradle.kts").readText()
        assertTrue(appBuild.contains("dependsOn(\":core:local:fetchRuntimes\")"))
        assertTrue(appBuild.contains("\"--skip-fetch\""))
        val recording=File(root,"features/src/main/kotlin/com/voicestudio/mobile/features/voices/VoicesScreen.kt").readText()
        assertTrue(recording.contains("seconds>=29"))
    }
}
