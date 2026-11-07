package com.voicestudio.mobile.data

import android.app.*
import android.app.job.*
import android.content.*
import android.content.pm.ServiceInfo
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.PersistableBundle
import androidx.core.app.NotificationCompat
import androidx.work.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/** The UI submits durable intents; services/workers, never a ViewModel, own transfers. */
object ModelDownloadJobs {
    @Serializable internal data class IntentRecord(val model: String,val pin: String,val mobile: Boolean,val jobID: Int,val paused: Boolean=false,val error: String?=null)
    private val active=ConcurrentHashMap<String,Job>()
    private fun preferences(context: Context)=context.getSharedPreferences("model-downloads",Context.MODE_PRIVATE)
    @Synchronized private fun read(context: Context): List<IntentRecord> = runCatching {
        Json.decodeFromString<List<IntentRecord>>(preferences(context).getString("queue","[]")!!)
    }.getOrDefault(emptyList())
    @Synchronized private fun write(context: Context,items: List<IntentRecord>) {
        if(!preferences(context).edit().putString("queue",Json.encodeToString(items)).commit()) throw ClientFailure("mobile.simple.checkpoint_failed")
    }
    @Synchronized fun enqueue(context: Context,model: PhoneModel,mobile: Boolean) {
        PhoneRuntime.get(context).licence.requireAccepted(model)
        val saved=read(context);val old=saved.firstOrNull {it.model==model.id}
        val item=IntentRecord(model.id,modelPin(model).identity,mobile,old?.jobID ?: ((saved.maxOfOrNull {it.jobID} ?: 10000)+1))
        write(context,saved.filterNot {it.model==model.id}+item)
        schedule(context,item,model.totalBytes)
    }
    private fun schedule(context: Context,item: IntentRecord,total: Long) {
        PhoneRuntime.get(context).downloads.scheduled(item.model,total,if(item.mobile) "waiting_network" else "waiting")
        if(Build.VERSION.SDK_INT>=34) {
            val extras=PersistableBundle().apply {putString("model",item.model)}
            val network=NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).apply {
                if(!item.mobile) {addTransportType(NetworkCapabilities.TRANSPORT_WIFI);addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)}
            }.build()
            val info=JobInfo.Builder(item.jobID,ComponentName(context,ModelDownloadService::class.java))
                .setUserInitiated(true).setRequiredNetwork(network).setEstimatedNetworkBytes(total,0).setExtras(extras).build()
            if(context.getSystemService(JobScheduler::class.java).schedule(info)!=JobScheduler.RESULT_SUCCESS) {
                pause(context,item.model,"mobile.local.download_failed")
                throw ClientFailure("mobile.local.download_failed")
            }
        } else {
            val request=OneTimeWorkRequestBuilder<ModelDownloadWorker>().setInputData(workDataOf("model" to item.model))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(if(item.mobile) NetworkType.CONNECTED else NetworkType.UNMETERED).build()).build()
            WorkManager.getInstance(context).enqueueUniqueWork("model:${item.model}",ExistingWorkPolicy.KEEP,request)
        }
    }
    /** Called only when the phone UI opens. Existing OS jobs retain ownership. */
    fun restore(context: Context,models: List<PhoneModel>,onFailure: (Throwable)->Unit = {}) {
        val scheduler=context.getSystemService(JobScheduler::class.java)
        for(item in read(context)) {
            try {
                val model=models.firstOrNull {it.id==item.model&&modelPin(it).identity==item.pin}
                if(model==null) {cancel(context,item.model);continue}
                val downloads=PhoneRuntime.get(context).downloads
                if(downloads.progress.value[item.model]?.error=="mobile.simple.checkpoint_failed") {
                    pauseInBackground(context,item.model,"mobile.simple.checkpoint_failed");continue
                }
                if(item.paused) {downloads.paused(item.model,model.totalBytes,item.error);continue}
                if(PhoneRuntime.get(context).downloads.installed(model)) {finished(context,item.model);continue}
                if(Build.VERSION.SDK_INT<34||scheduler.getPendingJob(item.jobID)==null) schedule(context,item,model.totalBytes)
                else PhoneRuntime.get(context).downloads.scheduled(item.model,model.totalBytes,if(item.mobile) "waiting_network" else "waiting")
            } catch(e:CancellationException) {throw e}
            catch(e:Exception) {onFailure(e as? ClientFailure ?: ClientFailure("mobile.local.download_failed"))}
        }
    }
    @Synchronized fun pause(context: Context,id: String,error: String?=null) {
        val saved=read(context);val item=saved.firstOrNull {it.model==id}
        try {
            write(context,saved.map {if(it.model==id) it.copy(paused=true,error=error) else it})
        } finally {
            if(item!=null&&Build.VERSION.SDK_INT>=34) context.getSystemService(JobScheduler::class.java).cancel(item.jobID)
            WorkManager.getInstance(context).cancelUniqueWork("model:$id")
            active[id]?.cancel()
            PhoneRuntime.get(context).downloads.paused(id,error=error)
        }
    }
    @Synchronized internal fun pauseInBackground(context: Context,id: String,error: String?=null) {
        try {pause(context,id,error)}
        catch(e:ClientFailure) {
            // A failed commit may still update SharedPreferences' in-memory copy.
            // Retry with the failure reason so restoration cannot silently clear it.
            try {pause(context,id,e.key)} catch(_:ClientFailure) {}
            PhoneRuntime.get(context).downloads.paused(id,error=e.key)
        }
    }
    fun cancel(context: Context,id: String) {try {pause(context,id)} finally {finished(context,id)}}
    suspend fun stop(context: Context,id: String) {cancel(context,id);active[id]?.join()}
    @Synchronized internal fun finished(context: Context,id: String) {write(context,read(context).filterNot {it.model==id})}
    internal fun waiting(context: Context,id: String) {
        val item=read(context).firstOrNull {it.model==id&&!it.paused} ?: return
        val downloads=PhoneRuntime.get(context).downloads
        downloads.scheduled(id,downloads.progress.value[id]?.total ?: 0,if(item.mobile) "waiting_network" else "waiting")
    }
    internal suspend fun run(context: Context,id: String) {
        val job=currentCoroutineContext().job
        active.putIfAbsent(id,job)?.let {previous -> previous.join();run(context,id);return}
        try {
            val item=read(context).firstOrNull {it.model==id&&!it.paused} ?: return
            val model=withContext(Dispatchers.IO) {context.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models.firstOrNull {m->m.id==id&&modelPin(m).identity==item.pin}}}
                ?: throw ClientFailure("mobile.local.catalogue_failed")
            currentCoroutineContext().ensureActive()
            PhoneRuntime.get(context).downloads.download(model,item.mobile);finished(context,id)
        } finally {active.remove(id,job)}
    }
    internal suspend fun notification(context: Context,id: String): Notification {
        val prefs=SessionStore(context).preferences.first()
        val l=L10n.load(context,prefs.locale.takeUnless {it=="system"} ?: Locale.getDefault().toLanguageTag())
        val channel="model-downloads"
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(channel,l.t("mobile.local.models"),NotificationManager.IMPORTANCE_LOW))
        val progress=PhoneRuntime.get(context).downloads.progress.value[id]
        val stop=PendingIntent.getBroadcast(context,id.hashCode(),Intent(context,ModelDownloadPauseReceiver::class.java).putExtra("model",id),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val launch=context.packageManager.getLaunchIntentForPackage(context.packageName)?.let {PendingIntent.getActivity(context,0,it,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)}
        return NotificationCompat.Builder(context,channel).setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(l.t("mobile.local.download")).setContentText(progress?.let {modelDownloadDescription(context,l,it)} ?: l.t("mobile.downloads.connecting"))
            .setContentIntent(launch).setOngoing(true).setOnlyAlertOnce(true)
            .setProgress(100,if(progress!=null&&progress.total>0) (progress.received*100/progress.total).toInt() else 0,progress==null||progress.total==0L)
            .addAction(android.R.drawable.ic_media_pause,l.t("mobile.downloads.pause"),stop).build()
    }
}

