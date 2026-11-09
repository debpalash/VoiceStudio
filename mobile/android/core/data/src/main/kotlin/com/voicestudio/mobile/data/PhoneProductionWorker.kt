package com.voicestudio.mobile.data

import android.content.Context
import androidx.work.*
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

@Serializable private data class ProductionJob(val project: ProductionProject,val tts: PhoneModel?,val clone: PhoneModel?)
class PhoneProductionWorker(context: Context,params: WorkerParameters): CoroutineWorker(context,params) {
    override suspend fun getForegroundInfo()=phoneJobForeground(applicationContext,id,"mobile.jobs.title",0)
    override suspend fun doWork(): Result {
        try {
            val job=requireNotNull(inputData.getString("job"));require(UUID.fromString(job).toString()==job)
            setForeground(getForegroundInfo());requirePhonePower(applicationContext)
            val request=withContext(Dispatchers.IO) {Json.decodeFromString<ProductionJob>(File(applicationContext.noBackupFilesDir,"production-jobs/$job.json").readText())}
            val runtime=PhoneRuntime.get(applicationContext)
            val selections=request.project.plan().segments.filter {it.text.isNotEmpty()}.map {request.project.voice(it.role)}
            listOfNotNull(request.tts.takeIf {selections.any {it.startsWith("preset:")}},request.clone.takeIf {selections.any {it.startsWith("phone:")}}).forEach {model->runtime.licence.requireAccepted(model);if(!runtime.downloads.installed(model)) throw ClientFailure("mobile.local.model_required")}
            val voices=PhoneVoiceStore(File(applicationContext.noBackupFilesDir,"voices"))
            val renderer=ProductionVoiceRenderer(runtime.engine,voices,voices.list(),request.tts,request.clone)
            val store=ProductionStore(File(applicationContext.noBackupFilesDir,"production"))
            val file=coroutineScope {
                val updates=Channel<Float>(Channel.CONFLATED)
                val reporter=launch {for(value in updates) {requirePhonePower(applicationContext);setProgress(workDataOf("percent" to (value*100).toInt()));setForeground(phoneJobForeground(applicationContext,id,"mobile.jobs.title",(value*100).toInt()))}}
                try {store.render(request.project,renderer.configuration,progress={updates.trySend(it)}) {segment,selection ->requirePhonePower(applicationContext);renderer.render(segment,selection)}} finally {updates.close();reporter.join()}
            }
            return Result.success(workDataOf("file" to file.absolutePath,"percent" to 100))
        } catch(e:CancellationException) {throw e} catch(e:Exception) {return Result.failure(workDataOf("error" to ((e as? ClientFailure)?.key ?: "mobile.errors.file")))}
    }
    companion object {
        suspend fun enqueue(context: Context,project: ProductionProject,tts: PhoneModel?,clone: PhoneModel?): UUID=withContext(Dispatchers.IO) {
            val job=UUID.randomUUID();val directory=File(context.noBackupFilesDir,"production-jobs").apply {mkdirs()}
            File(directory,"$job.json").writeText(Json.encodeToString(ProductionJob(project,tts,clone)))
            val request=OneTimeWorkRequestBuilder<PhoneProductionWorker>().setInputData(workDataOf("job" to job.toString())).addTag("production:${project.id}").build()
            WorkManager.getInstance(context).enqueueUniqueWork("production:${project.id}",ExistingWorkPolicy.KEEP,request);request.id
        }
    }
}
