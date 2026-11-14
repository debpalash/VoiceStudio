package com.voicestudio.mobile

import android.app.Application
import androidx.concurrent.futures.CallbackToFutureAdapter
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.work.*
import androidx.work.testing.TestListenableWorkerBuilder
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.network.*
import java.io.File
import java.nio.file.Files
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Real worker output and the production ViewModel share an isolated, consent-aware reporter. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = Application::class)
class WorkerDiagnosticConsentTest {
    private val app get() = ApplicationProvider.getApplicationContext<Application>()
    @Before fun initializeWorkerNotifications() {
        // Real foreground notifications include WorkManager's cancel PendingIntent.
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
    }
    private class Collector : DiagnosticTransport {
        val bodies = CopyOnWriteArrayList<String>()
        override suspend fun send(destination: DiagnosticDestination, body: ByteArray, allowed: () -> Boolean): Int {
            check(allowed())
            bodies.add(body.toString(Charsets.UTF_8))
            return 201
        }
        override fun cancelAll() = Unit
    }
    private suspend fun reports(block: suspend (DiagnosticReports, Collector) -> Unit) {
        val directory = Files.createTempDirectory("worker-diagnostics").toFile()
        val collector = Collector()
        val policy = app.assets.open("diagnostics.json").bufferedReader(Charsets.UTF_8).use { DiagnosticPolicy(it.readText()) }
        val reporter = DiagnosticReports(directory, policy,
            DiagnosticDestination("fixture", "https://collector.invalid", "https://app.invalid"), "1.0.0", transport = collector)
        val field = AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply { isAccessible = true }
        val previous = field.get(AutomaticDiagnostics)
        reporter.load()
        field.set(AutomaticDiagnostics, reporter)
        try { block(reporter, collector) }
        finally { field.set(AutomaticDiagnostics, previous); reporter.close(); directory.deleteRecursively() }
    }
    private fun foreground(error: Throwable? = null) = ForegroundUpdater { _, _, _ ->
        CallbackToFutureAdapter.getFuture<Void> { completion ->
            if (error == null) completion.set(null) else completion.setException(error)
            "controlled-worker-foreground"
        }
    }
    private fun localWorker(kind: String, error: Throwable? = null): CoroutineWorker {
        val data = workDataOf((if (kind == "transcription") "record" else "job") to UUID.randomUUID().toString())
        return when (kind) {
            "workflow" -> TestListenableWorkerBuilder<PhoneWorkflowWorker>(app).setInputData(data).setForegroundUpdater(foreground(error)).build()
            "production" -> TestListenableWorkerBuilder<PhoneProductionWorker>(app).setInputData(data).setForegroundUpdater(foreground(error)).build()
            "transcription" -> TestListenableWorkerBuilder<PhoneTranscriptionWorker>(app).setInputData(data).setForegroundUpdater(foreground(error)).build()
            else -> TestListenableWorkerBuilder<PhoneDubWorker>(app).setInputData(data).setForegroundUpdater(foreground(error)).build()
        }
    }
    private fun viewModel(): StudioViewModel {
        val store = SessionStore(app, object : CredentialVault {
            override fun read(host: String): Secret? = null
            override fun write(host: String, secret: Secret?) = Unit
        })
        return StudioViewModel(app, store)
    }

