package com.voicestudio.mobile.features.dub

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import com.voicestudio.mobile.data.PhoneDubWorker
import kotlinx.coroutines.*
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID

internal data class DubEditorSnapshot(val segments: List<DubSegmentState>,val voices: Map<String,String>)
class DubEditorSession: ViewModel() {
    internal val segments=mutableStateOf(emptyList<DubSegmentState>())
    internal val target=mutableStateOf("")
    internal val voices=mutableStateOf(emptyMap<String,String>())
    internal val undo=mutableStateOf(emptyList<DubEditorSnapshot>())
    internal val redo=mutableStateOf(emptyList<DubEditorSnapshot>())
    internal val mutationKey=mutableStateOf<String?>(null)
    internal val timingText=mutableStateOf(emptyMap<String,String>())
    internal val splitPositions=mutableStateOf(emptyMap<String,Float>())
    internal var initialized=false
}
@Composable fun DubEditorScreen(vm: StudioViewModel,id: String,onSaved: ()->Unit) {
    val phone by vm.phone.state.collectAsState()
    val record=phone.dubs.firstOrNull {it.request.id==id}
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    // Keep potentially large drafts out of Android's size-limited saved-state Bundle.
    val draft: DubEditorSession=viewModel(key="dub-editor-$id-${record?.checkpoint?.outputs?.joinToString {it.sha256}}")
    var segments by draft.segments
    var target by draft.target
    var voices by draft.voices
    var undo by draft.undo
    var redo by draft.redo
    var mutationKey by draft.mutationKey
    var timingText by draft.timingText
    var splitPositions by draft.splitPositions
    LaunchedEffect(record?.request?.id) {if(!draft.initialized&&record!=null) {segments=record.checkpoint.segments;target=record.request.targets.firstOrNull()?.code.orEmpty();draft.initialized=true}}
    var saving by remember {mutableStateOf(false)}
    var replaceCastOverrides by remember {mutableStateOf(false)}
    var previewing by remember {mutableStateOf(false)}
    var preview by remember(id) {mutableStateOf<DubPreview?>(null)}
    var previewLabel by remember {mutableStateOf("mobile.editing.original")}
    var previewSegmentID by remember {mutableStateOf<String?>(null)}
    var pendingImport by remember {mutableStateOf<DubEditorSnapshot?>(null)}
    fun selection(segment: DubSegmentState)=voices[segment.id.orEmpty()].orEmpty().ifEmpty {if(voices.containsKey(segment.id.orEmpty())) "" else if(segment.referenceFile==null) "" else "__keep__"}
    fun rememberEdit(key: String? = null) {
        if(key==null||mutationKey!=key) undo=(undo+DubEditorSnapshot(segments,voices)).takeLast(50)
        mutationKey=key;redo=emptyList();preview=null
    }
    fun restore(backward: Boolean) {
        val snapshot=if(backward) undo.lastOrNull() else redo.lastOrNull()
        if(snapshot!=null) {
            val current=DubEditorSnapshot(segments,voices)
            if(backward) {undo=undo.dropLast(1);redo=redo+current} else {redo=redo.dropLast(1);undo=undo+current}
            segments=snapshot.segments;voices=snapshot.voices;timingText=emptyMap();mutationKey=null;preview=null
        }
    }
    fun previewSegment(segment: DubSegmentState,generated: Boolean) {
        previewing=true
        scope.launch {
            try {preview=vm.phone.dubs.preview(id,segment,target,generated);previewSegmentID=segment.id;previewLabel=if(generated) "mobile.editing.generated" else "mobile.editing.original"}
            catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {previewing=false}
        }
    }
    fun export(vtt: Boolean) {
        scope.launch {
            try {
                val text=DubSubtitles.export(segments,target,vtt)
                val file=withContext(Dispatchers.IO) {File(context.cacheDir,"dub-subtitles-${UUID.randomUUID()}.${if(vtt) "vtt" else "srt"}").apply {writeText(text)}}
                try {shareLocalFile(context,file,if(vtt) "text/vtt" else "application/x-subrip")} finally {file.delete()}
            } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
        }
    }
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->
        if(uri!=null) scope.launch {
            try {
                val text=withContext(Dispatchers.IO) {
                    context.contentResolver.openInputStream(uri)?.use {input ->
                        val output=ByteArrayOutputStream();val bytes=ByteArray(16384)
                        while(true) {ensureActive();val count=input.read(bytes);if(count<0) break;if(output.size()+count>4_000_000) throw ClientFailure("mobile.editing.subtitle_invalid");output.write(bytes,0,count)}
                        val decoder=Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
                        decoder.decode(java.nio.ByteBuffer.wrap(output.toByteArray())).toString()
                    } ?: throw ClientFailure("mobile.editing.subtitle_invalid")
                }
                val imported=DubEditing.importTranslation(segments,DubSubtitles.parse(text),target,(record?.checkpoint?.frames ?: 0)/24000.0)
                val importedVoices=imported.associate {cue ->
                    val selections=segments.filter {it.speech.start<cue.speech.end&&it.speech.end>cue.speech.start}.map(::selection).distinct()
                    if(selections.size!=1) throw ClientFailure("mobile.editing.merge_invalid")
                    requireNotNull(cue.id) to selections.single()
                }
                pendingImport=DubEditorSnapshot(imported,importedVoices)
            } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(if(e is ClientFailure) e else ClientFailure("mobile.editing.subtitle_invalid"))}
        }
    }
    DisposableEffect(preview) {val old=preview;onDispose {old?.file?.delete()}}
    LaunchedEffect(target) {preview=null;mutationKey=null}
    Column(Modifier.fillMaxSize().imePadding().dismissKeyboardOnTap()) {
    LazyColumn(Modifier.weight(1f),contentPadding=PaddingValues(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
        item { Column(verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
        ScreenTitle("nav.dub")
        ChoicePicker("mobile.speak.language",target,record?.request?.targets.orEmpty().map {Choice(it.code,it.name)}) {if(!saving&&!previewing) target=it}
        Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Box(Modifier.weight(1f)) {Secondary("mobile.editing.undo",undo.isNotEmpty()&&!saving&&!previewing) {restore(true)}}
            Box(Modifier.weight(1f)) {Secondary("mobile.editing.redo",redo.isNotEmpty()&&!saving&&!previewing) {restore(false)}}
        }
        Secondary("mobile.editing.import_subtitles",!saving&&!previewing) {importer.launch(arrayOf("text/*","application/x-subrip","application/octet-stream"))}
        Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Box(Modifier.weight(1f)) {Secondary("mobile.editing.export_srt",!saving) {export(false)}}
            Box(Modifier.weight(1f)) {Secondary("mobile.editing.export_vtt",!saving) {export(true)}}
        }
        Heading("mobile.dub_advanced.cast")
        Text(tr("mobile.dub_advanced.manual_cast_hint"),style=MaterialTheme.typography.bodySmall)
        if(PhoneDubCast.speakers(segments).isNotEmpty()) {
            Toggle("mobile.dub_advanced.replace_overrides",replaceCastOverrides) {replaceCastOverrides=it}
            PhoneDubCast.speakers(segments).forEach {speaker ->
                val assignments=segments.filter {it.speaker?.trim()==speaker&&!PhoneDubCast.isLineOverride(it)}.mapNotNull {it.voiceID}.distinct()
                val selected=assignments.singleOrNull()?.takeIf {value ->value.isEmpty()||phone.voices.any {it.id==value}} ?: "__keep__"
                // Speaker names are user content, while the shared picker receives its localized role.
                Text(speaker,style=MaterialTheme.typography.titleSmall)
                ChoicePicker("mobile.speak.voice",selected,listOf(Choice("__keep__",tr("mobile.dub_advanced.keep_cast")),Choice("",tr("stories.defaultVoice")))+phone.voices.map {Choice(it.id,it.name)}) {value ->
                    if(!saving&&!previewing&&value!="__keep__") {rememberEdit()
                        val affected=segments.filter {it.speaker?.trim()==speaker&&(replaceCastOverrides||!PhoneDubCast.isLineOverride(it))}
                        voices=voices+affected.mapNotNull {it.id?.let {id ->id to value}}.toMap()
                        segments=PhoneDubCast.assigning(segments,speaker,value,replaceCastOverrides)
                    }
                }
            }
        }
        } }
        itemsIndexed(segments,key={index,segment ->segment.id ?: "legacy-$index"}) {index,segment ->
            val segmentID=segment.id ?: "legacy-$index"
            Group {Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                Field("mobile.dub_advanced.speaker",segment.speaker.orEmpty(),{value ->if(!saving&&!previewing) {rememberEdit("speaker-$segmentID")
                    val changed=if(segment.referenceOrigin=="cast") {voices=voices+(segmentID to "");segment.copy(speaker=value,referenceFile=null,referenceText=null,referenceOrigin=null,voiceID=null)} else segment.copy(speaker=value)
                    segments=segments.toMutableList().also {it[index]=changed}
                }})
                Field("mobile.editing.source_text",segment.speech.text,{if(!saving&&!previewing) {rememberEdit("source-$segmentID");segments=segments.toMutableList().also {list->list[index]=segment.copy(speech=segment.speech.copy(text=it))}}},multiline=true)
                Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    Box(Modifier.weight(1f)) {Field("dubWorkspace.start",timingText["$segmentID-start"] ?: segment.speech.start.toString(),{if(!saving&&!previewing) {rememberEdit("start-$segmentID");timingText=timingText+("$segmentID-start" to it);segments=segments.toMutableList().also {list->list[index]=segment.copy(speech=segment.speech.copy(start=it.toDoubleOrNull() ?: Double.NaN))}}})}
                    Box(Modifier.weight(1f)) {Field("dubWorkspace.end",timingText["$segmentID-end"] ?: segment.speech.end.toString(),{if(!saving&&!previewing) {rememberEdit("end-$segmentID");timingText=timingText+("$segmentID-end" to it);segments=segments.toMutableList().also {list->list[index]=segment.copy(speech=segment.speech.copy(end=it.toDoubleOrNull() ?: Double.NaN))}}})}
                }
                Field("tools.translated_line",segment.translations[target].orEmpty(),{if(!saving&&!previewing) {rememberEdit("translation-$segmentID-$target");segments=segments.toMutableList().also {list->list[index]=segment.copy(translations=segment.translations+(target to it))}}},multiline=true)
                if(!saving&&!previewing) ChoicePicker("mobile.speak.voice",selection(segment),(if(segment.referenceFile==null) emptyList() else listOf(Choice("__keep__",tr("clone.keep_reference"))))+listOf(Choice("",tr("stories.defaultVoice")))+phone.voices.map {Choice(it.id,it.name)}) {value ->rememberEdit();voices=voices+(segmentID to value)
                    if(value!="__keep__") segments=segments.toMutableList().also {it[index]=segment.copy(referenceOrigin="segment",voiceID=value)}
                }
                Text(tr("mobile.processing.segment_gain")+" "+(segment.gain*100).toInt()+"%",style=MaterialTheme.typography.bodySmall)
                val gainLabel=tr("mobile.processing.segment_gain")
                Slider(value=segment.gain.toFloat(),onValueChange={value ->if(!saving&&!previewing) {rememberEdit("gain-$segmentID");segments=segments.toMutableList().also {it[index]=segment.copy(gain=value.toDouble())}}},valueRange=0f..2f,enabled=!saving&&!previewing,modifier=Modifier.semantics {contentDescription=gainLabel})
                Row(Modifier.fillMaxWidth()) {Text(tr("mobile.processing.mute"),Modifier.weight(1f));Switch(segment.muted,{value ->rememberEdit();segments=segments.toMutableList().also {it[index]=segment.copy(muted=value)}},enabled=!saving&&!previewing)}
                Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    Box(Modifier.weight(1f)) {Secondary("mobile.editing.original",!saving&&!previewing) {previewSegment(segment,false)}}
                    Box(Modifier.weight(1f)) {Secondary("mobile.editing.generated",!saving&&!previewing&&selection(segment)==if(segment.referenceFile==null) "" else "__keep__") {previewSegment(segment,true)}}
                }
        preview?.takeIf {previewSegmentID==segment.id}?.let {clip ->
            Text(tr(previewLabel))
            val color=role("messageAction");val label=tr("mobile.editing.waveform")
            Canvas(Modifier.fillMaxWidth().height(64.dp).semantics {contentDescription=label}) {
                clip.peaks.forEachIndexed {index,value ->val x=size.width*index/clip.peaks.size;val height=value*size.height/2;drawLine(color,Offset(x,size.height/2-height),Offset(x,size.height/2+height),2f)}
            }
            LocalAudioPlayer(clip.file,vm::fail)
        }
                if(segment.speech.start.isFinite()&&segment.speech.end.isFinite()&&segment.speech.end-segment.speech.start>.1) {
                    val position=(splitPositions[segmentID] ?: ((segment.speech.start+segment.speech.end)/2).toFloat()).coerceIn((segment.speech.start+.05).toFloat(),(segment.speech.end-.05).toFloat())
                    Text(tr("mobile.editing.split_at")+" "+"%.2f".format(position),style=MaterialTheme.typography.bodySmall)
                    val splitLabel=tr("mobile.editing.split_at")
                    Slider(position,{splitPositions=splitPositions+(segmentID to it)},valueRange=(segment.speech.start+.05).toFloat()..(segment.speech.end-.05).toFloat(),enabled=!saving&&!previewing,modifier=Modifier.semantics {contentDescription=splitLabel})
                    Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                        Box(Modifier.weight(1f)) {Secondary("mobile.editing.split",!saving&&!previewing) {
                            try {val result=DubEditing.split(segments,index,position.toDouble());rememberEdit();voices=voices+(requireNotNull(result[index+1].id) to selection(segment));segments=result;timingText=emptyMap()}
                            catch(e:Exception) {vm.fail(ClientFailure("mobile.editing.split_invalid"))}
                        }}
                        Box(Modifier.weight(1f)) {Secondary("mobile.editing.merge",!saving&&!previewing&&index+1<segments.size) {
                            try {if(selection(segment)!=selection(segments[index+1])) throw ClientFailure("mobile.editing.merge_invalid");val result=DubEditing.merge(segments,index);rememberEdit();segments=result;timingText=emptyMap()}
                            catch(e:Exception) {vm.fail(if(e is ClientFailure) e else ClientFailure("mobile.editing.merge_invalid"))}
                        }}
                    }
                }
            }}
        }
    }
        Box(Modifier.fillMaxWidth().padding(VSTheme.inset)) { Action("dub.generate_dub",!saving&&!previewing&&segments.isNotEmpty()) {
            saving=true;preview=null
            scope.launch {
                try {
                    val referenceCache=mutableMapOf<String,Pair<LocalAudio,String>>()
                    val references=segments.mapIndexedNotNull {index,segment ->
                        val voiceID=selection(segment)
                        if(voiceID.isBlank()||voiceID=="__keep__") null else {
                            val voice=phone.voices.firstOrNull {it.id==voiceID} ?: throw ClientFailure("audiobook.blocked.voice")
                            index to referenceCache.getOrPut(voice.id) {vm.phone.voices.reference(voice) to voice.transcript}
                        }
                    }.toMap()
                    val edits=segments.map {segment ->if(selection(segment)=="") segment.copy(referenceFile=null,referenceText=if(segment.referenceFile!=null) null else segment.referenceText) else segment}
                    vm.phone.dubs.edit(id,edits,references)
                    vm.phone.refresh();PhoneDubWorker.enqueue(context,id);onSaved()
                } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {saving=false}
            }
        } }
    }
    if(pendingImport!=null) AlertDialog(onDismissRequest={pendingImport=null},title={Text(tr("mobile.editing.replace_subtitles"))},text={Text(tr("mobile.editing.replace_subtitles_detail"))},confirmButton={TextButton(onClick={pendingImport?.let {draft->rememberEdit();segments=draft.segments;voices=draft.voices;timingText=emptyMap();pendingImport=null}}) {Text(tr("mobile.editing.replace"))}},dismissButton={TextButton(onClick={pendingImport=null}) {Text(tr("common.cancel"))}})
}
