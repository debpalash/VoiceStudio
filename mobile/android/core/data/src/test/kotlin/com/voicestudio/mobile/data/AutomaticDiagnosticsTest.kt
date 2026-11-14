package com.voicestudio.mobile.data

import com.voicestudio.mobile.network.*
import java.io.File
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.coroutines.CancellationException
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AutomaticDiagnosticsTest {
    private class Store : DiagnosticStorage {
        val files = ConcurrentHashMap<String, ByteArray>()
        @Volatile var failOutboxRead = false
        @Volatile var beforeOutboxRead: (() -> Unit)? = null
        var outboxReads = 0
        override fun read(name: String, maxBytes: Int): ByteArray? {
            if (name == "outbox.json") {
                outboxReads++
                beforeOutboxRead?.invoke()
                if (failOutboxRead) throw IOException("private path")
            }
            return files[name]?.clone()
        }
        override fun write(name: String, bytes: ByteArray) { files[name] = bytes.clone() }
        override fun remove(name: String) { files.remove(name) }
    }
    private class Transport : DiagnosticTransport {
        var sends = 0
        override suspend fun send(destination: DiagnosticDestination, body: ByteArray, allowed: () -> Boolean): Int {
            check(allowed()); sends++; return 204
        }
        override fun cancelAll() {}
    }
    private fun policy(): DiagnosticPolicy {
        var root = File(System.getProperty("user.dir"))
        repeat(10) {
            val file = File(root, "config/diagnostics.json")
            if (file.isFile) return DiagnosticPolicy(file.readText(Charsets.UTF_8))
            root = root.parentFile ?: root
        }
        error("Canonical policy missing")
    }
    private fun reports(store: Store, transport: Transport, dispatcher: CoroutineDispatcher) = DiagnosticReports(
        File("unused-diagnostic-test"), policy(),
        DiagnosticDestination("localtest123", "http://127.0.0.1:1", "https://app.test.invalid"), "0.5.9-beta.2",
        storage = store, transport = transport, dispatcher = dispatcher,
        clock = object : DiagnosticClock {
            override fun nowMs() = 1_794_640_000_000L
            override suspend fun sleep(ms: Long) { delay(ms) }
        })

    @Test fun failedInitialOutboxReadRetainsReporterAndExposesManualRetry() = runTest {
        val store = Store(); val transport = Transport(); val dispatcher = StandardTestDispatcher(testScheduler)
        reports(store, transport, dispatcher).let { it.setConsent(true); it.close() }
        store.failOutboxRead = true
        val reports = reports(store, transport, dispatcher)
        var retained: DiagnosticReports? = null
        var loaded = 0; var created = 0
        var state = AutomaticDiagnosticState()
        val owner = AutomaticDiagnosticInitialization(backgroundScope,
            create = { created++; reports }, retain = { retained = it },
            onLoaded = { loaded++ }, publish = { state = it }, revision = { 0 }, requested = { null })
        owner.start(); runCurrent()
        assertSame("Keep the failed reporter available for consent withdrawal and retry", reports, retained)
        assertTrue(state.ready); assertTrue("Settings must show Retry", state.saveFailed)
        assertFalse(state.status.available); assertFalse(reports.capture(DiagnosticInput("generation_failed")))
        store.failOutboxRead = false
        owner.retry(); runCurrent()
        assertEquals(1, created); assertEquals(1, loaded)
        assertTrue(state.status.enabled); assertFalse(state.saveFailed)
        assertEquals(0, transport.sends)
        reports.close()
    }

    @Test fun blockedStorageRetriesAtMostOncePerMinuteAndRecoversAfterRepeatedFailure() = runTest {
        val store = Store(); val transport = Transport(); val dispatcher = StandardTestDispatcher(testScheduler)
        reports(store, transport, dispatcher).let { it.setConsent(true); it.close() }
        store.failOutboxRead = true
        val reports = reports(store, transport, dispatcher)
        val before = store.outboxReads
        var loaded = 0
        val owner = AutomaticDiagnosticInitialization(backgroundScope, { reports }, {}, { loaded++ }, {}, { 0 }, { null })
        owner.start(); runCurrent()
        assertEquals(before + 1, store.outboxReads)
        advanceTimeBy(59_999); runCurrent(); assertEquals(before + 1, store.outboxReads)
        advanceTimeBy(1); runCurrent(); assertEquals(before + 2, store.outboxReads)
        store.failOutboxRead = false
        advanceTimeBy(60_000); runCurrent()
        assertEquals(before + 3, store.outboxReads); assertEquals(1, loaded)
        advanceTimeBy(120_000); runCurrent(); assertEquals(before + 3, store.outboxReads)
        reports.close()
    }

    @Test fun cancelledInitializationDoesNotPublishFailureOrScheduleRetry() = runTest {
        var created = 0; val states = mutableListOf<AutomaticDiagnosticState>()
        val owner = AutomaticDiagnosticInitialization(backgroundScope,
            { created++; throw CancellationException() }, {}, {}, { states.add(it) }, { 0 }, { null })
        owner.start(); runCurrent(); advanceTimeBy(120_000); runCurrent()
        assertEquals(1, created); assertTrue(states.isEmpty())
    }

    @Test fun invalidResourcesWaitForManualRetryInsteadOfPolling() = runTest {
        val reports = reports(Store(), Transport(), StandardTestDispatcher(testScheduler))
        var valid = false; var created = 0; var loaded = 0
        var state = AutomaticDiagnosticState()
        val owner = AutomaticDiagnosticInitialization(backgroundScope,
            { created++; check(valid) { "Invalid packaged configuration" }; reports }, {}, { loaded++ }, { state = it }, { 0 }, { null })
        owner.start(); runCurrent(); advanceTimeBy(180_000); runCurrent()
        assertEquals(1, created); assertTrue(state.saveFailed); assertFalse(state.status.available)
        valid = true; owner.retry(); runCurrent()
        assertEquals(2, created); assertEquals(1, loaded); assertFalse(state.saveFailed)
        assertTrue(state.status.available); assertFalse(state.status.enabled)
        reports.close()
    }

    @Test fun offDuringBlockedLoadRetiresAttemptWithoutRestoringGrantAndAllowsFreshChoice() = runBlocking {
        val store = Store(); val transport = Transport()
        val oldEpoch = reports(store, transport, Dispatchers.IO).let {
            it.setConsent(true); val epoch = it.recoveryContext!!.epoch; it.close(); epoch
        }
        val entered = CompletableDeferred<Unit>(); val released = CountDownLatch(1)
        store.beforeOutboxRead = {
            entered.complete(Unit)
            check(released.await(5, TimeUnit.SECONDS)) { "Fixture did not release the controlled read" }
        }
        val reports = reports(store, transport, Dispatchers.IO)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val revision = AtomicLong(); val requested = AtomicReference<Boolean?>(null)
        val loaded = CompletableDeferred<Unit>(); val published = mutableListOf<AutomaticDiagnosticState>()
        val owner = AutomaticDiagnosticInitialization(scope, { reports }, {}, { loaded.complete(Unit) },
            { synchronized(published) { published.add(it) } }, revision::get, requested::get)
        try {
            owner.start(); withTimeout(5_000) { entered.await() }
            revision.incrementAndGet(); requested.set(false); owner.cancel()
            val withdrawal = async(start = CoroutineStart.UNDISPATCHED) { reports.setConsent(false) }
            assertFalse(reports.capture(DiagnosticInput("generation_failed")))
            released.countDown(); withTimeout(5_000) { withdrawal.await() }
            store.beforeOutboxRead = null
            owner.retry(); withTimeout(5_000) { loaded.await() }
            assertFalse(reports.status.enabled); assertNull(reports.recoveryContext)
            assertTrue(synchronized(published) { published.none { it.status.enabled } })
            assertEquals(0, transport.sends)
            reports.setConsent(true)
            assertNotEquals(oldEpoch, reports.recoveryContext!!.epoch)
            assertTrue(reports.capture(DiagnosticInput("generation_failed")))
            reports.flush(); assertEquals(1, transport.sends)
        } finally {
            released.countDown(); owner.cancel(); scope.cancel(); reports.close()
        }
    }

    @Test fun singletonBlocksAllIntakeAndGrantObservationsUntilOutboxReadCompletes() = runBlocking {
        val store = Store(); val transport = Transport()
        val epoch = reports(store, transport, Dispatchers.IO).let {
            it.setConsent(true); val value = it.recoveryContext!!.epoch; it.close(); value
        }
        val entered = CompletableDeferred<Unit>(); val released = CountDownLatch(1)
        store.beforeOutboxRead = {
            entered.complete(Unit)
            check(released.await(5, TimeUnit.SECONDS))
        }
        val reports = reports(store, transport, Dispatchers.IO)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val reporterField = AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply { isAccessible = true }
        val ownerField = AutomaticDiagnostics::class.java.getDeclaredField("initialization").apply { isAccessible = true }
        val previousReporter = reporterField.get(AutomaticDiagnostics); val previousOwner = ownerField.get(AutomaticDiagnostics)
        val ready = CompletableDeferred<Unit>()
        val owner = AutomaticDiagnosticInitialization(scope, { reports }, { reporterField.set(AutomaticDiagnostics, it) },
            {}, { if (it.ready && !it.saveFailed) ready.complete(Unit) }, { AutomaticDiagnostics.observationRevision }, { null })
        try {
            ownerField.set(AutomaticDiagnostics, owner)
            owner.start(); withTimeout(5_000) { entered.await() }
            assertTrue("Exercise the core's partially loaded grant", reports.status.enabled)
            val observation = AutomaticDiagnostics.observeConsent()
            AutomaticDiagnostics.captureFailure(IOException("private"), "generation")
            AutomaticDiagnostics.capture(DiagnosticInput("download_failed", "download"))
            AutomaticDiagnostics.captureObservedFailure(IOException("private"),
                CompanionDiagnosticObservation(AutomaticDiagnostics.observationRevision, epoch))
            released.countDown(); withTimeout(5_000) { ready.await() }; reports.flush()
            assertNull("Pending storage cannot grant ownership to a new task", observation)
            assertEquals("No pending-load failure may enter the durable queue", 0, transport.sends)
            assertNotNull(AutomaticDiagnostics.observeConsent())
            AutomaticDiagnostics.captureFailure(IOException("fresh"), "generation")
            reports.flush(); assertEquals(1, transport.sends)
        } finally {
            released.countDown(); owner.cancel(); scope.cancel()
            reporterField.set(AutomaticDiagnostics, previousReporter); ownerField.set(AutomaticDiagnostics, previousOwner)
            reports.close()
        }
    }

    @Test fun offDoesNotWaitForBinderCallbackAndStaleLoadCannotPublishEnabled() = runBlocking {
        val store = Store(); val transport = Transport()
        reports(store, transport, Dispatchers.IO).let { it.setConsent(true); it.close() }
        val reports = reports(store, transport, Dispatchers.IO)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val entered = CompletableDeferred<Unit>(); val released = CountDownLatch(1)
        val exited = CompletableDeferred<Unit>(); val states = mutableListOf<AutomaticDiagnosticState>()
        val reporterField = AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply { isAccessible = true }
        val ownerField = AutomaticDiagnostics::class.java.getDeclaredField("initialization").apply { isAccessible = true }
        val previousReporter = reporterField.get(AutomaticDiagnostics); val previousOwner = ownerField.get(AutomaticDiagnostics)
        val owner = AutomaticDiagnosticInitialization(scope, { reports }, { reporterField.set(AutomaticDiagnostics, it) }, {
            entered.complete(Unit)
            try { check(released.await(5, TimeUnit.SECONDS)) } finally { exited.complete(Unit) }
        }, { synchronized(states) { states.add(it) } }, { AutomaticDiagnostics.observationRevision }, { null })
        try {
            ownerField.set(AutomaticDiagnostics, owner)
            owner.start(); withTimeout(5_000) { entered.await() }
            // A separate caller makes the regression fail with a bounded timeout
            // if OFF still waits for the callback's lock before core invalidation.
            withContext(Dispatchers.Default) { withTimeout(1_000) {
                val off = async { AutomaticDiagnostics.setConsent(false) }
                off.await()
            } }
            assertFalse("OFF invalidates the core before returning", reports.status.enabled)
            assertFalse(reports.capture(DiagnosticInput("generation_failed")))
            released.countDown(); withTimeout(5_000) { exited.await() }
            assertTrue(synchronized(states) { states.none { it.status.enabled } })
            assertEquals(0, transport.sends)
        } finally {
            released.countDown(); owner.cancel(); scope.cancel()
            reporterField.set(AutomaticDiagnostics, previousReporter); ownerField.set(AutomaticDiagnostics, previousOwner)
            reports.close()
        }
    }

    @Test fun killedStartupWithoutReporterStillRetriesPendingOffChoice() {
        fun field(name: String) = AutomaticDiagnostics::class.java.getDeclaredField(name).apply { isAccessible = true }
        val reporter = field("reporter"); val owner = field("initialization")
        val requested = field("requestedConsent"); val declined = field("declinedThisRun")
        val choices = field("choices").get(AutomaticDiagnostics) as AtomicLong
        val oldReporter = reporter.get(AutomaticDiagnostics); val oldOwner = owner.get(AutomaticDiagnostics)
        val oldRequested = requested.get(AutomaticDiagnostics); val oldDeclined = declined.get(AutomaticDiagnostics)
        val oldChoice = choices.get()
        try {
            reporter.set(AutomaticDiagnostics, null); owner.set(AutomaticDiagnostics, null)
            requested.set(AutomaticDiagnostics, false)
            AutomaticDiagnostics.retryConsent()
            assertEquals("Retry still enters the explicit OFF cleanup path without a reporter", oldChoice + 1, choices.get())
            assertEquals(false, requested.get(AutomaticDiagnostics))
        } finally {
            reporter.set(AutomaticDiagnostics, oldReporter); owner.set(AutomaticDiagnostics, oldOwner)
            requested.set(AutomaticDiagnostics, oldRequested); declined.set(AutomaticDiagnostics, oldDeclined)
            choices.set(oldChoice)
        }
    }

    @Test fun normalCancellationPromptsAndDeniedAuthenticationAreNotFailureReports() {
        assertNull(automaticDiagnosticInput(CancellationException("private content")))
        for (key in listOf("mobile.local.licence_required", "mobile.local.model_required", "mobile.local.wifi_required",
            "mobile.android.operation_busy", "mobile.permissions.denied", "mobile.connect.error_session_expired")) {
            assertNull(key, automaticDiagnosticInput(ClientFailure(key)))
        }
        assertNull(automaticDiagnosticInput(ClientFailure("private-key", mapOf("secret" to "do not send"), 401)))
    }

    @Test fun technicalFailuresUseClosedLabelsWithoutMessageOrParameters() {
        val failure = automaticDiagnosticInput(ClientFailure("mobile.local.runtime_failed", mapOf("token" to "private")))!!
        assertEquals("engine_failed", failure.category)
        assertEquals("ENGINE_DEPENDENCY_MISSING", failure.errorCode)
        assertEquals("engine", failure.stage)
        assertFalse(failure.toString().contains("private"))
        assertEquals("generation_failed", automaticDiagnosticInput(ClientFailure("mobile.local.inference_failed"))!!.category)
        assertEquals("download_failed", automaticDiagnosticInput(ClientFailure("mobile.local.download_failed"))!!.category)
        assertEquals("incompatibility", automaticDiagnosticInput(ClientFailure("mobile.local.model_incompatible"))!!.category)
    }

    @Test fun acceptsOnlyOurVersionedBoundedProcessSummary() {
        val epoch = "a".repeat(32)
        val good = """{"v":1,"e":"$epoch","a":"0.5.9-beta.2"}"""
        assertEquals(epoch to "0.5.9-beta.2", parseDiagnosticExitSummary(good.toByteArray()))
        assertNull(parseDiagnosticExitSummary(null))
        assertNull(parseDiagnosticExitSummary(ByteArray(129)))
        assertNull(parseDiagnosticExitSummary(good.replace("\"v\":1", "\"v\":\"1\"").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("$epoch", "private user path").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("0.5.9-beta.2", "user@example.com").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("\"a\":", "\"extra\":true,\"a\":").toByteArray()))
    }
}
