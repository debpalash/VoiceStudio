package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import java.io.File
import java.nio.file.Files
import java.util.UUID
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

@Serializable data class PhoneTranscript(val id: String=UUID.randomUUID().toString(),val createdAt: Double=System.currentTimeMillis().toDouble(),val title: String,val originalText: String="",val editedText: String?=null,val segments: List<SpeechSegment> = emptyList(),val kind: String="dictation",val modelID: String?=null,val modelPin: String?=null,val frames: Long=0,val nextFrame: Long=0,val sourceHash: String?=null,val complete: Boolean=true) {
    val text get()=editedText ?: originalText
    val progress get()=if(frames>0) nextFrame.toDouble()/frames else 1.0
    val valid get()=runCatching {UUID.fromString(id).toString()==id}.getOrDefault(false)&&createdAt.isFinite()&&createdAt>=0&&title.isNotBlank()&&title.length<=300&&originalText.length<=1_000_000&&(editedText?.length ?: 0)<=1_000_000&&kind in listOf("dictation","file")&&frames in 0..345600000&&nextFrame in 0..frames&&segments.size<=50000&&segments.all {it.start.isFinite()&&it.end.isFinite()&&it.start>=0&&it.end>it.start&&it.end<=14400&&it.text.length<=20000}&&(complete||(kind=="file"&&modelID!=null&&modelPin!=null&&sourceHash!=null))
}
private fun boundedTranscriptTitle(title: String): String {
    val value=title.trim().take(300)
    return if(value.lastOrNull()?.isHighSurrogate()==true) value.dropLast(1) else value
}
class PhoneTranscriptStore(private val root: File) {
    companion object {const val MAX_SOURCE_BYTES=1024L*1024*1024;private val locks=ConcurrentHashMap<String,Mutex>()}
    private fun directory(id: String): File {
        require(UUID.fromString(id).toString()==id)
        val dir=File(root,id);require(!Files.isSymbolicLink(dir.toPath()));return dir
    }
    private fun lock(id: String)=locks.getOrPut(directory(id).absolutePath) {Mutex()}
    private fun write(value: PhoneTranscript) {
        require(value.valid);val file=File(directory(value.id),"transcript.json");require(!Files.isSymbolicLink(file.toPath()))
        atomicText(file,LocalJson.encodeToString(PhoneTranscript.serializer(),value))
    }
    private fun read(id: String): PhoneTranscript {
        val file=File(directory(id),"transcript.json");require(!Files.isSymbolicLink(file.toPath())&&file.isFile&&file.length()<=16*1024*1024)
        return LocalJson.decodeFromString(PhoneTranscript.serializer(),file.readText(Charsets.UTF_8)).also {require(it.id==id&&it.valid)}
    }
    suspend fun record(id: String)=withContext(Dispatchers.IO) {read(id)}
    suspend fun list(query: String=""): List<PhoneTranscript> = withContext(Dispatchers.IO) {
        root.listFiles().orEmpty().mapNotNull {runCatching {read(it.name)}.getOrNull()}.filter {query.isEmpty()||it.title.contains(query,true)||it.text.contains(query,true)}.sortedByDescending {it.createdAt}
    }
    suspend fun saveDictation(id: String,title: String,text: String,modelID: String?=null): PhoneTranscript=withContext(Dispatchers.IO) {
        lock(id).withLock {
            val clean=text.trim();if(clean.isEmpty()) throw ClientFailure("mobile.simple.no_speech")
            runCatching {read(id)}.getOrNull() ?: PhoneTranscript(id=id,title=boundedTranscriptTitle(title.takeUnless {it.isBlank()} ?: clean),originalText=clean,modelID=modelID).also {write(it)}
        }
    }
    suspend fun edit(id: String,text: String?)=withContext(Dispatchers.IO) {
        val guard=lock(id);check(guard.tryLock())
        try {val value=read(id);check(value.complete);write(value.copy(editedText=text))} finally {guard.unlock()}
    }
    suspend fun delete(id: String)=withContext(Dispatchers.IO) {
        val guard=lock(id);check(guard.tryLock());try {check(directory(id).deleteRecursively());Unit} finally {guard.unlock()}
    }
    suspend fun prepare(source: File,title: String,model: PhoneModel,decoder: AudioDecoder=AndroidAudioDecoder(),progress: (Float)->Unit = {}): PhoneTranscript=withContext(Dispatchers.IO) {
        require(model.capability=="asr");if(!source.isFile||source.length()>MAX_SOURCE_BYTES) throw ClientFailure("mobile.errors.file");val id=UUID.randomUUID().toString();val dir=directory(id).apply {mkdirs()}
        try {
            val pcm=decoder.decode(source,File(dir,"source.pcm"),14400,progress);val frames=pcm.frames;require(frames>0)
            val value=PhoneTranscript(id=id,title=boundedTranscriptTitle(title),kind="file",modelID=model.id,modelPin=modelPin(model).identity,frames=frames,sourceHash=sha256(pcm.file),complete=false)
            ensureActive();write(value);value
        } catch(e:Throwable) {dir.deleteRecursively();throw e}
    }
    suspend fun run(id: String,model: PhoneModel,progress: (Float)->Unit = {},recognize: suspend (LocalAudio)->List<SpeechSegment>): PhoneTranscript=withContext(Dispatchers.IO) {
        val guard=lock(id);check(guard.tryLock())
        try {
            var value=read(id);if(value.complete) return@withContext value
            if(model.capability!="asr"||value.modelID!=model.id||value.modelPin!=modelPin(model).identity) throw ClientFailure("mobile.local.model_incompatible")
            val pcm=PcmFile(File(directory(id),"source.pcm"));require(!Files.isSymbolicLink(pcm.file.toPath())&&pcm.frames==value.frames&&sha256(pcm.file)==value.sourceHash)
            while(value.nextFrame<value.frames) {
                ensureActive();val first=value.nextFrame;val count=minOf(480000,value.frames-first).toInt();val duration=count/24000.0
                val segments=recognize(pcm.read(first,count));ensureActive()
                require(segments.size<=2000&&segments.all {it.start.isFinite()&&it.end.isFinite()&&it.start>=0&&it.end>it.start&&it.end<=duration+.001&&it.text.length<=20000})
                val shifted=segments.filter {it.text.isNotBlank()}.map {SpeechSegment(first/24000.0+it.start,minOf(value.frames/24000.0,first/24000.0+it.end),it.text)}
                val all=value.segments+shifted
                value=value.copy(segments=all,originalText=all.joinToString("\n") {it.text},nextFrame=first+count);write(value);progress(value.progress.toFloat())
            }
            value=value.copy(complete=true);write(value);pcm.file.delete();value
        } finally {guard.unlock()}
    }
}
object PhoneTranscriptExport {
    fun text(records: List<PhoneTranscript>)=records.joinToString("\n\n---\n\n") {it.title+"\n"+java.time.Instant.ofEpochMilli(it.createdAt.toLong()).toString()+"\n\n"+it.text}+"\n"
    fun subtitles(record: PhoneTranscript,vtt: Boolean): String {
        require(record.editedText==null&&record.segments.isNotEmpty()&&record.valid)
        fun time(seconds: Double): String {val ms=kotlin.math.round(seconds*1000).toLong();return String.format(Locale.ROOT,"%02d:%02d:%02d%s%03d",ms/3600000,(ms/60000)%60,(ms/1000)%60,if(vtt) "." else ",",ms%1000)}
        return (if(vtt) "WEBVTT\n\n" else "")+record.segments.mapIndexed {index,cue ->
            val safe=cue.text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").lines().filter {it.isNotEmpty()}.joinToString("\n")
            "${index+1}\n${time(cue.start)} --> ${time(cue.end)}\n$safe\n"
        }.joinToString("\n")
    }
}
