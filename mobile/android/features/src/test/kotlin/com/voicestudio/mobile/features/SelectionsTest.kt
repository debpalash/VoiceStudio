package com.voicestudio.mobile.features

import com.voicestudio.mobile.network.*
import org.junit.Assert.*
import org.junit.Test

class SelectionsTest {
    @Test fun automaticPhoneChoicePrefersRecommendedInstalledModelButHonorsExplicitChoice() {
        val licence=com.voicestudio.mobile.local.ModelLicence("mit","commercial-ok",false,"Fixture","Fixture","a".repeat(64))
        val heavy=com.voicestudio.mobile.local.PhoneModel("heavy","clone","omnivoice.cpp","fixture/heavy","a".repeat(40),emptyList(),1,listOf("en"),8.0,licence)
        val small=heavy.copy(id="small",minRamGb=3.0)
        val candidates=listOf(heavy,small)
        val state=PhoneState(installed=setOf(heavy.id,small.id))
        assertEquals(small,state.preferredModel(candidates,7_512_518_656))
        assertEquals(heavy,state.preferredModel(candidates,7_512_518_656,heavy.id))
        assertEquals(heavy,state.copy(installed=setOf(heavy.id)).preferredModel(candidates,7_512_518_656))
        assertEquals(small,PhoneState().preferredModel(candidates,7_512_518_656))
    }
    private val family=EngineFamily("kitten",listOf(
        Engine("kitten","KittenTTS",supportsCloning=false),
        Engine("clone","VoiceStudio",supportsCloning=true),
        Engine("missing","Missing engine",available=false,reason="Install the engine",hint="Restart the host",supportsCloning=true)))
    @Test fun enginePickerUsesNamesActiveEngineAndDisabledExplanations() {
        val choices=engineChoices(family,null) {it}
        assertEquals("KittenTTS",choices.first().title)
        assertEquals("kitten",initialEngine(family,choices))
        assertFalse(choices.last().enabled)
        assertTrue(choices.last().supporting!!.contains("Install the engine"))
        assertTrue(choices.last().supporting!!.contains("Restart the host"))
    }
    @Test fun referenceAudioRequiresPositiveCloningCapability() {
        val clone=Profile("v","Clone",refAudioPath="ref.wav")
        val choices=engineChoices(family,clone) {it}
        assertFalse(choices.first().enabled)
        assertEquals("mobile.speak.engine_no_cloning",choices.first().supporting)
        assertEquals("clone",initialEngine(family,choices))
        assertTrue(choices.last().supporting!!.contains("Install the engine"))
        val unavailable=engineChoices(EngineFamily(backends=listOf(family.backends.last().copy(supportsCloning=false))),clone) {it}.single()
        assertTrue(unavailable.supporting!!.contains("Install the engine"))
        assertTrue(unavailable.supporting!!.contains("mobile.speak.engine_no_cloning"))
        assertFalse(engineChoices(EngineFamily(backends=listOf(Engine("unknown"))),clone) {it}.single().enabled)
        assertTrue(engineChoices(family,null) {it}.first().enabled)
    }
    @Test fun autoDefaultProfileLanguageAndDubExclusion() {
        val catalog=LanguageCatalog(listOf("Auto","Arabic","English","French"))
        assertEquals("Auto",catalog.preferred(null))
        assertEquals("English",catalog.preferred(Profile("v","Voice",language="english")))
        assertEquals("French",catalog.preferred(Profile("v","Voice",language="fr")))
        assertEquals("Auto",catalog.preferred(Profile("v","Voice",language="unknown")))
        assertFalse(catalog.dubChoices().any {it.second=="Auto"})
        assertTrue(catalog.dubChoices().contains("fr" to "French"))
    }
    @Test fun missingModelGuidanceHasLabelSizeAndNoAuthorizationAdvice() {
        val (key,params)=Readiness(false,MissingModel("asr_model_missing",RecommendedModel("Small ASR",0.6))).guidance()
        assertEquals("mobile.dictate.install_model_size",key)
        assertEquals(mapOf("model" to "Small ASR","size" to 0.6),params)
        assertEquals("mobile.dictate.install_model",Readiness(false,MissingModel("asr_model_missing",missingRepoId="org/model")).guidance().first)
        assertEquals("mobile.dictate.setup",Readiness(false).guidance().first)
    }
    @Test fun dubPickerHonestlyOffersDefaultPresetOrAnExistingClone() {
        assertEquals(listOf("" to "Default voice (engine preset)", "clone-id" to "My clone"),
            dubVoiceChoices(listOf(Profile("clone-id","My clone")),"Default voice (engine preset)"))
        assertEquals(listOf("" to "Default voice (engine preset)"),dubVoiceChoices(emptyList(),"Default voice (engine preset)"))
    }
    @Test fun playerUsesClockTime() {
        assertEquals("0:00",formatDuration(-1))
        assertEquals("0:01",formatDuration(1000))
        assertEquals("1:05",formatDuration(65999))
        assertEquals("61:00",formatDuration(3660000))
    }
}
