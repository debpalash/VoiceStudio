package com.voicestudio.mobile

import android.app.Application
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class ContentReportUiTest {
    @get:Rule val compose=createComposeRule()
    private fun show(subject: ReportSubject): ContentReports {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val reports=ContentReports(CoroutineScope(Dispatchers.Unconfined))
        reports.open(subject)
        compose.setContent {
            CompositionLocalProvider(LocalL10n provides L10n.load(app,"en")) {VoiceStudioTheme {
                val sheet by reports.sheet.collectAsState()
                sheet?.let {ContentReportSheet(it,edit=reports::edit,send={},dismiss=reports::close)}
            }}
        }
        return reports
    }
    @Test fun sendWaitsForAReasonAndTheTextBoxStartsUnticked() {
        val reports=show(ReportSubject("speak:phone:1","speak","phone","org/model","a".repeat(40),"Typed words"))
        compose.onNodeWithText("Report this result").assertExists()
        compose.onNodeWithText("Sends your reason, note, the app version and the model that made this to the VoiceStudio team. No audio, account or device ID is sent.").assertExists()
        compose.onNodeWithText("Send report").assertIsNotEnabled()
        listOf("Sexual content","Hateful or abusive","Impersonation or a voice cloned without consent","Misleading or harmful","Something else").forEach {
            compose.onNodeWithText(it).assertIsNotSelected()
        }
        compose.onNodeWithContentDescription("Include the text I entered").assertIsOff()
        compose.onNodeWithText("Hateful or abusive").performClick()
        compose.onNodeWithText("Hateful or abusive").assertIsSelected()
        compose.onNodeWithText("Send report").assertIsEnabled()
        compose.onNodeWithText("Cancel").performClick()
        compose.waitForIdle()
        assertNull(reports.sheet.value)
        assertEquals("hateful",reports.draft("speak:phone:1")!!.reason)
    }
    @Test fun includeTextIsHiddenWithoutTypedText() {
        show(ReportSubject("clone:computer:p","clone","computer","unknown"))
        compose.onNodeWithText("Report this result").assertExists()
        compose.onNodeWithText("Include the text I entered").assertDoesNotExist()
    }
    @Test fun everyBuildReportsToProductionUnlessOverridden() {
        val root=File(requireNotNull(System.getProperty("androidRoot")))
        val overridden=!System.getenv("VOICESTUDIO_CONTENT_REPORTS_URL").isNullOrBlank()||
            File(root,"../.env").let {it.isFile&&it.readText().contains("VOICESTUDIO_CONTENT_REPORTS_URL")}
        // Debug builds default to production too; only a deliberate override changes it.
        org.junit.Assume.assumeFalse(overridden)
        assertTrue(BuildConfig.DEBUG)
        assertEquals("https://appapi.voicestudio.sh",BuildConfig.CONTENT_REPORTS_URL)
        assertEquals("https://appapi.voicestudio.sh/v1/content-reports",contentReportClient().endpoint.toString())
        val script=File(root,"app/build.gradle.kts").readText()
        assertTrue("one URL for every build type",Regex("""buildConfigField\("String", "CONTENT_REPORTS_URL"""").findAll(script).count()==1)
    }
}
