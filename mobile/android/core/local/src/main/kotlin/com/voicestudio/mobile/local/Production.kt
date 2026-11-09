package com.voicestudio.mobile.local

import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*

data class ProductionSegment(val chapter: Int, val role: String, val text: String, val speed: Float=1f, val pause: Double=0.0)
data class ProductionPlan(val segments: List<ProductionSegment>, val chapters: List<String>) {
    val roles get() = segments.map {it.role}.filter {it.isNotEmpty()}.distinct()
    companion object {
        // Desktop [emotion:TEXT] directions (#3422): phone models take no emotion, so the tag is
        // dropped (never read aloud). Stripped from the whole script before it is split into lines,
        // because a desktop tag may span lines; the line breaks inside a tag are kept so a voice tag
        // after it still starts its line. A tag never spans a "# " heading, which the desktop
        // parser splits first. Case-insensitive like the desktop parser.
        private val EMOTION=Regex("\\[emotion:[^\\]\\[]*\\]",RegexOption.IGNORE_CASE)
        fun parse(script: String,pronunciations: Map<String,String> = emptyMap(),speed: Float=1f): ProductionPlan {
            require(speed.isFinite()&&speed in 0.5f..2f&&pronunciations.size<=100)
            val replacements=NarrationText.rules(pronunciations)
            require(script.length <= 1_000_000)
            val segments=mutableListOf<ProductionSegment>();val chapters=mutableListOf("");var role=""
            EMOTION.replace(script) {tag -> if(tag.value.lines().drop(1).any {it.trim().startsWith("# ")}) tag.value else tag.value.filter {it=='\n'||it=='\r'}.ifEmpty {" "}}.lineSequence().forEach {raw ->
                var line=raw.trim()
                if(line.startsWith("# ")) {
                    val title=line.drop(2).trim()
                    if(chapters.size==1&&chapters[0].isEmpty()&&segments.isEmpty()) chapters[0]=title else chapters+=title
                } else {
                    if(line.startsWith("[voice:")&&']' in line) {
                        role=line.substring(7,line.indexOf(']')).trim();line=line.substringAfter(']').trim()
                    }
                    var lineSpeed=speed
                    if(line.startsWith("[speed:")&&']' in line) {
                        lineSpeed=line.substring(7,line.indexOf(']')).toFloat();require(lineSpeed.isFinite()&&lineSpeed in 0.5f..2f);line=line.substringAfter(']').trim()
                    }
                    fun appendText(raw: String) {
                        val value=NarrationText.apply(raw,replacements);var cursor=0
                        while(cursor<value.length) {
                            var end=minOf(cursor+400,value.length)
                            if(end<value.length&&value[end-1].isHighSurrogate()) end--
                            if(end<value.length) value.substring(cursor,end).indexOfLast {it.isWhitespace()}.takeIf {it>0}?.let {end=cursor+it}
                            val text=value.substring(cursor,end).trim()
                            if(text.isNotEmpty()) segments+=ProductionSegment(chapters.lastIndex,role,text,lineSpeed)
                            require(segments.size<=10000)
                            cursor=end;while(cursor<value.length&&value[cursor].isWhitespace()) cursor++
                        }
                    }
                    var cursor=0
                    val pauses=Regex("\\[pause(?::|[ \\t]+)([0-9]+(?:\\.[0-9]+)?)(ms|s)?\\]")
                    for(match in pauses.findAll(line)) {
                        appendText(line.substring(cursor,match.range.first))
                        val duration=match.groupValues[1].toDouble()/(if(match.groupValues[2]=="ms") 1000 else 1)
                        require(duration.isFinite()&&duration>0&&duration<=30)
                        segments+=ProductionSegment(chapters.lastIndex,role,"",pause=duration);require(segments.size<=10000)
                        cursor=match.range.last+1
                    }
                    appendText(line.substring(cursor))
                }
            }
            require(segments.any {it.text.isNotEmpty()}&&segments.size<=10000)
            return ProductionPlan(segments,chapters)
        }
    }
}

