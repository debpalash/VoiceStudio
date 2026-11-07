package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

internal val LocalJson=Json {ignoreUnknownKeys=true}
internal fun atomicText(file: File, text: String) {
    file.parentFile!!.mkdirs()
    val pending=File(file.parentFile,file.name+".pending")
    java.io.FileOutputStream(pending).use {it.write(text.toByteArray(Charsets.UTF_8));it.fd.sync()}
    Files.move(pending.toPath(),file.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
}
/** [modelRepo]/[modelRevision] name the model that made the voice, for reports; older voices have none. */
@Serializable data class PhoneVoice(val id: String, val name: String, val transcript: String, val createdAt: Long, val consentAt: Long, val sampleRate: Int = 24000,
    val modelRepo: String? = null, val modelRevision: String? = null, val designInstruction: String? = null, val designSeed: Long? = null,val sourceAttribution: Map<String,String>?=null)
/** One atomic metadata publication after ASR + reference writes. No user paths. */
class PhoneVoiceStore(private val root: File) {
    private val mutex=Mutex()
    private fun directory(id: String): File {
        require(runCatching {UUID.fromString(id).toString()==id}.getOrDefault(false))
        return File(root,id)
    }
    internal fun handoffFiles(voice: PhoneVoice): Map<String,File> = listOf("reference.pcm","reference.wav","preview.wav").mapNotNull {name->File(directory(voice.id),name).takeIf {it.isFile}?.let {name to it}}.toMap()
    internal suspend fun installHandoff(name: String,transcript: String,reference: LocalAudio,metadata: JsonObject,synthetic: Boolean,consent: Boolean): PhoneVoice=withContext(Dispatchers.IO) {mutex.withLock {
        require(consent&&name.isNotBlank()&&name.length<=200&&transcript.isNotBlank()&&transcript.length<=4000&&reference.sampleRate==24000&&reference.samples.size in 2400..720000&&reference.samples.all {it.isFinite()})
        val id=UUID.randomUUID().toString();val dir=directory(id).apply {mkdirs()}
        try {
            writePcm(reference,File(dir,"reference.pcm"));if(synthetic) markSynthetic(reference,File(dir,"reference.wav")) else writeReferenceWave(reference,File(dir,"reference.wav"))
            val attribution=(metadata["sourceAttribution"] as? JsonObject)?.mapNotNull {(key,value)->(value as? JsonPrimitive)?.takeIf {it.isString&&it.content.length<=2000&&key in setOf("source","id","name","author","license","language","url")}?.content?.let {key to it}}?.toMap()?.takeIf {it.isNotEmpty()}
            val now=System.currentTimeMillis();val voice=PhoneVoice(id,name,transcript,now,now,modelRepo=metadata["modelRepo"]?.jsonPrimitive?.contentOrNull,modelRevision=metadata["modelRevision"]?.jsonPrimitive?.contentOrNull,designInstruction=if(metadata["kind"]?.jsonPrimitive?.contentOrNull=="design") metadata["instruct"]?.jsonPrimitive?.contentOrNull else null,designSeed=metadata["phone_design_seed"]?.jsonPrimitive?.contentOrNull?.toLongOrNull(),sourceAttribution=attribution)
            currentCoroutineContext().ensureActive();atomicText(File(dir,"voice.json"),LocalJson.encodeToString(PhoneVoice.serializer(),voice));voice
        } catch(e:Throwable) {dir.deleteRecursively();throw e}
    }}
    suspend fun list(): List<PhoneVoice> = withContext(Dispatchers.IO) {mutex.withLock {
        root.listFiles().orEmpty().filter {it.isDirectory&&!it.name.startsWith('.')}.mapNotNull {dir ->
            val metadata=File(dir,"voice.json")
            if(!metadata.exists()) return@mapNotNull null
            try {
                val voice=LocalJson.decodeFromString<PhoneVoice>(metadata.readText())
                directory(voice.id)
                require(voice.id==dir.name&&voice.sampleRate==24000&&voice.transcript.isNotBlank()&&voice.name.isNotBlank())
                require(File(dir,"reference.pcm").length() in 9600..2_880_000)
                voice
            } catch(e:CancellationException) {throw e} catch(_:Exception) {null}
        }.sortedByDescending {it.createdAt}
    }}
    suspend fun create(name: String, source: File, consent: Boolean, asr: PhoneModel, cloneModel: PhoneModel, engine: LocalEngine,
        decoder: AudioDecoder = AndroidAudioDecoder(), progress: (Float)->Unit = {}): PhoneVoice = withContext(Dispatchers.IO) {
        require(consent&&asr.capability=="asr"&&cloneModel.supportsCloning&&name.trim().isNotEmpty()&&name.length<=200)
        val id=UUID.randomUUID().toString()
        val dir=directory(id).apply {mkdirs()}
        try {
            val audio=decoder.decode(source,File(dir,"reference.pcm"),30) {progress(it*.25f)}
            val transcript=engine.transcribe(asr,audio.read(0,audio.frames.toInt())) {progress(.25f+it*.25f)}.trim()
            if(transcript.isEmpty()) throw ClientFailure("mobile.simple.no_speech")
            writeReferenceWave(audio.read(0,audio.frames.toInt()),File(dir,"reference.wav"))
            val preview=engine.clone(cloneModel,transcript,audio.read(0,audio.frames.toInt()),transcript) {progress(.5f+it*.5f)}
            require(preview.sampleRate>0&&preview.samples.isNotEmpty()&&preview.samples.all {it.isFinite()})
            markSynthetic(preview,File(dir,"preview.wav"))
            currentCoroutineContext().ensureActive()
            val now=System.currentTimeMillis()
            val voice=PhoneVoice(id,name.trim(),transcript,now,now,modelRepo=cloneModel.repo,modelRevision=cloneModel.revision)
            mutex.withLock {atomicText(File(dir,"voice.json"),LocalJson.encodeToString(PhoneVoice.serializer(),voice))}
            progress(1f);voice
        } catch(e:Throwable) {dir.deleteRecursively();throw e}
    }
    suspend fun importCommunity(entry:GalleryEntry,source:File,consent:Boolean,decoder:AudioDecoder=AndroidAudioDecoder()):PhoneVoice=withContext(Dispatchers.IO) {
        require(consent&&entry.type=="voice"&&source.length()<=32*1024*1024)
        val audio=requireNotNull(entry.audio)
        require(java.security.MessageDigest.getInstance("SHA-256").digest(source.readBytes()).joinToString("") {"%02x".format(it)}==audio.sha256)
        val scratch=File.createTempFile("community-",".pcm",source.parentFile)
        try {
            val pcm=decoder.decode(source,scratch,30)
            installHandoff(entry.name,audio.ref_text,pcm.read(0,pcm.frames.toInt()),buildJsonObject {put("sourceAttribution",JsonObject(entry.attribution.mapValues {JsonPrimitive(it.value)}))},PhoneAudioProcessing.containsSyntheticMarker(source),consent)
        } finally {scratch.delete()}
    }
    suspend fun saveDesign(name: String,text: String,audio: LocalAudio,model: PhoneModel,instruction: String,seed: Long,attribution: Map<String,String>?=null): PhoneVoice = withContext(Dispatchers.IO) {mutex.withLock {
        require(name.isNotBlank()&&name.length<=200&&text.isNotBlank()&&audio.sampleRate==24000&&audio.samples.size in 2400..720000&&audio.samples.all {it.isFinite()}&&seed>=0)
        val id=UUID.randomUUID().toString();val dir=directory(id).apply {mkdirs()}
        try {
            writePcm(audio,File(dir,"reference.pcm"));markSynthetic(audio,File(dir,"reference.wav"))
            currentCoroutineContext().ensureActive()
            val now=System.currentTimeMillis()
            val voice=PhoneVoice(id,name.trim(),text,now,now,modelRepo=model.repo,modelRevision=model.revision,designInstruction=instruction,designSeed=seed,sourceAttribution=attribution)
            atomicText(File(dir,"voice.json"),LocalJson.encodeToString(PhoneVoice.serializer(),voice));voice
        } catch(e:Throwable) {dir.deleteRecursively();throw e}
    }}
    fun preview(voice: PhoneVoice): File = File(directory(voice.id),"preview.wav").takeIf {it.isFile} ?: File(directory(voice.id),"reference.wav")
    suspend fun reference(voice: PhoneVoice): LocalAudio = withContext(Dispatchers.IO) {mutex.withLock {
        val audio=PcmFile(File(directory(voice.id),"reference.pcm"),voice.sampleRate)
        require(audio.frames in 2400..720000);audio.read(0,audio.frames.toInt())
    }}
    suspend fun rename(voice: PhoneVoice, name: String) = withContext(Dispatchers.IO) {mutex.withLock {
        require(name.trim().isNotEmpty()&&name.length<=200)
        val file=File(directory(voice.id),"voice.json")
        val current=LocalJson.decodeFromString<PhoneVoice>(file.readText())
        atomicText(file,LocalJson.encodeToString(PhoneVoice.serializer(),current.copy(name=name.trim())))
    }}
    suspend fun delete(voice: PhoneVoice) = withContext(Dispatchers.IO) {mutex.withLock {
        if(!directory(voice.id).deleteRecursively()) throw ClientFailure("mobile.simple.clone_failed")
    }}
}
