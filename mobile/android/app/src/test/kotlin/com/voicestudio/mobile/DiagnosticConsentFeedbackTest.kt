package com.voicestudio.mobile

import android.app.Application
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.AutomaticDiagnosticState
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.LocalL10n
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.network.DiagnosticStatus
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class DiagnosticConsentFeedbackTest {
    @get:Rule val compose=createComposeRule()
    private fun failedChoiceStillOffersRetry(saving:Boolean=false,failed:Boolean=true) {
        val app=ApplicationProvider.getApplicationContext<Application>();val l=L10n.load(app,"en")
        var retried=0
        compose.setContent {
            CompositionLocalProvider(LocalL10n provides l) {VoiceStudioTheme {
                val snackbars=remember {SnackbarHostState()}
                Scaffold(snackbarHost={SnackbarHost(snackbars)}) {padding->
                    DiagnosticSaveFailureNotice(AutomaticDiagnosticState(ready=true,status=DiagnosticStatus(enabled=false,prompted=true,consentSaveFailed=failed),saving=saving,saveFailed=failed),snackbars) {retried++}
                }
            }}
        }
        compose.waitForIdle()
        if(failed && !saving) {
            compose.onNodeWithText(l.t("diagnostics.save_failed")).assertIsDisplayed()
            compose.onNodeWithText(l.t("common.retry")).performClick()
            compose.runOnIdle {assertEquals(1,retried)}
        }else {
            compose.onNodeWithText(l.t("diagnostics.save_failed")).assertDoesNotExist()
            compose.runOnIdle {assertEquals(0,retried)}
        }
    }
    @Test fun failedKeepOffStillShowsRetryAfterThePermissionPromptIsClosed()=failedChoiceStillOffersRetry()
    @Test fun successfulKeepOffDoesNotAskPermissionAgain()=failedChoiceStillOffersRetry(failed=false)
    @Test fun pendingSaveDoesNotOfferAnOverlappingRetry()=failedChoiceStillOffersRetry(saving=true)
}
