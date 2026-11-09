package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import java.io.File
import java.io.RandomAccessFile
import java.nio.file.Files
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.CRC32
import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

@Serializable data class HandoffAsset(val path: String,val size: Long,val sha256: String)
@Serializable data class HandoffVoice(val id: String,val metadata: JsonObject,val reference: String,val pcm: String?=null,val preview: String?=null)
@Serializable data class HandoffManifest(val format: String="voicestudio.project",val version: Int=1,val kind: String,val title: String,val document: JsonObject,val voices: List<HandoffVoice>,val assets: List<HandoffAsset>,val createdAt: Double=System.currentTimeMillis()/1000.0)
data class HandoffPreview(val title: String,val kind: String,val voices: Int,val bytes: Long)
data class HandoffRestored(val id: String,val kind: String,val title: String)
private val HandoffJson=Json {ignoreUnknownKeys=true;encodeDefaults=true;explicitNulls=false}

/** Versioned project transfer; imported object/voice IDs are new and licence receipts are never imported. */
object PhoneProjectHandoff {
    private const val PLATFORM="android"
    fun preview(source: File): HandoffPreview {val manifest=readManifest(source);return HandoffPreview(manifest.title,manifest.kind,maxOf(manifest.voices.size,if(manifest.kind=="dub") 1 else 0),manifest.assets.sumOf {it.size})}
    suspend fun exportProduction(project: ProductionProject,store: ProductionStore,voices: PhoneVoiceStore,configuration: String="",output: File): File=withContext(Dispatchers.IO) {
        val root=store.handoffDirectory(project.id);val document=project.handoff?.get("document")?.jsonObject.orEmpty().toMutableMap()
        document["script"]=JsonPrimitive(project.script);document["defaultVoice"]=JsonPrimitive(project.defaultVoice);document["cast"]=json(project.cast);document["pronunciations"]=json(project.pronunciations);document["speed"]=JsonPrimitive(project.speed)
        document["native"]=buildJsonObject {put("platform",PLATFORM);put("project",json(project.copy(handoff=null)));put("configuration",configuration)}
        val files=attachments(project.handoff,root).toMutableMap()
        regularFiles(root).filterKeys {it!="project.json"&&!it.startsWith("handoff-assets/")&&!it.endsWith(".pending")}.forEach {(name,file)->files["native/production/$name"]=file}
        val records=exportVoices((project.cast.values+project.defaultVoice).toSet(),voices,files)
        write(HandoffManifest(kind=project.kind,title=project.title,document=clean(JsonObject(document)).jsonObject,voices=records,assets=emptyList()),files,output)
    }
    suspend fun exportDub(id: String,store: PhoneDubStore,models: List<PhoneModel>,voices: PhoneVoiceStore,output: File): File=store.exclusive(id) {withContext(Dispatchers.IO) {
        val request=store.request(id);val checkpoint=store.checkpoint(id);val root=store.directory(id)
        val memory=File(root,"handoff.json").takeIf {it.isFile}?.let {HandoffJson.parseToJsonElement(it.readText()).jsonObject}
        val files=attachments(memory,root).toMutableMap()
        regularFiles(root).filterKeys {!it.startsWith("handoff-assets/")&&!it.endsWith(".pending")}.forEach {(name,file)->files["native/dub/$name"]=file}
        val document=memory?.get("document")?.jsonObject.orEmpty().toMutableMap()
        document["source"]=JsonPrimitive("native/dub/${request.sourceFile}");document["duration"]=JsonPrimitive(checkpoint.frames/24000.0)
        if(File(root,"original.pcm").isFile) document["original"]=JsonPrimitive("native/dub/original.pcm")
        document["targets"]=json(request.targets);document["audioOnly"]=JsonPrimitive(request.audioOnly);document["models"]=json(models.filter {model->request.models.any {it.id==model.id}})
        document["segments"]=JsonArray(checkpoint.segments.map {segment->
            val raw=json(segment).jsonObject.toMutableMap();raw.remove("speech");raw["start"]=JsonPrimitive(segment.speech.start);raw["end"]=JsonPrimitive(segment.speech.end);raw["text"]=JsonPrimitive(segment.speech.text)
            segment.referenceFile?.let {raw["reference"]=JsonPrimitive("native/dub/$it")} ?: request.referenceText?.let {raw["reference"]=JsonPrimitive("native/dub/voice-reference.pcm");raw["referenceText"]=JsonPrimitive(it)};JsonObject(raw)
        })
        document["native"]=buildJsonObject {put("platform",PLATFORM);put("request",json(request));put("checkpoint",json(checkpoint))}
        val available=voices.list().map {it.id}.toSet()
        document["segments"]=JsonArray(document.getValue("segments").jsonArray.map {raw->val value=raw.jsonObject.toMutableMap();value["voiceID"]?.jsonPrimitive?.contentOrNull?.takeIf {it !in available}?.let {value["sourceVoiceID"]=JsonPrimitive(it);value.remove("voiceID")};JsonObject(value)})
        val records=exportVoices(checkpoint.segments.mapNotNull {it.voiceID}.filter {it in available}.map {"phone:$it"}.toSet(),voices,files)
        write(HandoffManifest(kind="dub",title=request.title,document=clean(JsonObject(document)).jsonObject,voices=records,assets=emptyList()),files,output)
    }}
    suspend fun restore(source: File,production: ProductionStore,dubs: PhoneDubStore,voices: PhoneVoiceStore,models: List<PhoneModel>,configuration: String="",allowVoices: Boolean=false): HandoffRestored=withContext(Dispatchers.IO) {
        val manifest=readManifest(source)
        if((manifest.voices.isNotEmpty()||manifest.kind=="dub")&&!allowVoices) throw ClientFailure("mobile.handoff.voices_required")
        val staging=File(source.parentFile,".handoff-${UUID.randomUUID()}");val files=ProjectArchive.extract(source,staging)
        val imported=mutableListOf<PhoneVoice>();var published: File?=null
        try {
            require(files.keys==manifest.assets.map {it.path}.toSet()+"manifest.json")
            for(asset in manifest.assets) {val file=requireNotNull(files[asset.path]);require(file.length()==asset.size&&sha256(file)==asset.sha256)}
            for(asset in manifest.assets.filter {it.path.endsWith(".pcm")}) {
                val pcm=PcmFile(files.getValue(asset.path));require(pcm.file.length()%4==0L)
                var offset=0L;while(offset<pcm.frames) {currentCoroutineContext().ensureActive();val samples=pcm.read(offset,minOf(8192L,pcm.frames-offset).toInt()).samples;require(samples.all {it.isFinite()});offset+=samples.size}
            }
            val mapping=mutableMapOf<String,String>()
            for(voice in manifest.voices) {
                val reference=requireNotNull(files[voice.reference]);val pcm=voice.pcm?.let {files[it]}?.let {PcmFile(it)} ?: AndroidAudioDecoder().decode(reference,File(staging,"${UUID.randomUUID()}.pcm"),30) {}
                require(pcm.frames in 2400..720000L)
                val saved=voices.installHandoff(voice.metadata.string("profile_name") ?: manifest.title,voice.metadata.string("ref_text").orEmpty(),pcm.read(0,pcm.frames.toInt()),voice.metadata,voice.metadata.string("kind")=="design"||containsMarker(reference),allowVoices)
                imported+=saved;mapping[voice.id]="phone:${saved.id}";if(voice.id.startsWith("phone:")) mapping[voice.id.removePrefix("phone:")]="phone:${saved.id}"
                voice.preview?.let {name->files[name]?.takeIf {it.length()<=6_000_000}?.let {preview->copy(preview,File(voices.handoffFiles(saved).getValue("reference.wav").parentFile,"preview.wav"))}}
            }
            if(manifest.kind!="dub") {
                val selections=listOf(manifest.document.string("defaultVoice") ?: "preset:0")+manifest.document["cast"]?.jsonObject.orEmpty().values.map {it.jsonPrimitive.content}
                require(selections.all {it.isEmpty()||it.startsWith("preset:")||it in mapping})
            }
            val memory=manifest.copy(document=rewrite(manifest.document,mapping).jsonObject);val document=memory.document;val id=UUID.randomUUID().toString()
            if(manifest.kind!="dub") {
                val project=ProductionProject(id=id,kind=manifest.kind,title=manifest.title,script=document.string("script").orEmpty(),defaultVoice=document.string("defaultVoice") ?: "preset:0",cast=document["cast"]?.jsonObject.orEmpty().mapValues {it.value.jsonPrimitive.content},pronunciations=document["pronunciations"]?.jsonObject.orEmpty().mapValues {it.value.jsonPrimitive.content},speed=document["speed"]?.jsonPrimitive?.floatOrNull ?: 1f,handoff=json(memory).jsonObject)
                val root=production.handoffDirectory(id);require(!root.exists());check(root.mkdirs());published=root;copyAttachments(manifest,files,root)
                val native=manifest.document["native"]?.jsonObject
                if(native?.string("platform")==PLATFORM) {
                    for(asset in manifest.assets.filter {it.path.startsWith("native/production/")}) {val name=asset.path.removePrefix("native/production/");if(name!="project.json") copy(files.getValue(asset.path),File(root,name))}
                    if(native.string("configuration")==configuration) {
                        val old=native["project"]?.let {runCatching {HandoffJson.decodeFromJsonElement<ProductionProject>(it)}.getOrNull()}
                        if(old!=null) {
                            for(segment in runCatching {old.plan().segments}.getOrDefault(emptyList())) {
                                val oldVoice=old.voice(segment.role);val newVoice=mapping[oldVoice] ?: oldVoice
                                val before=segmentKey(configuration,oldVoice,segment);val after=segmentKey(configuration,newVoice,segment);val cache=File(root,"segments/$before.pcm")
                                if(before!=after&&cache.isFile) copy(cache,File(root,"segments/$after.pcm"))
                            }
                            val previous=File(root,production.handoffRenderDirectory(old,configuration).name);val next=production.handoffRenderDirectory(project,configuration)
                            val sameInputs=old.script==project.script&&old.speed==project.speed&&old.pronunciations==project.pronunciations&&(mapping[old.defaultVoice] ?: old.defaultVoice)==project.defaultVoice&&old.cast.mapValues {mapping[it.value] ?: it.value}==project.cast
                            if(sameInputs&&previous.isDirectory&&previous.name!=next.name) {check(!next.exists());previous.copyRecursively(next)}
                        }
                    }
                }
                currentCoroutineContext().ensureActive();production.save(project)
            } else {
                val root=dubs.directory(id);require(!root.exists());check(root.mkdirs());published=root;copyAttachments(manifest,files,root)
                val originalSource=files.getValue(requireNotNull(document.string("source")));val sourceFile="source.${originalSource.extension}";copy(originalSource,File(root,sourceFile))
                val pcm=document.string("original")?.let {files[it]}?.let {original->copy(original,File(root,"original.pcm"));PcmFile(File(root,"original.pcm"))} ?: AndroidAudioDecoder().decode(originalSource,File(root,"original.pcm"),14400) {}
                require(pcm.frames in 1..345600000L)
                val targets=HandoffJson.decodeFromJsonElement<List<DubTarget>>(document["targets"] ?: JsonArray(emptyList()));require(targets.isNotEmpty()&&targets.size<=20)
                val selected=selectModels(document["models"],models);if(selected.size!=3) throw ClientFailure("mobile.local.model_required")
                val now=System.currentTimeMillis();val request=PhoneDubRequest(id,manifest.title,sourceFile,targets,selected.map(::modelPin),now,now,audioOnly=document["audioOnly"]?.jsonPrimitive?.booleanOrNull ?: false,sourceHash=sha256(File(root,sourceFile)))
                val segments=mutableListOf<DubSegmentState>();val referenceNames=mutableMapOf<String,String>()
                for(element in document["segments"]?.jsonArray.orEmpty()) {
                    val raw=element.jsonObject;val start=raw["start"]?.jsonPrimitive?.doubleOrNull ?: error("Missing start");val end=raw["end"]?.jsonPrimitive?.doubleOrNull ?: error("Missing end");val text=raw.string("text").orEmpty()
                    require(start>=0&&end>start&&end<=pcm.frames/24000.0+.01&&text.isNotBlank())
                    var referenceFile: String?=null
                    raw.string("reference")?.let {path->files[path]?.let {reference->
                        if(referenceNames[path]!=null) {referenceFile=referenceNames[path]} else {
                        val ref=if(reference.extension=="pcm") PcmFile(reference) else AndroidAudioDecoder().decode(reference,File(staging,"${UUID.randomUUID()}.pcm"),30) {}
                        require(ref.frames in 2400..720000L);val name="edited-reference-${UUID.randomUUID()}.pcm";copy(ref.file,File(root,name));referenceFile=name;referenceNames[path]=name
                        }
                    }}
                    var segment=DubSegmentState(SpeechSegment(start,end,text),referenceText=raw.string("referenceText"),translations=raw["translations"]?.jsonObject.orEmpty().mapValues {it.value.jsonPrimitive.content},referenceFile=referenceFile,id=raw.string("id") ?: UUID.randomUUID().toString(),referenceNeedsTranscription=raw["referenceNeedsTranscription"]?.jsonPrimitive?.booleanOrNull ?: false,gain=raw["gain"]?.jsonPrimitive?.doubleOrNull ?: 1.0,muted=raw["muted"]?.jsonPrimitive?.booleanOrNull ?: false,speaker=raw.string("speaker"),referenceOrigin=raw.string("referenceOrigin"),voiceID=raw.string("voiceID")?.removePrefix("phone:")?.takeIf {selected->imported.any {it.id==selected}})
                    if(segment.referenceFile==null&&segment.voiceID!=null) imported.firstOrNull {it.id==segment.voiceID}?.let {voice->
                        val key="voice:${voice.id}";val name=referenceNames[key] ?: "edited-reference-${UUID.randomUUID()}.pcm".also {copy(voices.handoffFiles(voice).getValue("reference.pcm"),File(root,it));referenceNames[key]=it}
                        segment=segment.copy(referenceFile=name,referenceText=voice.transcript,referenceOrigin=segment.referenceOrigin ?: "cast")
                    }
                    segments+=segment
                }
                var checkpoint=PhoneDubCheckpoint(frames=pcm.frames,nextFrame=pcm.frames,segments=segments,originalHash=sha256(pcm.file),stage="mobile.android.stage_translate",percent=35)
                val native=manifest.document["native"]?.jsonObject
                if(native?.string("platform")==PLATFORM&&(native["request"]?.jsonObject?.get("audioOnly")?.jsonPrimitive?.booleanOrNull ?: false)==request.audioOnly&&native["request"]?.jsonObject?.get("targets")?.let {runCatching {HandoffJson.decodeFromJsonElement<List<DubTarget>>(it)}.getOrNull()}==request.targets) {
                    val oldPins=native["request"]?.jsonObject?.get("models")?.let {runCatching {HandoffJson.decodeFromJsonElement<List<DubModelPin>>(it)}.getOrNull()}
                    val old=native["checkpoint"]?.let {runCatching {HandoffJson.decodeFromJsonElement<PhoneDubCheckpoint>(it)}.getOrNull()}
                    val hashes=manifest.assets.associate {it.path to it.sha256}
                    val restoredReferenceHashes=segments.mapNotNull {it.referenceFile}.distinct().associateWith {sha256(dubs.file(id,it))}
                    val referencesMatch=old?.segments?.mapIndexed {index,segment->
                        val before=segment.referenceFile?.let {"native/dub/$it"} ?: native["request"]?.jsonObject?.string("referenceText")?.let {"native/dub/voice-reference.pcm"}
                        val after=segments.getOrNull(index)?.referenceFile
                        if(before==null&&after==null) true else before!=null&&after!=null&&hashes[before]!=null&&hashes[before]==restoredReferenceHashes[after]
                    }?.all {it}==true
                    if(referencesMatch&&native["request"]?.jsonObject?.string("sourceHash")==request.sourceHash&&old?.originalHash==checkpoint.originalHash&&old!=null&&oldPins?.toSet()==request.models.toSet()&&old.frames==pcm.frames&&old.segments.size==segments.size&&old.segments.zip(segments).all {(before,after)->before.speech==after.speech&&before.translations==after.translations&&(before.referenceText ?: native["request"]?.jsonObject?.string("referenceText"))==after.referenceText&&before.gain==after.gain&&before.muted==after.muted&&before.speaker==after.speaker&&before.referenceNeedsTranscription==after.referenceNeedsTranscription}) {
                        for(asset in manifest.assets.filter {it.path.startsWith("native/dub/")}) {val name=asset.path.removePrefix("native/dub/");if(name !in setOf("request.json","checkpoint.json","handoff.json")) {ProjectArchive.path(name);copy(files.getValue(asset.path),File(root,name))}}
                        // Rebind fresh reference names only after checking the original receipt and identical inputs.
                        val uniqueReceipts=old.renders.map {it.target to it.index}.distinct().size==old.renders.size
                        val completeCoverage=!old.done||(segments.isNotEmpty()&&old.renders.size==segments.size*request.targets.size&&old.outputs.size==request.targets.size)
                        val validOutputs=old.outputs.withIndex().all {(index,output)->
                            request.targets.getOrNull(index)?.code==output.language&&runCatching {sha256(dubs.file(id,output.file))==output.sha256}.getOrDefault(false)
                        }
                        val validReceipts=uniqueReceipts&&completeCoverage&&validOutputs&&old.renders.all {render->
                            val before=old.segments.getOrNull(render.index) ?: return@all false
                            val name=render.file ?: return@all false
                            val referenceName=before.referenceFile ?: native["request"]?.jsonObject?.string("referenceText")?.let {"voice-reference.pcm"}
                            val referenceHash=referenceName?.let {hashes["native/dub/$it"]}.orEmpty()
                            request.targets.any {it.code==render.target}&&render.fingerprint!=null&&render.segmentID==before.id&&render.scope==dubSegmentScope(before,render.target!!)&&
                                render.referenceHash==referenceHash&&render.originalHash==old.originalHash&&render.clonePin==request.models.firstOrNull {it.id==selected.first {model->model.capability=="clone"}.id}?.identity&&
                                render.frames==maxOf(1L,minOf(pcm.frames,(before.speech.end*24000).toLong())-(before.speech.start*24000).toLong())&&
                                runCatching {val file=dubs.file(id,name);PcmFile(file).frames==render.frames&&sha256(file)==render.sha256}.getOrDefault(false)
                        }
                        if(validReceipts) checkpoint=old.copy(segments=segments,renders=old.renders.map {render->render.copy(segmentID=segments[render.index].id,scope=dubSegmentScope(segments[render.index],render.target!!))})
                    }
                }
                dubs.save(id,checkpoint);currentCoroutineContext().ensureActive();atomicText(File(root,"request.json"),HandoffJson.encodeToString(request));atomicText(File(root,"handoff.json"),HandoffJson.encodeToString(memory));dubs.checkpoint(id)
            }
            HandoffRestored(id,manifest.kind,manifest.title)
        } catch(e:Throwable) {published?.deleteRecursively();withContext(NonCancellable) {imported.forEach {runCatching {voices.delete(it)}}};throw e}
        finally {staging.deleteRecursively()}
    }
    private fun readManifest(source: File): HandoffManifest {
        val entries=ProjectArchive.entries(source);val entry=requireNotNull(entries.firstOrNull {it.path=="manifest.json"});require(entry.size<=4_194_304)
        val bytes=RandomAccessFile(source,"r").use {file->file.seek(entry.offset);ByteArray(entry.size.toInt()).also {file.readFully(it)}};require(CRC32().apply {update(bytes)}.value==entry.crc)
        val raw=HandoffJson.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
        require(raw.string("format")=="voicestudio.project"&&raw["version"]?.jsonPrimitive?.intOrNull==1)
        var manifest=HandoffJson.decodeFromJsonElement<HandoffManifest>(raw)
        require(manifest.format=="voicestudio.project"&&manifest.version==1&&manifest.kind in setOf("stories","audiobook","dub")&&manifest.title.isNotBlank()&&manifest.title.length<=250&&manifest.voices.size<=100&&manifest.assets.size+1<=ProjectArchive.MAX_FILES)
        val names=hashSetOf<String>();var total=0L
        val allowed=setOf("json","pcm","wav","mp3","m4a","m4b","ogg","flac","aac","aiff","caf","mp4","mov","mkv","webm","media","jpg","jpeg","png","webp","srt","vtt","txt")
        for(asset in manifest.assets) {ProjectArchive.path(asset.path);require(listOf("assets/","voices/","native/").any {asset.path.startsWith(it)}&&File(asset.path).extension.lowercase() in allowed&&asset.size in 0..ProjectArchive.MAX_MEMBER&&asset.sha256.matches(Regex("[0-9a-f]{64}"))&&names.add(asset.path.lowercase()));total+=asset.size}
        require(total<=ProjectArchive.MAX_BYTES&&manifest.voices.map {it.id}.distinct().size==manifest.voices.size&&manifest.voices.all {voice->manifest.assets.any {it.path==voice.reference}}&&entries.size==manifest.assets.size+1)
        manifest=manifest.copy(document=clean(manifest.document).jsonObject)
        if(manifest.kind!="dub") {require(manifest.document.string("script")?.length?.let {it<=1_000_000}==true);require((manifest.document["speed"]?.jsonPrimitive?.doubleOrNull ?: 1.0) in 0.5..2.0)}
        return manifest
    }
    private suspend fun write(manifest: HandoffManifest,files: Map<String,File>,output: File): File {
        val value=manifest.copy(assets=files.toSortedMap().map {(name,file)->HandoffAsset(name,file.length(),sha256(file))})
        val text=HandoffJson.encodeToString(value);require(text.toByteArray().size<=4_194_304)
        val temp=File(output.parentFile,"manifest-${UUID.randomUUID()}.json");temp.writeText(text)
        return try {ProjectArchive.write(listOf(ProjectArchiveFile("manifest.json",temp))+files.map {(name,file)->ProjectArchiveFile(name,file)},output)} finally {temp.delete()}
    }
    private suspend fun exportVoices(selected: Set<String>,store: PhoneVoiceStore,files: MutableMap<String,File>): List<HandoffVoice> {
        val records=mutableListOf<HandoffVoice>()
        for(voice in store.list().filter {"phone:${it.id}" in selected||it.id in selected}) {
            val source=store.handoffFiles(voice);val prefix="voices/${voice.id}";files["$prefix/ref_audio.wav"]=source.getValue("reference.wav");files["$prefix/reference.pcm"]=source.getValue("reference.pcm")
            val metadata=buildJsonObject {put("bundle_version",1);put("profile_name",voice.name);put("ref_text",voice.transcript);put("kind",if(voice.designInstruction==null) "clone" else "design");put("instruct",voice.designInstruction.orEmpty());put("language","Auto");voice.sourceAttribution?.let {put("sourceAttribution",json(it))};voice.designSeed?.let {put("phone_design_seed",it.toString());put("seed",it)};voice.modelRepo?.let {put("modelRepo",it)};voice.modelRevision?.let {put("modelRevision",it)}}
            source["preview.wav"]?.let {files["$prefix/preview.wav"]=it}
            records+=HandoffVoice("phone:${voice.id}",metadata,"$prefix/ref_audio.wav","$prefix/reference.pcm",source["preview.wav"]?.let {"$prefix/preview.wav"})
        };require(selected.count {it.startsWith("phone:")}==records.size);return records
    }
    private fun attachments(memory: JsonObject?,root: File): Map<String,File> {
        if(memory==null) return emptyMap();val manifest=HandoffJson.decodeFromJsonElement<HandoffManifest>(memory)
        return manifest.assets.mapNotNull {asset->File(root,"handoff-assets/${asset.path}").takeIf {it.isFile}?.let {asset.path to it}}.toMap()
    }
    private fun copyAttachments(manifest: HandoffManifest,files: Map<String,File>,root: File) {manifest.assets.forEach {copy(files.getValue(it.path),File(root,"handoff-assets/${it.path}"))}}
    private fun copy(source: File,destination: File) {if(source.canonicalFile==destination.canonicalFile||destination.exists()) return;destination.parentFile!!.mkdirs();source.copyTo(destination)}
    private fun regularFiles(root: File): Map<String,File> {if(!root.exists()) return emptyMap();return root.walkTopDown().onEnter {require(!Files.isSymbolicLink(it.toPath()));it==root||it.name!="handoff-assets"}.filter {file->require(!Files.isSymbolicLink(file.toPath()));file.isFile}.associateBy {file->ProjectArchive.path(file.relativeTo(root).invariantSeparatorsPath)}}
    private fun selectModels(raw: JsonElement?,models: List<PhoneModel>): List<PhoneModel> {val specs=(raw as? JsonArray).orEmpty();return listOf("asr","translate","clone").mapNotNull {capability->val spec=specs.mapNotNull {it as? JsonObject}.firstOrNull {it.string("capability")==capability};models.firstOrNull {it.capability==capability&&(spec==null||(it.id==spec.string("id")&&it.repo==spec.string("repo")&&it.revision==spec.string("revision")))}}}
    private fun containsMarker(file: File): Boolean {val marker="VoiceStudio synthetic audio";var tail="";file.inputStream().use {input->val bytes=ByteArray(262144);while(true) {val n=input.read(bytes);if(n<0) return false;val block=tail+String(bytes,0,n,Charsets.ISO_8859_1);if(block.contains(marker)) return true;tail=block.takeLast(marker.length)}}}
    private fun segmentKey(configuration: String,voice: String,segment: ProductionSegment)=MessageDigest.getInstance("SHA-256").digest("v2|$configuration|$voice|${segment.speed}|${segment.pause}|${segment.text}".toByteArray()).joinToString("") {"%02x".format(it)}
    private fun rewrite(value: JsonElement,mapping: Map<String,String>): JsonElement=when(value) {
        is JsonObject->JsonObject(value.mapValues {(key,child)->when {
            key in setOf("defaultVoice","profileId","voice","voiceID","voice_id")&&child is JsonPrimitive&&child.isString->{val changed=mapping[child.content] ?: child.content;JsonPrimitive(if(key=="voiceID") changed.removePrefix("phone:") else changed)}
            key in setOf("cast","voiceCast")&&child is JsonObject->JsonObject(child.mapValues {(_,voice)->JsonPrimitive(mapping[(voice as? JsonPrimitive)?.content] ?: (voice as? JsonPrimitive)?.content.orEmpty())})
            else->rewrite(child,mapping)
        }})
        is JsonArray->JsonArray(value.map {rewrite(it,mapping)})
        else->value
    }
    private fun clean(value: JsonElement,depth: Int=0): JsonElement {require(depth<=64);val denied=setOf("credentials","authorization","cookies","cookie_file","api_key","apikey","access_token","refresh_token","sessiontoken","verified","verified_owner","ownershipverified","consentat","consent_at","licencereceipts","licensereceipts","verified_own_voice","verifiedownvoice","consent_text","consent_audio_path","consent_recorded_at","consentrecordedat","consenttext","consentaudiopath");return when(value) {
        is JsonObject->JsonObject(value.filterKeys {it.lowercase() !in denied}.mapValues {(key,child)->val text=(child as? JsonPrimitive)?.takeIf {it.isString}?.content;if(text!=null&&(key.contains("path",true)||key.endsWith("file",true))&&(text.startsWith('/')||text.startsWith('\\')||text.matches(Regex("^[A-Za-z]:.*")))) JsonNull else clean(child,depth+1)})
        is JsonArray->{require(value.size<=20000);JsonArray(value.map {clean(it,depth+1)})}
        else->value
    }}
    private inline fun <reified T> json(value: T): JsonElement=HandoffJson.encodeToJsonElement(value)
}
private fun JsonObject.string(key: String): String?=(this[key] as? JsonPrimitive)?.contentOrNull
