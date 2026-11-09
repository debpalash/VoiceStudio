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
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class ProductionNavigationTest {
    @get:Rule val compose=createComposeRule()
    @Test fun allProductionWorkspacesOpenWithoutAHostOrAutomaticDownloads()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {override fun read(host:String):Secret?=null;override fun write(host:String,secret:Secret?)=Unit})
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {StudioApp(vm)}
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.phone.state.value.loading}
            val strings=com.voicestudio.mobile.l10n.L10n.load(app,"en")
            val routes=listOf("transcriptions" to strings.t("transcriptions.title"),"handoff" to strings.t("mobile.handoff.title"),"design" to "Voice design","stories" to "Stories","audiobook" to "Audiobook","batch" to "Batch dubbing","gallery" to "Gallery","workflows" to "Workflows","calls" to "Calls","local-api" to "Phone speech API","tools" to "Tools")
            for((route,title) in routes) {
                compose.onNodeWithTag("home.$route").performScrollTo().performClick()
                compose.onAllNodesWithText(title).onFirst().assertExists()
                assertNull(vm.api)
                assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
                compose.onAllNodesWithText("Home").filter(hasClickAction()).onLast().performClick()
                compose.onNodeWithTag("home.speak").performScrollTo().assertExists()
            }
        } finally {vm.viewModelScope.cancel()}
    }
}
