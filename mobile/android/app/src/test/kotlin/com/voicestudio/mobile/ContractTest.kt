package com.voicestudio.mobile

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.features.dub.displayStage
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class ContractTest {
    @Test fun localizationIsCachedPerLocaleForWorkerProgress() {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val english=L10n.load(context,"en")
        assertSame(english,L10n.load(context,"en"))
        assertNotSame(english,L10n.load(context,"fr"))
        assertSame(L10n.load(context,"zh-CN"),L10n.load(context,"zh-Hans-CN"))
    }
    @Test fun licenceMessageListsEachModelAndActionOnlyOnce() {
        val l=L10n.load(ApplicationProvider.getApplicationContext<Context>(),"en")
        val error=com.voicestudio.mobile.network.BackendErrors.parse(403,"""{"detail":{"code":"model_licence_required","message":"Licence not accepted for org/model, org/other. Review and accept it in Model Catalogue.","models":[{"repo_id":"org/model","license":"Example licence"},{"repo_id":"org/other","license":"Other terms"},{"repo_id":"org/model","license":"Example licence"}]}}""")
        val copy=l.t(error.key,error.params)
        assertEquals("org/model · Example licence\norg/other · Other terms\nAccept it on your computer in VoiceStudio \u2192 Model Catalogue",copy)
    }
    @Test fun unreadableLicenceRecordsKeepServerRecoveryAdvice() {
        val l=L10n.load(ApplicationProvider.getApplicationContext<Context>(),"en")
        val error=com.voicestudio.mobile.network.BackendErrors.parse(403,"""{"detail":{"code":"model_licence_required","message":"Model licence records are unreadable. Reinstall or update VoiceStudio.","models":[]}}""")
        assertEquals("The server reported an error: Model licence records are unreadable. Reinstall or update VoiceStudio.",l.t(error.key,error.params))
    }
    @Test fun everyLiteralUiAndErrorKeyExistsInBundledEnglish() {
        val l=L10n.load(ApplicationProvider.getApplicationContext<Context>(),"en")
        val root=File(requireNotNull(System.getProperty("androidRoot")))
        val pattern=Regex("[\\\"]((?:mobile|common|consent|player|projects)\\.[A-Za-z0-9_.]+)[\\\"]")
        val keys=root.walkTopDown().filter {it.extension=="kt"&&"/src/main/" in it.invariantSeparatorsPath}.flatMap {pattern.findAll(it.readText()).map {m->m.groupValues[1]}}.toSet()
        assertTrue(keys.size>60)
        val missing=keys.filterNot(l::has)
        assertEquals(emptyList<String>(),missing)
        for(mode in listOf("light","dark","system")) assertTrue(l.has("mobile.settings.mode_$mode"))
        assertTrue(l.has("themeAppearance.default"))
        val palettes=com.voicestudio.mobile.features.paletteChoices(l).toMap()
        assertEquals(l.t("themeAppearance.default"),palettes["default"])
        assertEquals("VoiceStudio Original",palettes["signal"])
        assertEquals("VoiceStudio Classic",palettes["heritage"])
        assertEquals("Connect",l.t("mobile.tabs.connect"))
        val guidance=l.t("mobile.dictate.install_model_size",mapOf("model" to "Small ASR","size" to 0.6))
        assertTrue(guidance,guidance.contains("0.6 GB"))
        assertEquals("Default voice (engine preset)",l.t("mobile.speak.voice_default"))
        val catalog=com.voicestudio.mobile.features.LanguageCatalog.load(ApplicationProvider.getApplicationContext())
        assertEquals("Auto",catalog.names.first())
        assertTrue(catalog.names.contains("English"))
        assertTrue(catalog.names.size>600)
        assertEquals("Connected to test",l.t("mobile.connect.connected_to",mapOf("host" to "test")))
    }
    @Test fun dubWorkerPersistsActionableErrorParameters() {
        val error=com.voicestudio.mobile.network.BackendErrors.parse(403,"""{"detail":{"code":"model_licence_required","message":"Accept on your computer","models":[{"repo_id":"org/model","license":"Example licence"}]}}""")
        val data=com.voicestudio.mobile.data.DubWorker.failureData(error,"job")
        assertEquals(error.key,data.getString("error"))
        val staleProgress=androidx.work.workDataOf("stage" to "mobile.android.stage_render")
        val info=androidx.work.WorkInfo(java.util.UUID.randomUUID(),androidx.work.WorkInfo.State.FAILED,emptySet(),data,staleProgress)
        assertEquals(error.key,info.displayStage())
        val encoded=data.getString("error_params")!!
        assertTrue(encoded.contains("Example licence"))
        assertTrue(encoded.contains("Accept on your computer"))
    }
}
