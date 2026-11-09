package com.voicestudio.mobile

import android.content.Context
import android.content.pm.ServiceInfo
import androidx.concurrent.futures.CallbackToFutureAdapter
import androidx.test.core.app.ApplicationProvider
import androidx.work.*
import androidx.work.testing.TestListenableWorkerBuilder
import com.voicestudio.mobile.data.*
import kotlinx.coroutines.*
import okhttp3.mockwebserver.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class WorkerAvailabilityRegressionTest {
    private suspend fun worker(foreground:ForegroundUpdater):Pair<DubWorker,MockWebServer> {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val server=MockWebServer().apply {start()}
        val host=server.url("/").toString()
        SessionStore(context).update {Preferences(activeUrl=host)}
        server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
        server.enqueue(MockResponse().setBody("""{"job_id":"job","task_id":"prep"}"""))
        server.enqueue(MockResponse().setBody("""{"job":{"status":"done"},"events":[{"seq":1,"payload":"{\"percent\":0.5}"}]}"""))
        server.enqueue(MockResponse().setBody("""{"segments":[{"start":0,"end":1,"text":"Speech"}]}"""))
        return TestListenableWorkerBuilder<DubWorker>(context).setInputData(workDataOf(
            "host" to host,"job" to "job","link" to "https://example.test/video",
            "languages" to emptyArray<String>(),"names" to emptyArray<String>(),
            "translation_provider" to "argos","translation_category" to "offline"))
            .setForegroundUpdater(foreground).build() to server
    }
    @Test fun foregroundRefusalDoesNotFailUploadOrSubsequentProgress()=runBlocking {
        var attempts=0
        val (worker,server)=worker(ForegroundUpdater {_,_,_->
            attempts++
            CallbackToFutureAdapter.getFuture<Void> {it.setException(IllegalStateException("foreground start refused"));"refused"}
        })
        try {
            assertEquals(ListenableWorker.Result.success(workDataOf("job" to "job","stage" to "mobile.android.stage_done")),worker.doWork())
            assertTrue(attempts>=4)
            assertEquals(4,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun foregroundInfoDeclaresDataSyncAndCancellationPropagates()=runBlocking {
        val (worker,server)=worker(ForegroundUpdater {_,_,_->
            CallbackToFutureAdapter.getFuture<Void> {it.setException(CancellationException("cancel"));"cancelled"}
        })
        try {
            assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,worker.getForegroundInfo().foregroundServiceType)
            val context=ApplicationProvider.getApplicationContext<Context>()
            val service=context.packageManager.getServiceInfo(android.content.ComponentName(context,
                "androidx.work.impl.foreground.SystemForegroundService"),0)
            assertTrue(service.foregroundServiceType and ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC != 0)
            try {worker.doWork();fail("Cancellation must propagate")} catch(expected:CancellationException) { }
            assertEquals(1,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun fileUploadKeepsDisplayNameWithNotificationPermissionDenied()=runBlocking {
        val context=ApplicationProvider.getApplicationContext<Context>()
        org.robolectric.Shadows.shadowOf(context as android.app.Application).denyPermissions(android.Manifest.permission.POST_NOTIFICATIONS)
        val server=MockWebServer().apply {start()}
        val host=server.url("/").toString()
        SessionStore(context).update {Preferences(activeUrl=host)}
        val file=java.io.File(context.filesDir,"temporary-upload.wav").apply {writeText("RIFF media")}
        var promoted=false
        server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
        server.enqueue(MockResponse().setBody("""{"job_id":"file-job","task_id":"prep"}"""))
        server.enqueue(MockResponse().setBody("""{"job":{"status":"done"},"events":[]}"""))
        server.enqueue(MockResponse().setBody("""{"segments":[{"start":0,"end":1,"text":"Speech"}]}"""))
        val worker=TestListenableWorkerBuilder<DubWorker>(context).setInputData(workDataOf(
            "host" to host,"job" to "file-job","file" to file.absolutePath,"source_name" to "vs-dub-test.wav","type" to "audio",
            "languages" to arrayOf("fr"),"names" to arrayOf("French"),"voice" to "",
            "translation_provider" to "argos","translation_category" to "offline"))
            .setForegroundUpdater {_,_,info ->
                promoted=true
                assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,info.foregroundServiceType)
                CallbackToFutureAdapter.getFuture<Void> {it.set(null);"foreground"}
            }.build()
        server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
        server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Bonjour"}]}"""))
        server.enqueue(MockResponse().setBody("""{"task_id":"render"}"""))
        server.enqueue(MockResponse().setBody("""{"job":{"status":"done"},"events":[]}"""))
        try {
            assertEquals(ListenableWorker.Result.success(workDataOf("job" to "file-job","stage" to "mobile.android.stage_done")),worker.doWork())
            assertTrue(promoted)
            assertEquals("/engines/translation",server.takeRequest().path)
            val upload=server.takeRequest()
            assertEquals("/dub/upload",upload.path)
            val multipart=upload.body.readUtf8()
            assertTrue(multipart,multipart.contains("filename=\"vs-dub-test.wav\""))
            assertTrue(multipart.contains("RIFF media"))
            assertEquals("/jobs/prep/events?after_seq=0",server.takeRequest().path)
            assertEquals("/dub/transcribe/file-job",server.takeRequest().path)
            assertEquals("/engines/translation",server.takeRequest().path)
            assertEquals("/dub/translate",server.takeRequest().path)
            assertEquals("/dub/generate/file-job",server.takeRequest().path)
            assertEquals("/jobs/render/events?after_seq=0",server.takeRequest().path)
            assertEquals(8,server.requestCount)
            val permissions=context.packageManager.getPackageInfo(context.packageName,android.content.pm.PackageManager.GET_PERMISSIONS).requestedPermissions.orEmpty().toSet()
            assertTrue(permissions.containsAll(listOf(android.Manifest.permission.FOREGROUND_SERVICE,android.Manifest.permission.FOREGROUND_SERVICE_DATA_SYNC,android.Manifest.permission.POST_NOTIFICATIONS)))
        } finally {file.delete();server.shutdown()}
    }
    @Test fun malformedJobHasActionableFailureInsteadOfEmptyOutput()=runBlocking {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val worker=TestListenableWorkerBuilder<DubWorker>(context).build()
        assertEquals(ListenableWorker.Result.failure(DubWorker.failureData(
            com.voicestudio.mobile.network.ClientFailure("mobile.android.dub_restart"),"")),worker.doWork())
    }
    @Test fun foregroundSecurityFailureIsActionableAndNeverEscapesWorker()=runBlocking {
        val (worker,server)=worker(ForegroundUpdater {_,_,_->
            CallbackToFutureAdapter.getFuture<Void> {it.setException(SecurityException("missing foreground permission"));"denied"}
        })
        try {
            assertEquals(ListenableWorker.Result.failure(DubWorker.failureData(
                com.voicestudio.mobile.network.ClientFailure("mobile.android.dub_foreground_failed"),"job")),worker.doWork())
            assertEquals(1,server.requestCount)
        } finally {server.shutdown()}
    }

    @Test fun providerChangedDuringTranscriptionCannotReceiveTheTranscript()=runBlocking {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val server=MockWebServer().apply {start()}
        val host=server.url("/").toString()
        SessionStore(context).update {Preferences(activeUrl=host)}
        server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
        server.enqueue(MockResponse().setBody("""{"job_id":"job","task_id":"prep"}"""))
        server.enqueue(MockResponse().setBody("""{"job":{"status":"done"},"events":[]}"""))
        server.enqueue(MockResponse().setBody("""{"segments":[{"start":0,"end":1,"text":"Private speech"}]}"""))
        server.enqueue(MockResponse().setBody("""{"active":"google","engines":[{"id":"google","category":"online","installed":true,"ready":true}]}"""))
        val worker=TestListenableWorkerBuilder<DubWorker>(context).setInputData(workDataOf(
            "host" to host,"job" to "job","link" to "https://example.test/video",
            "languages" to arrayOf("fr"),"names" to arrayOf("French"),"voice" to "",
            "translation_provider" to "argos","translation_category" to "offline"))
            .setForegroundUpdater {_,_,_->CallbackToFutureAdapter.getFuture<Void> {it.set(null);"promoted"}}.build()
        try {
            assertEquals(ListenableWorker.Result.failure(DubWorker.failureData(
                com.voicestudio.mobile.network.ClientFailure("mobile.dub.translation_changed"),"job")),worker.doWork())
            assertEquals(5,server.requestCount)
            val paths=List(5) {server.takeRequest().path.orEmpty()}
            assertFalse(paths.any {it=="/dub/translate" || it.startsWith("/dub/generate")})
        } finally {server.shutdown()}
    }

}
