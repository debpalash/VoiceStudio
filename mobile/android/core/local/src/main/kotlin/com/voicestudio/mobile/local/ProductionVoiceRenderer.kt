package com.voicestudio.mobile.local

/** Immutable voice/model snapshot: workers and the editor resolve a render identically. */
class ProductionVoiceRenderer(private val engine: LocalEngine,private val store: PhoneVoiceStore,
    private val voices: List<PhoneVoice>,private val tts: PhoneModel?,private val clone: PhoneModel?) {
    val configuration=listOfNotNull(tts,clone).joinToString("|") {modelPin(it).identity}
    suspend fun render(segment: ProductionSegment,selection: String): LocalAudio {
        if(selection.startsWith("phone:")) {
            val voice=requireNotNull(voices.firstOrNull {"phone:"+it.id==selection})
            return engine.clone(requireNotNull(clone),segment.text,store.reference(voice),voice.transcript)
        }
        require(selection.startsWith("preset:"))
        return engine.synthesize(requireNotNull(tts),segment.text,selection.removePrefix("preset:").toInt())
    }
}
