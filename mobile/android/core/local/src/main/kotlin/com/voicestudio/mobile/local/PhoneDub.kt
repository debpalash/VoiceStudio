package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

@Serializable data class DubTarget(val code: String,val name: String)
@Serializable data class DubModelPin(val id: String,val identity: String)
@Serializable data class PhoneDubRequest(val id: String,val title: String,val sourceFile: String,val targets: List<DubTarget>,
    val models: List<DubModelPin>,val createdAt: Long,val consentAt: Long,val referenceText: String? = null,val audioOnly: Boolean = false,val sourceHash: String? = null)
@Serializable data class DubSegmentState(val speech: SpeechSegment,val referenceText: String? = null,val translations: Map<String,String> = emptyMap(),val referenceFile: String? = null,val id: String? = null,val referenceNeedsTranscription: Boolean = false,val gain: Double = 1.0,val muted: Boolean = false,val speaker: String? = null,val referenceOrigin: String? = null,val voiceID: String? = null)
@Serializable data class DubRender(val index: Int,val sha256: String,val frames: Long,val target: String? = null,val fingerprint: String? = null,val file: String? = null,val segmentID: String? = null,val scope: String? = null,val originalHash: String? = null,val referenceHash: String? = null,val clonePin: String? = null)
@Serializable internal data class DubCachedAudio(val sha256: String,val frames: Long)
@Serializable data class PhoneDubOutput(val language: String,val name: String,val file: String,val sha256: String,val duration: Double,val fingerprint: String? = null)
@Serializable data class PhoneDubCheckpoint(val frames: Long = 0,val nextFrame: Long = 0,val segments: List<DubSegmentState> = emptyList(),
    val renders: List<DubRender> = emptyList(),val outputs: List<PhoneDubOutput> = emptyList(),val stage: String = "mobile.android.stage_prepare",val percent: Int = 0,val done: Boolean = false,val errorParams: Map<String,String> = emptyMap(),val originalHash: String? = null)
