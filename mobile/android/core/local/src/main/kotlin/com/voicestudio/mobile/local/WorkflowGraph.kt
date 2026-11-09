package com.voicestudio.mobile.local

import java.io.File
import java.util.UUID
import java.util.Locale
import java.security.MessageDigest
import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import com.voicestudio.mobile.network.ClientFailure

@Serializable data class WorkflowGraphStep(val id: String=UUID.randomUUID().toString(),val kind: String,val title: String?=null,val text: String?=null,val voiceId: String?=null,val language: String?=null,val sourceLanguage: String?=null,val provider: String?=null,val match: String?=null,val speed: Double?=null,val targetDb: Double?=null,val retries: Int?=null,val position: Position?=null,val media: List<Media>?=null,val scripts: List<Script>?=null) {
    @Serializable data class Position(val x: Double,val y: Double)
    @Serializable data class Media(val id: String,val name: String,val type: String,val size: Long)
    @Serializable data class Script(val name: String,val text: String)
    fun matches(value: String): Boolean {val phrase=text.orEmpty().trim().lowercase(Locale.ROOT);val current=value.trim().lowercase(Locale.ROOT);return when(match ?: "contains") {"equals"->current==phrase;"starts"->current.startsWith(phrase);"ends"->current.endsWith(phrase);else->current.contains(phrase)}}
}
@Serializable data class WorkflowGraphConnection(val id: String=UUID.randomUUID().toString(),val source: String,val target: String,val sourceHandle: String?=null)
@Serializable data class WorkflowGraph(val id: String=UUID.randomUUID().toString(),val name: String,val updatedAt: Double=System.currentTimeMillis().toDouble(),val steps: List<WorkflowGraphStep>,val connections: List<WorkflowGraphConnection>) {
    companion object {
        fun create(name: String,kinds: List<String> = listOf("start","speak","end")): WorkflowGraph {val nodes=kinds.map {WorkflowGraphStep(kind=it,voiceId=if(it in listOf("speak","convert")) "preset:0" else null)};return WorkflowGraph(name=name,steps=nodes,connections=nodes.zipWithNext().map {(a,b)->WorkflowGraphConnection(source=a.id,target=b.id)})}
    }
    val inputText get()=steps.firstOrNull {it.kind=="start"}?.let {it.scripts?.takeIf {s->s.isNotEmpty()}?.joinToString("\n---\n") {s->s.text} ?: it.text}.orEmpty()
    val needsAudio get()=steps.any {it.kind=="audio"}
    fun destination(id: String,branch: String?=null)=connections.firstOrNull {it.source==id&&it.sourceHandle.orEmpty()==branch.orEmpty()}?.target
    fun connect(source: String,target: String,branch: String?=null)=copy(connections=connections.filterNot {it.source==source&&it.sourceHandle.orEmpty()==branch.orEmpty()}+if(target.isEmpty()) emptyList() else listOf(WorkflowGraphConnection(source=source,target=target,sourceHandle=branch)))
    fun validate(requireVoices: Boolean=false) {
        fun bad(key: String="mobile.workflowParity.invalid_graph"): Nothing=throw ClientFailure(key)
        if(name.isBlank()||name.length>200||steps.size !in 3..40||connections.size>80||steps.map {it.id}.distinct().size!=steps.size||connections.map {it.id}.distinct().size!=connections.size||steps.any {it.id.isEmpty()||it.id.length>128}||steps.count {it.kind in listOf("start","audio")}!=1) bad()
        val supported=setOf("start","audio","speak","transcribe","translate","convert","normalize","condition","end")
        steps.forEach {s ->
            if(s.kind !in supported) throw ClientFailure("mobile.workflowParity.unsupported",mapOf("kind" to s.kind))
            if(s.text.orEmpty().length>20000||(s.retries ?: 0) !in 0..3||(s.match ?: "contains") !in setOf("contains","equals","starts","ends")||!(s.speed ?: 1.0).isFinite()||(s.speed ?: 1.0) !in .5..2.0||!(s.targetDb ?: -2.0).isFinite()||(s.targetDb ?: -2.0) !in -24.0..-1.0||s.scripts.orEmpty().size>50||s.media.orEmpty().size>1||s.voiceId.orEmpty().length>200||s.language.orEmpty().length>80||s.sourceLanguage.orEmpty().length>80||(s.provider ?: "argos") !in setOf("argos","nllb")||s.scripts.orEmpty().any {it.text.isBlank()||it.text.length>20000||it.name.length>120}) bad("mobile.workflowParity.unsupported_settings")
            if(requireVoices&&s.kind in setOf("speak","convert")&&s.voiceId.isNullOrEmpty()) bad("mobile.workflowParity.bind_voices")
            if(s.kind=="speak"&&s.voiceId.orEmpty().startsWith("phone:")&&s.language!=null&&s.language !in setOf("Auto","auto")) bad("mobile.workflowParity.auto_voice_language")
            if(s.kind=="translate"&&(s.language.isNullOrEmpty()||s.language=="Auto"||s.sourceLanguage.isNullOrEmpty())) bad("mobile.workflowParity.invalid_language")
        }
        val ids=steps.map {it.id}.toSet();val byID=steps.associateBy {it.id}
        if(connections.any {it.source !in ids||it.target !in ids||it.source==it.target}) bad()
        steps.forEach {s->val count=connections.count {it.target==s.id};if(if(s.kind in listOf("start","audio")) count!=0 else count==0) bad()}
        val entered=mutableMapOf<String,String>()
        fun walk(id: String,type: String,path: Set<String>) {
            if(id in path) bad();val s=byID[id] ?: bad()
            entered[id]?.let {if(it!=type) bad("mobile.workflowParity.incompatible_merge");return}
            entered[id]=type;val edges=connections.filter {it.source==id};val onward=path+id
            if(s.kind=="end") {if(edges.isNotEmpty()) bad();return}
            if(s.kind=="condition") {if(type!="text"||s.text.isNullOrBlank()||edges.size!=2||edges.count {it.sourceHandle=="yes"}!=1||edges.count {it.sourceHandle=="no"}!=1) bad();edges.forEach {walk(it.target,type,onward)};return}
            if(edges.size!=1||!edges.single().sourceHandle.isNullOrEmpty()) bad()
            val forward=when {s.kind in setOf("start","audio")->type;s.kind=="speak"&&type=="text"->"speech";s.kind=="convert"&&type!="text"->"speech";s.kind=="transcribe"&&type!="text"->"text";s.kind=="translate"&&type=="text"->"text";s.kind=="normalize"&&type=="speech"->"speech";else->bad("mobile.workflowParity.incompatible_merge")}
            walk(edges.single().target,forward,onward)
        }
        val input=steps.first {it.kind in setOf("start","audio")};walk(input.id,if(input.kind=="audio") "audio" else "text",emptySet());if(entered.size!=steps.size) bad()
    }
}
object WorkflowGraphInterchange {
    @Serializable private data class Library(val version: Int,val activeId: String,val documents: List<WorkflowGraph>)
    fun decode(text: String): WorkflowGraph {
        require(text.toByteArray(Charsets.UTF_8).size<=4*1024*1024)
        val value=LocalJson.parseToJsonElement(text).jsonObject
        val graph=if("documents" in value) {val library=LocalJson.decodeFromJsonElement<Library>(value);if(library.version!=1||library.documents.size>50) throw ClientFailure("mobile.workflowParity.invalid_graph");library.documents.firstOrNull {it.id==library.activeId} ?: throw ClientFailure("mobile.workflowParity.invalid_graph")} else LocalJson.decodeFromJsonElement<WorkflowGraph>(value)
        graph.validate();if(graph.steps.any {it.scripts.orEmpty().size>1}) throw ClientFailure("mobile.workflowParity.single_input");return graph
    }
    fun encode(graph: WorkflowGraph,input: String): String {
        graph.validate();val nodes=graph.steps.mapIndexed {index,s ->s.copy(position=WorkflowGraphStep.Position((index%3)*275.0+40,(index/3)*155.0+80),text=if(s.kind=="start") input else s.text,scripts=if(s.kind=="start") null else s.scripts)}
        return LocalJson.encodeToString(Library(1,graph.id,listOf(graph.copy(steps=nodes))))
    }
}
data class WorkflowGraphValue(val text: String?=null,val audio: File?=null,val synthetic: Boolean=false)
data class WorkflowGraphOutput(val text: String?=null,val audio: LocalAudio?=null,val file: File?=null)
@Serializable data class WorkflowGraphRun(val id: String,val name: String,val updatedAt: Double,val done: Boolean,val visited: List<String>,val text: String,val audio: String?=null,val failure: String?=null)
@Serializable private data class GraphArtifact(val text: String?=null,val file: String?=null,val hash: String?=null)
@Serializable private data class GraphCheckpoint(val artifacts: Map<String,GraphArtifact> = emptyMap(),val visited: List<String> = emptyList(),val done: Boolean=false)
private fun graphDigest(value: ByteArray)=MessageDigest.getInstance("SHA-256").digest(value).joinToString("") {"%02x".format(it)}
private fun graphFileHash(file: File): String {val digest=MessageDigest.getInstance("SHA-256");file.inputStream().use {input->val bytes=ByteArray(65536);while(true) {val count=input.read(bytes);if(count<0) break;digest.update(bytes,0,count)}};return digest.digest().joinToString("") {"%02x".format(it)}}

