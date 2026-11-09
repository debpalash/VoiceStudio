package com.voicestudio.mobile

import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voicestudio.mobile.l10n.L10n
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Runs on ART, not the JVM: Robolectric uses java.util.regex and the JVM
 * runtime, so it cannot catch Android-only failures such as ICU regex syntax.
 */
@RunWith(AndroidJUnit4::class)
class DeviceRuntimeTest {
    @Test
    fun interpolationWorksOnAndroidRegexEngine() {
        val l10n = L10n.load(InstrumentationRegistry.getInstrumentation().targetContext, "en")
        assertEquals("Connected to 10.0.0.2", l10n.t("mobile.connect.connected_to", mapOf("host" to "10.0.0.2")))
    }

    @Test
    fun appLaunchesToResumed() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            assertEquals(Lifecycle.State.RESUMED, scenario.state)
        }
    }
    @Test
    fun desktopLanguagesAndActionableErrorsWorkOnArt() {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val catalog=com.voicestudio.mobile.features.LanguageCatalog.load(context)
        assertEquals("Auto",catalog.names.first())
        assertEquals("English",catalog.canonical("en"))
        val error=com.voicestudio.mobile.network.BackendErrors.parse(403,"""{"detail":{"code":"model_licence_required","message":"Review model terms","models":[{"repo_id":"org/model","license":"Example licence"}]}}""")
        val rendered=L10n.load(context,"en").t(error.key,error.params)
        org.junit.Assert.assertTrue(rendered.contains("Example licence"))
        org.junit.Assert.assertTrue(rendered.contains("Model Catalogue"))
        assertEquals("1:05",com.voicestudio.mobile.features.formatDuration(65000))
    }
}
