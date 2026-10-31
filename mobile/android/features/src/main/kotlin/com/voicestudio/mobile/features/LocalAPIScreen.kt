package com.voicestudio.mobile.features

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.PersistableBundle
import androidx.compose.material3.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.*
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import java.io.File
import java.util.Locale
import java.util.UUID

@Composable fun LocalAPIScreen(vm: StudioViewModel,onVoices:(()->Unit)?=null) {
    val phone by vm.phone.state.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val lifecycle=LocalLifecycleOwner.current
    var lan by remember {mutableStateOf(false)}
    var speech by remember {mutableStateOf(true)}
    var transcription by remember {mutableStateOf(false)}
    var translation by remember {mutableStateOf(false)}
    var source by remember {mutableStateOf("preset")}
    var speaker by remember {mutableStateOf("0")}
    var voiceID by remember {mutableStateOf("")}
    var server by remember {mutableStateOf<PhoneSpeechServer?>(null)}
    var starting by remember {mutableStateOf(false)}
    var work by remember {mutableStateOf<Job?>(null)}
    var copied by remember {mutableStateOf(false)}
    fun selected(task:String,capability:String)=phone.preferredModel(phone.models.filter {it.capability==capability},vm.phone.ramBytes,preferences.phoneModels[task])
    val item=selected(if(source=="saved") "phone-api-clone" else "phone-api",if(source=="saved") "clone" else "tts")
    val asr=selected("phone-api-asr","asr");val translator=selected("phone-api-translate","translate")
    val savedVoice=phone.voices.firstOrNull {it.id==voiceID} ?: phone.voices.firstOrNull()
    val needed=listOfNotNull(item.takeIf {speech},asr.takeIf {transcription},translator.takeIf {translation})
    val ready=(speech||transcription||translation)&&(!speech||(item!=null&&phone.canRun(item)&&(source=="preset"||savedVoice!=null)))&&(!transcription||(asr!=null&&phone.canRun(asr)))&&(!translation||(translator!=null&&phone.canRun(translator)))
    fun stop() {work?.cancel();server?.close();server=null;copied=false}
    DisposableEffect(lifecycle) {
        val observer=LifecycleEventObserver {_,event -> if(event==Lifecycle.Event.ON_STOP) stop()}
        lifecycle.lifecycle.addObserver(observer)
        onDispose {lifecycle.lifecycle.removeObserver(observer);stop()}
    }
    LaunchedEffect(Unit) {try {vm.phone.refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    LaunchedEffect(item?.id) {speaker="0"}
    suspend fun marked(audio:LocalAudio):ByteArray=withContext(Dispatchers.IO) {
        val file=File.createTempFile("phone-api-",".wav",context.cacheDir)
        try {markSynthetic(audio,file);file.readBytes()} finally {file.delete()}
    }
    Screen {
        ScreenTitle("mobile.host.title")
        Text(tr("mobile.host.capabilities_hint"))
        val running=server
        if(running!=null) {
            Heading("mobile.host.running")
            SelectionContainer {Column {running.addresses.forEach {Text(it)}}}
            Text("/v1/models · /mcp")
            Secondary(if(copied) "sharing.copied" else "mobile.host.copy") {
                val clip=ClipData.newPlainText("VoiceStudio",running.token)
                clip.description.extras=PersistableBundle().apply {putBoolean("android.content.extra.IS_SENSITIVE",true)}
                (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(clip)
                copied=true
            }
            Action("common.stop") {stop()}
        } else {
            Toggle("mobile.host.lan",lan) {if(!starting) lan=it}
            Text(tr("mobile.host.lan_hint"))
            Toggle("mobile.host.allow_speech",speech) {if(!starting) speech=it}
            if(speech&&!starting) {
                ChoicePicker("mobile.host.voice_source",source,listOf(Choice("preset",tr("mobile.host.preset")),Choice("saved",tr("mobile.host.saved")))) {source=it}
                if(source=="saved") {
                    if(phone.voices.isEmpty()) {
                        Text(tr("mobile.simple.empty_voices"))
                        if(onVoices!=null) Secondary("mobile.voices.new_clone") {onVoices()}
                    } else ChoicePicker("mobile.speak.voice",savedVoice?.id.orEmpty(),phone.voices.map {Choice(it.id,it.name)}) {voiceID=it}
                } else ChoicePicker("mobile.speak.voice",speaker,(0 until (item?.option("num_speakers","1")?.toIntOrNull()?.coerceIn(1,128) ?: 1)).map {Choice(it.toString(),tr("mobile.local.preset_voice",mapOf("number" to it+1)))}) {speaker=it}
                TaskModelPicker(vm,if(source=="saved") "phone-api-clone" else "phone-api",setOf(if(source=="saved") "clone" else "tts"),item)
            }
            Toggle("mobile.host.allow_transcription",transcription) {if(!starting) transcription=it}
            if(transcription&&!starting) {TaskModelPicker(vm,"phone-api-asr",setOf("asr"),asr);Text(tr("mobile.host.audio_hint"))}
            Toggle("mobile.host.allow_translation",translation) {if(!starting) translation=it}
            if(translation&&!starting) TaskModelPicker(vm,"phone-api-translate",setOf("translate"),translator)
            if(needed.any {!phone.canRun(it)}) MissingModelsCard(vm,"mobile.host.title",needed)
            Action("mobile.host.start",!starting&&ready) {
                val selected=item;val voice=speaker.toInt();val localNetwork=lan;val useSaved=source=="saved";val saved=savedVoice
                val features=PhoneServerCapabilities(
                    speech=speech,
                    transcriptionModel=asr?.id.takeIf {transcription},
                    translationModel=translator?.id.takeIf {translation},
                    translationLanguages=translator?.languages.orEmpty(),
                    transcribe=if(transcription&&asr!=null) {audio ->vm.phone.engine.transcribe(asr,audio)} else null,
                    translate=if(translation&&translator!=null) {text,code ->vm.phone.engine.translate(translator,text,Locale.forLanguageTag(code).getDisplayLanguage(Locale.ENGLISH).ifBlank {code})} else null,
                    encodeMP3={wave ->withContext(Dispatchers.IO) {
                        val directory=File(context.cacheDir,"phone-api-"+UUID.randomUUID()).apply {mkdirs()}
                        try {
                            val input=File(directory,"source.wav").apply {writeBytes(wave)};val output=File(directory,"speech.mp3")
                            PhoneAudioExport.mp3(input,output);output.readBytes()
                        } finally {directory.deleteRecursively()}
                    }}
                )
                starting=true;copied=false
                work=scope.launch {
                    var candidate:PhoneSpeechServer?=null
                    try {
                        candidate=PhoneSpeechServer(selected?.id ?: "phone",features) {text,speed ->
                            val model=requireNotNull(selected)
                            val audio=if(useSaved) {
                                val target=requireNotNull(saved)
                                PhoneServerAudio.speech(vm.phone.engine.clone(model,text,vm.phone.voices.reference(target),target.transcript),speed)
                            } else vm.phone.engine.synthesize(model,text,voice,speed=speed)
                            marked(audio)
                        }
                        withContext(Dispatchers.IO) {candidate.start(localNetwork)}
                        ensureActive();server=candidate
                    } catch(e:CancellationException) {candidate?.close();throw e} catch(e:Exception) {candidate?.close();vm.fail(e)} finally {starting=false}
                }
            }
        }
    }
}