@Serializable data class ProductionProject(val id: String=UUID.randomUUID().toString(),val kind: String,val title: String,
    val script: String="",val defaultVoice: String="preset:0",val cast: Map<String,String> = emptyMap(),val modifiedAt: Long=System.currentTimeMillis(),val pronunciations: Map<String,String> = emptyMap(),val speed: Float=1f,val handoff: JsonObject?=null) {
    fun plan()=ProductionPlan.parse(script,pronunciations,speed)
    fun voice(role: String)=cast[role]?.takeIf {it.isNotEmpty()} ?: defaultVoice
}

/** Durable drafts; completed chunks survive interruption, and only completed renders publish output. */
class ProductionStore(private val root: File) {
    companion object {private val locks=ConcurrentHashMap<String,Mutex>()}
    private fun directory(id: String): File {require(UUID.fromString(id).toString()==id);return File(root,id)}
    internal fun handoffDirectory(id: String)=directory(id)
    internal fun handoffRenderDirectory(project: ProductionProject,configuration: String)=renderDirectory(project,configuration)
    private fun key(project: ProductionProject,configuration: String): String {
        val cast=buildJsonObject {project.cast.toSortedMap().forEach {(k,v)->put(k,v)}}
        // Scripts with a desktop [emotion:] tag parse differently since #3422 (the tag is no longer
        // spoken), so only they get a new key; every other script keeps its renders.
        val version=if(project.script.contains("[emotion:",ignoreCase=true)) "narration-v3" else "narration-v2"
        val value=buildJsonArray {add(version);add(project.script);add(project.defaultVoice);add(cast);add(project.speed);add(buildJsonObject {project.pronunciations.toSortedMap().forEach {(k,v)->put(k,v)}});add(configuration)}
        return MessageDigest.getInstance("SHA-256").digest(value.toString().toByteArray(Charsets.UTF_8)).joinToString("") {"%02x".format(it)}
    }
    private fun renderDirectory(project: ProductionProject,configuration: String)=File(directory(project.id),key(project,configuration))
    suspend fun save(project: ProductionProject)=withContext(Dispatchers.IO) {
        require(project.kind in listOf("stories","audiobook")&&project.title.isNotBlank()&&project.title.length<=200&&project.script.length<=1_000_000)
        atomicText(File(directory(project.id),"project.json"),LocalJson.encodeToString(ProductionProject.serializer(),project))
    }
    suspend fun list(): List<ProductionProject> = withContext(Dispatchers.IO) {
        root.listFiles().orEmpty().mapNotNull {file ->
            runCatching {val dir=directory(file.name);LocalJson.decodeFromString<ProductionProject>(File(dir,"project.json").readText())}.getOrNull()
        }.sortedByDescending {it.modifiedAt}
    }
    suspend fun delete(project: ProductionProject)=withContext(Dispatchers.IO) {
        locks.getOrPut(directory(project.id).absolutePath) {Mutex()}.withLock {check(directory(project.id).deleteRecursively())}
    }
    suspend fun output(project: ProductionProject,configuration: String): File? = withContext(Dispatchers.IO) {
        File(renderDirectory(project,configuration),"output.wav").takeIf {it.isFile}
    }
    suspend fun chapterOutputs(project: ProductionProject,configuration: String): List<File> = withContext(Dispatchers.IO) {
        if(output(project,configuration)==null) return@withContext emptyList()
        project.plan().segments.map {it.chapter}.distinct().map {File(renderDirectory(project,configuration),"chapter-$it.wav")}
    }
    suspend fun chapterCues(project: ProductionProject,configuration: String): List<BookChapter> = withContext(Dispatchers.IO) {
        if(output(project,configuration)==null) return@withContext emptyList()
        val plan=project.plan();val dir=renderDirectory(project,configuration);val cues=mutableListOf<BookChapter>();var frames=0L
        plan.segments.forEachIndexed {index,segment->
            if(index>0&&plan.segments[index-1].pause==0.0&&segment.pause==0.0) frames+=6000
            if(index==0||plan.segments[index-1].chapter!=segment.chapter) cues+=BookChapter(plan.chapters[segment.chapter].ifEmpty {(cues.size+1).toString()},frames/24000.0)
            frames+=PcmFile(File(dir,"$index.pcm")).frames
        };cues
    }
    suspend fun render(project: ProductionProject,configuration: String,progress: (Float)->Unit = {},
        synthesize: suspend (ProductionSegment,String)->LocalAudio): File = withContext(Dispatchers.IO) {
        val lock=locks.getOrPut(directory(project.id).absolutePath) {Mutex()}
        check(lock.tryLock())
        try {
            val plan=project.plan();save(project)
            val dir=renderDirectory(project,configuration).apply {mkdirs()}
            output(project,configuration)?.let {return@withContext it}
            plan.segments.forEachIndexed {index,segment ->
                currentCoroutineContext().ensureActive()
                val file=File(dir,"$index.pcm")
                if(!file.isFile) {
                    val voice=project.voice(segment.role)
                    val value="v2|$configuration|$voice|${segment.speed}|${segment.pause}|${segment.text}"
                    val cacheKey=MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).joinToString("") {"%02x".format(it)}
                    val cache=File(directory(project.id),"segments").apply {mkdirs()};val cached=File(cache,"$cacheKey.pcm")
                    if(!cached.isFile) {
                        val samples=if(segment.pause>0) FloatArray((segment.pause*24000).toInt()) else {
                            val audio=synthesize(segment,voice)
                            currentCoroutineContext().ensureActive()
                            require(audio.sampleRate>0&&audio.samples.isNotEmpty()&&audio.samples.all {it.isFinite()})
                            NarrationAudio.speed(if(audio.sampleRate==24000) audio.samples else MonoResampler(audio.sampleRate,24000).accept(audio.samples),segment.speed)
                        }
                        val pending=File(cache,"$cacheKey.pending");writePcm(LocalAudio(samples,24000),pending)
                        java.nio.file.Files.move(pending.toPath(),cached.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                    }
                    try {java.nio.file.Files.createLink(file.toPath(),cached.toPath())} catch(_:Exception) {
                        val pending=File(dir,"$index.pending");cached.copyTo(pending,overwrite=true)
                        java.nio.file.Files.move(pending.toPath(),file.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                    }
                }
                progress((index+1f)/(plan.segments.size+1))
            }
            suspend fun assemble(indexes: List<Int>,name: String): File {
                val pcm=File(dir,"assembly.pcm")
                FileOutputStream(pcm).use {out ->
                    var total=0L
                    indexes.forEachIndexed {position,index ->
                        File(dir,"$index.pcm").inputStream().use {input ->
                            val buffer=ByteArray(32768)
                            while(true) {
                                currentCoroutineContext().ensureActive()
                                val count=input.read(buffer);if(count<0) break
                                total+=count;require(total<=1_382_400_000L);out.write(buffer,0,count)
                            }
                        }
                        if(position<indexes.lastIndex&&plan.segments[index].pause==0.0&&plan.segments[indexes[position+1]].pause==0.0) {total+=24000;require(total<=1_382_400_000L);out.write(ByteArray(24000))}
                    }
                    out.fd.sync()
                }
                val pending=File(dir,"$name.pending")
                markSynthetic(PcmFile(pcm),pending)
                currentCoroutineContext().ensureActive()
                val output=File(dir,name)
                java.nio.file.Files.move(pending.toPath(),output.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                pcm.delete();return output
            }
            plan.segments.map {it.chapter}.distinct().forEach {chapter ->
                val name="chapter-$chapter.wav"
                if(!File(dir,name).isFile) assemble(plan.segments.indices.filter {plan.segments[it].chapter==chapter},name)
            }
            assemble(plan.segments.indices.toList(),"output.wav").also {progress(1f)}
        } finally {lock.unlock()}
    }
}
