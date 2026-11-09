package com.voicestudio.mobile.local

import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.serialization.Serializable

@Serializable data class PhoneWorkflowStep(val id: String=UUID.randomUUID().toString(),val kind: String,val language: String="en",val containsText: String?=null,val skipSteps: Int?=null,val retries: Int?=null)
@Serializable data class PhoneWorkflow(val id: String=UUID.randomUUID().toString(),val name: String,val steps: List<PhoneWorkflowStep> = emptyList(),val inputText: String? = null,val voiceSelection: String? = null,val inputFile: String?=null,val inputName: String?=null,val inputHash: String?=null,val graph: WorkflowGraph?=null) {
    val valid get()=if(graph!=null) name.isNotBlank()&&name.length<=200&&runCatching {graph!!.validate()}.isSuccess else name.isNotBlank()&&name.length<=200&&steps.isNotEmpty()&&steps.size<=20&&steps.map {it.id}.distinct().size==steps.size&&steps.withIndex().all {(index,step)->step.kind in listOf("normalize","translate","speak","transcribe","condition")&&step.language.isNotBlank()&&(step.retries ?: 0) in 0..3&&(step.kind!="transcribe"||index==0)&&(step.kind!="speak"||index==steps.lastIndex)&&(step.kind!="condition"||(!step.containsText.isNullOrEmpty()&&(step.skipSteps ?: 1) in 1..20))}
}
data class WorkflowValue(val text: String,val audio: LocalAudio? = null)
data class PhoneWorkflowResult(val text: String,val audio: File?)
@Serializable private data class WorkflowCheckpoint(val texts: List<String> = emptyList(),val nextIndex: Int?=null,val spoken: Boolean?=null)