class ModelDownloadPauseReceiver: BroadcastReceiver() {
    override fun onReceive(context: Context,intent: Intent) {intent.getStringExtra("model")?.let {ModelDownloadJobs.pauseInBackground(context,it)}}
}

@android.annotation.TargetApi(34)
class ModelDownloadService: JobService() {
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
    private val jobs=mutableMapOf<Int,Job>()
    override fun onStartJob(params: JobParameters): Boolean {
        val id=params.extras.getString("model") ?: return false
        jobs[params.jobId]=scope.launch {
            val updates=launch {
                while(isActive) {
                    setNotification(params,params.jobId,ModelDownloadJobs.notification(this@ModelDownloadService,id),JOB_END_NOTIFICATION_POLICY_REMOVE)
                    delay(1000)
                }
            }
            try {ModelDownloadJobs.run(this@ModelDownloadService,id);jobFinished(params,false)}
            catch(e:CancellationException) {ModelDownloadJobs.waiting(this@ModelDownloadService,id);throw e}
            catch(e:Exception) {
                if(e is ClientFailure&&e.key=="mobile.local.wifi_required") {
                    PhoneRuntime.get(this@ModelDownloadService).downloads.scheduled(id,PhoneRuntime.get(this@ModelDownloadService).downloads.progress.value[id]?.total ?: 0)
                    jobFinished(params,true)
                } else {ModelDownloadJobs.pauseInBackground(this@ModelDownloadService,id,(e as? ClientFailure)?.key ?: "mobile.local.download_failed");jobFinished(params,false)}
            }
            finally {updates.cancel();jobs.remove(params.jobId)}
        }
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean {
        params.extras.getString("model")?.let {getSharedPreferences("model-downloads",Context.MODE_PRIVATE).edit().putInt("stop:$it",params.stopReason).commit()}
        jobs.remove(params.jobId)?.cancel();return true
    }
    override fun onDestroy() {scope.cancel();super.onDestroy()}
}

class ModelDownloadWorker(context: Context,params: WorkerParameters): CoroutineWorker(context,params) {
    override suspend fun getForegroundInfo(): ForegroundInfo {
        val model=inputData.getString("model") ?: ""
        return ForegroundInfo(id.hashCode(),ModelDownloadJobs.notification(applicationContext,model),ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
    }
    override suspend fun doWork(): Result {
        val model=inputData.getString("model") ?: return Result.failure()
        return try {
            try {setForeground(getForegroundInfo())}
            catch(e:IllegalStateException) {ModelDownloadJobs.waiting(applicationContext,model);return Result.retry()}
            coroutineScope {
                val reporter=launch {while(isActive) {setForeground(getForegroundInfo());delay(1000)}}
                try {ModelDownloadJobs.run(applicationContext,model)} finally {reporter.cancel()}
            }
            Result.success()
        } catch(e:CancellationException) {ModelDownloadJobs.waiting(applicationContext,model);throw e} catch(e:ClientFailure) {
            if(e.key=="mobile.local.wifi_required") {ModelDownloadJobs.waiting(applicationContext,model);Result.retry()}
            else {ModelDownloadJobs.pauseInBackground(applicationContext,model,e.key);Result.failure()}
        } catch(e:Exception) {ModelDownloadJobs.pauseInBackground(applicationContext,model,"mobile.local.download_failed");Result.failure()}
    }
}
