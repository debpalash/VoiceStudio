package com.voicestudio.mobile.features

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.io.File

/** RAM guides model choice; explicit choices stay available on smaller devices. */
@androidx.compose.runtime.Composable
fun phoneModelChoice(model: PhoneModel, ramBytes: Long, supporting: String? = null) = Choice(
    model.id,model.name,supporting=if(model.fits(ramBytes)) supporting
    else tr("mobile.simple.ram",mapOf("ram" to model.minRamGb))
)

data class PhoneState(val models: List<PhoneModel> = emptyList(), val installed: Set<String> = emptySet(), val used: Long = 0,
    val storage: Map<String,Long> = emptyMap(), val receipts: Map<String,List<LicenceReceipt>> = emptyMap(), val loading: Boolean = true, val progress: Float? = null, val audio: File? = null, val voices: List<PhoneVoice> = emptyList(), val recent: List<PhoneGeneration> = emptyList(), val dubs: List<PhoneDubRecord> = emptyList()) {
    fun canRun(model: PhoneModel): Boolean = model.id in installed&&accepted(model)
    fun preferredModel(candidates: List<PhoneModel>, ramBytes: Long, preference: String? = null): PhoneModel? =
        candidates.firstOrNull {it.id==preference} ?: candidates.firstOrNull {canRun(it)&&it.fits(ramBytes)}
            ?: candidates.firstOrNull {canRun(it)} ?: candidates.firstOrNull {it.fits(ramBytes)} ?: candidates.firstOrNull()
    fun accepted(model: PhoneModel): Boolean = !model.gated || receipts[model.id]?.lastOrNull()?.let {it.accepted&&it.fingerprint==model.licence.termsFingerprint}==true
}
class PhoneStudio(context: Context, private val scope: CoroutineScope, private val failure: (Throwable)->Unit, private val retryFailure: (Throwable,()->Unit)->Unit = {error,_ ->failure(error)}) {
    private val application=context.applicationContext
    private val runtime=PhoneRuntime.get(application)
    val ramBytes=runtime.ramBytes
    val licence=runtime.licence
    val downloads=runtime.downloads
    val engine=runtime.engine
    val voices=PhoneVoiceStore(File(application.noBackupFilesDir,"voices"))
    val production=ProductionStore(File(application.noBackupFilesDir,"production"))
    val workflows=PhoneWorkflowStore(File(application.noBackupFilesDir,"workflows"))
    val transcripts=PhoneTranscriptStore(File(application.noBackupFilesDir,"transcripts"))
    val dubs=PhoneDubStore(File(application.noBackupFilesDir,"dubs"))
    private val mutable=MutableStateFlow(PhoneState())
    val state=mutable.asStateFlow()
    private val jobs=mutableMapOf<String,Job>()
    private var inference: Job?=null
    init {scope.launch {
        try {
            val models=withContext(Dispatchers.IO) {application.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models}}
            mutable.update {it.copy(models=models,loading=false)};refresh()
        } catch(e:CancellationException) {throw e} catch(e:Exception) {mutable.update {it.copy(loading=false)};failure(ClientFailure("mobile.local.catalogue_failed"))}
    }}
    suspend fun refresh() = withContext(Dispatchers.IO) {
        val installed=mutable.value.models.filter(downloads::installed).map {it.id}.toSet()
        val used=downloads.usedBytes()
        val storage=mutable.value.models.associate {it.id to downloads.usedBytes(it)}
        val receipts=mutable.value.models.associate {it.id to licence.history(it)}
        mutable.update {it.copy(installed=installed,used=used,storage=storage,receipts=receipts)}
        val phoneVoices=voices.list()
        val recent=history()
        val phoneDubs=dubs.list()
        mutable.update {it.copy(voices=phoneVoices,recent=recent,dubs=phoneDubs)}
    }
    fun download(model: PhoneModel, allowMobile: Boolean) {
        if(jobs[model.id]?.isActive==true) return
        jobs[model.id]=scope.launch {
            try {downloads.download(model,allowMobile)}
            catch(e:CancellationException) {throw e}
            catch(e:Exception) {retryFailure(e) {download(model,allowMobile)}}
            finally {withContext(NonCancellable) {refresh()}}
        }
    }
    fun cancelDownload(model: PhoneModel) {jobs[model.id]?.cancel()}
    fun delete(model: PhoneModel) {scope.launch {
        try {jobs[model.id]?.cancelAndJoin();cancelInference();downloads.delete(model);refresh()}
        catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}
    }}
    fun accept(model: PhoneModel, accepted: Boolean, fingerprint: String, completed: (Boolean)->Unit = {}) {scope.launch {
        try {
            if(!accepted) {jobs[model.id]?.cancelAndJoin();cancelInference()}
            withContext(Dispatchers.IO) {licence.record(model,accepted,fingerprint)};refresh();completed(true)
        } catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e);completed(false)}
    }}
    fun saveTranscript(text: String,modelID: String?=null) {
        if(text.isBlank()) return
        scope.launch {try {transcripts.saveDictation(java.util.UUID.randomUUID().toString(),text.lineSequence().first().take(120),text,modelID)} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}
    }
    fun speak(model: PhoneModel, text: String, speaker: Int, voice: PhoneVoice? = null, language: String? = null) {
        if(inference?.isActive==true) return
        inference=scope.launch {
            mutable.update {it.copy(progress=0f)}
            try {
                val report: (Float)->Unit={value -> mutable.update {it.copy(progress=value)}}
                val audio=if(voice==null) engine.synthesize(model,text,speaker,language=language,progress=report)
                    else engine.clone(model,text,voices.reference(voice),voice.transcript,progress=report)
                val file=withContext(Dispatchers.IO) {
                    val id=java.util.UUID.randomUUID().toString()
                    val output=File(application.noBackupFilesDir,"speech/$id.wav")
                    markSynthetic(audio,output)
                    val generation=PhoneGeneration(id,text,System.currentTimeMillis(),model.repo,model.revision)
                    File(output.parentFile,"$id.json").writeText(kotlinx.serialization.json.Json.encodeToString(PhoneGeneration.serializer(),generation))
                    output
                }
                val recent=withContext(Dispatchers.IO) {history()}
                mutable.update {it.copy(audio=file,recent=recent)}
            } catch(e:CancellationException) {throw e} catch(e:Exception) {retryFailure(e) {speak(model,text,speaker,voice,language)}}
            finally {mutable.update {it.copy(progress=null)}}
        }
    }
    private fun history(): List<PhoneGeneration> = File(application.noBackupFilesDir,"speech").listFiles().orEmpty().filter {it.extension=="json"}.mapNotNull {
        runCatching {kotlinx.serialization.json.Json.decodeFromString(PhoneGeneration.serializer(),it.readText())}.getOrNull()
    }.sortedByDescending {it.createdAt}
    fun generationFile(item: PhoneGeneration)=File(application.noBackupFilesDir,"speech/${item.id}.wav")
    fun play(item: PhoneGeneration) {mutable.update {it.copy(audio=generationFile(item))}}
    fun deleteGeneration(item: PhoneGeneration) {scope.launch(Dispatchers.IO) {
        generationFile(item).delete();File(application.noBackupFilesDir,"speech/${item.id}.json").delete()
        mutable.update {it.copy(recent=history(),audio=it.audio?.takeUnless {file -> file==generationFile(item)})}
    }}
    fun clone(name: String, file: File, model: PhoneModel, cloneModel: PhoneModel, consent: Boolean, created: ()->Unit = {}) {
        if(inference?.isActive==true) return
        inference=scope.launch {
            mutable.update {it.copy(progress=0f)}
            try {voices.create(name,file,consent,model,cloneModel,engine,progress={p -> mutable.update {it.copy(progress=p)}});refresh();withContext(Dispatchers.IO) {file.delete()};created()}
            catch(e:CancellationException) {throw e} catch(e:Exception) {retryFailure(e) {clone(name,file,model,cloneModel,consent,created)}}
            finally {mutable.update {it.copy(progress=null)}}
        }
    }
    fun renameVoice(voice: PhoneVoice, name: String) {scope.launch {try {voices.rename(voice,name);refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    fun deleteVoice(voice: PhoneVoice) {scope.launch {try {voices.delete(voice);refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    fun refreshDubs() {scope.launch {
        try {val records=dubs.list();mutable.update {it.copy(dubs=records)}}
        catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}
    }}
    fun saveProductionDraft(project: ProductionProject) {scope.launch {try {production.save(project)} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    fun cancelInference() {inference?.cancel()}
}

/** The model that made the take is kept so it can be reported; older takes have none. */
@kotlinx.serialization.Serializable data class PhoneGeneration(val id: String,val text: String,val createdAt: Long,val modelRepo: String? = null,val modelRevision: String? = null)
