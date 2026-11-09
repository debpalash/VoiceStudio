package com.voicestudio.mobile.local

import java.io.File
import java.util.Locale
import kotlinx.serialization.json.*
import com.voicestudio.mobile.network.ClientFailure

class WorkflowGraphExecutor(private val engine: LocalEngine,private val store: PhoneVoiceStore,private val voices: List<PhoneVoice>,private val tts: PhoneModel?,private val clone: PhoneModel?,private val asr: PhoneModel?,private val translator: PhoneModel?) {
    private fun languageName(value: String)=if(value=="Auto"||value=="auto") "auto" else if(value.matches(Regex("[A-Za-z]{2,3}(-[A-Za-z0-9]+)*"))) Locale.forLanguageTag(value).getDisplayLanguage(Locale.ENGLISH) else value
    suspend fun execute(step: WorkflowGraphStep,value: WorkflowGraphValue,workspace: File): WorkflowGraphOutput {
        fun unbound(): Nothing=throw ClientFailure("mobile.workflowParity.bind_voices")
        return when(step.kind) {
            "transcribe"->WorkflowGraphOutput(text=WorkflowTranscription.run(requireNotNull(value.audio),requireNotNull(asr),engine))
            "translate"->{
                val chosen=requireNotNull(translator);val source=languageName(step.sourceLanguage ?: "auto")
                val template=chosen.option("prompt_template","Translate the following text from {source_language} into {target_language}. Return only the translation.\n{text}").replace("{{source_language}}",source).replace("{source_language}",source)
                val configured=chosen.copy(config=JsonObject(chosen.config+("prompt_template" to JsonPrimitive(template))))
                WorkflowGraphOutput(text=engine.translate(configured,requireNotNull(value.text),languageName(requireNotNull(step.language))))
            }
            "speak"->{
                val text=requireNotNull(value.text);val selection=step.voiceId ?: unbound()
                val audio=if(selection.startsWith("phone:")) {
                    val voice=voices.firstOrNull {"phone:"+it.id==selection} ?: unbound()
                    val result=engine.clone(requireNotNull(clone),text,store.reference(voice),voice.transcript)
                    LocalAudio(NarrationAudio.speed(result.samples,(step.speed ?: 1.0).toFloat()),result.sampleRate)
                } else {
                    if(!selection.startsWith("preset:")) unbound();val model=requireNotNull(tts)
                    val language=step.language?.takeUnless {it=="Auto"||it=="auto"}?.let {requested ->model.languages.firstOrNull {it==requested||Locale.forLanguageTag(it).getDisplayLanguage(Locale.ENGLISH)==requested} ?: throw ClientFailure("mobile.local.model_incompatible")}
                    engine.synthesize(model,text,selection.removePrefix("preset:").toInt(),(step.speed ?: 1.0).toFloat(),language)
                };WorkflowGraphOutput(audio=audio)
            }
            "convert"->{val voice=voices.firstOrNull {"phone:"+it.id==step.voiceId} ?: unbound();WorkflowGraphOutput(audio=VoiceConversion.run(requireNotNull(value.audio),workspace,voice,store,requireNotNull(asr),requireNotNull(clone),engine,matchDuration=true).audio)}
            "normalize"->{if(!value.synthetic) throw ClientFailure("mobile.workflowParity.incompatible_merge");val document=PhoneAudioProcessing.open(requireNotNull(value.audio),workspace);try {WorkflowGraphOutput(file=PhoneAudioProcessing.process(document,AudioEditOptions(normalize=true,targetDB=step.targetDb ?: -2.0),workspace).file)} finally {document.pcm.file.delete()}}
            else->throw ClientFailure("mobile.workflowParity.unsupported",mapOf("kind" to step.kind))
        }
    }
}
