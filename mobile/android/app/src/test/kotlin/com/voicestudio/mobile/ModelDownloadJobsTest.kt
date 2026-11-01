package com.voicestudio.mobile

import android.app.Application
import android.app.job.JobScheduler
import android.net.NetworkCapabilities
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.ModelDownloadJobs
import com.voicestudio.mobile.local.ModelCatalog
import com.voicestudio.mobile.local.PhoneModel
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
    @Test fun cancelledDownloadHasNoAutomaticRestoreIntent() {
        ModelDownloadJobs.enqueue(app,model,false);ModelDownloadJobs.cancel(app,model.id)
        ModelDownloadJobs.restore(app,listOf(model))
        assertTrue(scheduler.allPendingJobs.isEmpty())
        assertEquals("[]",app.getSharedPreferences("model-downloads",0).getString("queue",null))
    }
}
