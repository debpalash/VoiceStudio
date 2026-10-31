package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.l10n.L10n
import kotlinx.coroutines.*
import org.junit.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class UiPolishRegressionTest {
    @get:Rule val compose=createComposeRule()
    @Test fun tabsHaveOneDownloadCardAndNoRawModelLabelsOrToolbarGear()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {StudioApp(vm)}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.phone.state.value.loading}
            fun tab(name:String)=compose.onNode(hasText(name) and hasClickAction()).performClick()
            fun noIdentifiers() {
                vm.phone.state.value.models.forEach {model ->
                    compose.onNodeWithText(model.id).assertDoesNotExist()
                    compose.onNodeWithText(model.repo).assertDoesNotExist()
                    compose.onNodeWithText(model.licence.id).assertDoesNotExist()
                }
                compose.onNodeWithContentDescription("Models").assertDoesNotExist()
            }
            compose.onNodeWithTag("home.speak").performScrollTo().performClick()
            noIdentifiers()
            compose.onAllNodesWithText("Download").assertCountEquals(1)
            tab("Voices");noIdentifiers()
            compose.onNodeWithText("Clone a voice").performClick()
            compose.onAllNodesWithText("Download").assertCountEquals(1)
            tab("Dub");noIdentifiers()
            compose.onAllNodesWithText("Download").assertCountEquals(1)
            compose.onNodeWithText("Dub needs 3 downloads",substring=true).assertExists()
            tab("Dictate");noIdentifiers()
            compose.onAllNodesWithText("Download").assertCountEquals(1)
            compose.onNodeWithContentDescription("Copy").assertIsDisplayed()
            compose.onNodeWithContentDescription("Share").assertIsDisplayed()
            val copy=compose.onNodeWithContentDescription("Copy").fetchSemanticsNode().boundsInRoot
            val tabBar=compose.onNode(hasText("Settings") and hasClickAction()).fetchSemanticsNode().boundsInRoot
            Assert.assertTrue("Copy must clear the tab bar",copy.bottom<tabBar.top)
            tab("Settings")
            compose.onNodeWithText("Light palette").performScrollTo().performClick()
            Assert.assertTrue(compose.onAllNodesWithTag("palette-swatch",useUnmergedTree=true).fetchSemanticsNodes().isNotEmpty())
            compose.onAllNodesWithTag("choice-check",useUnmergedTree=true).assertCountEquals(1)
            Assert.assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun dubGetRequiresSeparateConsentAndDeclineStopsTheQueue()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        shadowOf(app.getSystemService(android.app.ActivityManager::class.java)).setMemoryInfo(
            android.app.ActivityManager.MemoryInfo().apply {totalMem=16_000_000_000})
        // The test cannot contact model hosts, even after acceptance.
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
            compose.setContent {androidx.compose.runtime.CompositionLocalProvider(LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {
                    val phone=vm.phone.state.collectAsStateWithLifecycle().value
                    com.voicestudio.mobile.features.models.MissingModelsCard(vm,"mobile.tabs.dub",
                        listOf("asr","translate","clone").mapNotNull {cap ->phone.models.firstOrNull {it.capability==cap}})
                }
            }}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.phone.state.value.loading}
            compose.onNodeWithText("Download").performClick()
            compose.onNodeWithText("Whisper Tiny").assertExists()
            compose.onNodeWithText("Accept").assertIsNotEnabled()
            Assert.assertTrue(vm.phone.downloads.progress.value.isEmpty())
            compose.onNodeWithContentDescription("I have the rights this licence requires for my use.").performScrollTo().performClick()
            compose.onNodeWithText("Accept").performClick()
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();vm.phone.state.value.receipts.values.any {it.lastOrNull()?.accepted==true}}
            compose.waitForIdle()
            compose.onNodeWithText("OmniVoice").assertExists()
            compose.onNodeWithText("Accept").assertIsNotEnabled()
            compose.onNodeWithText("Decline").performClick()
            compose.onNodeWithText("Accept").assertDoesNotExist()
            val clone=vm.phone.state.value.models.first {it.capability=="clone"}
            Assert.assertFalse(vm.phone.licence.accepted(clone))
            Assert.assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
        } finally {vm.viewModelScope.cancel()}
    }
    @Test fun pickerShowsVisibleSelectedMark() {
        val app=ApplicationProvider.getApplicationContext<Application>()
        compose.setContent {androidx.compose.runtime.CompositionLocalProvider(LocalL10n provides L10n.load(app,"en")) {
            VoiceStudioTheme {Picker("mobile.settings.mode","dark",listOf("dark" to "Dark","light" to "Light")) {}}
        }}
        compose.onNodeWithText("Mode").performClick()
        compose.onNode(hasText("Dark") and isSelected()).assertExists()
        compose.onAllNodesWithTag("choice-check",useUnmergedTree=true).assertCountEquals(1)
        val layouts=mutableListOf<androidx.compose.ui.text.TextLayoutResult>()
        compose.onNode(hasText("Dark") and hasAnyAncestor(isDialog()),useUnmergedTree=true)
            .performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) {it(layouts)}
        Assert.assertEquals(androidx.compose.ui.graphics.Color(com.voicestudio.mobile.designsystem.VSTheme.roles("default",true).getValue("text")),layouts.single().layoutInput.style.color)
    }
}
