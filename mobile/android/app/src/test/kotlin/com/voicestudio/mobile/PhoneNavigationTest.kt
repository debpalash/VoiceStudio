package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.local.ModelCatalog
import kotlinx.coroutines.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PhoneNavigationTest {
    @get:Rule val compose=createComposeRule()
    @Test fun allTabsAndModelManagementWorkWithoutPairing() = runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val vault=object: CredentialVault {
            override fun read(host: String): Secret? = null
            override fun write(host: String,secret: Secret?) = Unit
        }
        val store=SessionStore(app,vault)
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {StudioApp(vm)}
            withTimeout(15000) {while(vm.state.value.loading||vm.phone.state.value.loading) {shadowOf(Looper.getMainLooper()).idle();delay(10)}}
            compose.waitForIdle()
            assertFalse(vm.state.value.connected);assertNull(vm.api)
            fun tab(label: String) = compose.onAllNodesWithText(label).filter(hasClickAction()).onFirst()
            for(label in listOf("Speech","Voices","Dub","Dictate","Settings")) tab(label).assertExists()
            compose.onNodeWithText("Run on").assertDoesNotExist()
            tab("Voices").performClick();compose.onNodeWithText("Clone a voice").assertExists()
            tab("Dub").performClick();compose.onNodeWithText("Choose video or audio").assertExists()
            compose.onNodeWithText("Original speaker").assertExists()
            compose.onNode(hasText("Dub") and SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.Role,androidx.compose.ui.semantics.Role.Button)).assertIsNotEnabled()
            tab("Dictate").performClick();compose.onNodeWithContentDescription("Record").assertExists()
            tab("Settings").performClick();compose.onNodeWithText("Connect").assertExists()
            compose.onNodeWithText("Appearance").assertExists()
            compose.onNodeWithText("Models").performClick()
            compose.onNodeWithText("Mobile data").assertExists()
            assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun downloadedModelAboveRecommendedRamStillOffersGenerationWithWarning()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val model=app.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models.first {it.capability=="tts"}}
        val directory=java.io.File(app.noBackupFilesDir,"models/${model.id}").apply {mkdirs()}
        // Sparse structural fixture: never passed to a native runtime. Installation
        // metadata can survive an update that raises the catalogue RAM recommendation.
        model.files.forEach {file ->java.io.File(directory,file.path).apply {parentFile!!.mkdirs()}.let {java.io.RandomAccessFile(it,"rw").use {out ->out.setLength(file.size)}}}
        java.io.File(directory,".verified").writeText(model.revision+model.files.joinToString {it.path+it.sha256+it.size})
        val runtime=com.voicestudio.mobile.local.PhoneRuntime.get(app)
        assertTrue("Sparse fixture must be structurally installed",runtime.downloads.installed(model))
        runtime.licence.record(model,true)
        val store=SessionStore(app,object:CredentialVault {override fun read(host:String):Secret?=null;override fun write(host:String,secret:Secret?) {}})
        store.update {Preferences(consent=false,runLocations=mapOf("speak" to "phone"),phoneModels=mapOf("speak" to model.id))}
        val vm=StudioViewModel(app,store)
        try {
            assertFalse(model.fits(vm.phone.ramBytes))
            compose.setContent {StudioApp(vm)}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();model.id in vm.phone.state.value.installed&&!vm.state.value.loading}
            compose.onNodeWithTag("home.speak").performScrollTo().performClick()
            val l=com.voicestudio.mobile.l10n.L10n.load(app,"en")
            compose.onNodeWithContentDescription(l.t("mobile.android.selection",mapOf("label" to l.t("mobile.local.model"),"value" to model.name)))
                .performScrollTo().performClick()
            compose.onNode(hasText(model.name) and hasClickAction() and hasAnyAncestor(isDialog())).assertIsEnabled().performClick()
            compose.onNodeWithText("Generate").assertExists()
            compose.onNode(hasSetTextAction()).performScrollTo().performTextInput("A short test.")
            compose.onNodeWithText("Generate").assertIsEnabled()
            compose.onNodeWithText("Try anyway").assertDoesNotExist()
            compose.onNodeWithText(l.t("mobile.local.ram_required",mapOf("ram" to model.minRamGb))).assertExists()
            assertNull(vm.phone.state.value.progress)
        } finally {vm.viewModelScope.cancel();directory.deleteRecursively()}
    }
    @Test fun shippedCatalogueHasEveryRuntimeAndExactLegalDisclosures() {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val catalogue=app.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText())}
        assertEquals(setOf("sherpa-onnx","omnivoice.cpp","llama.cpp"),catalogue.models.map {it.runtime}.toSet())
        assertEquals(setOf("tts","asr","asr-stream","clone","translate"),catalogue.models.map {it.capability}.toSet())
        for(model in catalogue.models) {
            assertTrue(model.licence.terms.isNotEmpty())
            if(model.licence.category!="commercial-ok") assertTrue(model.gated)
        }
    }
}
