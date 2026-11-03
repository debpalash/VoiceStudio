package com.voicestudio.mobile.features

import android.app.Application
import android.content.ClipboardManager
import android.content.ClipData
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.work.WorkManager
import com.voicestudio.mobile.data.PhoneTranscriptionWorker
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.MissingModelsCard
import com.voicestudio.mobile.features.models.TaskModelPicker
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import kotlinx.serialization.builtins.ListSerializer
import java.io.File
import java.util.UUID

class TranscriptImportSession(application: Application): AndroidViewModel(application) {
    var work by mutableStateOf<Job?>(null)
    var progress by mutableStateOf(0f)
    fun start(fail: (Throwable)->Unit,action: suspend ()->Unit) {
        if(work?.isActive==true) return
        progress=0f;work=viewModelScope.launch {try {action()} catch(e:CancellationException) {throw e} catch(e:Exception) {fail(e)} finally {work=null}}
    }
}
@Composable fun TranscriptsScreen(vm: StudioViewModel,onDictate: ()->Unit) {
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val importerSession: TranscriptImportSession=viewModel(key="transcript-import")
    val phone by vm.phone.state.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val asr=phone.preferredModel(phone.models.filter {it.capability=="asr"},vm.phone.ramBytes,preferences.phoneModels["file-transcribe"])
    val ready=asr?.let {phone.canRun(it)}==true
    val manager=remember(context) {WorkManager.getInstance(context)}
    val jobs by remember(manager) {manager.getWorkInfosByTagFlow("transcriptions")}.collectAsState(initial=emptyList())
    var records by remember {mutableStateOf(emptyList<PhoneTranscript>())}
    var query by rememberSaveable {mutableStateOf("")}
    var editing by rememberSaveable {mutableStateOf<String?>(null)}
    var deleting by rememberSaveable {mutableStateOf<String?>(null)}
    var editText by rememberSaveable {mutableStateOf("")}
    var format by rememberSaveable {mutableStateOf("txt")}
    suspend fun refresh() {records=vm.phone.transcripts.list()}
    fun action(block: suspend ()->Unit) {scope.launch {try {block()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}}
    fun export(items: List<PhoneTranscript>,selectedFormat: String) {action {
        val file=withContext(Dispatchers.IO) {
            val text=when(selectedFormat) {
                "json"->kotlinx.serialization.json.Json.encodeToString(ListSerializer(PhoneTranscript.serializer()),items)
                "srt","vtt"->PhoneTranscriptExport.subtitles(items.first(),selectedFormat=="vtt")
                else->PhoneTranscriptExport.text(items)
            }
            val bytes=text.toByteArray(Charsets.UTF_8);require(bytes.size<=64*1024*1024)
            val directory=File(context.cacheDir,"transcript-export/${UUID.randomUUID()}").apply {mkdirs()}
            File(directory,"transcripts.$selectedFormat").apply {writeBytes(bytes)}
        }
        shareLocalFile(context,file,if(selectedFormat=="json") "application/json" else "text/plain")
    }}
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->if(uri!=null&&asr!=null&&ready) importerSession.start(vm::fail) {
        val title=withContext(Dispatchers.IO) {mediaDisplayName(context,uri)} ?: java.util.UUID.randomUUID().toString()
        val source=copyMedia(context,uri,"audio",maxBytes=PhoneTranscriptStore.MAX_SOURCE_BYTES)
        try {val record=vm.phone.transcripts.prepare(source,title,asr,progress={importerSession.progress=it});PhoneTranscriptionWorker.enqueue(context,record);refresh()} finally {source.delete()}
    }}
    LaunchedEffect(Unit) {try {vm.phone.refresh();refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    LaunchedEffect(jobs) {try {refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    val visible=remember(records,query) {records.filter {query.isEmpty()||it.title.contains(query,true)||it.text.contains(query,true)}}
    Screen {
        ScreenTitle("transcriptions.title")
        Text(tr("mobile.transcripts.hint"))
        TaskModelPicker(vm,"file-transcribe",setOf("asr"),asr,key="mobile.simple.asr")
        if(!ready&&asr!=null) MissingModelsCard(vm,"transcriptions.title",listOf(asr))
        Action("mobile.dub.pick_media",ready&&importerSession.work==null) {importer.launch(arrayOf("audio/*","video/*"))}
        if(importerSession.work!=null) {LinearProgressIndicator(progress={importerSession.progress},modifier=Modifier.fillMaxWidth());Secondary("common.cancel") {importerSession.work?.cancel()}}
        Secondary("transcriptions.capture",onClick=onDictate)
        Field("transcriptions.search_placeholder",query,{query=it})
        if(records.isNotEmpty()) Secondary("transcriptions.export_title") {export(records,"txt")}
        ChoicePicker("audiobook.format",format,listOf(Choice("txt","TXT"),Choice("json","JSON"),Choice("srt","SRT"),Choice("vtt","VTT"))) {format=it}
        if(visible.isEmpty()) EmptyState(if(query.isEmpty()) "transcriptions.empty_title" else "transcriptions.empty_search_title")
        visible.forEach {record ->
            val active=jobs.firstOrNull {"transcript:${record.id}" in it.tags&&!it.state.isFinished}
            val failed=jobs.filter {"transcript:${record.id}" in it.tags}.maxByOrNull {it.runAttemptCount}?.outputData?.getString("error")
            Group {Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                Text(record.title,style=MaterialTheme.typography.titleMedium)
                Text(java.text.DateFormat.getDateTimeInstance().format(java.util.Date(record.createdAt.toLong())),style=MaterialTheme.typography.bodySmall)
                Text(record.text,maxLines=5)
                if(!record.complete) {
                    LinearProgressIndicator(progress={if(active!=null) active.progress.getInt("percent",0)/100f else record.progress.toFloat()},modifier=Modifier.fillMaxWidth())
                    if(active!=null) {PhoneJobStatus(true);Secondary("common.cancel") {manager.cancelWorkById(active.id)}}
                    else Secondary("common.resume") {PhoneTranscriptionWorker.enqueue(context,record)}
                    if(failed!=null&&active==null) ErrorMessage(failed)
                }
                Row(horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    TextButton(onClick={context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText(null,record.text))}) {Text(tr("transcriptions.copy"))}
                    TextButton(onClick={editing=record.id;editText=record.text},enabled=record.complete) {Text(tr("clone.edit"))}
                    TextButton(onClick={export(listOf(record),format)},enabled=format !in listOf("srt","vtt")||(record.segments.isNotEmpty()&&record.editedText==null)) {Text(tr("transcriptions.export"))}
                }
                TextButton(onClick={deleting=record.id},enabled=active==null) {Text(tr("transcriptions.delete"))}
            }}
        }
    }
    records.firstOrNull {it.id==editing}?.let {record ->
        AlertDialog(onDismissRequest={editing=null},title={Text(record.title)},text={Column {
            Text(tr("mobile.transcripts.edited_hint"))
            Field("transcriptions.title",editText,{editText=it},multiline=true)
            TextButton(onClick={editText=record.originalText}) {Text(tr("transcriptions.original_transcript"))}
        }},confirmButton={TextButton(onClick={action {vm.phone.transcripts.edit(record.id,editText.takeUnless {it==record.originalText});refresh();editing=null}},enabled=editText.length<=1_000_000) {Text(tr("common.save"))}},dismissButton={TextButton(onClick={editing=null}) {Text(tr("common.cancel"))}})
    }
    records.firstOrNull {it.id==deleting}?.let {record ->
        AlertDialog(onDismissRequest={deleting=null},title={Text(tr("transcriptions.delete"))},text={Text(record.title)},confirmButton={TextButton(onClick={action {vm.phone.transcripts.delete(record.id);refresh();deleting=null}}) {Text(tr("common.delete"))}},dismissButton={TextButton(onClick={deleting=null}) {Text(tr("common.cancel"))}})
    }
}