data class PhoneDubRecord(val request: PhoneDubRequest,val checkpoint: PhoneDubCheckpoint)
data class DubProgress(val stage: String,val percent: Int)
fun modelPin(model: PhoneModel): DubModelPin {
    val bytes=LocalJson.encodeToString(PhoneModel.serializer(),model).toByteArray()
    return DubModelPin(model.id,MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") {"%02x".format(it)})
}
/** App-private jobs contain source, immutable model identities and atomic checkpoints. */
class PhoneDubStore(private val root: File) {
    internal val checkpointWrites=java.util.concurrent.atomic.AtomicLong()
    fun directory(id: String): File {
        require(runCatching {UUID.fromString(id).toString()==id}.getOrDefault(false))
        return File(root,id)
    }
    fun file(id: String, name: String): File {
        require(name.matches(Regex("[A-Za-z0-9_.-]+"))&&name !in setOf(".",".."))
        return File(directory(id),name)
    }
    suspend fun create(source: File,title: String,targets: List<DubTarget>,models: List<PhoneModel>,consent: Boolean,
        reference: LocalAudio? = null,referenceText: String? = null,audioOnly: Boolean = false): PhoneDubRequest = withContext(Dispatchers.IO) {
        require(consent&&targets.isNotEmpty()&&targets.size<=20&&targets.map {it.code}.distinct().size==targets.size)
        require(targets.all {it.code.matches(Regex("[A-Za-z0-9_-]{1,80}"))&&it.name.length in 2..120&&it.name.none(Char::isISOControl)})
        require(models.size==3&&models.map {it.capability}.toSet()==setOf("asr","translate","clone"))
        require((reference==null)==(referenceText==null)&&referenceText?.isBlank()!=true)
        val id=UUID.randomUUID().toString();val dir=directory(id).apply {mkdirs()}
        try {
            if(dir.usableSpace<source.length()+16_000_000) throw ClientFailure("mobile.local.storage_required")
            val extension=source.extension.takeIf {it.matches(Regex("[A-Za-z0-9]{1,10}"))} ?: "media"
            val sourceName="source.$extension"
            source.inputStream().use {input -> file(id,sourceName).outputStream().use {out ->
                val bytes=ByteArray(262144)
                while(true) {currentCoroutineContext().ensureActive();val count=input.read(bytes);if(count<0) break;out.write(bytes,0,count)}
                out.fd.sync()
            }}
            if(reference!=null) {require(reference.sampleRate==24000);writePcm(reference,file(id,"voice-reference.pcm"))}
            val now=System.currentTimeMillis()
            val request=PhoneDubRequest(id,title.take(250),sourceName,targets,models.map(::modelPin),now,now,referenceText,audioOnly,sha256(file(id,sourceName)))
            save(id,PhoneDubCheckpoint())
            atomicText(file(id,"request.json"),LocalJson.encodeToString(PhoneDubRequest.serializer(),request));request
        } catch(e:Throwable) {dir.deleteRecursively();throw e}
    }
    suspend fun request(id: String): PhoneDubRequest = withContext(Dispatchers.IO) {
        try {LocalJson.decodeFromString<PhoneDubRequest>(file(id,"request.json").readText()).also {require(it.id==id);file(id,it.sourceFile)}}
        catch(e:CancellationException) {throw e} catch(e:Exception) {throw ClientFailure("mobile.simple.checkpoint_failed")}
    }
    suspend fun checkpoint(id: String): PhoneDubCheckpoint = withContext(Dispatchers.IO) {
        try {LocalJson.decodeFromString<PhoneDubCheckpoint>(file(id,"checkpoint.json").readText()).let {cp ->
            require(cp.frames in 0..345600000L&&cp.nextFrame in 0..cp.frames&&cp.segments.size<=10000&&cp.outputs.size<=20)
            cp.outputs.forEach {file(id,it.file)}
            val segments=cp.segments.mapIndexed {index,segment ->if(segment.id==null) segment.copy(id="legacy-$index") else segment}
            require(segments.map {it.id}.distinct().size==segments.size&&segments.all {it.speech.start.isFinite()&&it.speech.end.isFinite()&&it.speech.start>=0&&it.speech.end>it.speech.start&&it.speech.end<=cp.frames/24000.0+.01&&it.speech.text.isNotBlank()&&it.gain.isFinite()&&it.gain in 0.0..2.0})
            cp.copy(segments=segments)
        }}
        catch(e:CancellationException) {throw e} catch(e:Exception) {throw ClientFailure("mobile.simple.checkpoint_failed")}
    }
    suspend fun save(id: String,value: PhoneDubCheckpoint) = withContext(Dispatchers.IO) {
        try {atomicText(file(id,"checkpoint.json"),LocalJson.encodeToString(PhoneDubCheckpoint.serializer(),value));checkpointWrites.incrementAndGet();Unit}
        catch(e:CancellationException) {throw e} catch(e:Exception) {throw ClientFailure("mobile.simple.checkpoint_failed")}
    }
    suspend fun list(): List<PhoneDubRecord> = withContext(Dispatchers.IO) {
        root.listFiles().orEmpty().filter {File(it,"request.json").exists()}.mapNotNull {
            currentCoroutineContext().ensureActive()
            try {PhoneDubRecord(request(it.name),checkpoint(it.name))}
            catch(e:CancellationException) {throw e} catch(_:Exception) {null}
        }.sortedByDescending {it.request.createdAt}
    }
    /**
     * Serialises everything that writes one job (a running worker, its cancel cleanup, a resumed worker,
     * deletion). WorkManager reports a cancelled job finished before its native call has returned, so
     * the lock, not WorkInfo, is what makes resume and delete wait for the old run to finish saving.
     */
    suspend fun <T> exclusive(id: String,block: suspend ()->T): T {
        val key=directory(id).absolutePath
        return locks.getOrPut(key) {Mutex()}.withLock {block()}
    }
    suspend fun edit(id: String,segments: List<DubSegmentState>,references: Map<Int,Pair<LocalAudio,String>> = emptyMap()) = exclusive(id) {withContext(Dispatchers.IO) {
        val request=request(id);val current=checkpoint(id);val duration=current.frames/24000.0
        require(file(id,"original.pcm").isFile&&segments.isNotEmpty()&&segments.size<=10000&&segments.mapNotNull {it.id}.distinct().size==segments.size)
        require(segments.all {s ->s.speech.start.isFinite()&&s.speech.end.isFinite()&&s.speech.start>=0&&s.speech.end>s.speech.start&&s.speech.end<=duration&&s.speech.end-s.speech.start<=30&&s.speech.text.isNotBlank()&&s.speech.text.length<=4000&&s.gain.isFinite()&&s.gain in 0.0..2.0&&s.translations.all {(key,value)->request.targets.any {it.code==key}&&value.isNotBlank()&&value.length<=4000}})
        require(segments.all(PhoneDubCast::valid))
        val updated=segments.map {it.copy(speaker=it.speaker?.trim()?.takeIf(String::isNotEmpty))}.toMutableList();val created=mutableListOf<File>();val sharedReferences=mutableMapOf<String,String>()
        val referenceFingerprints=java.util.IdentityHashMap<FloatArray,MutableMap<String,String>>()
        try {
            references.forEach {(index,reference) ->
                require(index in updated.indices&&reference.second.isNotBlank()&&reference.first.sampleRate==24000&&reference.first.samples.size in 2400..720000)
                val byText=referenceFingerprints.getOrPut(reference.first.samples) {mutableMapOf()}
                val fingerprint=byText.getOrPut(reference.second) {dubFingerprint(listOf(dubAudioHash(reference.first),reference.second))}
                val name=sharedReferences.getOrPut(fingerprint) {
                    "edited-reference-${UUID.randomUUID()}.pcm".also {name ->val output=file(id,name);writePcm(reference.first,output);created+=output}
                }
                updated[index]=updated[index].copy(referenceFile=name,referenceText=reference.second)
            }
            val previous=current.segments.associateBy {it.id}
            updated.indices.forEach {index ->
                val old=previous[updated[index].id]
                if(old!=null&&old.speech.text!=updated[index].speech.text) updated[index]=updated[index].copy(translations=updated[index].translations.filter {(language,text) ->text!=old.translations[language]})
                updated[index].referenceFile?.let {require(file(id,it).isFile)} ?: run {
                    if(updated[index].speech!=previous[updated[index].id]?.speech) updated[index]=updated[index].copy(referenceText=null,referenceNeedsTranscription=true)
                }
            }
            currentCoroutineContext().ensureActive()
            val updatedByID=updated.mapIndexed {index,segment ->segment.id to (index to segment)}.toMap()
            val renders=current.renders.mapNotNull {r ->updatedByID[r.segmentID]?.takeIf {dubSegmentScope(it.second,r.target.orEmpty())==r.scope}?.let {r.copy(index=it.first)}}
            save(id,current.copy(segments=updated,renders=renders,done=false,percent=35,stage="mobile.android.stage_translate"))
        } catch(e:Throwable) {created.forEach {it.delete()};throw e}
    }}
    suspend fun delete(id: String) = exclusive(id) {withContext(Dispatchers.IO) {if(!directory(id).deleteRecursively()) throw ClientFailure("mobile.simple.checkpoint_failed")}}
    private companion object {val locks=java.util.concurrent.ConcurrentHashMap<String,Mutex>()}
}
interface DubMedia {
    /** Returns the output file; a video export replaces its original soundtrack. */
    suspend fun finish(source: File,audio: PcmFile,markedWave: File,output: File,audioOnly: Boolean,progress: (Float)->Unit = {}): File
}
/** No network API exists here. Every native call passes through LocalEngine. */
class PhoneDubPipeline(private val store: PhoneDubStore,private val engine: LocalEngine,private val decoder: AudioDecoder = AndroidAudioDecoder(),
    private val media: DubMedia = AndroidDubMedia(),private val authorize: ()->Unit = {}) {
    suspend fun run(id: String,models: List<PhoneModel>,report: (DubProgress)->Unit = {}): PhoneDubCheckpoint = withContext(Dispatchers.IO) {
        val request=store.request(id)
        if(request.models.toSet()!=models.map(::modelPin).toSet()) throw ClientFailure("mobile.local.model_incompatible")
        val asr=models.single {it.capability=="asr"};val translator=models.single {it.capability=="translate"};val clone=models.single {it.capability=="clone"}
        var checkpoint=store.checkpoint(id)
        suspend fun save(value: PhoneDubCheckpoint) {authorize();currentCoroutineContext().ensureActive();store.save(id,value);checkpoint=value}
        suspend fun stage(key: String,percent: Int) {save(checkpoint.copy(stage=key,percent=percent,errorParams=emptyMap()));report(DubProgress(key,percent))}
        fun nativeProgress(key: String,base: Int,span: Int): (Float)->Unit = {value ->report(DubProgress(key,(base+value.coerceIn(0f,1f)*span).toInt().coerceIn(0,99)))}
        if(checkpoint.done&&checkpoint.outputs.size==request.targets.size&&checkpoint.outputs.all {output ->
            val file=store.file(id,output.file);file.exists()&&sha256(file)==output.sha256
        }) {report(DubProgress("mobile.android.stage_done",100));return@withContext checkpoint}
        if(checkpoint.done) throw ClientFailure("mobile.simple.media_failed")
        authorize()
        val source=store.file(id,request.sourceFile)
        if(request.sourceHash!=null&&sha256(source)!=request.sourceHash) throw ClientFailure("mobile.simple.media_failed")
        val original=store.file(id,"original.pcm")
        if(checkpoint.frames==0L||original.length()!=checkpoint.frames*4||(checkpoint.originalHash!=null&&sha256(original)!=checkpoint.originalHash)) {
            // An interrupted extraction is discarded; a checkpoint is published only after fsync.
            stage("mobile.android.stage_prepare",0)
            val decoded=decoder.decode(source,original,14400,nativeProgress("mobile.android.stage_prepare",0,10))
            // Re-extraction must never discard an edited timeline or a previously published result.
            if(checkpoint.frames!=0L&&checkpoint.frames!=decoded.frames) throw ClientFailure("mobile.simple.media_failed")
            save(checkpoint.copy(frames=decoded.frames,originalHash=sha256(original),done=false))
        }
        if(checkpoint.originalHash==null) save(checkpoint.copy(originalHash=sha256(original)))
        val pcm=PcmFile(original)
        val window=24000*20L
        while(checkpoint.nextFrame<pcm.frames) {
            currentCoroutineContext().ensureActive();authorize()
            val start=checkpoint.nextFrame
            val count=minOf(window,pcm.frames-start).toInt()
            stage("mobile.android.stage_transcribe",10+(start.toDouble()/pcm.frames*25).toInt())
            val audio=pcm.read(start,count)
            val segments=if(count<2400||audio.samples.none {kotlin.math.abs(it)>.001f}) emptyList() else engine.transcribeSegments(asr,audio)
            val offset=start.toDouble()/24000
            val duration=count.toDouble()/24000
            require(segments.all {it.start.isFinite()&&it.end.isFinite()&&it.start>=0&&it.end>it.start&&it.end<=duration+.01&&it.text.isNotBlank()})
            val absolute=segments.map {DubSegmentState(it.copy(start=offset+it.start,end=minOf(pcm.duration,offset+it.end)),id=UUID.randomUUID().toString())}
            save(checkpoint.copy(nextFrame=start+count,segments=checkpoint.segments+absolute))
        }
        if(checkpoint.segments.isEmpty()) throw ClientFailure("mobile.simple.no_speech")
        require(checkpoint.segments.size<=10000)
        val cloneIdentity=modelPin(clone).identity
        val referenceHashes=mutableMapOf<String,String>()
        fun referenceHash(segment: DubSegmentState): String {
            val name=segment.referenceFile ?: if(request.referenceText!=null) "voice-reference.pcm" else return ""
            return referenceHashes.getOrPut(name) {sha256(store.file(id,name))}
        }
        for(targetIndex in request.targets.indices) {
            val target=request.targets[targetIndex]
            val previousRenders=checkpoint.renders.filter {it.target==target.code}.associateBy {it.index}
            val needed=pcm.frames*12+source.length()+16_000_000
            if(store.directory(id).usableSpace<needed) throw ClientFailure("mobile.local.storage_required")
            fun targetPercent(fraction: Double)=(35+(targetIndex+fraction)/request.targets.size*65).toInt().coerceIn(35,99)
            for(index in checkpoint.segments.indices) {
                currentCoroutineContext().ensureActive();authorize()
                val base=targetPercent(index.toDouble()/checkpoint.segments.size*.85)
                var segment=checkpoint.segments[index]
                if(segment.translations[target.code].isNullOrBlank()) {
                    stage("mobile.android.stage_translate",base)
                    val translated=engine.translate(translator,segment.speech.text,target.name,nativeProgress("mobile.android.stage_translate",base,1)).trim()
                    if(translated.isBlank()) throw ClientFailure("mobile.android.translation_failed")
                    segment=segment.copy(translations=segment.translations+(target.code to translated))
                    save(checkpoint.copy(segments=checkpoint.segments.toMutableList().also {it[index]=segment}))
                }
                val start=(segment.speech.start*24000).toLong()
                val end=minOf(pcm.frames,(segment.speech.end*24000).toLong())
                val frames=(end-start).toInt().coerceAtLeast(1)
                val referenceIdentity=referenceHash(segment)
                val previous=previousRenders[index]
                if(previous?.fingerprint!=null&&previous.file!=null&&previous.segmentID==segment.id&&previous.scope==dubSegmentScope(segment,target.code)&&previous.originalHash==checkpoint.originalHash&&previous.referenceHash==referenceIdentity&&previous.clonePin==cloneIdentity&&previous.frames==frames.toLong()) {
                    val cached=store.file(id,previous.file)
                    if(cached.length()==frames*4L&&sha256(cached)==previous.sha256) continue
                }
                stage("mobile.android.stage_render",base)
                val duration=segment.speech.end-segment.speech.start
                // A saved text equal to the segment's own transcript means recognition of the 8 s clip came back
                // blank: the reference is then the whole segment, so a resumed job pairs the same audio with the same words.
                val wholeSegment=request.referenceText==null&&duration>8.0&&!segment.referenceText.isNullOrBlank()&&segment.referenceText==segment.speech.text
                var reference=if(segment.referenceFile!=null) {
                    val saved=PcmFile(store.file(id,segment.referenceFile));saved.read(0,saved.frames.toInt())
                } else if(request.referenceText!=null) {
                    val saved=PcmFile(store.file(id,"voice-reference.pcm"));saved.read(0,saved.frames.toInt())
                } else pcm.clip(segment.speech.start,if(wholeSegment) segment.speech.end else minOf(segment.speech.end,segment.speech.start+8.0))
                val referenceText=(if(segment.referenceFile!=null) segment.referenceText else request.referenceText ?: segment.referenceText)?.takeIf {it.isNotBlank()} ?: run {
                    val recognized=if(duration<=8.0&&!segment.referenceNeedsTranscription) segment.speech.text else engine.transcribe(asr,reference).trim()
                    val text=recognized.ifBlank {
                        if(segment.referenceNeedsTranscription) throw ClientFailure("mobile.simple.no_speech")
                        // Quiet or short clip: use the whole segment with its own transcript, never a clip with someone else's words.
                        reference=pcm.clip(segment.speech.start,segment.speech.end)
                        segment.speech.text
                    }
                    segment=segment.copy(referenceText=text)
                    save(checkpoint.copy(segments=checkpoint.segments.toMutableList().also {it[index]=segment}))
                    text
                }
                val fingerprint=dubFingerprint(listOf(target.code,segment.speech.text,requireNotNull(segment.translations[target.code]),segment.speech.start.toString(),segment.speech.end.toString(),frames.toString(),checkpoint.originalHash.orEmpty(),dubAudioHash(reference),referenceText,cloneIdentity))
                val rendered=store.file(id,"cache-$fingerprint.pcm")
                val metadata=store.file(id,"cache-$fingerprint.json")
                val cached=runCatching {LocalJson.decodeFromString<DubCachedAudio>(metadata.readText())}.getOrNull()
                if(cached!=null&&cached.frames==frames.toLong()&&rendered.length()==frames*4L&&sha256(rendered)==cached.sha256) {
                    val render=DubRender(index,cached.sha256,frames.toLong(),target.code,fingerprint,rendered.name,segment.id,dubSegmentScope(segment,target.code),checkpoint.originalHash,referenceIdentity,cloneIdentity)
                    save(checkpoint.copy(renders=checkpoint.renders.filterNot {it.target==target.code&&it.index==index}+render));continue
                }
                val generated=engine.clone(clone,requireNotNull(segment.translations[target.code]),reference,referenceText,nativeProgress("mobile.android.stage_render",base,1))
                val fitted=fitTiming(generated,frames)
                val pending=File(rendered.parentFile,rendered.name+".pending")
                writePcm(fitted,pending);currentCoroutineContext().ensureActive();authorize()
                Files.move(pending.toPath(),rendered.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
                val hash=sha256(rendered)
                atomicText(metadata,LocalJson.encodeToString(DubCachedAudio.serializer(),DubCachedAudio(hash,frames.toLong())))
                val render=DubRender(index,hash,frames.toLong(),target.code,fingerprint,rendered.name,segment.id,dubSegmentScope(segment,target.code),checkpoint.originalHash,referenceIdentity,cloneIdentity)
                save(checkpoint.copy(renders=checkpoint.renders.filterNot {it.target==target.code&&it.index==index}+render))
            }
            val renderedByIndex=checkpoint.renders.filter {it.target==target.code}.associateBy {it.index}
            val targetRenders=checkpoint.segments.indices.map {index ->requireNotNull(renderedByIndex[index])}
            val outputFingerprint=dubFingerprint(listOf(checkpoint.originalHash.orEmpty(),request.sourceHash ?: sha256(source),request.audioOnly.toString())+targetRenders.map {requireNotNull(it.fingerprint)}+checkpoint.segments.flatMap {listOf(it.gain.toString(),it.muted.toString())})
            val previousOutput=checkpoint.outputs.firstOrNull {it.language==target.code}
            if(previousOutput?.fingerprint==outputFingerprint&&store.file(id,previousOutput.file).let {it.isFile&&sha256(it)==previousOutput.sha256}) continue
            stage("mobile.simple.mixing",targetPercent(.9))
            val mixed=mixTimeline(store.file(id,"mix.pcm"),pcm.frames,checkpoint.segments.mapIndexed {index,segment ->
                (segment.speech.start*24000).toLong() to PcmFile(store.file(id,requireNotNull(targetRenders[index].file)))
            },gains=checkpoint.segments.map {if(it.muted) 0.0 else it.gain})
            val wave=store.file(id,"marked.wav")
            markSynthetic(mixed,wave)
            stage("mobile.simple.exporting",targetPercent(.95))
            val result=media.finish(source,mixed,wave,store.file(id,"output-$targetIndex-${outputFingerprint.take(20)}"),request.audioOnly,nativeProgress("mobile.simple.exporting",targetPercent(.95),maxOf(1,targetPercent(1.0)-targetPercent(.95))))
            authorize();currentCoroutineContext().ensureActive()
            require(result.parentFile.canonicalFile==store.directory(id).canonicalFile&&result.isFile)
            val output=PhoneDubOutput(target.code,target.name,result.name,sha256(result),pcm.duration,outputFingerprint)
            save(checkpoint.copy(outputs=(checkpoint.outputs.filterNot {it.language==target.code}+output).sortedBy {out ->request.targets.indexOfFirst {it.code==out.language}}))
            previousOutput?.takeIf {it.file!=result.name}?.let {store.file(id,it.file).delete()}
            mixed.file.delete();if(result!=wave) wave.delete()
        }
        save(checkpoint.copy(stage="mobile.android.stage_done",percent=100,done=true))
        runCatching {pruneDubCaches(store.directory(id),checkpoint.renders.mapNotNull {it.file}.toSet())}
        // Retain sources and voice references until explicit deletion so finished dubs remain editable.
        report(DubProgress(checkpoint.stage,100));checkpoint
    }
}

/** Keys describe the actual synthesis inputs, never mutable array positions. */
internal fun dubFingerprint(parts: List<String>): String = MessageDigest.getInstance("SHA-256")
    .digest(parts.joinToString("") {it.toByteArray(Charsets.UTF_8).size.toString()+":"+it}.toByteArray(Charsets.UTF_8)).joinToString("") {"%02x".format(it)}
internal fun dubAudioHash(audio: LocalAudio): String {
    val bytes=java.nio.ByteBuffer.allocate(audio.samples.size*4).order(java.nio.ByteOrder.LITTLE_ENDIAN)
    audio.samples.forEach {bytes.putFloat(it)}
    return MessageDigest.getInstance("SHA-256").digest(bytes.array()).joinToString("") {"%02x".format(it)}
}
fun dubSegmentScope(segment: DubSegmentState,target: String): String = dubFingerprint(listOf(segment.speech.start.toString(),segment.speech.end.toString(),segment.speech.text,segment.translations[target].orEmpty(),segment.referenceFile.orEmpty(),segment.referenceText.orEmpty(),segment.referenceNeedsTranscription.toString()))

/** Retain all published render inputs plus a small undo cache; never remove sources or voice references. */
internal fun pruneDubCaches(directory: File,referenced: Set<String>,maxBytes: Long = 256L*1024*1024,maxFiles: Int = 512) {
    val pattern=Regex("cache-[0-9a-f]{64}\\.pcm")
    val files=directory.listFiles().orEmpty()
    val unused=files.filter {it.name.matches(pattern)&&it.name !in referenced}.sortedByDescending {it.lastModified()}
    var bytes=0L;var count=0
    unused.forEach {file ->
        val size=file.length()
        if(count>=maxFiles||size>maxBytes-bytes) {
            if(file.delete()) File(directory,file.name.removeSuffix(".pcm")+".json").delete()
        } else {bytes+=size;count++}
    }
    files.filter {it.name.matches(Regex("cache-[0-9a-f]{64}\\.pcm\\.pending"))}.forEach {it.delete()}
    files.filter {it.name.matches(Regex("cache-[0-9a-f]{64}\\.json"))&&!File(directory,it.name.removeSuffix(".json")+".pcm").exists()}.forEach {it.delete()}
}
