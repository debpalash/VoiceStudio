package com.voicestudio.mobile.local

import java.io.File
import java.util.UUID
import kotlinx.coroutines.*
import com.voicestudio.mobile.network.ClientFailure

data class VoiceConversionResult(val transcript: String,val audio: LocalAudio)

/** ASR followed by re-synthesis; source pitch and expression are not transferred. */
object VoiceConversion {
    suspend fun run(source: File,workspace: File,voice: PhoneVoice,voices: PhoneVoiceStore,asr: PhoneModel,clone: PhoneModel,
        engine: LocalEngine,decoder: AudioDecoder=AndroidAudioDecoder(),matchDuration: Boolean,progress: (Float)->Unit = {}): VoiceConversionResult = withContext(Dispatchers.IO) {
        workspace.mkdirs()
        val file=File(workspace,"${UUID.randomUUID()}.pcm")
        try {
            val pcm=decoder.decode(source,file,30) {progress(it*.15f)}
            require(pcm.frames in 2400..720000)
            val audio=pcm.read(0,pcm.frames.toInt())
            val text=engine.transcribe(asr,audio) {progress(.15f+it*.25f)}.trim()
            if(text.isEmpty()) throw ClientFailure("mobile.simple.no_speech")
            currentCoroutineContext().ensureActive()
            val converted=engine.clone(clone,text,voices.reference(voice),voice.transcript) {progress(.4f+it*.6f)}
            val result=if(matchDuration) fitTiming(converted,audio.samples.size) else converted
            currentCoroutineContext().ensureActive()
            VoiceConversionResult(text,result)
        } finally {file.delete()}
    }
}