suspend fun PhoneWorkflowStore.graphRuns(workflow: PhoneWorkflow): List<WorkflowGraphRun> = withContext(Dispatchers.IO) {File(root,workflow.id).listFiles().orEmpty().mapNotNull {runCatching {LocalJson.decodeFromString<WorkflowGraphRun>(File(it,"graph-run.json").readText())}.getOrNull()}.sortedByDescending {it.updatedAt}}
suspend fun PhoneWorkflowStore.graphRunAudio(workflow: PhoneWorkflow,run: WorkflowGraphRun): File?=withContext(Dispatchers.IO) {val name=run.audio ?: return@withContext null;require(run.id.length==64&&run.id.all {it in '0'..'9'||it in 'a'..'f'}&&File(name).name==name);File(File(File(root,workflow.id),run.id),name).also {require(it.isFile)}}
suspend fun PhoneWorkflowStore.runGraph(workflow: PhoneWorkflow,text: String,configuration: String,progress: (Float)->Unit={},execute: suspend (WorkflowGraphStep,WorkflowGraphValue,File)->WorkflowGraphOutput): PhoneWorkflowResult=withContext(Dispatchers.IO) {
    val graph=requireNotNull(workflow.graph);graph.validate(true)
    if(graph.steps.any {it.scripts.orEmpty().size>1}||Regex("(?m)^\\s*---\\s*$").containsMatchIn(text)||text.length>20000||(!graph.needsAudio&&text.isBlank())) throw ClientFailure("mobile.workflowParity.single_input")
    val source=if(graph.needsAudio) source(workflow) else null
    require(UUID.fromString(workflow.id).toString()==workflow.id)
    val parent=File(root,workflow.id);val lock=PhoneWorkflowStore.locks.getOrPut(parent.absolutePath) {kotlinx.coroutines.sync.Mutex()};check(lock.tryLock())
    try {
        save(workflow.copy(inputText=text))
        val settings=graph.copy(name="",updatedAt=0.0,steps=graph.steps.map {it.copy(title=null,position=null)})
        val key=graphDigest((LocalJson.encodeToString(settings)+"\u0000"+text+"\u0000"+configuration+"\u0000"+workflow.inputHash.orEmpty()).toByteArray(Charsets.UTF_8))
        val directory=File(parent,key).apply {mkdirs()};val manifest=File(directory,"graph-checkpoint.json");val summary=File(directory,"graph-run.json")
        var checkpoint=if(manifest.isFile) LocalJson.decodeFromString<GraphCheckpoint>(manifest.readText()) else GraphCheckpoint()
        var run=WorkflowGraphRun(key,workflow.name,System.currentTimeMillis().toDouble(),false,checkpoint.visited,text)
        atomicText(summary,LocalJson.encodeToString(run))
        fun resolve(artifact: GraphArtifact): WorkflowGraphValue {val name=artifact.file ?: return WorkflowGraphValue(text=requireNotNull(artifact.text).also {if(it.isBlank()||it.length>20000) throw ClientFailure("mobile.simple.checkpoint_failed")});require(File(name).name==name);val file=File(directory,name);if(graphFileHash(file)!=artifact.hash) throw ClientFailure("mobile.simple.checkpoint_failed");return WorkflowGraphValue(audio=file,synthetic=true)}
        var value=WorkflowGraphValue(text=text,audio=source);var cursor: String?=graph.steps.first {it.kind in setOf("start","audio")}.id;val visited=mutableListOf<String>()
        try {
            while(cursor!=null) {
                currentCoroutineContext().ensureActive();val step=graph.steps.first {it.id==cursor};require(step.id !in visited);visited+=step.id
                if(step.kind=="end") break
                if(step.kind in setOf("start","audio")) {cursor=graph.destination(step.id);continue}
                if(step.kind=="condition") {cursor=graph.destination(step.id,if(step.matches(value.text.orEmpty())) "yes" else "no");continue}
                val cached=checkpoint.artifacts[step.id]
                if(cached!=null) value=resolve(cached) else {
                    var output: WorkflowGraphOutput?=null;var retries=0
                    while(output==null) {try {output=execute(step,value,directory)} catch(e:CancellationException) {throw e} catch(e:Exception) {currentCoroutineContext().ensureActive();if(retries++ >= (step.retries ?: 0)) throw e}}
                    currentCoroutineContext().ensureActive();val audio=output.audio;val file=output.file
                    val artifact=if(audio!=null||file!=null) {val name="node-"+graphDigest(step.id.toByteArray(Charsets.UTF_8))+".wav";val target=File(directory,name);if(audio!=null) {require(audio.samples.isNotEmpty()&&audio.samples.all {it.isFinite()}&&step.kind in setOf("speak","convert","normalize"));markSynthetic(audio,target)} else {require(step.kind=="normalize"&&PhoneAudioProcessing.containsSyntheticMarker(requireNotNull(file)));requireNotNull(file).copyTo(target,overwrite=true);if(file.parentFile?.name?.startsWith("audio-edit-")==true&&file.parentFile?.parentFile?.canonicalFile==directory.canonicalFile) file.parentFile?.deleteRecursively()};GraphArtifact(file=name,hash=graphFileHash(target))} else {val transformed=requireNotNull(output.text);require(transformed.isNotBlank()&&transformed.length<=20000&&step.kind in setOf("transcribe","translate"));GraphArtifact(text=transformed)}
                    checkpoint=checkpoint.copy(artifacts=checkpoint.artifacts+(step.id to artifact),visited=visited.toList());atomicText(manifest,LocalJson.encodeToString(checkpoint));value=resolve(artifact)
                }
                if(if(step.kind in setOf("transcribe","translate")) value.text==null else value.audio==null) throw ClientFailure("mobile.simple.checkpoint_failed")
                cursor=graph.destination(step.id);run=run.copy(visited=visited.toList(),updatedAt=System.currentTimeMillis().toDouble());atomicText(summary,LocalJson.encodeToString(run));progress(visited.size.toFloat()/graph.steps.size)
            }
            checkpoint=checkpoint.copy(done=true,visited=visited.toList());atomicText(manifest,LocalJson.encodeToString(checkpoint))
            run=run.copy(done=true,visited=visited.toList(),text=value.text.orEmpty(),audio=value.audio?.name,updatedAt=System.currentTimeMillis().toDouble());atomicText(summary,LocalJson.encodeToString(run));progress(1f)
            PhoneWorkflowResult(value.text.orEmpty(),value.audio)
        } catch(e:Exception) {withContext(NonCancellable) {run=run.copy(failure=if(e is CancellationException) "mobile.simple.cancelled" else (e as? ClientFailure)?.key ?: "mobile.errors.file",visited=visited.toList(),updatedAt=System.currentTimeMillis().toDouble());atomicText(summary,LocalJson.encodeToString(run))};throw e}
    } finally {lock.unlock()}
}
