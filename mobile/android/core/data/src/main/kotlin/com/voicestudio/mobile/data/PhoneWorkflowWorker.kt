package com.voicestudio.mobile.data

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.PowerManager
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import androidx.core.app.NotificationCompat
import androidx.work.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.channels.Channel
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID
import java.util.Locale

internal fun requirePhonePower(context: Context) {
    val power=context.getSystemService(PowerManager::class.java)
    val battery=context.registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val level=battery?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1) ?: -1
    val scale=battery?.getIntExtra(BatteryManager.EXTRA_SCALE,100) ?: 100
    val plugged=(battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED,0) ?: 0)!=0
    if(power.currentThermalStatus>=PowerManager.THERMAL_STATUS_CRITICAL||(!plugged&&level>=0&&level*100/scale.coerceAtLeast(1)<5)) throw ClientFailure("mobile.jobs.power_paused")
}
internal suspend fun phoneJobForeground(context: Context,id: UUID,titleKey: String,percent: Int): ForegroundInfo {
    val prefs=SessionStore(context).preferences.first()
    val l=L10n.load(context,prefs.locale.takeUnless {it=="system"} ?: Locale.getDefault().toLanguageTag())
    val channel="phone-processing"
    context.getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(channel,l.t("mobile.jobs.title"),NotificationManager.IMPORTANCE_LOW))
    val launch=context.packageManager.getLaunchIntentForPackage(context.packageName)
    val pending=launch?.let {PendingIntent.getActivity(context,0,it,PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)}
    val notification=NotificationCompat.Builder(context,channel).setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle(l.t(titleKey)).setContentText(l.t("mobile.jobs.checkpointed"))
        .setContentIntent(pending).setOngoing(true).setProgress(100,percent,false)
        .addAction(android.R.drawable.ic_menu_close_clear_cancel,l.t("common.cancel"),WorkManager.getInstance(context).createCancelPendingIntent(id)).build()
    return ForegroundInfo(id.hashCode(),notification,if(Build.VERSION.SDK_INT>=35) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
}
@Serializable private data class WorkflowJob(val workflow: PhoneWorkflow,val models: List<PhoneModel>,val voices: List<PhoneVoice>)
class PhoneWorkflowWorker(context: Context,params: WorkerParameters): CoroutineWorker(context,params) {
    override suspend fun getForegroundInfo()=phoneJobForeground(applicationContext,id,"workflows.title",0)
    override suspend fun doWork(): Result {
        val job=inputData.getString("job") ?: return Result.failure()
        try {
            require(UUID.fromString(job).toString()==job);setForeground(getForegroundInfo());requirePhonePower(applicationContext)
            val request=withContext(Dispatchers.IO) {Json.decodeFromString<WorkflowJob>(File(applicationContext.noBackupFilesDir,"workflow-jobs/$job.json").readText())}
            val runtime=PhoneRuntime.get(applicationContext)
            request.models.forEach {model->runtime.licence.requireAccepted(model);if(!runtime.downloads.installed(model)) throw ClientFailure("mobile.local.model_required")}
            fun model(capability: String)=request.models.firstOrNull {it.capability==capability}
            val workflow=request.workflow;val selection=workflow.voiceSelection ?: "preset:0"
            val store=PhoneWorkflowStore(File(applicationContext.noBackupFilesDir,"workflows"))
            val voiceStore=PhoneVoiceStore(File(applicationContext.noBackupFilesDir,"voices"))
            val configuration=request.models.joinToString("|") {modelPin(it).identity}+"|"+selection
            val finished=coroutineScope {
                val updates=Channel<Float>(Channel.CONFLATED)
                val reporter=launch {for(value in updates) {requirePhonePower(applicationContext);setProgress(workDataOf("percent" to (value*100).toInt()));setForeground(phoneJobForeground(applicationContext,id,"workflows.title",(value*100).toInt()))}}
                try {if(workflow.graph!=null) {
                    val executor=WorkflowGraphExecutor(runtime.engine,voiceStore,request.voices,model("tts"),model("clone"),model("asr"),model("translate"))
                    store.runGraph(workflow,workflow.inputText.orEmpty(),configuration,progress={updates.trySend(it)},execute=executor::execute)
                } else store.run(workflow,workflow.inputText.orEmpty(),configuration,progress={updates.trySend(it)}) {step,text ->
                    requirePhonePower(applicationContext)
                    when(step.kind) {
                        "transcribe"->WorkflowValue(WorkflowTranscription.run(store.source(workflow),requireNotNull(model("asr")),runtime.engine))
                        "translate"->WorkflowValue(runtime.engine.translate(requireNotNull(model("translate")),text,Locale.forLanguageTag(step.language).getDisplayLanguage(Locale.ENGLISH)))
                        "speak"->{val audio=if(selection.startsWith("phone:")) {val voice=request.voices.first {"phone:"+it.id==selection};runtime.engine.clone(requireNotNull(model("clone")),text,voiceStore.reference(voice),voice.transcript)} else runtime.engine.synthesize(requireNotNull(model("tts")),text,selection.removePrefix("preset:").toInt());WorkflowValue(text,audio)}
                        else->throw ClientFailure("mobile.errors.invalid_response")
                    }
                }} finally {updates.close();reporter.join()}
            }
            val result=File(applicationContext.noBackupFilesDir,"workflow-jobs/$job.result.txt")
            withContext(Dispatchers.IO) {result.writeText(finished.text)}
            val producer=if(workflow.graph!=null) null else model(if(selection.startsWith("phone:")) "clone" else "tts")
            return Result.success(workDataOf("file" to finished.audio?.absolutePath,"text_file" to result.absolutePath,"percent" to 100,"repo" to producer?.repo,"revision" to producer?.revision))
        } catch(e:CancellationException) {throw e} catch(e:Exception) {return Result.failure(workDataOf("error" to ((e as? ClientFailure)?.key ?: "mobile.errors.file")))}
    }
    companion object {
        suspend fun enqueue(context: Context,workflow: PhoneWorkflow,models: List<PhoneModel>,voices: List<PhoneVoice>): UUID=withContext(Dispatchers.IO) {
            val job=UUID.randomUUID();val directory=File(context.noBackupFilesDir,"workflow-jobs").apply {mkdirs()}
            File(directory,"$job.json").writeText(Json.encodeToString(WorkflowJob(workflow,models,voices)))
            val request=OneTimeWorkRequestBuilder<PhoneWorkflowWorker>().setInputData(workDataOf("job" to job.toString())).addTag("workflow:${workflow.id}").build()
            WorkManager.getInstance(context).enqueueUniqueWork("workflow:${workflow.id}",ExistingWorkPolicy.KEEP,request);request.id
        }
    }
}
