package com.voicestudio.mobile.features

import android.Manifest
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.MissingModelsCard
import com.voicestudio.mobile.features.models.TaskModelPicker
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.*
import java.io.File
import java.util.UUID

private data class VoiceToolResult(val titleKey: String,val file: File,val subject: ReportSubject)

@Composable fun VoiceToolsScreen(vm: StudioViewModel) {
    val phone by vm.phone.state.collectAsState()
    val prefs by vm.preferences.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var mode by rememberSaveable {mutableStateOf("convert")}
    var source by remember {mutableStateOf<File?>(null)}
    var recordingConsent by rememberSaveable {mutableStateOf(false)}
    var recording by remember {mutableStateOf(false)}
    var seconds by remember {mutableIntStateOf(0)}
    val recorder=remember {SampleRecorder(context)}
    val lifecycle=LocalLifecycleOwner.current
    var voice by rememberSaveable {mutableStateOf("")}
    var voiceA by rememberSaveable {mutableStateOf("preset:0")}
    var voiceB by rememberSaveable {mutableStateOf("preset:1")}
    var text by rememberSaveable {mutableStateOf("")}
    var matchDuration by rememberSaveable {mutableStateOf(true)}
    var transcript by remember {mutableStateOf("")}
    var results by remember {mutableStateOf(emptyList<VoiceToolResult>())}
    var work by remember {mutableStateOf<Job?>(null)}
    var progress by remember {mutableStateOf(0f)}
    val running=work?.isActive==true
    fun selected(task: String,capability: String)=phone.preferredModel(phone.models.filter {it.capability==capability},vm.phone.ramBytes,prefs.phoneModels[task])
    val tts=selected("production-tts","tts")
    val clone=selected("production-clone","clone")
    val asr=selected("convert-asr","asr")
    val speakers=tts?.option("num_speakers","1")?.toIntOrNull()?.coerceIn(1,128) ?: 1
    val choices=(0 until speakers).map {Choice("preset:$it",tr("mobile.local.preset_voice",mapOf("number" to it+1)))}+phone.voices.map {Choice("phone:"+it.id,it.name)}
    val needed=if(mode=="convert") listOfNotNull(asr,clone) else listOfNotNull(tts.takeIf {listOf(voiceA,voiceB).any {it.startsWith("preset:")}},clone.takeIf {listOf(voiceA,voiceB).any {it.startsWith("phone:")}})
    val ready=!running&&!recording&&needed.isNotEmpty()&&needed.all {phone.canRun(it)}&&if(mode=="convert") source!=null&&phone.voices.any {it.id==voice}&&asr!=null&&clone!=null else text.isNotBlank()&&text.length<=2000&&listOf(voiceA,voiceB).all {selection -> choices.any {it.id==selection}}
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri -> if(uri!=null) scope.launch {
        try {val file=copyMedia(context,uri,"wav");source?.delete();source=file} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
    }}
    LaunchedEffect(Unit) {try {vm.phone.refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    DisposableEffect(lifecycle) {
        val observer=LifecycleEventObserver {_,event ->if(event==Lifecycle.Event.ON_STOP&&recording) {recorder.discard();recording=false;source=null}}
        lifecycle.lifecycle.addObserver(observer)
        onDispose {lifecycle.lifecycle.removeObserver(observer);recorder.discard();source?.delete()}
    }
    LaunchedEffect(recording) {seconds=0;while(recording) {delay(1000);seconds++;if(seconds>=29) {
        try {source=recorder.stop()} catch(e:Exception) {source=null;vm.fail(e)} finally {recording=false}
    }}}
    val record=permissionAction(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",vm::fail) {
        if(mode=="convert"&&recordingConsent&&!running) try {val file=recorder.start();source?.delete();source=file;recording=true;results=emptyList();transcript=""} catch(e:Exception) {vm.fail(e)}
    }
    if(mode=="edit") {AudioEditorScreen(vm) {mode="convert"};return}
    Screen {
        ScreenTitle("tools.title")
        if(!running&&!recording) ChoicePicker("tools.title",mode,listOf(Choice("convert",tr("convert.convert")),Choice("compare",tr("compare.title")),Choice("edit",tr("mobile.processing.audio_editor")))) {mode=it;results=emptyList();transcript=""}
        Text(tr("mobile.local.this_phone"),color=role("textMuted"))
        if(mode=="convert") {
            Text(tr("mobile.production.conversion_hint"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
            Secondary("convert.source_kicker",!running&&!recording) {importer.launch(arrayOf("audio/*"))}
            Row(Modifier.fillMaxWidth()) {Text(tr("mobile.processing.record_consent"),Modifier.weight(1f));Switch(recordingConsent,{recordingConsent=it},enabled=!running&&!recording)}
            if(recording) {
                Text(tr("mobile.voices.recording",mapOf("seconds" to seconds)))
                Secondary("common.stop") {try {source=recorder.stop()} catch(e:Exception) {source=null;vm.fail(e)} finally {recording=false}}
            } else Secondary("mobile.voices.record",recordingConsent&&!running,record)
            source?.let {Text(it.name,style=MaterialTheme.typography.bodySmall)}
            if(!running) ChoicePicker("convert.target_voice",voice,phone.voices.map {Choice(it.id,it.name)}) {voice=it}
            Row(Modifier.fillMaxWidth()) {Text(tr("convert.match_duration"),Modifier.weight(1f));Switch(checked=matchDuration,onCheckedChange={matchDuration=it},enabled=!running)}
            if(!running) TaskModelPicker(vm,"convert-asr",setOf("asr"),asr,key="convert-asr")
        } else {
            Field("compare.test_phrase",text,{if(!running) text=it},multiline=true)
            if(!running) {
                ChoicePicker("compare.voice_a",voiceA,choices) {voiceA=it}
                ChoicePicker("compare.voice_b",voiceB,choices) {voiceB=it}
                TaskModelPicker(vm,"production-tts",setOf("tts"),tts,key="production-tts")
            }
        }
        if(!running) TaskModelPicker(vm,"production-clone",setOf("clone"),clone,key="production-clone")
        if(needed.any {!phone.canRun(it)}) MissingModelsCard(vm,"tools.title",needed)
        if(running) {
            LinearProgressIndicator(progress={progress},modifier=Modifier.fillMaxWidth())
            Secondary("common.cancel") {work?.cancel()}
        } else Action(if(mode=="convert") "convert.convert" else "compare.compare_btn",ready) {
            val directory=File(context.cacheDir,"voice-tools/${UUID.randomUUID()}")
            val snapshotVoices=phone.voices
            val snapshotText=text
            progress=0f;results=emptyList();transcript=""
            work=scope.launch {
                try {
                    withContext(Dispatchers.IO) {directory.mkdirs()}
                    if(mode=="convert") {
                        val target=snapshotVoices.first {it.id==voice}
                        val result=VoiceConversion.run(requireNotNull(source),directory,target,vm.phone.voices,requireNotNull(asr),requireNotNull(clone),vm.phone.engine,matchDuration=matchDuration) {progress=it}
                        val file=File(directory,"converted.wav")
                        withContext(Dispatchers.IO) {markSynthetic(result.audio,file)}
                        transcript=result.transcript
                        results=listOf(VoiceToolResult("convert.result_kicker",file,ReportSubject("speak:phone:"+file.absolutePath,"speak","phone",clone.repo,clone.revision)))
                    } else {
                        listOf(voiceA,voiceB).forEachIndexed {index,selection ->
                            val producer: PhoneModel
                            val audio=if(selection.startsWith("phone:")) {
                                producer=requireNotNull(clone)
                                val target=snapshotVoices.first {"phone:"+it.id==selection}
                                vm.phone.engine.clone(producer,snapshotText,vm.phone.voices.reference(target),target.transcript)
                            } else {
                                require(selection.startsWith("preset:"));producer=requireNotNull(tts)
                                vm.phone.engine.synthesize(producer,snapshotText,selection.removePrefix("preset:").toInt())
                            }
                            currentCoroutineContext().ensureActive()
                            val file=File(directory,"$index.wav")
                            withContext(Dispatchers.IO) {markSynthetic(audio,file)}
                            results=results+VoiceToolResult(if(index==0) "compare.voice_a" else "compare.voice_b",file,ReportSubject("speak:phone:"+file.absolutePath,"speak","phone",producer.repo,producer.revision,snapshotText))
                            progress=(index+1)/2f
                        }
                    }
                } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
            }
        }
        if(transcript.isNotEmpty()) {Heading("convert.transcript");Text(transcript)}
        results.forEach {result ->
            Heading(result.titleKey)
            LocalAudioPlayer(result.file,vm::fail)
            Secondary("mobile.speak.share") {scope.launch {try {shareLocalFile(context,result.file,"audio/wav")} catch(e:Exception) {vm.fail(e)}}}
            ReportButton(vm) {result.subject}
        }
    }
}
