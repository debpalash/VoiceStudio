package com.voicestudio.mobile

import android.app.Application
import android.app.job.JobScheduler
import android.net.NetworkCapabilities
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.ModelDownloadJobs
import com.voicestudio.mobile.local.ModelCatalog
import com.voicestudio.mobile.local.PhoneModel
import kotlinx.coroutines.launch
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class ModelDownloadJobsTest {
    private lateinit var app: Application
    private lateinit var model: PhoneModel
    private lateinit var scheduler: JobScheduler
    @Before fun prepare() {
        app=ApplicationProvider.getApplicationContext()
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        app.getSharedPreferences("model-downloads",0).edit().clear().commit()
        scheduler=app.getSystemService(JobScheduler::class.java);scheduler.cancelAll()
        model=app.assets.open("models.json").bufferedReader().use {ModelCatalog.decode(it.readText()).models.first {!it.gated}}
    }
    @After fun clean() {ModelDownloadJobs.cancel(app,model.id);scheduler.cancelAll()}
    @Test fun explicitDownloadSchedulesPersistentUserInitiatedWifiWork() {
        ModelDownloadJobs.enqueue(app,model,false)
        val job=scheduler.allPendingJobs.single()
        assertTrue(job.isUserInitiated)
        assertEquals(model.id,job.extras.getString("model"))
        assertTrue(job.requiredNetwork!!.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))
        assertTrue(job.requiredNetwork!!.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED))
        assertTrue(app.getSharedPreferences("model-downloads",0).getString("queue","")!!.contains(model.id))
        // Losing the OS queue simulates restoring a persisted intent after reboot.
        scheduler.cancelAll();ModelDownloadJobs.restore(app,listOf(model))
        assertEquals(model.id,scheduler.allPendingJobs.single().extras.getString("model"))
    }
    @Test fun pausedDownloadDoesNotRestartWhenTheUiReopens() {
        ModelDownloadJobs.enqueue(app,model,false)
        ModelDownloadJobs.pause(app,model.id)
        ModelDownloadJobs.restore(app,listOf(model))
        assertTrue(scheduler.allPendingJobs.isEmpty())
        ModelDownloadJobs.enqueue(app,model,true)
        assertFalse(scheduler.allPendingJobs.single().requiredNetwork!!.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))
    }
    @Test fun catalogueChangesDiscardStaleAutomaticIntents() {
        ModelDownloadJobs.enqueue(app,model,false)
        ModelDownloadJobs.restore(app,listOf(model.copy(revision="b".repeat(40))))
        assertTrue(scheduler.allPendingJobs.isEmpty())
        assertEquals("[]",app.getSharedPreferences("model-downloads",0).getString("queue",null))
    }
    @Test fun progressPresentationShowsPercentSpeedEtaAndVerificationSeparately() {
        val l=com.voicestudio.mobile.l10n.L10n.load(app,"en")
        val progress=com.voicestudio.mobile.local.DownloadProgress(500,1000,true,bytesPerSecond=100.0,etaSeconds=5.0)
        val text=com.voicestudio.mobile.data.modelDownloadDescription(app,l,progress)
        assertTrue(text.contains("50%"));assertTrue(text.contains("/s"));assertTrue(text.contains("00:05"))
        val verifying=com.voicestudio.mobile.data.modelDownloadDescription(app,l,progress.copy(stage="verifying"))
        assertTrue(verifying.contains("Verifying model"));assertFalse(verifying.contains("00:05"))
    }
    @Test @Config(sdk=[33]) fun olderPhonesUseOneDurableWorkerAndPauseStopsIt() {
        ModelDownloadJobs.enqueue(app,model,false)
        val manager=androidx.work.WorkManager.getInstance(app)
        val infos=manager.getWorkInfosForUniqueWork("model:${model.id}").get(5,java.util.concurrent.TimeUnit.SECONDS)
        assertEquals(1,infos.size)
        ModelDownloadJobs.pause(app,model.id)
        ModelDownloadJobs.restore(app,listOf(model))
        assertTrue(app.getSharedPreferences("model-downloads",0).getString("queue","")!!.contains("\"paused\":true"))
    }
    @Test fun oneRejectedRestoreDoesNotBlockTheOtherQueuedModels() {
        val second=model.copy(id=model.id+"-second")
        ModelDownloadJobs.enqueue(app,model,false);ModelDownloadJobs.enqueue(app,second,false)
        val rejected=scheduler.allPendingJobs.single {it.extras.getString("model")==model.id}.id
        scheduler.cancelAll()
        org.robolectric.Shadows.shadowOf(scheduler).failOnJob(rejected)
        val failures=mutableListOf<Throwable>()
        ModelDownloadJobs.restore(app,listOf(model,second)) {failures+=it}
        assertEquals(1,failures.size)
        assertEquals("mobile.local.download_failed",(failures.single() as com.voicestudio.mobile.network.ClientFailure).key)
        assertEquals(second.id,scheduler.allPendingJobs.single().extras.getString("model"))
        assertFalse(com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads.progress.value[model.id]!!.running)
        ModelDownloadJobs.cancel(app,second.id)
    }
    @Test fun terminalFailureReasonSurvivesRestorationAndClearsOnRetry() {
        ModelDownloadJobs.enqueue(app,model,false)
        ModelDownloadJobs.pause(app,model.id,"mobile.local.storage_required")
        val downloads=com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads
        // Discard the in-memory error so restoration must recover the saved reason.
        downloads.paused(model.id)
        assertNull(downloads.progress.value[model.id]?.error)
        ModelDownloadJobs.restore(app,listOf(model))
        assertEquals("mobile.local.storage_required",downloads.progress.value[model.id]?.error)
        assertEquals("failed",downloads.progress.value[model.id]?.stage)
        assertEquals(model.totalBytes,downloads.progress.value[model.id]?.total)
        assertTrue(scheduler.allPendingJobs.isEmpty())
        ModelDownloadJobs.enqueue(app,model,false)
        assertNull(downloads.progress.value[model.id]?.error)
        assertFalse(app.getSharedPreferences("model-downloads",0).getString("queue","")!!.contains("mobile.local.storage_required"))
    }
    @Test fun cancelledDownloadHasNoAutomaticRestoreIntent() {
        ModelDownloadJobs.enqueue(app,model,false);ModelDownloadJobs.cancel(app,model.id)
        ModelDownloadJobs.restore(app,listOf(model))
        assertTrue(scheduler.allPendingJobs.isEmpty())
        assertEquals("[]",app.getSharedPreferences("model-downloads",0).getString("queue",null))
    }
    @Test fun failedPauseStillStopsScheduledWorkAndCancelRetriesIntentRemoval() {
        ModelDownloadJobs.enqueue(app,model,false)
        val preferences=app.getSharedPreferences("model-downloads",0)
        var commits=0
        val editor=org.mockito.Mockito.mock(android.content.SharedPreferences.Editor::class.java)
        org.mockito.Mockito.`when`(editor.putString(org.mockito.ArgumentMatchers.anyString(),org.mockito.ArgumentMatchers.anyString())).thenReturn(editor)
        org.mockito.Mockito.`when`(editor.commit()).thenAnswer {commits++;false}
        val failingPreferences=org.mockito.Mockito.mock(android.content.SharedPreferences::class.java)
        org.mockito.Mockito.`when`(failingPreferences.getString("queue","[]")).thenAnswer {preferences.getString("queue","[]")}
        org.mockito.Mockito.`when`(failingPreferences.edit()).thenReturn(editor)
        val context=object: android.content.ContextWrapper(app) {
            override fun getSharedPreferences(name: String,mode: Int)=if(name=="model-downloads") failingPreferences else super.getSharedPreferences(name,mode)
        }
        try {ModelDownloadJobs.pause(context,model.id);fail("Persistence failure must be reported")}
        catch(e:com.voicestudio.mobile.network.ClientFailure) {assertEquals("mobile.simple.checkpoint_failed",e.key)}
        assertTrue(scheduler.allPendingJobs.isEmpty())
        commits=0
        try {ModelDownloadJobs.cancel(context,model.id);fail("Persistence failure must be reported")}
        catch(e:com.voicestudio.mobile.network.ClientFailure) {assertEquals("mobile.simple.checkpoint_failed",e.key)}
        assertEquals("Cancel must attempt both pause and intent removal",2,commits)
        com.voicestudio.mobile.data.ModelDownloadPauseReceiver().onReceive(context,android.content.Intent().putExtra("model",model.id))
        assertEquals("mobile.simple.checkpoint_failed",com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads.progress.value[model.id]?.error)
    }

    @Test @Config(sdk=[33]) fun blockedForegroundPromotionRetriesWithoutPausingTheIntent()=kotlinx.coroutines.runBlocking {
        ModelDownloadJobs.enqueue(app,model,false)
        val worker=androidx.work.testing.TestListenableWorkerBuilder<com.voicestudio.mobile.data.ModelDownloadWorker>(app)
            .setInputData(androidx.work.workDataOf("model" to model.id))
            .setForegroundUpdater {_,_,_->androidx.concurrent.futures.CallbackToFutureAdapter.getFuture<Void> {
                it.setException(IllegalStateException("Background foreground service start blocked"));"blocked"
            }}.build()
        assertEquals(androidx.work.ListenableWorker.Result.retry(),worker.doWork())
        assertFalse(app.getSharedPreferences("model-downloads",0).getString("queue","")!!.contains("\"paused\":true"))
        assertNull(com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads.progress.value[model.id]?.error)
    }

    @Test fun backgroundPauseRetriesPersistenceAndKeepsItsErrorOnRestore() {
        ModelDownloadJobs.enqueue(app,model,false)
        val preferences=app.getSharedPreferences("model-downloads",0)
        var commits=0
        var queued=""
        val editor=org.mockito.Mockito.mock(android.content.SharedPreferences.Editor::class.java)
        org.mockito.Mockito.`when`(editor.putString(org.mockito.ArgumentMatchers.anyString(),org.mockito.ArgumentMatchers.anyString())).thenAnswer {queued=it.getArgument(1);editor}
        org.mockito.Mockito.`when`(editor.commit()).thenAnswer {++commits>1&&preferences.edit().putString("queue",queued).commit()}
        val proxy=org.mockito.Mockito.mock(android.content.SharedPreferences::class.java)
        org.mockito.Mockito.`when`(proxy.getString("queue","[]")).thenAnswer {preferences.getString("queue","[]")}
        org.mockito.Mockito.`when`(proxy.edit()).thenReturn(editor)
        val context=object:android.content.ContextWrapper(app) {
            override fun getSharedPreferences(name:String,mode:Int)=if(name=="model-downloads") proxy else super.getSharedPreferences(name,mode)
        }
        com.voicestudio.mobile.data.ModelDownloadPauseReceiver().onReceive(context,android.content.Intent().putExtra("model",model.id))
        assertEquals(2,commits)
        com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads.paused(model.id)
        ModelDownloadJobs.restore(app,listOf(model))
        assertTrue(scheduler.allPendingJobs.isEmpty())
        assertEquals("mobile.simple.checkpoint_failed",com.voicestudio.mobile.local.PhoneRuntime.get(app).downloads.progress.value[model.id]?.error)
    }
    @Test @Config(sdk=[33]) fun pauseDuringCatalogueLookupCancelsTheRegisteredWorker()=kotlinx.coroutines.runBlocking {
        ModelDownloadJobs.enqueue(app,model,false)
        val entered=java.util.concurrent.CountDownLatch(1)
        val release=java.util.concurrent.CountDownLatch(1)
        val context=object:android.content.ContextWrapper(app) {
            override fun getApplicationContext():android.content.Context=this
            override fun getAssets():android.content.res.AssetManager {
                if(Thread.currentThread().stackTrace.any {it.className.contains("ModelDownloadJobs\$run")}) {
                    entered.countDown();check(release.await(10,java.util.concurrent.TimeUnit.SECONDS))
                }
                return app.assets
            }
        }
        val worker=androidx.work.testing.TestListenableWorkerBuilder<com.voicestudio.mobile.data.ModelDownloadWorker>(context)
            .setInputData(androidx.work.workDataOf("model" to model.id))
            .setForegroundUpdater {_,_,_->androidx.concurrent.futures.CallbackToFutureAdapter.getFuture<Void> {it.set(null);"promoted"}}.build()
        val work=kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.Default).launch {worker.doWork()}
        try {
            assertTrue(entered.await(10,java.util.concurrent.TimeUnit.SECONDS))
            ModelDownloadJobs.pause(app,model.id)
        } finally {release.countDown()}
        work.join()
        assertTrue("Pause must cancel a worker still loading the catalogue",work.isCancelled)
    }

}