class PhoneWorkflowStore(internal val root: File) {
    companion object {internal val locks=ConcurrentHashMap<String,Mutex>()}
    private fun id(value: String): String {require(UUID.fromString(value).toString()==value);return value}
    private fun hash(file: File): String {val digest=MessageDigest.getInstance("SHA-256");file.inputStream().use {stream->val buffer=ByteArray(1024*1024);while(true) {val count=stream.read(buffer);if(count<0) break;digest.update(buffer,0,count)}};return digest.digest().joinToString("") {"%02x".format(it)}}
    suspend fun save(workflow: PhoneWorkflow)=withContext(Dispatchers.IO) {
        require(workflow.name.isNotBlank()&&workflow.name.length<=200&&workflow.steps.size<=20)
        atomicText(File(root,id(workflow.id)+".json"),LocalJson.encodeToString(PhoneWorkflow.serializer(),workflow))
    }
    suspend fun importAudio(source: File,workflow: PhoneWorkflow): PhoneWorkflow=withContext(Dispatchers.IO) {
        require(source.isFile&&source.length() in 1..512L*1024*1024)
        val directory=File(root,id(workflow.id)).apply {mkdirs()}
        val suffix=source.extension.lowercase().filter {it in 'a'..'z'||it in '0'..'9'}.take(10)
        val filename="source-"+UUID.randomUUID()+if(suffix.isEmpty()) "" else ".$suffix"
        val target=File(directory,filename);source.copyTo(target)
        workflow.copy(inputFile=filename,inputName=source.name,inputHash=hash(target)).also {save(it)}
    }
    suspend fun source(workflow: PhoneWorkflow): File=withContext(Dispatchers.IO) {
        val name=requireNotNull(workflow.inputFile);require(name.startsWith("source-")&&File(name).name==name)
        File(File(root,id(workflow.id)),name).also {require(it.isFile&&hash(it)==workflow.inputHash)}
    }
    suspend fun list(): List<PhoneWorkflow> = withContext(Dispatchers.IO) {
        root.listFiles().orEmpty().filter {it.extension=="json"}.mapNotNull {runCatching {LocalJson.decodeFromString<PhoneWorkflow>(it.readText())}.getOrNull()}.sortedBy {it.name}
    }
    suspend fun delete(workflow: PhoneWorkflow)=withContext(Dispatchers.IO) {
        val lock=locks.getOrPut(File(root,id(workflow.id)).absolutePath) {Mutex()};check(lock.tryLock())
        try {check(File(root,id(workflow.id)+".json").delete());File(root,id(workflow.id)).deleteRecursively();Unit} finally {lock.unlock()}
    }
    suspend fun run(workflow: PhoneWorkflow,text: String,configuration: String,progress: (Float)->Unit = {},
        execute: suspend (PhoneWorkflowStep,String)->WorkflowValue): PhoneWorkflowResult = withContext(Dispatchers.IO) {
        require(workflow.valid&&(text.isNotBlank()||workflow.steps.first().kind=="transcribe")&&text.length<=20000)
        if(workflow.steps.first().kind=="transcribe") source(workflow)
        val parent=File(root,id(workflow.id));val lock=locks.getOrPut(parent.absolutePath) {Mutex()};check(lock.tryLock())
        try {
            save(workflow.copy(inputText=text))
            val input=LocalJson.encodeToString(kotlinx.serialization.builtins.ListSerializer(PhoneWorkflowStep.serializer()),workflow.steps)+"\u0000"+text+"\u0000"+configuration+"\u0000"+workflow.inputHash.orEmpty()
            val key=MessageDigest.getInstance("SHA-256").digest(input.toByteArray(Charsets.UTF_8)).joinToString("") {"%02x".format(it)}
            val dir=File(parent,key).apply {mkdirs()};val manifest=File(dir,"checkpoint.json")
            var checkpoint=if(manifest.isFile) LocalJson.decodeFromString<WorkflowCheckpoint>(manifest.readText()) else WorkflowCheckpoint()
            var index=checkpoint.nextIndex ?: checkpoint.texts.size
            require(index in 0..workflow.steps.size&&checkpoint.texts.size<=workflow.steps.size)
            while(index<workflow.steps.size) {
                currentCoroutineContext().ensureActive()
                val step=workflow.steps[index];val current=checkpoint.texts.lastOrNull() ?: text
                var next=index+1
                val value=when(step.kind) {
                    "normalize"->WorkflowValue(current.split(Regex("(?U)\\s+")).filter {it.isNotEmpty()}.joinToString(" "))
                    "condition"->{if(!current.contains(step.containsText.orEmpty())) next=(next+(step.skipSteps ?: 1)).coerceAtMost(workflow.steps.size);WorkflowValue(current)}
                    else->{var attempt=0;var completed: WorkflowValue?=null;while(completed==null) {try {completed=execute(step,current)} catch(e:CancellationException) {throw e} catch(e:Exception) {currentCoroutineContext().ensureActive();if(attempt++ >= (step.retries ?: 0)) throw e}};completed}
                }
                currentCoroutineContext().ensureActive();require(value.text.isNotBlank()&&value.text.length<=20000)
                if(step.kind=="speak") markSynthetic(requireNotNull(value.audio),File(dir,"output.wav"))
                checkpoint=WorkflowCheckpoint(checkpoint.texts+value.text,next,checkpoint.spoken==true||step.kind=="speak")
                atomicText(manifest,LocalJson.encodeToString(WorkflowCheckpoint.serializer(),checkpoint));index=next;progress(index.toFloat()/workflow.steps.size)
            }
            val spoken=checkpoint.spoken ?: (workflow.steps.last().kind=="speak"&&checkpoint.texts.size==workflow.steps.size)
            val audio=if(spoken) File(dir,"output.wav").also {check(it.isFile)} else null
            PhoneWorkflowResult(checkpoint.texts.lastOrNull() ?: text,audio)
        } finally {lock.unlock()}
    }
}

object WorkflowTranscription {
    suspend fun run(source: File,model: PhoneModel,engine: LocalEngine,decoder: AudioDecoder=AndroidAudioDecoder(),progress: (Float)->Unit={}): String=withContext(Dispatchers.IO) {
        val scratch=File.createTempFile("workflow-",".pcm",source.parentFile)
        try {
            val pcm=decoder.decode(source,scratch,14400) {progress(it*.2f)}
            require(pcm.frames>0);val text=StringBuilder();var start=0L
            while(start<pcm.frames) {
                currentCoroutineContext().ensureActive()
                val part=engine.transcribe(model,pcm.read(start,minOf(480000L,pcm.frames-start).toInt())).trim()
                if(part.isNotEmpty()) {if(text.isNotEmpty()) text.append(' ');text.append(part)}
                require(text.length<=20000);start+=480000;progress(.2f+.8f*minOf(start,pcm.frames).toFloat()/pcm.frames)
            }
            require(text.isNotBlank());text.toString()
        } finally {scratch.delete()}
    }
}
