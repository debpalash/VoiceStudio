package com.voicestudio.mobile

import android.Manifest
import android.app.Application
import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.core.app.ActivityOptionsCompat
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.LocalL10n
import com.voicestudio.mobile.features.permissionAction
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.network.ClientFailure
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],application=Application::class)
class PermissionRestorationTest {
    @get:Rule val compose=createComposeRule()
    private class Registry:ActivityResultRegistry() {
        var pending:Int?=null
        override fun <I,O> onLaunch(requestCode:Int,contract:ActivityResultContract<I,O>,input:I,options:ActivityOptionsCompat?) {
            pending=requestCode
        }
        fun deny() {dispatchResult(checkNotNull(pending),false);pending=null}
    }
    private fun denial(restore:Boolean) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val registry=Registry()
        val owner=object:ActivityResultRegistryOwner {override val activityResultRegistry=registry}
        val saved=StateRestorationTester(compose)
        var grant=0
        var observations=0
        val callbacks=mutableListOf<Int>()
        val feedback=mutableListOf<String>()
        saved.setContent {
            CompositionLocalProvider(LocalActivityResultRegistryOwner provides owner,LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {
                    val action=permissionAction(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",
                        failure={observations++;val original=grant;{error ->callbacks+=original;feedback+=(error as ClientFailure).key}},
                        restoreFailure={feedback+=(it as ClientFailure).key}) {fail("Denied permission must not run the action")}
                    Button(onClick=action) {Text("Request fixture permission")}
                }
            }
        }
        fun request() {
            compose.onNodeWithText("Request fixture permission").performClick()
            compose.onNodeWithText("Continue").performClick()
            compose.waitForIdle()
            assertNotNull(registry.pending)
        }
        request()
        if(restore) {saved.emulateSavedInstanceStateRestore();compose.waitForIdle()}
        compose.runOnIdle {grant=1;registry.deny()}
        compose.waitForIdle()
        assertEquals(listOf("mobile.permissions.denied"),feedback)
        assertEquals("Restored denial cannot observe a new grant",1,observations)
        assertEquals(if(restore) emptyList<Int>() else listOf(0),callbacks)
        // A new request after recreation still binds the current ordinary callback.
        request()
        compose.runOnIdle {registry.deny()}
        compose.waitForIdle()
        assertEquals(listOf("mobile.permissions.denied","mobile.permissions.denied"),feedback)
        assertEquals(if(restore) listOf(1) else listOf(0,1),callbacks)
        assertEquals(2,observations)
    }
    @Test fun restoredDenialShowsLocalFeedbackWithoutAcquiringLaterConsent()=denial(true)
    @Test fun ordinaryDenialKeepsTheRequestHandlerAndLaterRequestsCanBindAgain()=denial(false)
}