    @Test fun savedOffFailuresStayDisplayOnlyAfterOptInAndReopen() = runBlocking {
        reports { reporter, collector ->
            // Produce actual translated worker outputs while diagnostic consent is OFF.
            val directory = Files.createTempDirectory("saved-work-output").toFile()
            try {
                val saved = listOf("workflow", "production").mapIndexed { index, kind ->
                    val result = localWorker(kind, ClientFailure("mobile.local.inference_failed")).doWork() as ListenableWorker.Result.Failure
                    val file = File(directory, "$index.data").apply { writeBytes(result.outputData.toByteArray()) }
                    WorkInfo(UUID.randomUUID(), WorkInfo.State.FAILED, emptySet(), Data.fromByteArray(file.readBytes()), Data.EMPTY)
                }
                assertTrue(collector.bodies.isEmpty())
                reporter.setConsent(true)
                // Reopening screens (new ViewModel) or refreshing the same history must not upload it.
                repeat(2) {
                    val vm = viewModel()
                    try {
                        for (work in saved) {
                            vm.restoreFailure(ClientFailure(requireNotNull(work.outputData.getString("error"))))
                            assertEquals("mobile.local.inference_failed", vm.state.value.error?.key)
                            assertFalse(vm.state.value.canRetry)
                        }
                    } finally { vm.viewModelScope.cancel() }
                }
                reporter.flush()
                assertEquals("pre-consent saved WorkInfo was reported on display", 0, collector.bodies.size)
                reporter.setConsent(false)
                reporter.setConsent(true)
                val reopened = viewModel()
                try { reopened.restoreFailure(ClientFailure(saved.first().outputData.getString("error")!!)) }
                finally { reopened.viewModelScope.cancel() }
                reporter.flush()
                assertEquals("a new consent epoch must not reclassify old history", 0, collector.bodies.size)
                val live = viewModel()
                try { live.fail(ClientFailure("mobile.local.inference_failed")) }
                finally { live.viewModelScope.cancel() }
                reporter.flush()
                assertEquals("fresh UI failures remain reportable", 1, collector.bodies.size)
            } finally { directory.deleteRecursively() }
        }
    }

    @Test fun freshLocalWorkerFailuresAreCapturedBeforeTheyBecomeStoredErrorKeys() = runBlocking {
        for (kind in listOf("workflow", "production", "transcription", "dub")) reports { reporter, collector ->
            reporter.setConsent(true)
            // Fail before model resolution/downloads, using each real worker's foreground boundary.
            val result = localWorker(kind, ClientFailure("mobile.local.inference_failed", mapOf("message" to "private speech"))).doWork() as ListenableWorker.Result.Failure
            assertEquals("mobile.local.inference_failed", result.outputData.getString("error"))
            reporter.flush()
            assertEquals("missing original failure capture for $kind", 1, collector.bodies.size)
            assertFalse(collector.bodies.single().contains("private speech"))
        }
    }

    @Test fun originalUnexpectedWorkerErrorsAreNotLostBehindGenericFileDisplayKeys() = runBlocking {
        for (kind in listOf("workflow", "production", "transcription")) reports { reporter, collector ->
            reporter.setConsent(true)
            val key = if (kind == "transcription") "record" else "job"
            val data = workDataOf(key to "invalid-uuid")
            val worker = when (kind) {
                "workflow" -> TestListenableWorkerBuilder<PhoneWorkflowWorker>(app).setInputData(data).build()
                "production" -> TestListenableWorkerBuilder<PhoneProductionWorker>(app).setInputData(data).build()
                else -> TestListenableWorkerBuilder<PhoneTranscriptionWorker>(app).setInputData(data).build()
            }
            val result = worker.doWork() as ListenableWorker.Result.Failure
            assertEquals(if (kind == "transcription") "transcriptions.failed" else "mobile.errors.file", result.outputData.getString("error"))
            reporter.flush()
            assertEquals("original unexpected error was discarded by $kind", 1, collector.bodies.size)
        }
    }

    @Test fun cancellationsAndFailuresObservedWhileOffNeverBecomeReports() = runBlocking {
        for (kind in listOf("workflow", "production", "transcription", "dub")) reports { reporter, collector ->
            localWorker(kind, ClientFailure("mobile.local.inference_failed")).doWork()
            reporter.setConsent(true)
            try { localWorker(kind, CancellationException("cancel")).doWork(); fail("$kind swallowed cancellation") }
            catch (_: CancellationException) { }
            reporter.flush()
            assertTrue("OFF failure/cancellation reported by $kind", collector.bodies.isEmpty())
        }
    }

