package com.voicestudio.mobile.features

import androidx.work.WorkManager
import androidx.work.WorkInfo
import com.voicestudio.mobile.data.PhoneProductionWorker
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.MissingModelsCard
import com.voicestudio.mobile.features.models.TaskModelPicker
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.*
import kotlinx.serialization.json.Json
import java.io.File

@Composable fun ProductionScreen(vm: StudioViewModel, kind: String) {
    val phone by vm.phone.state.collectAsState()
    val prefs by vm.preferences.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val untitled=tr(if(kind=="stories") "stories.untitled" else "audiobook.untitled")
    val titleKey=if(kind=="stories") "nav.stories" else "audiobook.title"
    val saver=remember {Saver<ProductionProject,String>(save={Json.encodeToString(ProductionProject.serializer(),it)},restore={Json.decodeFromString(ProductionProject.serializer(),it)})}
    var project by rememberSaveable(kind,stateSaver=saver) {mutableStateOf(ProductionProject(kind=kind,title=untitled))}
    var projects by remember {mutableStateOf(emptyList<ProductionProject>())}
    var output by remember {mutableStateOf<File?>(null)}
    var chapters by remember {mutableStateOf(emptyList<File>())}
    var exportFormat by rememberSaveable {mutableStateOf("wav")}
    var exporting by remember {mutableStateOf(false)}
    var exportJob by remember {mutableStateOf<Job?>(null)}
    var word by rememberSaveable {mutableStateOf("")}
    var spoken by rememberSaveable {mutableStateOf("")}
    val manager=remember(context) {WorkManager.getInstance(context)}
    val jobs by remember(project.id) {manager.getWorkInfosByTagFlow("production:${project.id}")}.collectAsState(initial=emptyList())
    val active=jobs.firstOrNull {!it.state.isFinished}
    val progress=(active?.progress?.getInt("percent",0) ?: 0)/100f
    var deleting by remember {mutableStateOf<ProductionProject?>(null)}
    val running=active!=null
    val tts=phone.preferredModel(phone.models.filter {it.capability=="tts"},vm.phone.ramBytes,prefs.phoneModels["production-tts"])
    val clone=phone.preferredModel(phone.models.filter {it.capability=="clone"},vm.phone.ramBytes,prefs.phoneModels["production-clone"])
    val configuration=listOfNotNull(tts,clone).joinToString("|") {modelPin(it).identity}
    val plan=remember(project.script,project.pronunciations,project.speed) {runCatching {project.plan()}.getOrNull()}
    val selections=plan?.segments?.map {project.voice(it.role)} ?: listOf(project.defaultVoice)
    val needed=listOfNotNull(tts.takeIf {selections.any {it.startsWith("preset:")}},clone.takeIf {selections.any {it.startsWith("phone:")}})
    val speakers=tts?.option("num_speakers","1")?.toIntOrNull()?.coerceIn(1,128) ?: 1
    val voices=(0 until speakers).map {Choice("preset:$it",tr("mobile.local.preset_voice",mapOf("number" to it+1)))}+phone.voices.map {Choice("phone:"+it.id,it.name)}
    val valid=plan!=null&&selections.all {selection->voices.any {it.id==selection}}&&needed.isNotEmpty()&&needed.all {phone.canRun(it)}
    suspend fun refreshLibrary() {projects=vm.phone.production.list()}
    fun switchProject(next: ProductionProject) {val previous=project;scope.launch {try {if(previous.title.isNotBlank()) vm.phone.production.save(previous);project=next} catch(e:Exception) {vm.fail(e)}}}
    fun fresh() {switchProject(ProductionProject(kind=kind,title=untitled))}
    val latestProject by rememberUpdatedState(project)
    DisposableEffect(Unit) {onDispose {if(latestProject.title.isNotBlank()) vm.phone.saveProductionDraft(latestProject)}}
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri -> if(uri!=null) scope.launch {
        try {
            val text=withContext(Dispatchers.IO) {
                val type=context.contentResolver.getType(uri)
                val extension=when(type) {"application/epub+zip"->"epub";"application/pdf"->"pdf";else->"txt"}
                val source=File(context.cacheDir,"book-import/${java.util.UUID.randomUUID()}.$extension")
                source.parentFile!!.mkdirs()
                try {
                    requireNotNull(context.contentResolver.openInputStream(uri)).use {input->source.outputStream().use {out->
                        val buffer=ByteArray(8192);var total=0L
                        while(true) {currentCoroutineContext().ensureActive();val count=input.read(buffer);if(count<0) break;total+=count;require(total<=67_108_864L);out.write(buffer,0,count)}
                    }}
                    BookImport.read(source,context)
                } finally {source.delete()}
            }
            project=project.copy(script=text)
        } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
    }}
    LaunchedEffect(Unit) {try {vm.phone.refresh();refreshLibrary()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    LaunchedEffect(project,configuration) {
        output=null;chapters=emptyList()
        if(project.title.isBlank()) return@LaunchedEffect
        delay(350)
        try {
            vm.phone.production.save(project)
            output=vm.phone.production.output(project,configuration)
            if(output!=null) chapters=vm.phone.production.chapterOutputs(project,configuration)
            refreshLibrary()
        } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
    }
    LaunchedEffect(jobs,configuration) {
        val done=jobs.firstOrNull {it.state==WorkInfo.State.SUCCEEDED}
        if(!running&&done!=null) {
            output=vm.phone.production.output(project,configuration)
            if(output!=null) chapters=vm.phone.production.chapterOutputs(project,configuration)
            refreshLibrary()
        }
        jobs.firstOrNull {it.state==WorkInfo.State.FAILED}?.outputData?.getString("error")?.let {vm.fail(com.voicestudio.mobile.network.ClientFailure(it))}
    }
    Screen {
        ScreenTitle(titleKey)
        Text(tr("mobile.local.this_phone"),color=role("textMuted"))
        Column(verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            // All edits are disabled while a snapshot is rendering.
            androidx.compose.runtime.CompositionLocalProvider(androidx.compose.material3.LocalContentColor provides role("text")) {
                Field("stories.projectName",project.title,{if(!running) project=project.copy(title=it)},multiline=false)
                Text(tr("mobile.production.script_hint"),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
                Field("audiobook.script",project.script,{if(!running) project=project.copy(script=it)},multiline=true)
                Secondary("audiobook.import",!running) {importer.launch(arrayOf("text/plain","application/epub+zip","application/pdf"))}
                if(!running) {
                    Text(tr("mobile.books.controls_hint"),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
                    Row {TextButton(onClick={project=project.copy(script=project.script+"\n[pause:0.5]\n")}) {Text(tr("stories.insertPause"))};TextButton(onClick={project=project.copy(script=project.script+"\n[speed:1.25] ")}) {Text(tr("mobile.books.line_speed"))}}
                    ChoicePicker("stories.global_speed",project.speed.toString(),listOf(0.5f,0.75f,1f,1.25f,1.5f,2f).map {Choice(it.toString(),"$it×")}) {project=project.copy(speed=it.toFloat())}
                    Heading("pronunciation.title")
                    Field("audiobook.lex_word",word,{word=it})
                    Field("audiobook.lex_say",spoken,{spoken=it})
                    Secondary("audiobook.lex_add",word.isNotBlank()&&spoken.isNotBlank()&&word.length<=100&&spoken.length<=500&&project.pronunciations.size<100) {project=project.copy(pronunciations=project.pronunciations+(word.trim() to spoken.trim()));word="";spoken=""}
                    project.pronunciations.toSortedMap().forEach {(key,value)->Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {Text("$key → $value",Modifier.weight(1f));IconAction("common.delete",Icons.Default.Delete) {project=project.copy(pronunciations=project.pronunciations-key)}}}
                    TaskModelPicker(vm,"production-tts",setOf("tts"),tts,key="production-tts")
                    TaskModelPicker(vm,"production-clone",setOf("clone"),clone,key="production-clone")
                    ChoicePicker("audiobook.default_voice",project.defaultVoice,voices) {project=project.copy(defaultVoice=it)}
                    plan?.roles.orEmpty().forEach {name ->
                        VoiceCastPicker(name,project.cast[name].orEmpty(),listOf(Choice("",tr("stories.defaultVoice")))+voices) {project=project.copy(cast=project.cast+(name to it))}
                    }
                }
            }
        }
        if(needed.any {!phone.canRun(it)}) MissingModelsCard(vm,titleKey,needed)
        PhoneJobStatus(running)
        if(running) {
            LinearProgressIndicator(progress={progress},modifier=Modifier.fillMaxWidth())
            Secondary("audiobook.stop") {active?.let {manager.cancelWorkById(it.id)}}
        } else Action("stories.generateAll",valid&&project.title.isNotBlank()) {
            val snapshot=project
            scope.launch {try {PhoneProductionWorker.enqueue(context,snapshot,tts,clone)} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
        }

        output?.let {file ->
            LocalAudioPlayer(file,vm::fail)
            ChoicePicker("audiobook.format",exportFormat,listOf(Choice("wav","WAV"),Choice("m4a","AAC / M4A"),Choice("mp3","MP3"),Choice("m4b",tr("audiobook.format_m4b")))) {exportFormat=it}
            Secondary("mobile.speak.share",!exporting) {
                val format=exportFormat;val snapshot=project;val exportConfiguration=configuration
                exporting=true
                exportJob=scope.launch {try {
                    val exported=if(format=="wav") file else BookExport.encode(file,format,vm.phone.production.chapterCues(snapshot,exportConfiguration))
                    shareLocalFile(context,exported,when(format) {"wav"->"audio/wav";"mp3"->"audio/mpeg";else->"audio/mp4"})
                } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {exporting=false;exportJob=null}}
            }
            if(exporting) {CircularProgressIndicator();Secondary("common.cancel") {exportJob?.cancel()}}
            ReportButton(vm) {ReportSubject("speak:phone:"+file.absolutePath,"speak","phone",needed.singleOrNull()?.repo ?: "unknown",needed.singleOrNull()?.revision,project.script)}
            chapters.forEachIndexed {index,chapter ->
                TextButton(onClick={scope.launch {try {shareLocalFile(context,chapter,"audio/wav")} catch(e:Exception) {vm.fail(e)}}}) {Text(tr("audiobook.chapter_n",mapOf("n" to index+1)))}
            }
        }
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {Heading("stories.projects")}
            IconAction("mobile.production.new",Icons.Default.Add,!running) {fresh()}
        }
        projects.filter {it.kind==kind}.forEach {item ->
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                TextButton(onClick={switchProject(item)},enabled=!running,modifier=Modifier.weight(1f)) {Text(item.title)}
                IconAction("common.delete",Icons.Default.Delete,!running) {deleting=item}
            }
        }
    }
    deleting?.let {item -> AlertDialog(onDismissRequest={deleting=null},title={Text(tr("stories.deleteProjectConfirm"))},
        confirmButton={TextButton(onClick={deleting=null;scope.launch {try {vm.phone.production.delete(item);if(project.id==item.id) project=ProductionProject(kind=kind,title=untitled);refreshLibrary()} catch(e:Exception) {vm.fail(e)}}}) {Text(tr("common.delete"))}},
        dismissButton={TextButton(onClick={deleting=null}) {Text(tr("common.cancel"))}}) }
}

/** Character names are manuscript data, so they are not localization keys. */
@Composable private fun VoiceCastPicker(name: String,value: String,choices: List<Choice>,change: (String)->Unit) {
    Column {Text(name,style=MaterialTheme.typography.titleSmall);ChoicePicker("mobile.speak.voice",value,choices,change=change)}
}
