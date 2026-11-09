package com.voicestudio.mobile.features

import android.app.Application
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import java.io.File
import java.util.UUID
import kotlin.math.*

class AudioEditorSession(application: Application): AndroidViewModel(application) {
    internal val workspace=File(application.cacheDir,"audio-editor/${UUID.randomUUID()}")
    internal var document by mutableStateOf<PhoneAudioDocument?>(null)
    internal var sourceName by mutableStateOf("")
    internal var options by mutableStateOf(AudioEditOptions())
    internal var rendered by mutableStateOf<AudioEditOptions?>(null)
    internal var result by mutableStateOf<AudioEditResult?>(null)
    internal var progress by mutableStateOf(0f)
    internal var work by mutableStateOf<Job?>(null)
    internal fun perform(fail: (Throwable)->Unit,action: suspend CoroutineScope.()->Unit) {
        progress=0f;work=viewModelScope.launch {try {action()} catch(e:CancellationException) {throw e} catch(e:Exception) {fail(e)} finally {work=null}}
    }
    override fun onCleared() {val active=work;active?.cancel();if(active?.isCompleted!=false) workspace.deleteRecursively() else active.invokeOnCompletion {workspace.deleteRecursively()};super.onCleared()}
}
@Composable fun AudioEditorScreen(vm: StudioViewModel,onBack: ()->Unit) {
    val editor: AudioEditorSession=viewModel(key="phone-audio-editor")
    val context=LocalContext.current
    val document=editor.document
    val options=editor.options
    val result=editor.result
    val running=editor.work?.isActive==true
    var format by remember {mutableStateOf("wav")}
    var preview by remember {mutableStateOf<File?>(null)}
    var previewIsTemporary by remember {mutableStateOf(false)}
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->if(uri!=null) editor.perform(vm::fail) {
        val source=copyMedia(context,uri,"audio")
        try {
            val fresh=PhoneAudioProcessing.open(source,editor.workspace) {editor.progress=it}
            withContext(Dispatchers.IO) {editor.document?.pcm?.file?.delete();editor.result?.file?.parentFile?.deleteRecursively()}
            editor.document=fresh;editor.sourceName=uri.lastPathSegment.orEmpty();editor.result=null;editor.rendered=null;editor.options=AudioEditOptions();preview=null
        } finally {source.delete()}
    }}
    DisposableEffect(preview) {val old=preview;val temporary=previewIsTemporary;onDispose {if(temporary) old?.parentFile?.deleteRecursively()}}
    LaunchedEffect(options) {preview=null}
    Screen {
        Secondary("common.back",!running,onBack)
        ScreenTitle("mobile.processing.audio_editor")
        Text(tr("mobile.processing.hint"),color=role("textMuted"))
        Secondary("mobile.processing.select_audio",!running) {importer.launch(arrayOf("audio/*"))}
        if(document!=null) {
            Text(editor.sourceName)
            val color=role("messageAction");val waveform=tr("mobile.editing.waveform")
            Canvas(Modifier.fillMaxWidth().height(64.dp).semantics {contentDescription=waveform}) {
                document.peaks.forEachIndexed {index,value ->val x=size.width*index/maxOf(1,document.peaks.size);val height=value.coerceIn(0f,1f)*size.height/2;drawLine(color,Offset(x,size.height/2-height),Offset(x,size.height/2+height),2f)}
            }
            Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                Box(Modifier.weight(1f)) {AudioNumberField("dubWorkspace.start",options.start,!running) {editor.options=options.copy(start=it)}}
                Box(Modifier.weight(1f)) {AudioNumberField("dubWorkspace.end",options.end ?: document.duration,!running) {editor.options=options.copy(end=it)}}
            }
            Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                Box(Modifier.weight(1f)) {AudioNumberField("mobile.processing.fade_in",options.fadeIn,!running) {editor.options=options.copy(fadeIn=it)}}
                Box(Modifier.weight(1f)) {AudioNumberField("mobile.processing.fade_out",options.fadeOut,!running) {editor.options=options.copy(fadeOut=it)}}
            }
            AudioSlider("mobile.processing.gain_db",options.gainDB,-24f..24f,!running) {editor.options=options.copy(gainDB=it)}
            Toggle("mobile.processing.normalize",options.normalize) {if(!running) editor.options=options.copy(normalize=it)}
            if(options.normalize) AudioSlider("mobile.processing.target_peak",options.targetDB,-24f..-1f,!running) {editor.options=options.copy(targetDB=it)}
            Toggle("mobile.processing.noise_gate",options.noiseGate) {if(!running) editor.options=options.copy(noiseGate=it)}
            if(options.noiseGate) AudioSlider("mobile.processing.gate_threshold",options.gateDB,-80f..-10f,!running) {editor.options=options.copy(gateDB=it)}
            Secondary("mobile.editing.original",!running) {editor.perform(vm::fail) {
                val clip=PhoneAudioProcessing.process(document,AudioEditOptions(start=options.start,end=minOf(options.end ?: document.duration,options.start+30)),editor.workspace)
                previewIsTemporary=true;preview=clip.file
            }}
            Text(tr("mobile.processing.preview_hint"),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
        }
        if(running) {LinearProgressIndicator(progress={editor.progress},modifier=Modifier.fillMaxWidth());Secondary("common.cancel") {editor.work?.cancel()}}
        Action("mobile.processing.process",document!=null&&!running) {editor.perform(vm::fail) {
            val fresh=PhoneAudioProcessing.process(requireNotNull(document),options,editor.workspace) {editor.progress=it}
            preview=null
            withContext(Dispatchers.IO) {editor.result?.file?.parentFile?.deleteRecursively()}
            editor.result=fresh;editor.rendered=options;previewIsTemporary=false;preview=fresh.file
        }}
        if(result!=null) {
            Heading("mobile.processing.result")
            Text(tr("mobile.processing.peak")+" "+"%.1f dBFS".format(20*log10(max(.000001,result.peak))),style=MaterialTheme.typography.bodySmall)
            if(editor.rendered!=options) Text(tr("mobile.processing.changed_settings"),color=role("textMuted"))
            Secondary("common.play",!running) {previewIsTemporary=false;preview=result.file}
            if(!running) ChoicePicker("audiobook.format",format,listOf(Choice("wav","WAV"),Choice("m4a","AAC / M4A"),Choice("mp3","MP3"))) {format=it}
            Secondary("mobile.speak.share",!running&&editor.rendered==options) {editor.perform(vm::fail) {
                val file=BookExport.encode(result.file,format,synthetic=result.synthetic)
                shareLocalFile(context,file,when(format) {"mp3" -> "audio/mpeg";"m4a" -> "audio/mp4";else -> "audio/wav"})
            }}
        }
        preview?.let {LocalAudioPlayer(it,vm::fail)}
    }
}
@Composable private fun AudioNumberField(key: String,value: Double,enabled: Boolean,onChange: (Double)->Unit) {
    var text by remember(key) {mutableStateOf(value.toString())}
    LaunchedEffect(value) {if(text.toDoubleOrNull()!=value&&!value.isNaN()) text=value.toString()}
    Field(key,text,{if(enabled) {text=it;onChange(it.toDoubleOrNull() ?: Double.NaN)}})
}
@Composable private fun AudioSlider(key: String,value: Double,range: ClosedFloatingPointRange<Float>,enabled: Boolean,onChange: (Double)->Unit) {
    val label=tr(key);Text(label+" "+"%.1f".format(value),style=MaterialTheme.typography.bodySmall)
    Slider(value.toFloat(),{onChange(it.toDouble())},valueRange=range,enabled=enabled,modifier=Modifier.semantics {contentDescription=label})
}
