package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.designsystem.ThemePreferences
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.designsystem.role
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.features.settings.*
import com.voicestudio.mobile.l10n.L10n
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],qualifiers="w320dp-h640dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class SupportPagesTest {
    @get:Rule val compose=createComposeRule()
    private val app get()=ApplicationProvider.getApplicationContext<Application>()
    private val opened=mutableListOf<String>()
    private val links=object:UriHandler {override fun openUri(uri:String) {opened+=uri}}

    private fun capture(name:String) {
        val directory=System.getenv("VOICESTUDIO_SUPPORT_SCREENSHOTS") ?: return
        val output=java.io.File(directory,"$name.png").apply {parentFile?.mkdirs()}
        // Robolectric has no compositor frame for PixelCopy; draw the native
        // graphics-backed view directly for these optional review artifacts.
        compose.runOnIdle {
            val activity=androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(androidx.test.runner.lifecycle.Stage.RESUMED).first()
            val view=activity.window.decorView
            val bitmap=android.graphics.Bitmap.createBitmap(view.width,view.height,android.graphics.Bitmap.Config.ARGB_8888)
            view.draw(android.graphics.Canvas(bitmap))
            output.outputStream().use {bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it)}
            bitmap.recycle()
        }
    }

    @Composable private fun Content(light:Boolean=false,large:Boolean=false,content:@Composable ()->Unit) {
        val density=LocalDensity.current
        CompositionLocalProvider(LocalL10n provides L10n.load(app,"en"),LocalUriHandler provides links,
            LocalDensity provides Density(density.density,if(large) 2f else 1f)) {
            VoiceStudioTheme(ThemePreferences(mode=if(light) "light" else "dark")) {
                Surface(Modifier.fillMaxSize(),color=role("canvas"),content=content)
            }
        }
    }

    @Test fun donationsRemainOptionalAndOpenOnlyTheChosenLinkAtLargeTextSize() {
        compose.setContent {Content(large=true) {DonateScreen()}}
        compose.waitUntil(5000) {compose.onAllNodesWithTag("support.donate.github").fetchSemanticsNodes().isNotEmpty()}
        capture("android-donate-large-text")
        assertTrue(opened.isEmpty())
        val expected=listOf("github" to "https://github.com/sponsors/debpalash","kofi" to "https://ko-fi.com/debpalash","paypal" to "https://paypal.me/palashCoder",
            "star" to "https://github.com/debpalash/VoiceStudio","discord" to "https://discord.gg/bzQavDfVV9")
        for((id,url) in expected) {
            compose.onNodeWithTag("support.donate.$id").performScrollTo().assertIsDisplayed().assertHeightIsAtLeast(48.dp).performClick()
            assertEquals(url,opened.last())
        }
        assertEquals(5,opened.size)
        compose.onNodeWithText(L10n.load(app,"en").t("mobile.support.gift_note")).assertExists()
    }

    @Test fun projectArtAndSponsorLabelsAreBundledAndLinksRequireATap() {
        val expected=listOf("opengtm" to "https://github.com/debpalash/OpenGTM","opal" to "https://github.com/debpalash/Opal",
            "supermetry" to "https://github.com/velixio/supermetry","spicrawl" to "https://spicrawl.com")
        compose.setContent {Content(light=true,large=true) {OtherAppsScreen()}}
        compose.waitUntil(5000) {expected.all {(id,_) ->
            compose.onAllNodesWithTag("support.artwork.$id",useUnmergedTree=true).fetchSemanticsNodes().isNotEmpty()
        }}
        capture("android-other-apps-large-text")
        assertTrue(opened.isEmpty())
        for((id,url) in expected) {
            compose.onNodeWithTag("support.artwork.$id",useUnmergedTree=true).assertExists()
            compose.onNodeWithTag("support.project.$id").performScrollTo().assertIsDisplayed().assertHeightIsAtLeast(48.dp).performClick()
            assertEquals(url,opened.last())
        }
        compose.onNodeWithText(L10n.load(app,"en").t("mobile.support.special_sponsor")).assertExists()
        assertEquals(4,opened.size)
    }

    @Test fun proExplainsDesktopScopeAndFreePhoneToolsWithoutPurchaseControls() {
        compose.setContent {Content(large=true) {ProScreen()}}
        capture("android-pro-large-text")
        val l=L10n.load(app,"en")
        compose.onNodeWithText(l.t("mobile.support.pro_scope")).assertExists()
        compose.onNodeWithText(l.t("proPage.free_note")).assertExists()
        compose.onNodeWithText(l.t("proPage.commercial_body")).assertExists()
        for(feature in listOf("automation","history","delivery","preflight")) {
            compose.onNodeWithTag("support.pro.$feature").performScrollTo().assertIsDisplayed()
        }
        assertTrue(compose.onAllNodes(hasClickAction()).fetchSemanticsNodes().isEmpty())
        assertTrue(opened.isEmpty())
    }

    @Test @Config(qualifiers="w900dp-h1000dp") fun projectCardsUseTwoColumnsOnTablets() {
        compose.setContent {Content {OtherAppsScreen()}}
        compose.waitUntil(5000) {compose.onAllNodesWithTag("support.project.opal").fetchSemanticsNodes().isNotEmpty()}
        capture("android-other-apps-tablet")
        val first=compose.onNodeWithTag("support.project.opengtm").fetchSemanticsNode().boundsInRoot
        val second=compose.onNodeWithTag("support.project.opal").fetchSemanticsNode().boundsInRoot
        assertEquals(first.top,second.top,1f)
        assertTrue(first.right<second.left)
        assertTrue(opened.isEmpty())
    }

    @Test fun settingsPagesNavigateOfflineAndReturnWithoutBecomingTabs()=runBlocking {
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {override fun read(host:String):Secret?=null;override fun write(host:String,secret:Secret?)=Unit})
        store.update {Preferences(consent=false,locale="en")}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {CompositionLocalProvider(LocalUriHandler provides links) {StudioApp(vm)}}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.phone.state.value.loading}
            compose.onAllNodesWithText("Settings").filter(hasClickAction()).onLast().performClick()
            for(route in listOf("donate","pro","other-apps")) {
                compose.onNodeWithTag("settings.$route").performScrollTo().assertHeightIsAtLeast(48.dp).performClick()
                compose.onNodeWithTag("support.$route").assertExists()
                assertFalse(isWorkspace(route))
                compose.onNodeWithContentDescription("Back").performClick()
                compose.onNodeWithTag("settings.$route").performScrollTo().assertExists()
            }
            assertNull(vm.api)
            assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
            assertTrue(opened.isEmpty())
        } finally {vm.viewModelScope.cancel()}
    }
}
