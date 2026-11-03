package com.voicestudio.mobile.features.dub

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.work.WorkManager
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.data.PhoneDubWorker
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.features.models.*
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import java.io.File

@Composable fun PhoneProjectScreen(vm: StudioViewModel, projectId: String? = null) {
    val phone by vm.phone.state.collectAsState()
    PhoneDubScreen(vm,phone.models.firstOrNull {it.capability=="clone"},projectId,libraryOnly=true)
}

@Composable internal fun PhoneDubScreen(vm: StudioViewModel,clone: PhoneModel?, projectId: String? = null, libraryOnly: Boolean = false,batchMode: Boolean = false) {
    val context=LocalContext.current
    val phone by vm.phone.state.collectAsState()
    val prefs by vm.preferences.collectAsState()
    val state by vm.state.collectAsState()
    val scope=rememberCoroutineScope()
    val work=remember {WorkManager.getInstance(context)}
    val flow=remember(work) {work.getWorkInfosByTagFlow(PhoneDubWorker.TAG)}
    val works by flow.collectAsState(emptyList())
    LaunchedEffect(works) {vm.phone.refreshDubs()}
    var batchPaths by rememberSaveable {mutableStateOf(emptyList<String>())}
    var batchNames by rememberSaveable {mutableStateOf(emptyList<String>())}
    var path by rememberSaveable {mutableStateOf<String?>(null)}
    var sourceName by rememberSaveable {mutableStateOf<String?>(null)}
    var targets by rememberSaveable {mutableStateOf(emptyList<String>())}
    var voice by rememberSaveable {mutableStateOf("")}
    var consent by rememberSaveable {mutableStateOf(false)}
    var audioOnly by rememberSaveable {mutableStateOf(false)}
    var copying by remember {mutableStateOf(false)}
    var more by rememberSaveable {mutableStateOf(false)}
    var exportID by remember {mutableStateOf<String?>(null)}
    var exportTarget by remember {mutableStateOf("")}
    var editing by remember {mutableStateOf<String?>(null)}
    var playing by rememberSaveable {mutableStateOf<String?>(null)}
    var savePath by rememberSaveable {mutableStateOf<String?>(null)}
    val catalog=rememberLanguageCatalog(vm::fail)
    val languages=catalog?.dubChoices().orEmpty()
    fun model(capability: String): PhoneModel? = phone.preferredModel(phone.models.filter {it.capability==capability},vm.phone.ramBytes,prefs.phoneModels["dub-$capability"])
    val asr=model("asr");val translator=model("translate")
    val models=listOfNotNull(asr,translator,clone)
    val missing=models.filterNot {phone.canRun(it)}
    val ready=models.size==3&&missing.isEmpty()
    LaunchedEffect(phone.voices) {if(voice.isNotEmpty()&&phone.voices.none {it.id==voice}) voice=""}
    val picker=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->
        if(uri!=null) scope.launch {
            copying=true
            try {
                val selected=withContext(Dispatchers.IO) {copyMedia(context,uri,"media") to mediaDisplayName(context,uri)}
                path?.let {File(it).delete()};path=selected.first.path;sourceName=selected.second ?: selected.first.name
            } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {copying=false}
        }
    }
    val batchPicker=rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) {uris ->
        scope.launch {
            copying=true
            try {for(uri in uris) {
                val selected=copyMedia(context,uri,"media")
                batchPaths=batchPaths+selected.path;batchNames=batchNames+(mediaDisplayName(context,uri) ?: selected.name)
            }} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {copying=false}
        }
    }
    val save=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) {uri ->
        val file=savePath?.let(::File);savePath=null
        if(file!=null&&uri!=null) scope.launch {
            try {withContext(Dispatchers.IO) {context.contentResolver.openOutputStream(uri)!!.use {out ->file.inputStream().use {it.copyTo(out)}}}}
            catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
        }
    }
    Screen {
        ScreenTitle(if(libraryOnly) "projects.title" else if(batchMode) "batch.title" else "mobile.tabs.dub")
        if(projectId==null && !libraryOnly) {
        DubSourceCard(!copying&&!state.busy) {if(batchMode) batchPicker.launch(arrayOf("audio/*","video/*")) else picker.launch(arrayOf("audio/*","video/*"))}
        batchPaths.forEachIndexed {index,source ->SelectedDubSource(batchNames.getOrNull(index) ?: File(source).name) {
            File(source).delete();batchPaths=batchPaths.filterIndexed {i,_->i!=index};batchNames=batchNames.filterIndexed {i,_->i!=index}
        }}
        path?.let {source ->SelectedDubSource(sourceName ?: File(source).name) {File(source).delete();path=null;sourceName=null}}
        if(copying||catalog==null) InlineLoading()
        MultiPicker("mobile.dub.target_languages",targets,languages,tile=true) {targets=it}
        ChoicePicker("mobile.speak.voice",voice,listOf(Choice("",tr("mobile.simple.reference")))+phone.voices.map {Choice(it.id,it.name)},tile=true) {voice=it}
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {ConsentCheck("mobile.simple.voice_consent",consent) {consent=it}}
            Info("mobile.voices.consent_title","mobile.voices.consent_body")
        }
        ModelRamWarning(vm,models)
        Secondary("mobile.simple.more") {more=!more}
        if(more) {
            Toggle("mobile.simple.audio_only",audioOnly) {audioOnly=it}
            TaskModelPicker(vm,"dub-asr",setOf("asr"),asr,showRamWarning=false)
            TaskModelPicker(vm,"dub-translate",setOf("translate"),translator,showRamWarning=false)
            TaskModelPicker(vm,"dub",setOf("clone"),clone,showRamWarning=false)
        }
        MissingModelsCard(vm,"mobile.tabs.dub",missing)
        Action("mobile.simple.dub",ready&&consent&&(if(batchMode) batchPaths.isNotEmpty() else path!=null)&&targets.isNotEmpty()&&!copying&&!state.busy) {
            val sources=if(batchMode) batchPaths.mapIndexed {index,path->File(path) to (batchNames.getOrNull(index) ?: File(path).name)} else path?.let {listOf(File(it) to (sourceName ?: File(it).name))}.orEmpty()
            val chosenVoice=phone.voices.firstOrNull {it.id==voice}
            val chosenTargets=languages.filter {it.first in targets}.map {DubTarget(it.first,it.second)}
            val selectedModels=models.toList()
            val exportAudio=audioOnly
            vm.run {
                val reference=chosenVoice?.let {vm.phone.voices.reference(it)}
                for((source,title) in sources) {
                    val request=vm.phone.dubs.create(source,title,chosenTargets,selectedModels,true,reference,chosenVoice?.transcript,exportAudio)
                    PhoneDubWorker.enqueue(context,request.id)
                    source.delete()
                    if(batchMode) {val index=batchPaths.indexOf(source.path);batchPaths=batchPaths.filterIndexed {i,_->i!=index};batchNames=batchNames.filterIndexed {i,_->i!=index}}
                    else {path=null;sourceName=null}
                }
                consent=false
                vm.phone.refreshDubs()
            }
        }
        }
        phone.dubs.filter {projectId==null || it.request.id==projectId}.filterNot {it.checkpoint.done}.forEach {record ->
            val active=works.firstOrNull {"phone-dub:${record.request.id}" in it.tags&&!it.state.isFinished}
            val percent=active?.progress?.getInt("percent",record.checkpoint.percent) ?: record.checkpoint.percent
            val stage=active?.progress?.getString("stage") ?: record.checkpoint.stage
            Group {
                Column(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    DubGlyph(44.dp,active=active!=null)
                    Text(record.request.title,maxLines=1,overflow=TextOverflow.Ellipsis)
                    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                        if(stage.contains("stage_")||stage in setOf("mobile.simple.mixing","mobile.simple.exporting","mobile.simple.cancelled"))
                            Text(tr(stage),Modifier.weight(1f),maxLines=1,overflow=TextOverflow.Ellipsis)
                        else ErrorMessage(stage,record.checkpoint.errorParams,Modifier.weight(1f))
                        Text(tr("mobile.simple.percent",mapOf("percent" to percent)))
                    }
                    LinearProgressIndicator(progress={percent/100f},modifier=Modifier.fillMaxWidth())
                    PhoneJobStatus(running=active!=null)
                    if(active!=null) Secondary("common.cancel") {work.cancelUniqueWork("phone-dub:${record.request.id}")}
                    else Row {
                        Secondary("mobile.simple.resume") {PhoneDubWorker.enqueue(context,record.request.id)}
                        IconAction("mobile.simple.remove_job",Icons.Default.Delete) {vm.run {vm.phone.dubs.delete(record.request.id);vm.phone.refreshDubs()}}
                    }
                }
            }
        }
        Heading("mobile.dub.history")
        if(phone.dubs.filter {projectId==null || it.request.id==projectId}.none {it.checkpoint.outputs.isNotEmpty()}) EmptyState("mobile.simple.empty_dubs")
        phone.dubs.filter {projectId==null || it.request.id==projectId}.forEach {record ->record.checkpoint.outputs.forEach {output ->
            val file=vm.phone.dubs.file(record.request.id,output.file)
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(record.request.title,maxLines=1,overflow=TextOverflow.Ellipsis)
                    Text(output.name,style=MaterialTheme.typography.labelSmall,color=role("textMuted"))
                }
                IconAction("player.play",Icons.Default.PlayArrow) {playing=file.path}
                IconAction("mobile.speak.share",Icons.Default.Share) {scope.launch {try {shareLocalFile(context,file,if(file.extension=="mp4") "video/mp4" else "audio/wav")} catch(e:Exception) {vm.fail(e)}}}
                IconAction("common.save",VSIcons.Download) {savePath=file.path;save.launch(record.request.title.substringBeforeLast('.').ifBlank {record.request.id}+"-${output.language}.${file.extension}")}
                ReportAction(vm) {ReportSubjects.phoneDub(record.request,phone.models)}
                if(record.checkpoint.done) IconAction("mobile.dub_advanced.exports",Icons.Default.Share) {exportID=record.request.id;exportTarget=output.language}
                if(record.checkpoint.done && vm.phone.dubs.file(record.request.id,"original.pcm").isFile) IconAction("clone.edit",Icons.Default.Edit) {editing=record.request.id}
                if(record.checkpoint.done) IconAction("mobile.simple.remove_job",Icons.Default.Delete) {vm.run {if(playing==file.path) playing=null;vm.phone.dubs.delete(record.request.id);vm.phone.refreshDubs()}}
            }
        }}
        playing?.let {LocalAudioPlayer(File(it),vm::fail)}
        exportID?.let {id ->
            androidx.compose.ui.window.Dialog(onDismissRequest={exportID=null},properties=androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth=false)) {
                Surface(Modifier.fillMaxSize(),color=role("canvas")) {PhoneDubExportScreen(vm,id,exportTarget) {exportID=null}}
            }
        }
        editing?.let {id ->
            androidx.compose.ui.window.Dialog(onDismissRequest={editing=null},properties=androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth=false)) {
                Surface(Modifier.fillMaxSize(),color=role("canvas")) {DubEditorScreen(vm,id) {editing=null}}
            }
        }
    }
}

@Composable fun BatchDubScreen(vm: StudioViewModel) {
    val phone by vm.phone.state.collectAsState();val prefs by vm.preferences.collectAsState()
    val models=phone.models.filter {it.capability=="clone"}
    val clone=phone.preferredModel(models,vm.phone.ramBytes,prefs.phoneModels["dub"])
    PhoneDubScreen(vm,clone,batchMode=true)
}
