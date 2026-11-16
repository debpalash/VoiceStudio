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
import com.voicestudio.mobile.features.permissionActionWithFailure
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
        fun allow() {dispatchResult(checkNotNull(pending),true);pending=null}
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

    private fun success(restore:Boolean) {
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
                    val action=permissionActionWithFailure(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",
                        failure={observations++;val original=grant;{error ->callbacks+=original;feedback+=(error as ClientFailure).key}},
                        restoreFailure={feedback+=(it as ClientFailure).key}) {failed ->
                            failed(ClientFailure("mobile.local.recording_failed"))
                        }
                    Button(onClick=action) {Text("Request fixture recording")}
                }
            }
        }
        fun request() {
            compose.onNodeWithText("Request fixture recording").performClick()
            compose.onNodeWithText("Continue").performClick()
            compose.waitForIdle()
            assertNotNull(registry.pending)
        }
        request()
        if(restore) {saved.emulateSavedInstanceStateRestore();compose.waitForIdle()}
        compose.runOnIdle {grant=1;registry.allow()}
        compose.waitForIdle()
        assertEquals("Recording must not observe consent again after permission returns",1,observations)
        assertEquals(if(restore) emptyList<Int>() else listOf(0),callbacks)
        assertEquals(listOf("mobile.local.recording_failed"),feedback)
        request();compose.runOnIdle {registry.allow()};compose.waitForIdle()
        assertEquals(if(restore) listOf(1) else listOf(0,1),callbacks)
        assertEquals(2,observations)
    }
    @Test fun grantedMicrophoneKeepsTheOriginalRequestOwner()=success(false)
    @Test fun restoredMicrophoneSuccessIsLocalAndFreshRequestsStillReport()=success(true)

    private fun rationaleOwner(initial:String?,replacement:String?,abandon:String?=null) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val registry=Registry()
        val owner=object:ActivityResultRegistryOwner {override val activityResultRegistry=registry}
        val saved=StateRestorationTester(compose)
        var grant=initial
        var observations=0
        val origins=mutableListOf<String?>()
        val reports=mutableListOf<String>()
        val feedback=mutableListOf<String>()
        saved.setContent {
            CompositionLocalProvider(LocalActivityResultRegistryOwner provides owner,LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {
                    val action=permissionActionWithFailure(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",
                        failure={
                            observations++
                            val original=grant;
                            {error ->
                                origins+=original
                                feedback+=(error as ClientFailure).key
                                if(original!=null && original==grant) reports+=original
                            }
                        },
                        restoreFailure={feedback+=(it as ClientFailure).key}) {failed ->
                            failed(ClientFailure("mobile.local.recording_failed"))
                        }
                    Button(onClick=action) {Text("Request rationale fixture")}
                }
            }
        }
        compose.onNodeWithText("Request rationale fixture").performClick()
        compose.onNodeWithText("Continue").assertExists()
        compose.runOnIdle {grant=replacement}
        if(abandon!=null) {
            when(abandon) {
                "cancel" -> compose.onNodeWithText("Cancel").performClick()
                "dismiss" -> compose.runOnIdle {checkNotNull(org.robolectric.shadows.ShadowDialog.getLatestDialog()).onBackPressed()}
                "restore" -> saved.emulateSavedInstanceStateRestore()
                else -> error("Unknown fixture action")
            }
            compose.waitForIdle()
            compose.onNodeWithText("Continue").assertDoesNotExist()
            assertNull(registry.pending)
            assertTrue(reports.isEmpty())
            assertTrue(feedback.isEmpty())
            assertEquals("Abandoning or restoring rationale cannot acquire another owner",1,observations)
            // Only a genuinely new action may bind the later grant.
            compose.onNodeWithText("Request rationale fixture").performClick()
        }
        compose.onNodeWithText("Continue").performClick()
        compose.waitForIdle()
        assertNotNull(registry.pending)
        compose.runOnIdle {registry.allow()}
        compose.waitForIdle()
        val expected=if(abandon==null) initial else replacement
        assertEquals(listOf(expected),origins)
        assertEquals(if(expected!=null && expected==replacement) listOf(expected) else emptyList<String>(),reports)
        assertEquals(listOf("mobile.local.recording_failed"),feedback)
        assertEquals(if(abandon==null) 1 else 2,observations)
    }

    @Test fun rationaleOpenedWhileOffCannotBorrowLaterOptIn()=rationaleOwner(null,"grant-b")
    @Test fun rationaleOpenedUnderOldGrantCannotBorrowReplacement()=rationaleOwner("grant-a","grant-b")
    @Test fun unchangedRationaleReportsUnderOriginalGrant()=rationaleOwner("grant-a","grant-a")
    @Test fun cancelledRationaleLeavesFreshActionEligible()=rationaleOwner("grant-a","grant-b","cancel")
    @Test fun dismissedRationaleLeavesFreshActionEligible()=rationaleOwner("grant-a","grant-b","dismiss")
    @Test fun restoredRationaleNeedsNewActionToObserveLaterConsent()=rationaleOwner("grant-a","grant-b","restore")

    @Test fun alreadyGrantedPermissionKeepsActionOwnerAndFreshActionsObserveAgain() {
        val app=ApplicationProvider.getApplicationContext<Application>()
        org.robolectric.Shadows.shadowOf(app).grantPermissions(Manifest.permission.RECORD_AUDIO)
        val registry=Registry()
        val owner=object:ActivityResultRegistryOwner {override val activityResultRegistry=registry}
        var grant="grant-a"
        var observations=0
        var started:((Throwable)->Unit)?=null
        val origins=mutableListOf<String>()
        val reports=mutableListOf<String>()
        compose.setContent {
            CompositionLocalProvider(LocalActivityResultRegistryOwner provides owner,LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {
                    val action=permissionActionWithFailure(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",
                        failure={observations++;val original=grant;{_ ->origins+=original;if(original==grant) reports+=original}},
                        restoreFailure={fail("No restored request")}) {started=it}
                    Button(onClick=action) {Text("Granted fixture")}
                }
            }
        }
        compose.onNodeWithText("Granted fixture").performClick()
        compose.onNodeWithText("Continue").assertDoesNotExist()
        assertNull(registry.pending)
        compose.runOnIdle {grant="grant-b";checkNotNull(started)(ClientFailure("mobile.local.recording_failed"))}
        assertEquals(listOf("grant-a"),origins);assertTrue(reports.isEmpty())
        compose.onNodeWithText("Granted fixture").performClick()
        compose.runOnIdle {checkNotNull(started)(ClientFailure("mobile.local.recording_failed"))}
        assertEquals(listOf("grant-a","grant-b"),origins);assertEquals(listOf("grant-b"),reports);assertEquals(2,observations)
    }

}
