package com.voicestudio.mobile

import android.app.Application
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Modifier
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.ThemePreferences
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.l10n.L10n
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],qualifiers="w320dp-h640dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class HomeContributorsTest {
    @get:Rule val compose=createComposeRule()
    private val app get()=ApplicationProvider.getApplicationContext<Application>()
    private val opened=mutableListOf<String>()
    private val links=object: UriHandler {override fun openUri(uri: String) {opened+=uri}}
    @Composable private fun Content(dark: Boolean=true,large: Boolean=false,cluster: Boolean=false,hero: Boolean=false,locale: String="en") {
        val density=LocalDensity.current
        CompositionLocalProvider(LocalL10n provides L10n.load(app,locale),LocalUriHandler provides links,
            LocalDensity provides Density(density.density,if(large) 2f else 1f)) {
            VoiceStudioTheme(ThemePreferences(mode=if(dark) "dark" else "light")) {
                if(hero) HomeHero(Modifier.padding(12.dp)) else if(cluster) ContributorCluster() else ContributorStrip()
            }
        }
    }
    @Test fun translatedAllButtonKeepsContributorTouchTargetsVisibleAtLargeTextSize() {
        compose.setContent {Content(large=true,locale="id")}
        val contributor=compose.onNodeWithTag("home.contributor.kevin9327")
        val visible=compose.onNodeWithTag("home.other-contributors").fetchSemanticsNode().boundsInRoot
        assertTrue(visible.width>=with(compose.density) {48.dp.toPx()})
        contributor.performClick()
        assertEquals("https://github.com/kevin9327",opened.last())
        compose.onNodeWithTag("home.contributor.all").performScrollTo().assertIsDisplayed().performClick()
        assertEquals("${HomeContributors.REPOSITORY}/graphs/contributors",opened.last())
    }
    @Test fun homeHeroKeepsTextAndPortraitsParallelAndCompact() {
        compose.setContent {Content(hero=true)}
        compose.onNodeWithText("VoiceStudio").assertExists()
        compose.onNodeWithText(L10n.load(app,"en").t("mobile.home.brought_by")).assertExists()
        for(login in listOf("kevin9327","psiberfunk","jaketame","rudycelekli")) compose.onNodeWithTag("home.contributor.$login").assertIsDisplayed()
        compose.onNodeWithText(L10n.load(app,"en").t("mobile.home.subtitle")).assertExists()
        val text=compose.onNodeWithTag("home.hero-text").fetchSemanticsNode().boundsInRoot
        val portraits=compose.onNodeWithTag("home.contributors").fetchSemanticsNode().boundsInRoot
        assertTrue(text.right<=portraits.left)
        assertTrue(text.top<portraits.bottom && portraits.top<text.bottom)
        val hero=compose.onNodeWithTag("home.hero").fetchSemanticsNode().boundsInRoot
        assertTrue(hero.height<=with(compose.density) {220.dp.toPx()})
        compose.onNodeWithTag("home.contributor.debpalash").assertIsDisplayed().assertHeightIsAtLeast(48.dp)
        compose.onNodeWithTag("home.contributor.velixio").assertIsDisplayed().assertHeightIsAtLeast(48.dp)
    }
    @Test fun homeStripShowsBothLocalCardsAndOnlyLinksOpenTheBrowser() {
        val l=L10n.load(app,"en")
        compose.setContent {Content(dark=false)}
        val expected=mapOf("debpalash" to listOf("https://palash.dev","https://github.com/debpalash","https://x.com/idebpalash"),
            "velixio" to listOf("https://shimul.me","https://github.com/velixio","https://x.com/shimuldn"))
        expected.forEach {(login,urls) ->
            val before=opened.toList()
            compose.onNodeWithTag("home.contributor.$login").assertHeightIsAtLeast(48.dp).assertWidthIsAtLeast(48.dp).performClick()
            compose.waitUntil(5000) {compose.onAllNodesWithTag("home.maintainer-card").fetchSemanticsNodes().isNotEmpty()}
            assertEquals(before,opened)
            compose.onNodeWithText(l.t(if(login=="debpalash") "homeUi.maintainerPalash" else "homeUi.maintainerShimul")).assertExists()
            compose.onNodeWithText(l.t(if(login=="debpalash") "homeUi.creatorCoreTeam" else "homeUi.coreTeam")).assertExists()
            listOf("website","github","x").forEachIndexed {index,kind ->
                compose.onNodeWithTag("home.maintainer-link.$kind").performScrollTo().assertHeightIsAtLeast(48.dp).performClick()
                assertEquals(urls[index],opened.last())
            }
            compose.onNodeWithText(l.t("common.close")).performScrollTo().performClick()
            compose.onNodeWithTag("home.maintainer-card").assertDoesNotExist()
        }
        compose.onNodeWithTag("home.contributor.kevin9327").assertHeightIsAtLeast(48.dp).performClick()
        assertEquals("https://github.com/kevin9327",opened.last())
        compose.onNodeWithTag("home.contributor.all").performScrollTo().assertHeightIsAtLeast(48.dp).performClick()
        assertEquals("${HomeContributors.REPOSITORY}/graphs/contributors",opened.last())
        for(name in listOf("contributor_github","contributor_x")) assertNotEquals(0,app.resources.getIdentifier(name,"drawable",app.packageName))
    }
    @Test fun clusterCardSurvivesRecreationAndLargeTextKeepsEveryLinkReachable() {
        val restoration=StateRestorationTester(compose)
        restoration.setContent {Content(large=true,cluster=true)}
        compose.onNodeWithTag("home.contributor.velixio").performClick()
        compose.waitUntil(5000) {compose.onAllNodesWithTag("home.maintainer-card").fetchSemanticsNodes().isNotEmpty()}
        restoration.emulateSavedInstanceStateRestore()
        compose.waitUntil(5000) {compose.onAllNodesWithTag("home.maintainer-card").fetchSemanticsNodes().isNotEmpty()}
        assertTrue(opened.isEmpty())
        for(kind in listOf("website","github","x")) compose.onNodeWithTag("home.maintainer-link.$kind")
            .performScrollTo().assertIsDisplayed().assertHeightIsAtLeast(48.dp).performClick()
        assertEquals(listOf("https://shimul.me","https://github.com/velixio","https://x.com/shimuldn"),opened)
        compose.onNodeWithText(L10n.load(app,"en").t("common.close")).performScrollTo().assertIsDisplayed().performClick()
        compose.onNodeWithTag("home.maintainer-card").assertDoesNotExist()
    }
}