    @Test fun pairedWorkerReportsFreshTerminalFailureButNotTransientRetry() = runBlocking {
        reports { reporter, collector ->
            reporter.setConsent(true)
            val server = MockWebServer().apply { start() }
            try {
                val host = server.url("/").toString()
                SessionStore(app).update { Preferences(activeUrl = host) }
                suspend fun run(error: Throwable): ListenableWorker.Result {
                    server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
                    val worker = TestListenableWorkerBuilder<DubWorker>(app).setInputData(workDataOf(
                        "host" to host, "job" to "job", "languages" to emptyArray<String>(), "names" to emptyArray<String>(),
                        "translation_provider" to "argos", "translation_category" to "offline"))
                        .setForegroundUpdater(foreground(error)).build()
                    return worker.doWork()
                }
                assertTrue(run(ClientFailure("mobile.local.inference_failed")) is ListenableWorker.Result.Failure)
                reporter.flush()
                assertEquals(1, collector.bodies.size)
                // HTTP transient failures keep WorkManager retry semantics without a terminal report.
                assertTrue(run(ClientFailure("mobile.errors.backend", status = 503)) is ListenableWorker.Result.Retry)
                try { run(CancellationException("cancel")); fail("paired cancellation swallowed") }
                catch (_: CancellationException) { }
                reporter.flush()
                assertEquals(1, collector.bodies.size)
            } finally { server.shutdown() }
        }
    }

    @Test fun pairedHostCancellationRowsAndEventsRemainDisplayOnlyWhileRealFailureReports() = runBlocking {
        for (kind in listOf("cancelled-row", "cancelled-event", "failed")) reports { reporter, collector ->
            reporter.setConsent(true)
            val server = MockWebServer().apply { start() }
            try {
                val host = server.url("/").toString()
                val job = UUID.randomUUID().toString()
                SessionStore(app).update { Preferences(activeUrl = host) }
                server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
                server.enqueue(MockResponse().setBody("""{"job_id":"$job","task_id":"prep"}"""))
                val events = when (kind) {
                    "cancelled-event" -> """{"job":{"status":"done"},"events":[{"seq":1,"payload":"{\"type\":\"cancelled\"}"}]}"""
                    "cancelled-row" -> """{"job":{"status":"cancelled"},"events":[]}"""
                    else -> """{"job":{"status":"failed"},"events":[]}"""
                }
                server.enqueue(MockResponse().setBody(events))
                val worker = TestListenableWorkerBuilder<DubWorker>(app).setInputData(workDataOf(
                    "host" to host, "job" to job, "link" to "https://example.test/video",
                    "languages" to emptyArray<String>(), "names" to emptyArray<String>(),
                    "translation_provider" to "argos", "translation_category" to "offline"))
                    .setForegroundUpdater(foreground()).build()
                val result = worker.doWork() as ListenableWorker.Result.Failure
                assertEquals("mobile.android.job_failed", result.outputData.getString("error"))
                assertEquals("pipeline must stop at the terminal task event", 3, server.requestCount)
                reporter.flush()
                assertEquals("host $kind diagnostic count", if (kind == "failed") 1 else 0, collector.bodies.size)
            } finally { server.shutdown() }
        }
    }

