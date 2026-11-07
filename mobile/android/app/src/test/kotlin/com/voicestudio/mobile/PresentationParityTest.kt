package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import kotlinx.coroutines.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],qualifiers="w360dp-h800dp")
class PresentationParityTest {
    @get:Rule val compose=createComposeRule()

    @Test fun phoneHomeKeepsTheFirstTwoTasksTogether()=withApp {
        compose.onNodeWithTag("home.speak").performScrollTo()
        val speak=compose.onNodeWithTag("home.speak").getUnclippedBoundsInRoot()
        val clone=compose.onNodeWithTag("home.voices").getUnclippedBoundsInRoot()
        assertEquals("Speech and cloning belong in the same phone grid row",speak.top.value,clone.top.value,1f)
        assertTrue("The second task must have its own column",clone.left>speak.left)
        compose.onNodeWithTag("home.speak").assertIsDisplayed()
        compose.onNodeWithTag("home.voices").assertIsDisplayed()
    }

    @Test fun homeShowsEveryTaskWithoutScrolling()=withApp {
        val dockTop=compose.onNode(hasText("Settings") and hasClickAction()).getUnclippedBoundsInRoot().top
        for(task in listOf("speak","voices","dub","dictate","models","connect")) {
            val bounds=compose.onNodeWithTag("home.$task").getUnclippedBoundsInRoot()
            assertTrue("Home task $task must be fully above the dock without scrolling: ${bounds.bottom} > $dockTop",bounds.bottom<=dockTop)
        }
    }

    @Test fun settingsKeepsItsDockSelectionInsideModels()=withApp {
        val tab=hasText("Settings") and SemanticsMatcher.expectValue(SemanticsProperties.Role,androidx.compose.ui.semantics.Role.Tab)
        compose.onNode(tab).performClick()
        compose.onNodeWithText("Models").performClick()
        compose.onNode(tab).assertIsSelected()
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithText("Connect").assertExists()
        compose.onNode(tab).assertIsSelected()
    }

    @Test fun workspaceHeadingDoesNotConsumeAFullDisplayLine()=withApp {
        compose.onNode(hasText("Settings") and hasClickAction()).performClick()
        val title=compose.onNode(hasText("Settings") and SemanticsMatcher.keyIsDefined(SemanticsProperties.Heading))
        val layouts=mutableListOf<androidx.compose.ui.text.TextLayoutResult>()
        title.performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) {it(layouts)}
        assertTrue("Workspace labels must use inline type rather than display type",layouts.single().layoutInput.style.fontSize.value<=20f)
    }

    private fun withApp(check:()->Unit) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        runBlocking {store.update {Preferences(consent=false)}}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {Box(Modifier.requiredSize(360.dp,800.dp)) {StudioApp(vm)}}
            compose.waitUntil(15_000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.phone.state.value.loading}
            check()
        } finally {vm.viewModelScope.cancel()}
    }
}
