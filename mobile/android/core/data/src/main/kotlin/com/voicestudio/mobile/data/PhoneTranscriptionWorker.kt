package com.voicestudio.mobile.data

import android.content.Context
import androidx.work.*
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import java.io.File
import java.util.UUID

class PhoneTranscriptionWorker(context: Context,params: WorkerParameters): CoroutineWorker(context,params) {
    override suspend fun getForegroundInfo()=phoneJobForeground(applicationContext,id,"transcriptions.title",0)
    override suspend fun doWork(): Result {
        try {
            val recordID=requireNotNull(inputData.getString("record"));require(UUID.fromString(recordID).toString()==recordID)
            setForeground(getForegroundInfo());requirePhonePower(applicationContext)
            val store=PhoneTranscriptStore(File(applicationContext.noBackupFilesDir,"transcripts"));val record=store.record(recordID)
            val models=applicationContext.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models}
            val model=models.firstOrNull {it.id==record.modelID} ?: throw ClientFailure("mobile.local.model_required")
            val runtime=PhoneRuntime.get(applicationContext)
            runtime.licence.requireAccepted(model)
            if(!runtime.downloads.installed(model)) throw ClientFailure("mobile.local.model_required")
            coroutineScope {
                val updates=Channel<Float>(Channel.CONFLATED)
                val reporter=launch {for(value in updates) {requirePhonePower(applicationContext);setProgress(workDataOf("percent" to (value*100).toInt()));setForeground(phoneJobForeground(applicationContext,id,"transcriptions.title",(value*100).toInt()))}}
                try {store.run(recordID,model,progress={updates.trySend(it)}) {audio ->requirePhonePower(applicationContext);runtime.engine.transcribeSegments(model,audio)}} finally {updates.close();reporter.join()}
            }
            return Result.success(workDataOf("percent" to 100))
        } catch(e:CancellationException) {throw e} catch(e:Exception) {return Result.failure(workDataOf("error" to ((e as? ClientFailure)?.key ?: "transcriptions.failed")))}
    }
    companion object {
        fun enqueue(context: Context,record: PhoneTranscript) {
            require(record.kind=="file"&&!record.complete&&record.valid)
            val request=OneTimeWorkRequestBuilder<PhoneTranscriptionWorker>().setInputData(workDataOf("record" to record.id)).addTag("transcriptions").addTag("transcript:${record.id}").build()
            WorkManager.getInstance(context).enqueueUniqueWork("transcript:${record.id}",ExistingWorkPolicy.KEEP,request)
        }
    }
}
