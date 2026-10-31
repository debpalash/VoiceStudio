package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import kotlinx.coroutines.*
import org.junit.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class FirstUseFlowsTest {
    @get:Rule val compose=createComposeRule()

    private suspend fun withPhone(ramBytes: Long = 16_000_000_000, check: (StudioViewModel)->Unit) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        shadowOf(app.getSystemService(android.app.ActivityManager::class.java)).setMemoryInfo(
            android.app.ActivityManager.MemoryInfo().apply {totalMem=ramBytes})
        // Acceptance may attempt a download, but these UI tests cannot contact model hosts.
        shadowOf(app.getSystemService(android.net.ConnectivityManager::class.java)).apply {
            clearAllNetworks();setActiveNetworkInfo(null)
        }
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {StudioApp(vm)}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.phone.state.value.loading}
            check(vm)
        } finally {vm.viewModelScope.cancel()}
    }

    @Test fun destinationsHaveVisibleTextAndHomeStartsCloningDirectly()=runBlocking {
        withPhone {vm ->
            for(label in listOf("Home","Voices","Dub","Dictate","Settings")) {
                compose.onAllNodes(hasText(label) and !hasClickAction(),useUnmergedTree=true).onLast().assertIsDisplayed()
            }
            compose.onNodeWithTag("home.voices").performScrollTo().performClick()
            compose.onNodeWithText("Voice name").assertExists()
            compose.onNodeWithText("Cancel").performScrollTo().performClick()
            compose.onNodeWithTag("home.speak").assertExists()
            Assert.assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
        }
    }

    @Test fun speechSetupRetainsTheDraftAndRequiresExplicitLicenceConsent()=runBlocking {
        withPhone {vm ->
            compose.onNodeWithTag("home.speak").performScrollTo().performClick()
            compose.onNodeWithText("Type what you want to hear…").performTextInput("My first speech draft.")
            compose.onNodeWithText("Generate").assertDoesNotExist()
            compose.onNodeWithText("Download").assertIsEnabled().performClick()
            compose.onNodeWithText("Accept").assertIsNotEnabled()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
            compose.onNodeWithText("Decline").performClick()
            compose.onNodeWithText("My first speech draft.").assertExists()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
        }
    }

    @Test fun dictationExplainsTheRequiredDownload()=runBlocking {
        withPhone {
            compose.onNode(hasText("Dictate") and hasClickAction()).performClick()
            compose.onNodeWithText("to use this phone offline.",substring=true).assertExists()
            compose.onNodeWithText("Tap to start").assertDoesNotExist()
            compose.onNodeWithText("Download").assertIsEnabled()
        }
    }
    @Test fun belowRecommendedRamCanChooseOmniAndTryWithoutBypassingLicenceConsent()=runBlocking {
        withPhone(ramBytes=7_512_518_656) {vm ->
            val omni=vm.phone.state.value.models.first {it.name=="OmniVoice"}
            Assert.assertFalse(omni.fits(vm.phone.ramBytes))
            val l=com.voicestudio.mobile.l10n.L10n.load(ApplicationProvider.getApplicationContext(),"en")
            compose.onNodeWithTag("home.speak").performScrollTo().performClick()
            compose.onNodeWithText("Model").performScrollTo().performClick()
            compose.onNode(hasSetTextAction() and hasAnyAncestor(isDialog())).performTextInput("OmniVoice")
            compose.onNode(hasText("OmniVoice") and !hasSetTextAction() and hasClickAction() and hasAnyAncestor(isDialog())).assertIsEnabled().performClick()
            compose.onNodeWithText(l.t("mobile.local.ram_required",mapOf("ram" to omni.minRamGb))).assertExists()
            compose.onNodeWithText("Try anyway").assertIsEnabled().performClick()
            compose.onNodeWithText("Accept").assertIsNotEnabled()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
            compose.onNodeWithText("Decline").performClick()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
            compose.onNodeWithText("Try anyway").performClick()
            compose.onNodeWithContentDescription("I have the rights this licence requires for my use.").performScrollTo().performClick()
            compose.onNodeWithText("Accept").assertIsEnabled().performClick()
            compose.waitUntil(15000) {
                shadowOf(Looper.getMainLooper()).idle()
                vm.phone.state.value.accepted(omni)&&vm.state.value.error?.key=="mobile.local.wifi_required"
            }
            // Reaching the offline network policy proves Try anyway invoked download
            // after recording consent, without a RAM rejection or external request.
            Assert.assertFalse(omni.id in vm.phone.state.value.installed)
        }
    }

    @Test fun installedDesignModelKeepsTheMemoryWarningBeforeGeneration()=runBlocking {
        withPhone(ramBytes=7_512_518_656) {vm ->
            val app=ApplicationProvider.getApplicationContext<Application>()
            val omni=vm.phone.state.value.models.first {it.supportsDesign}
            val directory=java.io.File(app.noBackupFilesDir,"models/${omni.id}").apply {mkdirs()}
            // Structural installation fixture only; this test never invokes inference.
            omni.files.forEach {file ->
                val target=java.io.File(directory,file.path).apply {parentFile!!.mkdirs()}
                java.io.RandomAccessFile(target,"rw").use {it.setLength(file.size)}
            }
            java.io.File(directory,".verified").writeText(omni.revision+omni.files.joinToString {it.path+it.sha256+it.size})
            try {
                vm.phone.licence.record(omni,true)
                runBlocking {vm.phone.refresh()}
                Assert.assertFalse(omni.fits(vm.phone.ramBytes))
                compose.onNodeWithTag("home.design").performScrollTo().performClick()
                compose.onNodeWithText("Generate").assertExists()
                compose.onNodeWithText("Try anyway").assertDoesNotExist()
                val l=com.voicestudio.mobile.l10n.L10n.load(app,"en")
                compose.onNodeWithText(l.t("mobile.local.ram_required",mapOf("ram" to omni.minRamGb))).assertExists()
                Assert.assertNull(vm.phone.state.value.progress)
            } finally {directory.deleteRecursively()}
        }
    }

    @Test fun installedCloneAndDubKeepAdviceWithCollapsedAdvancedControls()=runBlocking {
        withPhone(ramBytes=7_512_518_656) {vm ->
            val app=ApplicationProvider.getApplicationContext<Application>()
            val items=listOf("clone","asr","translate").map {capability ->vm.phone.state.value.models.first {it.capability==capability}}
            val directories=items.map {item ->
                val directory=java.io.File(app.noBackupFilesDir,"models/${item.id}").apply {mkdirs()}
                item.files.forEach {file ->
                    val target=java.io.File(directory,file.path).apply {parentFile!!.mkdirs()}
                    java.io.RandomAccessFile(target,"rw").use {it.setLength(file.size)}
                }
                java.io.File(directory,".verified").writeText(item.revision+item.files.joinToString {it.path+it.sha256+it.size})
                vm.phone.licence.record(item,true)
                directory
            }
            try {
                runBlocking {vm.phone.refresh()}
                val l=com.voicestudio.mobile.l10n.L10n.load(app,"en")
                val warning=l.t("mobile.local.ram_required",mapOf("ram" to items.first().minRamGb))
                compose.onNodeWithTag("home.voices").performScrollTo().performClick()
                compose.onAllNodesWithText(warning).assertCountEquals(1)
                compose.onNodeWithText("More").performScrollTo().performClick()
                compose.onAllNodesWithText(warning).assertCountEquals(1)
                compose.onNodeWithText("Cancel").performScrollTo().performClick()
                compose.onNodeWithTag("home.dub").performScrollTo().performClick()
                compose.onAllNodesWithText(warning).assertCountEquals(1)
                compose.onNodeWithText("More").performScrollTo().performClick()
                compose.onAllNodesWithText(warning).assertCountEquals(1)
                Assert.assertNull(vm.phone.state.value.progress)
            } finally {directories.forEach {it.deleteRecursively()}}
        }
    }

    @Test fun communityGalleryHasNamedVoicesWithoutAComputerOrModels()=runBlocking {
        withPhone {vm ->
            compose.onNodeWithTag("home.gallery").performScrollTo().performClick()
            compose.waitUntil(5000) {compose.onAllNodes(hasText("The Librarian")).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("The Librarian").assertExists()
            compose.onNodeWithText("Search voices").performTextInput("Thalia")
            compose.onNode(hasText("Thalia") and !hasSetTextAction()).assertExists()
            compose.onNodeWithText("CC-BY-4.0",substring=true).assertExists()
            Assert.assertNull(vm.api)
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
        }
    }

    @Test fun communityRecipeOpensRealDesignAndRecordedImportRequiresPermission()=runBlocking {
        withPhone {vm ->
            compose.onNodeWithTag("home.gallery").performScrollTo().performClick()
            compose.waitUntil(5000) {compose.onAllNodes(hasText("The Librarian")).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("Search voices").performTextInput("Thalia")
            compose.onNodeWithText("Use voice").performScrollTo().performClick()
            compose.onNodeWithText("Only clone voices you have permission to use").assertExists()
            compose.onNodeWithText("Cancel").performClick()
            Assert.assertTrue(vm.phone.state.value.voices.isEmpty())
            compose.onNodeWithText("Search voices").performTextClearance()
            compose.onNodeWithText("Search voices").performTextInput("The Librarian")
            compose.onNodeWithText("Use voice").performScrollTo().performClick()
            compose.onNodeWithText("The Librarian").assertExists()
            compose.onNodeWithText("female, middle-aged, low pitch, british accent").assertDoesNotExist() // recipe is parsed into editable traits
            compose.waitUntil(5000) {compose.onAllNodes(hasText("female")).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("female").assertExists()
            compose.onNodeWithText("The valley had been quiet for a hundred years, and tonight, for the first time, something stirred beneath the old stone bridge.").assertExists()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
        }
    }

    @Test fun galleryCanBrowseAndSelectPresetsBeforeDownloadingModels()=runBlocking {
        withPhone {vm ->
            compose.onNodeWithTag("home.gallery").performScrollTo().performClick()
            compose.onNodeWithText("Community").performClick()
            compose.onNodeWithText("This phone").performClick()
            compose.onNodeWithText("Kitten").assertExists()
            compose.onNodeWithTag("gallery.preset.kitten-mini-en-fp16.0").performScrollTo().performClick()
            compose.waitUntil(5000) {compose.onAllNodes(hasText("Type what you want to hear…") and hasSetTextAction()).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("Kitten").assertExists()
            compose.onNodeWithText("Download").assertIsEnabled()
            Assert.assertTrue(vm.phone.state.value.installed.isEmpty())
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
        }
    }

    @Test fun gallerySearchFindsTheVoiceAndRetainsItsModelSelection()=runBlocking {
        withPhone {vm ->
            compose.onNodeWithTag("home.gallery").performScrollTo().performClick()
            compose.onNodeWithText("Community").performClick()
            compose.onNodeWithText("This phone").performClick()
            compose.onNodeWithText("Search voices").performTextInput("Voice 103")
            compose.onNodeWithTag("gallery.preset.kokoro-multi-int8.102").performScrollTo().performClick()
            compose.waitUntil(5000) {compose.onAllNodes(hasText("Type what you want to hear…") and hasSetTextAction()).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("Kokoro").assertExists()
            compose.onNodeWithText("Voice 103").assertExists()
            compose.onNodeWithText("Download").assertIsEnabled()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
        }
    }
}
