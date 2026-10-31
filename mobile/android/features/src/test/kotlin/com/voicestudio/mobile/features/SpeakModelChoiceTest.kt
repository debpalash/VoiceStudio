package com.voicestudio.mobile.features

import com.voicestudio.mobile.features.speak.chooseSpeakModel
import com.voicestudio.mobile.features.speak.speakModelKey
import com.voicestudio.mobile.local.ModelLicence
import com.voicestudio.mobile.local.PhoneModel
import org.junit.Assert.*
import org.junit.Test

class SpeakModelChoiceTest {
    private fun model(id: String,capability: String)=PhoneModel(id,capability,"sherpa-onnx","org/m","a".repeat(40),emptyList(),1,listOf("en"),1.0,ModelLicence("mit","commercial-ok",false,"","","b".repeat(64)))
    private val models=listOf(model("tts-a","tts"),model("tts-b","tts"),model("clone-a","clone"),model("clone-b","clone"))
    @Test fun presetAndCloneChoicesDoNotOverwriteEachOther() {
        var choices=emptyMap<String,String>()
        choices=choices+(speakModelKey("tts") to "tts-b")
        choices=choices+(speakModelKey("clone") to "clone-b")
        assertEquals("tts-b",chooseSpeakModel(models,setOf("tts-a"),choices,"tts")?.id)
        assertEquals("clone-b",chooseSpeakModel(models,emptySet(),choices,"clone")?.id)
    }
    @Test fun explicitReferenceOnlyChoiceStaysVisibleBeforeVoiceSelection() {
        assertEquals("clone-b",chooseSpeakModel(models,setOf("tts-a"),mapOf("speak" to "clone-b"),"tts")?.id)
        assertEquals("tts-a",chooseSpeakModel(models,setOf("clone-b"),emptyMap(),"tts")?.id)
    }
    @Test fun automaticChoicePrefersFittingInstalledModelAndExplicitOverrideRemainsAvailable() {
        val heavy=model("heavy","clone").copy(minRamGb=8.0)
        val small=model("small","clone").copy(minRamGb=3.0)
        val candidates=listOf(heavy,small);val installed=candidates.map {it.id}.toSet()
        assertEquals("small",chooseSpeakModel(candidates,installed,emptyMap(),"clone",7_512_518_656)?.id)
        assertEquals("heavy",chooseSpeakModel(candidates,installed,mapOf("speak-clone" to "heavy"),"clone",7_512_518_656)?.id)
    }
    @Test fun withoutAChoiceFirstInstalledThenFirstModelOfThatCapability() {
        assertEquals("clone-b",chooseSpeakModel(models,setOf("clone-b"),emptyMap(),"clone")?.id)
        assertEquals("tts-a",chooseSpeakModel(models,emptySet(),emptyMap(),"tts")?.id)
    }
}
