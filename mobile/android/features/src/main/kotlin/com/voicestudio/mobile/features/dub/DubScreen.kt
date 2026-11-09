package com.voicestudio.mobile.features.dub

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.layout.*
import androidx.compose.ui.semantics.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.work.WorkManager
import androidx.work.WorkInfo
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.data.DubWorker
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.serialization.decodeFromString
import java.io.File

@Composable fun DubScreen(vm:StudioViewModel) {
    com.voicestudio.mobile.features.models.TaskLocation(vm,"dub",setOf("clone"),dependencies=setOf("asr","translate")) {location,model ->
        if(location==com.voicestudio.mobile.local.RunLocation.COMPUTER) ComputerDubScreen(vm) else PhoneDubScreen(vm,model)
    }
}
@Composable fun ComputerDubScreen(vm:StudioViewModel) {
    val context=LocalContext.current
    val state by vm.state.collectAsState()
    val work=remember {WorkManager.getInstance(context)}
    val host=state.hostUrl ?: return
    val workFlow=remember(host) {work.getWorkInfosByTagFlow(host)}
    val works by workFlow.collectAsState(emptyList())
    val scope=rememberCoroutineScope()
    var visibleFailures by remember(host) {mutableStateOf(emptySet<java.util.UUID>())}
    LaunchedEffect(host) {
        collectDubFailureReceipts(
            snapshotFlow { works.filter { it.state == WorkInfo.State.FAILED }.map { it.id }.toSet() },
            consume = { vm.store.consumeDubFailure(it) },
            display = { visibleFailures = visibleFailures + it },
            report = vm::fail,
        )
    }
    var link by rememberSaveable {mutableStateOf("")}
    var filePath by rememberSaveable {mutableStateOf<String?>(null)}
    var sourceName by rememberSaveable {mutableStateOf<String?>(null)}
    var inputType by rememberSaveable {mutableStateOf("video")}
    var targets by rememberSaveable {mutableStateOf(listOf<String>())}
    var voice by rememberSaveable {mutableStateOf(DubVoiceChoice.UNSELECTED)}
    var pending by remember {mutableStateOf<PendingDub?>(null)}
    var advancedJob by remember {mutableStateOf<String?>(null)}
    var advancedTitle by remember {mutableStateOf("")}
    var copying by remember {mutableStateOf(false)}
    var savePath by rememberSaveable {mutableStateOf<String?>(null)}
    val save=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) {uri ->
        val path=savePath;savePath=null
        if(path!=null) scope.launch {
            val file=File(path)
            try {
                if(uri!=null) withContext(Dispatchers.IO) {
                    requireNotNull(context.contentResolver.openOutputStream(uri)).use {out -> file.inputStream().use {it.copyTo(out)}}
                }
            } catch(cancelled:CancellationException) {throw cancelled}
            catch(error:Exception) {vm.fail(error)}
            finally {file.delete()}
        }
    }
    val catalog=rememberLanguageCatalog(vm::fail)
    val languages=catalog?.dubChoices().orEmpty()
    LaunchedEffect(state.profiles) {if(voice.isNotEmpty() && state.profiles.none {it.id==voice}) voice=DubVoiceChoice.UNSELECTED}
    val picker=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->
        if(uri!=null) scope.launch {
            copying=true
            try {
                val selected=withContext(Dispatchers.IO) {
                    val type=if(context.contentResolver.getType(uri)?.startsWith("audio/")==true) "audio" else "video"
                    Triple(copyMedia(context,uri,if(type=="audio") "m4a" else "mp4"),mediaDisplayName(context,uri),type)
                }
                filePath?.let {File(it).delete()}
                filePath=selected.first.absolutePath
                sourceName=selected.second ?: uri.lastPathSegment ?: selected.first.name
                inputType=selected.third
            }
            catch(cancelled:CancellationException) {throw cancelled}
            catch(e:Exception) {vm.fail(e)} finally {copying=false}
        }
    }
    fun enqueue(draft: PendingDub, confirmed: Boolean) {
        val authorization = draft.engine.authorize(confirmed) ?: return
        // The entire screen is disposed on a connection change. Also reject a
        // click delivered while the old composition is being removed.
        if(vm.state.value.hostUrl != draft.host) return
        DubWorker.enqueue(context,draft.host,draft.file,draft.link,draft.type,draft.languages,draft.voice,authorization,draft.sourceName)
        filePath=null;sourceName=null;link=""
    }
    pending?.let {draft ->
        AlertDialog(shape=MaterialTheme.shapes.large,containerColor=role("surfaceOverlay"),
            onDismissRequest={pending=null},title={Text(tr("mobile.dub.translation_online_title"))},
            text={Text(tr("mobile.dub.translation_online_body",mapOf("service" to draft.engine.consentName)))},
            confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={pending=null;try {enqueue(draft,true)} catch(e:Exception) {vm.fail(e)}}) {Text(tr("mobile.dub.translation_allow"))}},
            dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={pending=null}) {Text(tr("mobile.dub.translation_decline"))}})
    }
    Screen {
        ScreenTitle("mobile.tabs.dub")
        if(copying || catalog==null) InlineLoading()
        DubSourceCard(!copying) {picker.launch(arrayOf("video/*","audio/*"))}
        if(filePath!=null) {
            SelectedDubSource(sourceName ?: File(filePath!!).name) {File(filePath!!).delete();filePath=null;sourceName=null}
        }
        Field("mobile.dub.paste_url",link,{link=it})
        MultiPicker("mobile.dub.target_languages",targets,languages,tile=true) {targets=it}
        ChoicePicker("mobile.speak.voice",voice,dubVoiceChoices(state.profiles,tr("mobile.simple.default_voice")).map {Choice(it.first,it.second)},tile=true) {voice=it}
        Action("mobile.simple.dub",!state.busy&&!copying&&voice!=DubVoiceChoice.UNSELECTED&&languages.any {it.first in targets}&&(filePath!=null||link.isNotBlank())) {
            val current = vm.api ?: return@Action
            val draftHost = state.hostUrl ?: return@Action
            val draftFile = filePath?.let(::File)
            val draftLink = link.takeIf {draftFile==null}
            val draftType = inputType
            val draftName = sourceName
            val draftLanguages = languages.filter {it.first in targets}
            val draftVoice = voice
            vm.run {
                val engine = current.translationEngine()
                if(vm.api !== current) return@run
                val draft = PendingDub(draftHost,draftFile,draftLink,draftType,draftLanguages,draftVoice,engine,draftName)
                if(engine.requiresConsent) pending=draft else enqueue(draft,false)
            }
        }
        works.filter {it.state != WorkInfo.State.FAILED || it.id in visibleFailures}.forEach {info ->
            val stage=info.displayStage()
            Group {Column(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            val value=if(info.state==WorkInfo.State.SUCCEEDED) 100 else info.progress.getInt("percent",0)
            if(!info.state.isFinished) DubGlyph(44.dp,active=true)
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                if(info.state==WorkInfo.State.FAILED) {
                    val params=runCatching {kotlinx.serialization.json.Json.decodeFromString<Map<String,String>>(info.outputData.getString("error_params") ?: "{}")}.getOrDefault(emptyMap())
                    ErrorMessage(stage,params,Modifier.weight(1f))
                } else Text(tr(stage),Modifier.weight(1f),maxLines=1,overflow=TextOverflow.Ellipsis)
                Text(tr("mobile.simple.percent",mapOf("percent" to value)))
            }
            if(!info.state.isFinished) {
                val progress=info.progress.getInt("percent",0)
                if(progress>0) LinearProgressIndicator(progress={progress/100f}) else LinearProgressIndicator()
                Secondary("mobile.dub.abort") {
                    val job=info.progress.getString("job")
                    vm.run {
                        // Stop host work before cancelling the durable monitor.
                        if(job!=null) vm.api?.abort(job)
                        work.cancelWorkById(info.id)
                    }
                }
            }
            val job=info.outputData.getString("job") ?: info.progress.getString("job")
            if(info.state==WorkInfo.State.SUCCEEDED&&job!=null) {
                Secondary("mobile.dub.download",!state.busy) {
                    vm.run {shareFile(context,sharedDownload(context,vm.api!!,"/dub/download/${VoiceStudioApi.segment(job)}",if(inputType=="audio") "m4a" else "mkv"),"application/octet-stream")}
                }
                Secondary("mobile.dub_advanced.exports",!state.busy) {advancedJob=job;advancedTitle=sourceName.orEmpty()}
                ReportButton(vm) {ReportSubjects.computerDub(job)}
            }
            }}
            HorizontalDivider()
        }
        Heading("mobile.dub.history")
        Secondary("common.refresh",!state.busy,vm::refresh)
        if(state.catalogLoading) InlineLoading()
        else if(state.dubHistory.isEmpty()) EmptyState("mobile.simple.empty_dubs")
        if(state.dubHistory.isNotEmpty()) Group {
            state.dubHistory.forEachIndexed {index,item ->
                val job=item.jobId.ifBlank {item.id}
                DubHistoryRow(item,!state.busy,
                    download={vm.run {
                        val file=sharedDownload(context,vm.api!!,dubDownloadPath(job),"mkv")
                        savePath=file.absolutePath
                        save.launch(item.filename.substringBeforeLast('.').ifBlank {item.id}+"."+file.extension)
                    }},
                    share={vm.run {shareFile(context,sharedDownload(context,vm.api!!,dubDownloadPath(job),"mkv"),"application/octet-stream")}},
                    advanced={advancedJob=job;advancedTitle=item.filename.ifBlank {item.id}},
                    report=if(LocalContentReportClient.current!=null) {{vm.reports.open(ReportSubjects.computerDub(job))}} else null)
                if(index<state.dubHistory.lastIndex) HorizontalDivider()
            }
        }
        advancedJob?.let {job ->
            androidx.compose.ui.window.Dialog(onDismissRequest={advancedJob=null},properties=androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth=false)) {
                Surface(Modifier.fillMaxSize(),color=role("canvas")) {ComputerDubAdvancedScreen(vm,job,advancedTitle) {advancedJob=null}}
            }
        }
    }
}

