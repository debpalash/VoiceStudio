package com.voicestudio.mobile.network

/** An explicit empty profile means the engine preset, never source cloning. */
object DubVoiceChoice {
    const val UNSELECTED = "_choose_voice_"
    fun validate(profile: String) {
        if(profile == UNSELECTED) throw ClientFailure("mobile.dub.choose_voice")
    }
}
