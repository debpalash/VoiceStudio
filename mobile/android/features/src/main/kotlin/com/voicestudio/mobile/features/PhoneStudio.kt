package com.voicestudio.mobile.features

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.data.ModelDownloadJobs
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
    val storage: Map<String,Long> = emptyMap(), val receipts: Map<String,List<LicenceReceipt>> = emptyMap(), val loading: Boolean = true, val progress: Float? = null, val interrupted: Boolean = false, val audio: File? = null, val voices: List<PhoneVoice> = emptyList(), val recent: List<PhoneGeneration> = emptyList(), val dubs: List<PhoneDubRecord> = emptyList()) {
    fun canRun(model: PhoneModel): Boolean = model.id in installed&&accepted(model)
    fun preferredModel(candidates: List<PhoneModel>, ramBytes: Long, preference: String? = null): PhoneModel? =
        candidates.firstOrNull {it.id==preference} ?: candidates.firstOrNull {canRun(it)&&it.fits(ramBytes)}
            ?: candidates.firstOrNull {canRun(it)} ?: candidates.firstOrNull {it.fits(ramBytes)} ?: candidates.firstOrNull()
    fun accepted(model: PhoneModel): Boolean = !model.gated || receipts[model.id]?.lastOrNull()?.let {it.accepted&&it.fingerprint==model.licence.termsFingerprint}==true
}
class PhoneStudio(context: Context, private val scope: CoroutineScope, private val failure: (Throwable)->Unit, private val retryFailure: (Throwable,()->Unit)->Unit = {error,_ ->failure(error)},
    engine: LocalEngine? = null, private val decoder: AudioDecoder = AndroidAudioDecoder()) {
    private val application=context.applicationContext
    private val runtime=PhoneRuntime.get(application)
    val ramBytes=runtime.ramBytes
    val licence=runtime.licence
    val downloads=runtime.downloads
    val engine=engine ?: runtime.engine
    val voices=PhoneVoiceStore(File(application.noBackupFilesDir,"voices"))
    val production=ProductionStore(File(application.noBackupFilesDir,"production"))
    val workflows=PhoneWorkflowStore(File(application.noBackupFilesDir,"workflows"))
    val transcripts=PhoneTranscriptStore(File(application.noBackupFilesDir,"transcripts"))
    val dubs=PhoneDubStore(File(application.noBackupFilesDir,"dubs"))
    private val mutable=MutableStateFlow(PhoneState())
    val state=mutable.asStateFlow()
    private var inference: Job?=null
    /** Only an interrupted clone gets the clone editor's Retry notice; other on-device work is just stopped. */
    private var inferenceIsClone=false
    /** Set once a clone's voice is saved: its cleanup still shows progress but can no longer be interrupted. */
    private var cloneSaved=false
    /** A cancelled job finishing late must not clear the progress of the job that replaced it. */
    private fun clearProgress(owner: Job?) {if(inference===owner||inference?.isActive!=true) mutable.update {it.copy(progress=null)}}
    init {scope.launch {
        try {
            val models=withContext(Dispatchers.IO) {application.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models}}
            mutable.update {it.copy(models=models,loading=false)}
            try {ModelDownloadJobs.restore(application,models,failure)}
            catch(e:CancellationException) {throw e}
            catch(e:Exception) {failure(e as? ClientFailure ?: ClientFailure("mobile.local.download_failed"))}
            refresh()
            scope.launch {downloads.progress.map {it.mapValues {entry->entry.value.running to entry.value.stage}}.distinctUntilChanged().collect {refresh()}}
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
        if(downloads.progress.value[model.id]?.running==true) return
        try {ModelDownloadJobs.enqueue(application,model,allowMobile)} catch(e:Exception) {retryFailure(e) {download(model,allowMobile)}}
    }
    fun pauseDownload(model: PhoneModel) {try {ModelDownloadJobs.pause(application,model.id)} catch(e:Exception) {failure(e)}}
    fun cancelDownload(model: PhoneModel) {try {ModelDownloadJobs.cancel(application,model.id)} catch(e:Exception) {failure(e)}}
    fun delete(model: PhoneModel) {scope.launch {
        try {ModelDownloadJobs.stop(application,model.id);cancelInference();downloads.delete(model);refresh()}
        catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}
    }}
    fun accept(model: PhoneModel, accepted: Boolean, fingerprint: String, completed: (Boolean)->Unit = {}) {scope.launch {
        try {
            if(!accepted) {ModelDownloadJobs.stop(application,model.id);cancelInference()}
            withContext(Dispatchers.IO) {licence.record(model,accepted,fingerprint)};refresh();completed(true)
        } catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e);completed(false)}
    }}
    fun saveTranscript(text: String,modelID: String?=null) {
        if(text.isBlank()) return
        scope.launch {try {transcripts.saveDictation(java.util.UUID.randomUUID().toString(),text.lineSequence().first().take(120),text,modelID)} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}
    }
    fun speak(model: PhoneModel, text: String, speaker: Int, voice: PhoneVoice? = null, language: String? = null) {
        if(inference?.isActive==true) {failure(ClientFailure("mobile.android.operation_busy"));return}
        inferenceIsClone=false
        inference=scope.launch {
            mutable.update {it.copy(progress=0f,interrupted=false)}
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
            catch(e:Throwable) {retryFailure(ClientFailure(if(e is LinkageError) "mobile.local.runtime_failed" else "mobile.local.inference_failed")) {speak(model,text,speaker,voice,language)}}
            finally {clearProgress(coroutineContext[Job])}
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
        if(inference?.isActive==true) {failure(ClientFailure("mobile.android.operation_busy"));return}
        inferenceIsClone=true;cloneSaved=false
        inference=scope.launch {
            val job=coroutineContext[Job]
            mutable.update {it.copy(progress=0f,interrupted=false)}
            try {
                voices.create(name,file,consent,model,cloneModel,engine,decoder,progress={p -> mutable.update {it.copy(progress=p)}})
                // The voice is saved: a cancel or an interruption from here on must not turn it into a failed or interrupted
                // clone (Retry would make a duplicate). Progress stays up so the editor remains busy until cleanup ends.
                cloneSaved=true
                withContext(NonCancellable) {
                    try {refresh()} catch(e:Exception) {failure(e)}
                    withContext(Dispatchers.IO) {file.delete()}
                    mutable.update {it.copy(progress=null)}
                    // Saving and cleanup finish even after the screen is gone, but a screen that already cancelled must not navigate.
                    if(job?.isActive==true) created()
                }
            }
            catch(e:CancellationException) {throw e} catch(e:Exception) {retryFailure(e) {clone(name,file,model,cloneModel,consent,created)}}
            catch(e:Throwable) {retryFailure(ClientFailure(if(e is LinkageError) "mobile.local.runtime_failed" else "mobile.local.inference_failed")) {clone(name,file,model,cloneModel,consent,created)}}
            finally {clearProgress(coroutineContext[Job])}
        }
    }
    fun renameVoice(voice: PhoneVoice, name: String) {scope.launch {try {voices.rename(voice,name);refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    fun deleteVoice(voice: PhoneVoice) {scope.launch {try {voices.delete(voice);refresh()} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    fun refreshDubs() {scope.launch {
        try {val records=dubs.list();mutable.update {it.copy(dubs=records)}}
        catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}
    }}
    fun saveProductionDraft(project: ProductionProject) {scope.launch {try {production.save(project)} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}}}
    /** A user cancel: stops on-device work and clears any interrupted notice. */
    fun cancelInference() {inference?.cancel();mutable.update {it.copy(interrupted=false)}}
    /** Leaving the foreground or tearing the screen down stops on-device work; unlike a user cancel it never clears the
     *  interrupted notice, so the task can be retried after the screen returns or is recreated. */
    fun interruptInference() {
        if(mutable.value.progress==null||(inferenceIsClone&&cloneSaved)) return
        val job=inference?.takeIf {it.isActive} ?: return
        if(inferenceIsClone) mutable.update {it.copy(interrupted=true)}
        job.cancel()
    }
    fun dismissInterrupted() {mutable.update {it.copy(interrupted=false)}}
}

/** The model that made the take is kept so it can be reported; older takes have none. */
@kotlinx.serialization.Serializable data class PhoneGeneration(val id: String,val text: String,val createdAt: Long,val modelRepo: String? = null,val modelRevision: String? = null)
