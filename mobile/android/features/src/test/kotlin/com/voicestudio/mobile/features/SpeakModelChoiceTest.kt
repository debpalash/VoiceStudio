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
    @Test fun withoutAChoiceFirstInstalledThenFirstModelOfThatCapability() {
        assertEquals("clone-b",chooseSpeakModel(models,setOf("clone-b"),emptyMap(),"clone")?.id)
        assertEquals("tts-a",chooseSpeakModel(models,emptySet(),emptyMap(),"tts")?.id)
    }
}