    @Test fun restoredRunningTaskCannotReportItsOffPeriodFailureAfterOptIn() = runBlocking {
        for (kind in listOf("failed-row", "error-event")) reports { reporter, collector ->
            val server = MockWebServer().apply { start() }
            try {
                val host = server.url("/").toString()
                val job = UUID.randomUUID().toString()
                val store = SessionStore(app)
                store.update { Preferences(activeUrl = host) }
                // Real checkpoint saved while OFF, before the server failed offline.
                store.checkpoint(host, job, DubCheckpoint(uploaded = true, prepTask = "old-prep"))
                reporter.setConsent(true)
                server.enqueue(MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""))
                val page = if (kind == "failed-row")
                    """{"job":{"status":"failed","created_at":100,"updated_at":101},"events":[]}"""
                else """{"job":{"status":"done","created_at":100,"updated_at":101},"events":[{"seq":1,"created_at":101,"payload":"{\"type\":\"error\"}"}]}"""
                server.enqueue(MockResponse().setBody(page))
                val worker = TestListenableWorkerBuilder<DubWorker>(app).setInputData(workDataOf(
                    "host" to host, "job" to job, "languages" to emptyArray<String>(), "names" to emptyArray<String>(),
                    "translation_provider" to "argos", "translation_category" to "offline"))
                    .setForegroundUpdater(foreground()).build()
                val result = worker.doWork() as ListenableWorker.Result.Failure
                assertEquals("mobile.android.job_failed", result.outputData.getString("error"))
                reporter.flush()
                assertEquals("restored $kind was retroactively reported", 0, collector.bodies.size)
            } finally { server.shutdown() }
        }
    }

    @Test fun restoredTaskReportsOnlyNewServerFailureUnderItsUnchangedObservedEpoch() = runBlocking {
        for (scenario in listOf("fresh", "old-sequence", "old-time", "missing-time", "new-epoch")) reports { reporter, collector ->
            val server = MockWebServer()
            val secondPage = java.util.concurrent.CountDownLatch(1)
            val release = java.util.concurrent.CountDownLatch(1)
            val polls = java.util.concurrent.atomic.AtomicInteger()
            val running = """{"job":{"status":"running","created_at":100,"updated_at":100},"events":[{"seq":7,"created_at":100,"payload":"data: {\"type\":\"progress\",\"percent\":50}"}]}"""
            var failed = """{"job":{"status":"failed","created_at":100,"updated_at":101},"events":[{"seq":8,"created_at":101,"payload":"data: {\"type\":\"error\"}"}]}"""
            if (scenario == "old-sequence") failed = failed.replace("\"seq\":8", "\"seq\":7")
            if (scenario == "old-time") failed = failed.replace("\"created_at\":101", "\"created_at\":99")
            if (scenario == "missing-time") failed = failed.replace(",\"created_at\":101", "")
            server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse {
                    if (request.path == "/engines/translation") return MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}""")
                    if (request.path?.startsWith("/jobs/old-prep/events?") != true) return MockResponse().setResponseCode(404)
                    if (polls.getAndIncrement() == 0) return MockResponse().setBody(running)
                    secondPage.countDown()
                    if (scenario == "new-epoch") check(release.await(5, java.util.concurrent.TimeUnit.SECONDS))
                    return MockResponse().setBody(failed)
                }
            }
            server.start()
            try {
                val host = server.url("/").toString()
                val job = UUID.randomUUID().toString()
                val store = SessionStore(app)
                store.update { Preferences(activeUrl = host) }
                store.checkpoint(host, job, DubCheckpoint(uploaded = true, prepTask = "old-prep"))
                reporter.setConsent(true)
                val worker = TestListenableWorkerBuilder<DubWorker>(app).setInputData(workDataOf(
                    "host" to host, "job" to job, "languages" to emptyArray<String>(), "names" to emptyArray<String>(),
                    "translation_provider" to "argos", "translation_category" to "offline"))
                    .setForegroundUpdater(foreground()).build()
                val attempt = async(Dispatchers.Default) { worker.doWork() }
                try {
                    if (scenario == "new-epoch") {
                        assertTrue(withContext(Dispatchers.IO) {secondPage.await(5, java.util.concurrent.TimeUnit.SECONDS)})
                        reporter.setConsent(false); reporter.setConsent(true)
                        release.countDown()
                    }
                    val result = withTimeout(12_000) {attempt.await()} as ListenableWorker.Result.Failure
                    assertEquals("mobile.android.job_failed", result.outputData.getString("error"))
                } finally { release.countDown(); attempt.cancelAndJoin() }
                reporter.flush()
                assertEquals("$scenario diagnostic count", if (scenario == "fresh") 1 else 0, collector.bodies.size)
            } finally {release.countDown();server.shutdown()}
        }
    }
}
