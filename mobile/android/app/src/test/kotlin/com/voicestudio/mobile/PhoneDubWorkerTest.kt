package com.voicestudio.mobile

import android.app.Application
import android.content.Context
import android.content.pm.ServiceInfo
import androidx.concurrent.futures.CallbackToFutureAdapter
import androidx.test.core.app.ApplicationProvider
import androidx.work.*
import androidx.work.testing.TestListenableWorkerBuilder
import com.voicestudio.mobile.data.PhoneDubWorker
import kotlinx.coroutines.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PhoneDubWorkerTest {
    private val context get()=ApplicationProvider.getApplicationContext<Context>()
    @Test fun foregroundIsRequiredAndCancellationPropagatesWithoutNetwork()=runBlocking {
        var promoted=false
        val worker=TestListenableWorkerBuilder<PhoneDubWorker>(context).setInputData(workDataOf("job" to java.util.UUID.randomUUID().toString()))
            .setForegroundUpdater {_,_,info ->
                promoted=true;assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,info.foregroundServiceType)
                CallbackToFutureAdapter.getFuture<Void> {it.setException(CancellationException("stop"));"cancelled"}
            }.build()
        (context as Application).let {org.robolectric.Shadows.shadowOf(it).denyPermissions(android.Manifest.permission.POST_NOTIFICATIONS)}
        try {worker.doWork();fail()} catch(_:CancellationException) {}
        assertTrue(promoted)
        assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,worker.getForegroundInfo().foregroundServiceType)
    }
    @Test fun phoneDubFailurePersistsRecoveryParametersInCheckpointAndWorkData()=runBlocking {
        val app=context as Application
        val catalog=app.assets.open("models.json").bufferedReader().use {com.voicestudio.mobile.local.ModelCatalog.decode(it.readText())}
        val models=listOf("asr","translate","clone").map {cap ->catalog.models.first {it.capability==cap}}
        val runtime=com.voicestudio.mobile.local.PhoneRuntime.get(app)
        models.filter {it.gated}.forEach {runtime.licence.record(it,true)}
        val jobs=com.voicestudio.mobile.local.PhoneDubStore(java.io.File(app.noBackupFilesDir,"dubs"))
        val source=java.io.File(app.cacheDir,"fixture.wav").apply {writeText("fixture")}
        val job=jobs.create(source,"Fixture",listOf(com.voicestudio.mobile.local.DubTarget("fr","French")),models,true)
        val failure=com.voicestudio.mobile.network.ClientFailure("mobile.errors.backend",mapOf("message" to "Foreground initialization failed"))
        val worker=TestListenableWorkerBuilder<PhoneDubWorker>(app).setInputData(workDataOf("job" to job.id))
            .setForegroundUpdater {_,_,_->CallbackToFutureAdapter.getFuture<Void> {it.setException(failure);"foreground-failure"}}.build()
        val result=worker.doWork() as ListenableWorker.Result.Failure
        assertEquals(failure.key,result.outputData.getString("error"))
        assertEquals("{\"message\":\"Foreground initialization failed\"}",result.outputData.getString("error_params"))
        assertEquals(failure.params,jobs.checkpoint(job.id).errorParams)
    }
    @Test fun invalidCheckpointIsActionableWithoutComputerCredentials()=runBlocking {
        val worker=TestListenableWorkerBuilder<PhoneDubWorker>(context).setInputData(workDataOf("job" to java.util.UUID.randomUUID().toString()))
            .setForegroundUpdater {_,_,_->CallbackToFutureAdapter.getFuture<Void> {it.set(null);"foreground"}}.build()
        val result=worker.doWork()
        assertEquals(ListenableWorker.Result.failure(workDataOf("job" to worker.inputData.getString("job"),"error" to "mobile.simple.checkpoint_failed","error_params" to "{}")),result)
    }
}
