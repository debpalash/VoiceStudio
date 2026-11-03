package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.work.*
import com.voicestudio.mobile.data.PhoneWorkflowWorker
import java.io.File
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.MissingModelsCard
import com.voicestudio.mobile.features.models.TaskModelPicker
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.*
import kotlinx.serialization.json.Json
import java.util.Locale

private fun workflowStepKey(kind: String)=if(kind=="normalize") "mobile.production.normalize_text" else if(kind in listOf("transcribe","condition")) "mobile.flow.step_$kind" else "workflows.step_$kind"

@Composable fun WorkflowsScreen(vm: StudioViewModel,initialWorkflow: PhoneWorkflow?=null) {
    val phone by vm.phone.state.collectAsState()
    val prefs by vm.preferences.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val untitled=tr("workflows.untitled")
    val saver=remember {Saver<PhoneWorkflow,String>(save={Json.encodeToString(PhoneWorkflow.serializer(),it)},restore={Json.decodeFromString(PhoneWorkflow.serializer(),it)})}
    fun fresh()=PhoneWorkflow(name=untitled,graph=WorkflowGraph.create(untitled))
    var workflow by rememberSaveable(stateSaver=saver) {mutableStateOf(initialWorkflow ?: fresh())}
    var library by remember {mutableStateOf(emptyList<PhoneWorkflow>())}
    var runs by remember {mutableStateOf(emptyList<WorkflowGraphRun>())}
    var deleting by remember {mutableStateOf<PhoneWorkflow?>(null)}
    var input by rememberSaveable {mutableStateOf(initialWorkflow?.inputText.orEmpty())}
    var voice by rememberSaveable {mutableStateOf("preset:0")}
    var operation by rememberSaveable {mutableStateOf("normalize")}
    var workers by remember {mutableStateOf(emptyList<WorkInfo>())}
    val workManager=remember {WorkManager.getInstance(context)}
    val activeWorker=workers.firstOrNull {!it.state.isFinished}
    var progress by remember {mutableStateOf(0f)}
    var result by remember {mutableStateOf<PhoneWorkflowResult?>(null)}
    var subject by remember {mutableStateOf<ReportSubject?>(null)}
    val running=activeWorker!=null
    fun model(task: String,capability: String)=phone.preferredModel(phone.models.filter {it.capability==capability},vm.phone.ramBytes,prefs.phoneModels[task])
    val recognizer=model("workflow-asr","asr");val tts=model("production-tts","tts");val clone=model("production-clone","clone");val translator=model("workflow-translate","translate")
    val speaks=workflow.steps.any {it.kind=="speak"};val translates=workflow.steps.any {it.kind=="translate"}
    val selected=if(voice.startsWith("phone:")) clone else tts
    val nodes=workflow.graph?.steps.orEmpty()
    val needed=if(workflow.graph!=null) listOfNotNull(recognizer.takeIf {nodes.any {it.kind in setOf("transcribe","convert")}},translator.takeIf {nodes.any {it.kind=="translate"}},clone.takeIf {nodes.any {it.kind=="convert"||(it.kind=="speak"&&it.voiceId.orEmpty().startsWith("phone:"))}},tts.takeIf {nodes.any {it.kind=="speak"&&it.voiceId.orEmpty().startsWith("preset:")}}) else listOfNotNull(selected.takeIf {speaks},translator.takeIf {translates},recognizer.takeIf {workflow.steps.any {it.kind=="transcribe"}})
    val speakers=tts?.option("num_speakers","1")?.toIntOrNull()?.coerceIn(1,128) ?: 1
    val voices=(0 until speakers).map {Choice("preset:$it",tr("mobile.local.preset_voice",mapOf("number" to it+1)))}+phone.voices.map {Choice("phone:"+it.id,it.name)}
    val catalog=rememberLanguageCatalog(vm::fail)
    val graphReady=nodes.filter {it.kind in setOf("speak","convert")}.all {node ->voices.any {it.id==node.voiceId}&&(if(node.voiceId.orEmpty().startsWith("phone:")) clone!=null else tts!=null)&&(node.kind!="convert"||node.voiceId.orEmpty().startsWith("phone:"))}
    val valid=workflow.valid&&input.length<=20000&&needed.all {phone.canRun(it)}&&if(workflow.graph!=null) graphReady&&(if(workflow.graph!!.needsAudio) workflow.inputFile!=null else input.isNotBlank())&&(!nodes.any {it.kind in setOf("transcribe","convert")}||recognizer!=null)&&(!nodes.any {it.kind=="translate"}||translator!=null) else (input.isNotBlank()||workflow.steps.firstOrNull()?.kind=="transcribe")&&(workflow.steps.firstOrNull()?.kind!="transcribe"||(workflow.inputFile!=null&&recognizer!=null))&&(!translates||translator!=null)&&(!speaks||(selected!=null&&voices.any {it.id==voice}))
    suspend fun reload() {library=vm.phone.workflows.list();runs=vm.phone.workflows.graphRuns(workflow)}
    LaunchedEffect(Unit) {try {vm.phone.refresh();reload()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    LaunchedEffect(workflow.id) {
        runs=vm.phone.workflows.graphRuns(workflow)
        workManager.getWorkInfosForUniqueWorkFlow("workflow:${workflow.id}").collect {items ->
            workers=items;val latest=items.firstOrNull {!it.state.isFinished} ?: items.lastOrNull()
            progress=latest?.progress?.getInt("percent",0)?.div(100f) ?: 0f
            if(latest?.state==WorkInfo.State.SUCCEEDED) {
                val textFile=latest.outputData.getString("text_file")
                result=PhoneWorkflowResult(withContext(Dispatchers.IO) {textFile?.let {File(it).takeIf(File::isFile)?.readText()}.orEmpty()},latest.outputData.getString("file")?.let(::File))
                runs=vm.phone.workflows.graphRuns(workflow)
                result?.audio?.let {subject=ReportSubject("speak:phone:"+it.absolutePath,"speak","phone",latest.outputData.getString("repo") ?: "unknown",latest.outputData.getString("revision"))}
            }
            latest?.outputData?.getString("error")?.let {vm.fail(com.voicestudio.mobile.network.ClientFailure(it))}
        }
    }
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->
        if(uri!=null) scope.launch {try {
            val source=withContext(Dispatchers.IO) {File.createTempFile("workflow-import-",".media",context.cacheDir).also {file ->
                try {context.contentResolver.openInputStream(uri).use {input ->requireNotNull(input);file.outputStream().use {out ->val buffer=ByteArray(65536);var total=0L;while(true) {val count=input.read(buffer);if(count<0) break;total+=count;require(total<=512L*1024*1024);out.write(buffer,0,count)}}}} catch(e:Exception) {file.delete();throw e}
            }}
            try {workflow=vm.phone.workflows.importAudio(source,workflow)} finally {source.delete()}
        } catch(e:Exception) {vm.fail(e)}}
    }
    val workflowImporter=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->if(uri!=null) scope.launch {try {
        val raw=withContext(Dispatchers.IO) {context.contentResolver.openInputStream(uri).use {stream->requireNotNull(stream);val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(65536);var total=0;while(true) {val count=stream.read(buffer,0,minOf(buffer.size,4*1024*1024+1-total));if(count<0) break;total+=count;require(total<=4*1024*1024);output.write(buffer,0,count)};output.toString(Charsets.UTF_8.name())}}
        val graph=WorkflowGraphInterchange.decode(raw);workflow=PhoneWorkflow(name=graph.name,graph=graph,inputText=graph.inputText);input=graph.inputText;result=null;runs=emptyList()
    } catch(e:Exception) {vm.fail(e)}}}
    val workflowExporter=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) {uri ->if(uri!=null) scope.launch {try {
        val graph=requireNotNull(workflow.graph).copy(name=workflow.name);val raw=WorkflowGraphInterchange.encode(graph,input)
        withContext(Dispatchers.IO) {context.contentResolver.openOutputStream(uri).use {output->requireNotNull(output);output.write(raw.toByteArray(Charsets.UTF_8))}}
    } catch(e:Exception) {vm.fail(e)}}}
    Screen {
        ScreenTitle("workflows.title")
        Text(tr("mobile.local.this_phone"))
        Field("workflows.name",workflow.name,{if(!running) workflow=workflow.copy(name=it)})
        Field("clone.script",input,{if(!running) input=it},multiline=true)
        Secondary("mobile.flow.import_media",!running) {importer.launch(arrayOf("audio/*","video/*"))}
        workflow.inputName?.let {Text(it)}
        Row {listOf("clean","translate","transcribe").forEach {kind ->TextButton(enabled=!running,onClick={workflow=PhoneWorkflow(name=untitled,steps=(if(kind=="clean") listOf("normalize","speak") else if(kind=="translate") listOf("normalize","translate","speak") else listOf("transcribe","normalize","translate","speak")).map {PhoneWorkflowStep(kind=it)})}) {Text(tr("mobile.flow.template_$kind"))}}}
        Row {
            TextButton(enabled=!running,onClick={workflowImporter.launch(arrayOf("application/json","text/json"))}) {Text(tr("mobile.workflowParity.import_json"))}
            TextButton(enabled=!running&&workflow.graph!=null&&workflow.valid,onClick={workflowExporter.launch("workflow.json")}) {Text(tr("mobile.workflowParity.export_json"))}
        }
        Row {
            TextButton(enabled=!running,onClick={
                var graph=WorkflowGraph.create(untitled,listOf("start","condition","speak","speak","end"));val n=graph.steps
                graph=graph.copy(steps=n.mapIndexed {i,s->if(i==1) s.copy(text="?") else s},connections=listOf(WorkflowGraphConnection(source=n[0].id,target=n[1].id),WorkflowGraphConnection(source=n[1].id,target=n[2].id,sourceHandle="yes"),WorkflowGraphConnection(source=n[1].id,target=n[3].id,sourceHandle="no"),WorkflowGraphConnection(source=n[2].id,target=n[4].id),WorkflowGraphConnection(source=n[3].id,target=n[4].id)))
                workflow=PhoneWorkflow(name=untitled,graph=graph);input=""
            }) {Text(tr("mobile.workflowParity.branch_template"))}
            TextButton(enabled=!running,onClick={workflow=PhoneWorkflow(name=untitled,graph=WorkflowGraph.create(untitled,listOf("audio","convert","normalize","end")));input=""}) {Text(tr("workflows.step_convert"))}
        }
        if(workflow.graph!=null) {
            Text(tr("mobile.workflowParity.bind_hint"))
            if(!running) WorkflowGraphEditor(workflow.graph!!,voices,catalog?.dubChoices().orEmpty().map {Choice(it.first,it.second)}) {workflow=workflow.copy(graph=it.copy(updatedAt=System.currentTimeMillis().toDouble()))}
            if(!running) {
                TaskModelPicker(vm,"production-tts",setOf("tts"),tts,key="production-tts")
                TaskModelPicker(vm,"production-clone",setOf("clone"),clone,key="production-clone")
                TaskModelPicker(vm,"workflow-translate",setOf("translate"),translator,key="workflow-translate")
                TaskModelPicker(vm,"workflow-asr",setOf("asr"),recognizer,key="workflow-asr")
                Secondary("common.save",workflow.name.isNotBlank()) {scope.launch {try {workflow=workflow.copy(inputText=input);vm.phone.workflows.save(workflow);reload()} catch(e:Exception) {vm.fail(e)}}}
            }
        } else {
        workflow.steps.forEachIndexed {index,step ->
            Group {Column(Modifier.padding(VSTheme.inset)) {
                Row {
                    Text(tr(workflowStepKey(step.kind)),Modifier.weight(1f))
                    IconAction("stories.moveUp",Icons.Default.KeyboardArrowUp,!running&&index>0&&step.kind!="speak") {workflow=workflow.copy(steps=workflow.steps.toMutableList().also {java.util.Collections.swap(it,index,index-1)})}
                    IconAction("stories.moveDown",Icons.Default.KeyboardArrowDown,!running&&index<workflow.steps.lastIndex&&workflow.steps[index+1].kind!="speak") {workflow=workflow.copy(steps=workflow.steps.toMutableList().also {java.util.Collections.swap(it,index,index+1)})}
                    IconAction("common.delete",Icons.Default.Delete,!running) {workflow=workflow.copy(steps=workflow.steps.filterIndexed {i,_->i!=index})}
                }
                if(step.kind=="condition"&&!running) {
                    Field("mobile.flow.contains",step.containsText.orEmpty(),{value->workflow=workflow.copy(steps=workflow.steps.toMutableList().also {it[index]=step.copy(containsText=value)})})
                    ChoicePicker("mobile.flow.skip_label",(step.skipSteps ?: 1).toString(),(1..20).map {Choice(it.toString(),it.toString())}) {value->workflow=workflow.copy(steps=workflow.steps.toMutableList().also {it[index]=step.copy(skipSteps=value.toInt())})}
                }
                if(step.kind in listOf("transcribe","translate","speak")&&!running) ChoicePicker("mobile.flow.retries_label",(step.retries ?: 0).toString(),(0..3).map {Choice(it.toString(),it.toString())}) {value->workflow=workflow.copy(steps=workflow.steps.toMutableList().also {it[index]=step.copy(retries=value.toInt())})}
                if(step.kind=="translate"&&!running) ChoicePicker("mobile.speak.language",step.language,catalog?.dubChoices().orEmpty().map {Choice(it.first,it.second)}) {language->workflow=workflow.copy(steps=workflow.steps.toMutableList().also {it[index]=step.copy(language=language)})}
            }}
        }
        if(!running) {
            ChoicePicker("workflows.add_step",operation,listOf("normalize","translate","transcribe","condition","speak").map {Choice(it,tr(workflowStepKey(it)))}) {operation=it}
            Secondary("workflows.add_step",workflow.steps.size<20&&!(operation in listOf("speak","transcribe")&&workflow.steps.any {it.kind==operation})) {
                val index=if(operation=="transcribe") 0 else if(operation!="speak"&&workflow.steps.lastOrNull()?.kind=="speak") workflow.steps.lastIndex else workflow.steps.size
                workflow=workflow.copy(steps=workflow.steps.toMutableList().also {it.add(index,PhoneWorkflowStep(kind=operation,containsText=if(operation=="condition") "?" else null))})
            }
            ChoicePicker("mobile.speak.voice",voice,voices) {voice=it}
            TaskModelPicker(vm,"production-tts",setOf("tts"),tts,key="production-tts")
            TaskModelPicker(vm,"production-clone",setOf("clone"),clone,key="production-clone")
            TaskModelPicker(vm,"workflow-translate",setOf("translate"),translator,key="workflow-translate")
            TaskModelPicker(vm,"workflow-asr",setOf("asr"),recognizer,key="workflow-asr")
            Secondary("common.save",workflow.name.isNotBlank()) {scope.launch {try {workflow=workflow.copy(inputText=input,voiceSelection=voice);vm.phone.workflows.save(workflow);reload()} catch(e:Exception) {vm.fail(e)}}}
        }
        }
        if(needed.any {!phone.canRun(it)}) MissingModelsCard(vm,"workflows.title",needed)
        if(running) {
            LinearProgressIndicator(progress={progress},modifier=Modifier.fillMaxWidth())
            Text(tr("mobile.jobs.checkpointed"))
            PhoneJobStatus(running)
            Secondary("common.cancel") {activeWorker?.let {workManager.cancelWorkById(it.id)}}
        } else Action("workflowRun.run",valid) {
            val snapshot=workflow.copy(inputText=input,voiceSelection=voice)
            result=null;subject=null;progress=0f
            scope.launch {try {PhoneWorkflowWorker.enqueue(context,snapshot,needed,phone.voices);reload()} catch(e:Exception) {vm.fail(e)}}
        }
        result?.let {value ->
            Text(value.text)
            value.audio?.let {file ->LocalAudioPlayer(file,vm::fail);Secondary("mobile.speak.share") {scope.launch {try {shareLocalFile(context,file,"audio/wav")} catch(e:Exception) {vm.fail(e)}}}}
            subject?.let {report->ReportButton(vm) {report}}
        }
        if(runs.isNotEmpty()) Heading("mobile.workflowParity.runs")
        runs.forEach {run ->Group {Column(Modifier.padding(VSTheme.inset)) {
            Text(java.text.DateFormat.getDateTimeInstance().format(java.util.Date(run.updatedAt.toLong())))
            Text(tr(if(run.done) "mobile.android.stage_done" else run.failure ?: "mobile.simple.cancelled"))
            Text(tr("mobile.workflowParity.saved_steps",mapOf("count" to run.visited.size)))
            if(run.text.isNotBlank()) Text(run.text,maxLines=4)
            if(run.audio!=null) Secondary("mobile.speak.share") {scope.launch {try {vm.phone.workflows.graphRunAudio(workflow,run)?.let {shareLocalFile(context,it,"audio/wav")}} catch(e:Exception) {vm.fail(e)}}}
        }}}
        Heading("workflows.library")
        Secondary("workflows.new",!running) {workflow=fresh();input="";voice="preset:0";result=null}
        library.forEach {item->Row {
            TextButton(onClick={workflow=item;input=item.inputText.orEmpty();voice=item.voiceSelection ?: "preset:0";result=null},enabled=!running,modifier=Modifier.weight(1f)) {Text(item.name)}
            IconAction("common.delete",Icons.Default.Delete,!running) {deleting=item}
        }}
    }
    deleting?.let {item->AlertDialog(onDismissRequest={deleting=null},title={Text(tr("stories.deleteProjectConfirm"))},
        confirmButton={TextButton(onClick={deleting=null;scope.launch {try {vm.phone.workflows.delete(item);if(workflow.id==item.id) {workflow=fresh();result=null};reload()} catch(e:Exception) {vm.fail(e)}}}) {Text(tr("common.delete"))}},
        dismissButton={TextButton(onClick={deleting=null}) {Text(tr("common.cancel"))}})}
}