/** Terminal failures take precedence over the last persisted progress update. */
fun WorkInfo.displayStage(): String = when(state) {
    WorkInfo.State.FAILED -> outputData.getString("error") ?: "mobile.android.job_failed"
    WorkInfo.State.SUCCEEDED -> "mobile.android.stage_done"
    WorkInfo.State.CANCELLED -> "mobile.android.stage_cancelled"
    WorkInfo.State.ENQUEUED -> if(runAttemptCount>0) "mobile.errors.offline" else progress.getString("stage") ?: "mobile.android.stage_prepare"
    else -> progress.getString("stage") ?: outputData.getString("stage") ?: "mobile.android.stage_prepare"
}

private data class PendingDub(val host: String, val file: File?, val link: String?, val type: String,
    val languages: List<Pair<String,String>>, val voice: String, val engine: TranslationEngine, val sourceName: String?)

private fun dubDownloadPath(job: String) = "/dub/download/${VoiceStudioApi.segment(job)}"

@Composable fun SelectedDubSource(name: String, remove: ()->Unit) {
    val description=tr("common.remove",mapOf("term" to name))
    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
        Text(name,Modifier.weight(1f))
        TextButton(onClick=remove,shape=MaterialTheme.shapes.small,
            modifier=Modifier.heightIn(min=VSTheme.touchTarget).semantics {contentDescription=description}) {
            Text(tr("mobile.dub.remove_source"))
        }
    }
}

@Composable fun DubHistoryRow(item: DubHistory, enabled: Boolean, download: ()->Unit, share: ()->Unit, report: (()->Unit)? = null, advanced: (()->Unit)? = null) {
    val name=item.filename.ifBlank {item.id}
    Row(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalAlignment=Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(name,style=MaterialTheme.typography.titleMedium,maxLines=1,overflow=TextOverflow.Ellipsis)
            Text(tr("mobile.dub.history_meta",mapOf("language" to (item.language?.takeIf {it.isNotBlank()} ?: tr("mobile.android.stage_done")),
                "duration" to formatDuration(((item.duration ?: 0.0)*1000).toLong()))),
                style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
        }
        IconAction("mobile.dub.download_file",VSIcons.Download,enabled,mapOf("name" to name),download)
        IconAction("mobile.dub.share_file",Icons.Default.Share,enabled,mapOf("name" to name),share)
        advanced?.let {IconAction("mobile.dub_advanced.exports",Icons.Default.Settings,onClick=it)}
        report?.let {IconAction("mobile.report.action",Icons.Default.Warning,onClick=it)}
    }
}
