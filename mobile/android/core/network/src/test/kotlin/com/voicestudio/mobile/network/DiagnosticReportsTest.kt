package com.voicestudio.mobile.network

import java.io.File
import java.io.IOException
import java.util.concurrent.CancellationException
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

@OptIn(ExperimentalCoroutinesApi::class)
class DiagnosticReportsTest {
    private class Store : DiagnosticStorage {
        val files = mutableMapOf<String,ByteArray>()
        var reads=0;var writes=0;var failWrite=false;var failRemove=false
        var failOutboxWrite=false;var outboxWriteAttempts=0
        val blockedWrites=mutableSetOf<String>();val blockedRemoves=mutableSetOf<String>()
        var unreadableRevocation=false;var revocationWriteAttempts=0;var unreadableOutbox=false
        var revocationReads=0;var failRevocationReadAt:Int?=null
        var unreadableConsent=false;var consentReads=0;var outboxReads=0
        override fun read(name:String,maxBytes:Int):ByteArray? {
            reads++
            if(name=="consent.json"){consentReads++;if(unreadableConsent)throw IOException("controlled consent EIO")}
            if(name=="outbox.json"){outboxReads++;if(unreadableOutbox)throw IOException("controlled outbox EIO")}
            if(name=="revoked.json") {
                revocationReads++
                if(unreadableRevocation || revocationReads==failRevocationReadAt)throw IOException("private path")
            }
            return files[name]?.clone()?.also { require(it.size<=maxBytes) }
        }
        override fun write(name:String,bytes:ByteArray) {
            if(name=="outbox.json")outboxWriteAttempts++
            if(name=="revoked.json")revocationWriteAttempts++
            if(failWrite || name in blockedWrites || name=="outbox.json" && failOutboxWrite)throw IOException("private path")
            writes++;files[name]=bytes.clone()
        }
        override fun remove(name:String) { if(failRemove || name in blockedRemoves)throw IOException("private path");files.remove(name) }
        fun json(name:String)=Json.parseToJsonElement(files.getValue(name).toString(Charsets.UTF_8))
    }
    private class Clock : DiagnosticClock {
        var now=1_794_640_000_000L
        override fun nowMs()=now
        override suspend fun sleep(ms:Long) { yield() }
    }
    private class ScheduledClock(private val scheduler:TestCoroutineScheduler) : DiagnosticClock {
        override fun nowMs()=1_794_640_000_000L+scheduler.currentTime
        override suspend fun sleep(ms:Long) { delay(ms) }
    }
    private class Transport : DiagnosticTransport {
        val bodies=mutableListOf<JsonObject>();var code=503;var cancelled=0
        var hold=false;var continuation:CancellableContinuation<Int>?=null
        override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int {
            if(!allowed())throw CancellationException()
            bodies.add(Json.parseToJsonElement(body.toString(Charsets.UTF_8)).jsonObject)
            return if(hold)suspendCancellableCoroutine { continuation=it } else code
        }
        override fun cancelAll() { cancelled++;continuation?.cancel();continuation=null }
    }
    private class CloseOrderFileStore(directory:File) : DiagnosticStorage {
        private val disk=FileDiagnosticStorage(directory)
        val entered=java.util.concurrent.CountDownLatch(1)
        val release=java.util.concurrent.CountDownLatch(1)
        val armed=java.util.concurrent.atomic.AtomicBoolean(false)
        val denyRevocation=java.util.concurrent.atomic.AtomicBoolean(false)
        val revocationWrites=java.util.concurrent.atomic.AtomicInteger(0)
        var blockName="outbox.json"
        override fun read(name:String,maxBytes:Int)=disk.read(name,maxBytes)
        override fun write(name:String,bytes:ByteArray) {
            if(name=="revoked.json")revocationWrites.incrementAndGet()
            if(denyRevocation.get() && name in setOf("consent.json","revoked.json"))throw IOException("Controlled unavailable revocation")
            disk.write(name,bytes)
            if(name==blockName && armed.compareAndSet(true,false)) {
                entered.countDown();check(release.await(10,java.util.concurrent.TimeUnit.SECONDS))
            }
        }
        override fun remove(name:String) {
            if(denyRevocation.get() && name=="consent.json")throw IOException("Controlled locked grant")
            disk.remove(name)
        }
    }
    private fun policy():DiagnosticPolicy {
        var root=File(System.getProperty("user.dir"))
        repeat(10) {
            val file=File(root,"config/diagnostics.json")
            if(file.isFile)return DiagnosticPolicy(file.readText(Charsets.UTF_8))
            root=root.parentFile?:root
        }
        error("Canonical diagnostic policy missing")
    }
    private fun makeReporter(store:DiagnosticStorage,transport:DiagnosticTransport,clock:DiagnosticClock,dispatcher:CoroutineDispatcher,appVersion:String="0.5.9-beta.2",buildCommit:String="a".repeat(40),disabled:()->Boolean={false})=DiagnosticReports(
        File("unused-test-store"),policy(),DiagnosticDestination("localtest123","http://127.0.0.1:1","https://app.test.invalid"),appVersion,
        buildCommit=buildCommit,transport=transport,clock=clock,storage=store,dispatcher=dispatcher,disabled=disabled)
    private fun event(errorType:String="TypeError")=DiagnosticInput("generation_failed","generation",errorType=errorType)

    private fun closeAfterBlockedFlushRetainsTheLatestChoice(withdraw:Boolean,failRevocation:Boolean=false)=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-close-choice-").toFile()
        val storage=CloseOrderFileStore(directory);val transport=Transport()
        val reporter=makeReporter(storage,transport,Clock(),Dispatchers.IO)
        try {
            reporter.setConsent(true)
            val grant=storage.read("consent.json",4096)!!
            storage.armed.set(true);assertTrue(reporter.capture(event()))
            assertTrue("An actual disk-backed flush must own the worker",storage.entered.await(10,java.util.concurrent.TimeUnit.SECONDS))
            storage.denyRevocation.set(failRevocation)
            val off=if(withdraw)async(start=CoroutineStart.UNDISPATCHED){runCatching{reporter.setConsent(false)}}else null
            if(off!=null)assertFalse("Withdrawal must be waiting for the blocked worker",off.isCompleted)
            val closing=async(start=CoroutineStart.UNDISPATCHED){reporter.close()}
            assertFalse(reporter.status.enabled);assertFalse(reporter.capture(event()))
            assertFalse(closing.isCompleted)
            storage.release.countDown();closing.await();val offResult=off?.await()
            assertTrue(transport.bodies.isEmpty())
            if(failRevocation) {
                assertTrue("A queued OFF cannot acknowledge a failed final save",offResult!!.isFailure)
                assertTrue(reporter.status.consentSaveFailed);assertFalse(reporter.capture(event()))
                return@runTest
            }
            if(!withdraw)assertArrayEquals("Ordinary close retains a saved positive choice",grant,storage.read("consent.json",4096))
            val nextTransport=Transport().apply{code=201}
            val next=makeReporter(FileDiagnosticStorage(directory),nextTransport,Clock(),Dispatchers.IO)
            try {
                next.load();next.flush()
                assertEquals("Close must not discard a pending withdrawal",!withdraw,next.status.enabled)
                assertEquals(if(withdraw)0 else 1,nextTransport.bodies.size)
                if(withdraw)assertFalse(next.capture(event()))
            }finally{next.close()}
        }finally{storage.release.countDown();storage.denyRevocation.set(false);reporter.close();directory.deleteRecursively()}
    }
    @Test fun queuedOffCompletesBeforeCloseCanPersistAnOldGrant()=closeAfterBlockedFlushRetainsTheLatestChoice(true)
    @Test fun ordinaryOnCloseRetainsTheGrantedQueue()=closeAfterBlockedFlushRetainsTheLatestChoice(false)
    @Test fun queuedOffCloseFailureIsNotAcknowledgedAsSaved()=closeAfterBlockedFlushRetainsTheLatestChoice(true,true)

    @Test fun closeCannotPublishAnOnChoiceBlockedAfterItsRename()=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-close-on-").toFile()
        val storage=CloseOrderFileStore(directory).apply{blockName="consent.json";armed.set(true)}
        val transport=Transport();val reporter=makeReporter(storage,transport,Clock(),Dispatchers.IO)
        try {
            val on=async(Dispatchers.IO){runCatching{reporter.setConsent(true)}}
            assertTrue(storage.entered.await(10,java.util.concurrent.TimeUnit.SECONDS))
            val closing=async(start=CoroutineStart.UNDISPATCHED){reporter.close()}
            assertFalse(reporter.status.enabled);assertFalse(reporter.capture(event()))
            storage.release.countDown();closing.await();on.await()
            val next=makeReporter(FileDiagnosticStorage(directory),transport,Clock(),Dispatchers.IO)
            try{next.load();next.flush();assertFalse("A closed pending ON must not activate after restart",next.status.enabled);assertTrue(transport.bodies.isEmpty())}
            finally{next.close()}
        }finally{storage.release.countDown();reporter.close();directory.deleteRecursively()}
    }

    @Test fun closeRetriesPendingOffBeforeItsStorageCooldownExpires()=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-close-retry-").toFile()
        val storage=CloseOrderFileStore(directory);val transport=Transport();val clock=Clock()
        val reporter=makeReporter(storage,transport,clock,Dispatchers.IO)
        try {
            reporter.setConsent(true);storage.denyRevocation.set(true)
            try{reporter.setConsent(false);fail("Expected failed durable withdrawal")}catch(_:IOException){}
            val attempts=storage.revocationWrites.get()
            assertTrue(reporter.status.consentSaveFailed);assertFalse(reporter.capture(event()))
            storage.denyRevocation.set(false)
            // No clock advance: close must not leave a previous enabled grant
            // until a retry timer which it is about to cancel could fire.
            reporter.close()
            assertTrue(storage.revocationWrites.get()>attempts)
            val next=makeReporter(FileDiagnosticStorage(directory),transport,clock,Dispatchers.IO)
            try{next.load();next.flush();assertFalse(next.status.enabled);assertTrue(transport.bodies.isEmpty())}
            finally{next.close()}
        }finally{storage.denyRevocation.set(false);reporter.close();directory.deleteRecursively()}
    }

    private fun corruptFileOutboxRecoversWithoutChangingGrant(oversized:Boolean)=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-corrupt-outbox-").toFile()
        val storage=FileDiagnosticStorage(directory);val dispatcher=StandardTestDispatcher(testScheduler)
        val clock=ScheduledClock(testScheduler)
        val first=makeReporter(storage,Transport(),clock,dispatcher)
        first.setConsent(true);first.close()
        val grant=storage.read("consent.json",4096)!!
        val bytes=if(oversized)ByteArray(policy().limit("queue_bytes",524288)+1){32}else "{invalid".toByteArray()
        File(directory,"outbox.json").writeBytes(bytes)
        if(oversized) {
            val read=runCatching { storage.read("outbox.json",policy().limit("queue_bytes",524288)) }
            assertTrue("The actual file reader must enforce its byte bound",read.isFailure)
        }
        val transport=Transport().apply{code=201}
        val next=makeReporter(storage,transport,clock,dispatcher)
        try {
            val loaded=runCatching {next.load()}
            assertTrue("Proven corrupt outbox must recover without an endless read retry",loaded.isSuccess)
            assertTrue(next.status.enabled);assertEquals(0,next.status.pendingCount)
            assertFalse(File(directory,"outbox.json").exists())
            assertArrayEquals("Corruption recovery must not renew consent",grant,storage.read("consent.json",4096))
            assertTrue(transport.bodies.isEmpty())
            assertTrue(next.capture(event()));runCurrent()
            assertEquals("Fresh failures can report after corrupt storage is retired",1,transport.bodies.size)
            assertArrayEquals(grant,storage.read("consent.json",4096))
        }finally{next.close();directory.deleteRecursively()}
    }
    @Test fun oversizedFileOutboxRecoversWithoutChangingGrant()=corruptFileOutboxRecoversWithoutChangingGrant(true)
    @Test fun malformedFileOutboxStillRecoversWithoutChangingGrant()=corruptFileOutboxRecoversWithoutChangingGrant(false)

    @Test fun unreadOutboxPreservesReportsAndAutomaticallyRetriesSameGrantOncePerMinute()=runTest {
        val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();first.close()
        val saved=store.files.getValue("outbox.json").clone();val grant=store.files.getValue("consent.json").clone()
        val original=store.json("outbox.json").jsonArray.single().jsonObject
        store.unreadableOutbox=true
        val transport=Transport().apply {code=201};val next=makeReporter(store,transport,clock,dispatcher)
        try {
            try {next.load()}catch(_:IOException){}
            assertFalse(next.status.enabled);assertFalse(next.capture(event()))
            assertArrayEquals(saved,store.files.getValue("outbox.json"));assertArrayEquals(grant,store.files.getValue("consent.json"))
            val reads=store.outboxReads
            advanceTimeBy(59_999);runCurrent();assertEquals(reads,store.outboxReads)
            advanceTimeBy(1);runCurrent();assertEquals("One failed background retry per minute",reads+1,store.outboxReads)
            assertTrue(transport.bodies.isEmpty())
            store.unreadableOutbox=false;advanceTimeBy(59_999);runCurrent();assertTrue(transport.bodies.isEmpty())
            advanceTimeBy(1);runCurrent()
            assertTrue(next.status.enabled);assertEquals(1,transport.bodies.size)
            assertEquals(original["id"],transport.bodies.single()["id"])
            assertEquals(original["timestamp_ms"],transport.bodies.single()["ts"])
            assertArrayEquals(grant,store.files.getValue("consent.json"))
        }finally{next.close()}
    }

    @Test fun unreadOutboxRecoveryCannotCrossWithdrawalNewGrantOrClose()=runTest {
        for(control in listOf("off","regrant","close")) {
            val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
            val first=makeReporter(store,Transport(),clock,dispatcher)
            first.setConsent(true);first.capture(event());runCurrent();first.close()
            store.unreadableOutbox=true
            val transport=Transport().apply {code=201};val next=makeReporter(store,transport,clock,dispatcher)
            try {
                try {next.load()}catch(_:IOException){}
                if(control=="close")next.close() else next.setConsent(control=="regrant")
                store.unreadableOutbox=false;advanceTimeBy(60_000);runCurrent()
                assertTrue(transport.bodies.isEmpty())
                if(control=="regrant") {
                    assertTrue(next.capture(event("ValueError")));runCurrent();assertEquals(1,transport.bodies.size)
                } else assertFalse(next.capture(event()))
            }finally{next.close()}
        }
    }

    @Test fun unreadRevocationPreservesGrantAndOutboxUntilItsBoundedReread()=runTest {
        val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();first.close()
        val saved=store.files.getValue("outbox.json").clone();val grant=store.files.getValue("consent.json").clone()
        val original=store.json("outbox.json").jsonArray.single().jsonObject
        store.unreadableRevocation=true
        val transport=Transport().apply {code=201};val next=makeReporter(store,transport,clock,dispatcher)
        try {
            try {next.load()}catch(_:IOException){}
            assertFalse(next.status.enabled);assertFalse(next.capture(event()))
            assertArrayEquals("An unknown marker is not withdrawal",saved,store.files["outbox.json"])
            assertArrayEquals(grant,store.files["consent.json"]);assertFalse(store.files.containsKey("revoked.json"));assertTrue(transport.bodies.isEmpty())
            store.unreadableRevocation=false;advanceTimeBy(59999);runCurrent();assertTrue(transport.bodies.isEmpty())
            advanceTimeBy(1);runCurrent();assertEquals(1,transport.bodies.size)
            assertEquals(original["id"],transport.bodies.single()["id"]);assertArrayEquals(grant,store.files["consent.json"]);assertTrue(next.status.enabled)
        }finally{store.unreadableRevocation=false;next.close()}
    }
    @Test fun bootstrapHardKillCleanupRetriesWithoutReporterAndHonorsCancellation()=runTest {
        for(cancel in listOf(false,true)) {
            val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
            val first=makeReporter(store,Transport(),clock,dispatcher)
            first.setConsent(true);first.close()
            store.blockedWrites.addAll(listOf("consent.json","revoked.json"));store.blockedRemoves.add("consent.json")
            assertFalse(DiagnosticReports.revokeStoredConsent(File("unused"),store))
            val attempts=store.revocationWriteAttempts
            val results=mutableListOf<Boolean>()
            val retry=launch {DiagnosticReports.retryStoredRevocation(File("unused"),store,clock) {results.add(it)}}
            runCurrent();advanceTimeBy(59999);runCurrent();assertEquals(attempts,store.revocationWriteAttempts)
            store.blockedWrites.clear();store.blockedRemoves.clear()
            if(cancel)retry.cancel()
            advanceTimeBy(1);runCurrent();retry.join()
            if(cancel) {assertTrue(results.isEmpty());assertEquals(attempts,store.revocationWriteAttempts)}
            else {
                assertEquals(listOf(true),results);assertTrue(store.files.containsKey("revoked.json"))
                val next=makeReporter(store,Transport(),clock,dispatcher);next.load();assertFalse(next.status.enabled);assertFalse(next.capture(event()));next.close()
            }
        }
    }
    @Test fun unreadConsentPreservesReportsAndSchedulesSameGrantRecovery()=runTest {
        val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();first.close()
        val saved=store.files.getValue("outbox.json").clone();val grant=store.files.getValue("consent.json").clone()
        val original=store.json("outbox.json").jsonArray.single().jsonObject
        store.unreadableConsent=true
        val transport=Transport().apply {code=201};val next=makeReporter(store,transport,clock,dispatcher)
        try {
            try {next.load()}catch(_:IOException){}
            assertFalse(next.status.enabled);assertFalse(next.status.optedIn);assertFalse(next.capture(event()))
            assertArrayEquals(saved,store.files.getValue("outbox.json"));assertArrayEquals(grant,store.files.getValue("consent.json"))
            assertTrue(transport.bodies.isEmpty())
            val reads=store.consentReads
            repeat(20){try {next.load()}catch(_:IOException){}}
            assertEquals(reads,store.consentReads)
            store.unreadableConsent=false;advanceTimeBy(59999);runCurrent();assertTrue(transport.bodies.isEmpty())
            advanceTimeBy(1);runCurrent()
            assertEquals(1,transport.bodies.size)
            assertEquals(original["id"],transport.bodies.single()["id"]);assertEquals(original["timestamp_ms"],transport.bodies.single()["ts"])
            assertArrayEquals(grant,store.files.getValue("consent.json"))
            assertTrue(next.status.enabled);assertTrue(next.capture(event("ValueError")));runCurrent();assertEquals(2,transport.bodies.size)
        }finally{next.close()}
    }

    @Test fun unreadConsentRetryCannotCrossNewChoiceOrClose()=runTest {
        for(control in listOf("off","regrant","close")) {
            val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
            val first=makeReporter(store,Transport(),clock,dispatcher)
            first.setConsent(true);first.capture(event());runCurrent();first.close()
            val grant=store.files.getValue("consent.json").clone();store.unreadableConsent=true
            val transport=Transport().apply {code=201};val next=makeReporter(store,transport,clock,dispatcher)
            try {next.load()}catch(_:IOException){}
            if(control=="close")next.close() else next.setConsent(control=="regrant")
            store.unreadableConsent=false;advanceTimeBy(60000);runCurrent()
            assertTrue(transport.bodies.isEmpty())
            if(control=="regrant") {
                assertFalse(grant.contentEquals(store.files.getValue("consent.json")))
                assertTrue(next.capture(event()));runCurrent();assertEquals(1,transport.bodies.size)
            }else assertFalse(next.capture(event()))
            next.close()
        }
    }

    @Test fun absentOrInvalidConsentIsKnownOffWithoutRetry()=runTest {
        for(invalid in listOf(false,true)) {
            val store=Store();val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
            store.files["outbox.json"]="[]".toByteArray()
            if(invalid)store.files["consent.json"]="{invalid".toByteArray()
            val next=makeReporter(store,Transport(),clock,dispatcher)
            next.load();val reads=store.consentReads
            assertFalse(next.status.enabled);assertFalse(next.capture(event()));assertFalse(store.files.containsKey("outbox.json"))
            advanceTimeBy(60000);runCurrent();assertEquals(reads,store.consentReads)
            if(!invalid)assertFalse(store.files.containsKey("consent.json"))
            next.close()
        }
    }

    @Test fun closeRetainsUnreadOutboxWhenTheReadStillFails()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();first.close()
        val original=store.files.getValue("outbox.json").clone()
        store.unreadableOutbox=true
        val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher)
        try {next.load();fail("Expected the controlled outbox read failure")}catch(_:IOException){}
        assertFalse(next.status.enabled)
        next.close()
        assertTrue("Closing must not delete an unread valid outbox",store.files.containsKey("outbox.json"))
        assertArrayEquals(original,store.files.getValue("outbox.json"))
        assertTrue(transport.bodies.isEmpty())
    }

    @Test fun closeRestoresUnreadOutboxBeforeFinalPublicationWhenStorageRecovers()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();first.close()
        val original=store.files.getValue("outbox.json").clone()
        store.unreadableOutbox=true
        val next=makeReporter(store,Transport(),clock,dispatcher)
        try {next.load();fail("Expected the controlled outbox read failure")}catch(_:IOException){}
        store.unreadableOutbox=false;next.close()
        assertTrue("Closing must restore accepted records before publication",store.files.containsKey("outbox.json"))
        assertArrayEquals(original,store.files.getValue("outbox.json"))
        val transport=Transport().apply {code=201};val reopened=makeReporter(store,transport,clock,dispatcher)
        try {
            reopened.load();runCurrent()
            assertEquals(1,transport.bodies.size)
            assertEquals(Json.parseToJsonElement(original.toString(Charsets.UTF_8)).jsonArray.single().jsonObject["id"],transport.bodies.single()["id"])
            assertFalse(store.files.containsKey("outbox.json"))
        }finally{reopened.close()}
    }

    @Test fun independentDefaultOffAndNoCaptureIO()=runTest {
        val store=Store();val transport=Transport();val clock=Clock();val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        assertFalse(reporter.capture(event(),IllegalStateException("secret transcript")))
        assertEquals(0,store.reads);assertEquals(0,store.writes)
        reporter.load();assertFalse(reporter.status.enabled);assertTrue(store.files.isEmpty());assertTrue(transport.bodies.isEmpty())
        reporter.setConsent(true);val reads=store.reads;val writes=store.writes
        assertTrue(reporter.capture(event()));assertEquals(reads,store.reads);assertEquals(writes,store.writes)
        runCurrent();assertTrue(transport.bodies.isNotEmpty());reporter.close()
    }
    @Test fun projectsOnlyClosedMetadataAndNeverReadsOrRetainsThrowable()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        class SecretError(val retained:ByteArray):IllegalArgumentException() {
            override val message:String get()=error("Message read")
            override fun toString():String=error("Throwable rendered")
        }
        val payload=ByteArray(1024*1024);val error=SecretError(payload)
        assertTrue(reporter.capture(DiagnosticInput("hf_secret","/home/private",errorType="user voice",engineID="secret model",frames=listOf(DiagnosticFrame("/home/secret.kt",1)),reason="private"),error))
        // Inspect retained work before the dispatcher runs, rather than asking
        // the JVM to collect on demand. Pending work must contain closed values.
        val queued=DiagnosticReports::class.java.getDeclaredField("intake").apply {isAccessible=true}.get(reporter) as ArrayDeque<*>
        val pending=queued.single()!!
        val retained=pending.javaClass.declaredFields.filterNot {java.lang.reflect.Modifier.isStatic(it.modifiers)}.map {it.isAccessible=true;it.get(pending)}
        assertTrue(retained.none {it===error || it===payload || it is Throwable || it is DiagnosticInput})
        assertTrue(retained.all {it==null || it is String || it is Long || it is JsonObject})
        runCurrent()
        val body=store.json("outbox.json").jsonArray.single().jsonObject
        val props=body["props"]!!.jsonObject
        assertEquals("operation_failed",props["category"]!!.jsonPrimitive.content)
        assertEquals("ValueError",props["error_type"]!!.jsonPrimitive.content)
        assertEquals("unknown",props["engine_id"]!!.jsonPrimitive.content)
        assertFalse(body.toString().contains("secret"));assertFalse(props.containsKey("frames"));reporter.close()
    }
    @Test fun offlineReplayUsesStableIdentityTimestampAndSanitizesAgain()=runTest {
        val store=Store();val first=Transport();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,first,clock,dispatcher);reporter.setConsent(true);reporter.capture(event());runCurrent()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject;reporter.close()
        val nextTransport=Transport().also { it.code=201 };val next=makeReporter(store,nextTransport,clock,dispatcher)
        next.load();runCurrent()
        assertEquals(saved["id"],nextTransport.bodies.single()["id"]);assertEquals(saved["timestamp_ms"],nextTransport.bodies.single()["ts"])
        assertFalse(store.files.containsKey("outbox.json"));next.close()
    }
    @Test fun withdrawalCancelsInFlightPurgesAndRotatesEpoch()=runTest {
        val store=Store();val transport=Transport().also { it.hold=true };val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);val old=reporter.recoveryContext!!.epoch
        reporter.capture(event());runCurrent();assertEquals(1,transport.bodies.size)
        reporter.setConsent(false);runCurrent()
        assertFalse(reporter.status.enabled);assertFalse(store.files.containsKey("outbox.json"));assertNull(reporter.recoveryContext)
        assertFalse(reporter.capture(event()));assertEquals(1,transport.bodies.size)
        reporter.setConsent(true);assertNotEquals(old,reporter.recoveryContext!!.epoch);reporter.close()
    }
    @Test fun recoveredCrashChecksEpochTimeVersionAndStableTechnicalKey()=runTest {
        val store=Store();val transport=Transport().also { it.code=201 };val clock=Clock();val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);val context=reporter.recoveryContext!!;clock.now+=1000
        val input=DiagnosticInput("app_crash",reason="crashed");val key="android:${clock.now}:crashed"
        assertFalse(reporter.captureRecovered(input,context.enabledAtMs-1,context.epoch,"android:${context.enabledAtMs-1}:crashed","0.5.8-beta.1"))
        assertFalse(reporter.captureRecovered(input,clock.now,"f".repeat(32),key,"0.5.8-beta.1"))
        assertFalse(reporter.captureRecovered(input,clock.now,context.epoch,key,"0.5.8-secret-token"))
        repeat(2) { assertTrue(reporter.captureRecovered(input,clock.now,context.epoch,key,"0.5.8-beta.1"));runCurrent() }
        assertEquals(1,transport.bodies.map { it["id"] }.toSet().size)
        assertEquals("0.5.8-beta.1",transport.bodies.first()["props"]!!.jsonObject["app_version"]!!.jsonPrimitive.content)
        assertFalse(transport.bodies.first()["props"]!!.jsonObject.containsKey("build_commit"))
        assertTrue(reporter.captureRecovered(input.copy(reason="killed"),clock.now,context.epoch,"android:${clock.now}:killed","0.5.8b1"));runCurrent()
        assertEquals("0.5.8b1",transport.bodies.last()["props"]!!.jsonObject["app_version"]!!.jsonPrimitive.content)
        assertTrue(reporter.captureRecovered(input,clock.now,context.epoch,"android:${clock.now}:oom"));runCurrent()
        assertFalse(transport.bodies.last()["props"]!!.jsonObject.containsKey("app_version"));reporter.close()
    }
    @Test fun corruptExpiredAndLegacyConsentFailClosedForHistory()=runTest {
        val store=Store();val transport=Transport();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,transport,clock,dispatcher);reporter.setConsent(true);reporter.capture(event());runCurrent();reporter.close()
        val consent=store.json("consent.json").jsonObject;store.files["consent.json"]=JsonObject(consent-"enabled_at_ms").toString().toByteArray()
        val legacy=makeReporter(store,Transport(),clock,dispatcher);legacy.load();assertNull(legacy.recoveryContext);legacy.close()
        clock.now+=604800001L
        val expired=makeReporter(store,Transport(),clock,dispatcher);expired.load();runCurrent();assertFalse(store.files.containsKey("outbox.json"));expired.close()
        store.files["consent.json"]="{broken".toByteArray();store.files["outbox.json"]="[]".toByteArray()
        val broken=makeReporter(store,Transport(),clock,dispatcher);broken.load();assertFalse(broken.status.enabled);assertFalse(store.files.containsKey("outbox.json"));broken.close()
    }
    @Test fun boundedQueueAndCancellationControl()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        assertFalse(reporter.capture(event(),CancellationException("private")))
        repeat(100) { reporter.capture(DiagnosticInput("generation_failed",frames=listOf(DiagnosticFrame("backend/core/tasks.py",it+1)))) }
        assertEquals(36,reporter.status.droppedCount);runCurrent()
        val records=store.json("outbox.json").jsonArray;assertEquals(64,records.size);assertTrue(store.files.getValue("outbox.json").size<=524288)
        reporter.close()
    }
    @Test fun writeFailureAndLockedOutboxCannotRestoreOldConsent()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);reporter.capture(event());runCurrent()
        store.failRemove=true;reporter.setConsent(false);assertFalse(reporter.status.enabled)
        assertFalse(store.json("consent.json").jsonObject["enabled"]!!.jsonPrimitive.boolean)
        store.failRemove=false;reporter.setConsent(true);store.failWrite=true
        try { reporter.setConsent(false);fail("Expected unavailable consent storage") }catch(_:IOException){}
        assertFalse(reporter.capture(event()));store.failWrite=false;reporter.close()
    }
    @Test fun realLocalHTTPTransportHasNoCredentialsAndNeverFollowsRedirect()=runBlocking {
        val receiver=MockWebServer();receiver.start()
        try {
            val destination=DiagnosticDestination("localtest123",receiver.url("/").toString(),"https://app.test.invalid")
            val transport=OkHttpDiagnosticTransport()
            receiver.enqueue(MockResponse().setResponseCode(201))
            assertEquals(201,transport.send(destination,"{\"v\":1}".toByteArray()){true})
            val request=receiver.takeRequest();assertEquals("/api/public/v1/events",request.path)
            assertEquals("https://app.test.invalid",request.getHeader("Origin"));assertNull(request.getHeader("Cookie"));assertNull(request.getHeader("Authorization"));assertNull(request.getHeader("Referer"))
            receiver.enqueue(MockResponse().setResponseCode(302).addHeader("Location",receiver.url("/private")))
            assertEquals(302,transport.send(destination,"{}".toByteArray()){true});assertEquals(2,receiver.requestCount)
            transport.cancelAll()
        } finally { receiver.shutdown() }
    }
    @Test fun killSwitchMatchesExistingAnalyticsTruthiness() {
        for(value in listOf("1","true","YES"," ON ","True"))assertTrue(diagnosticKillSwitch(value))
        for(value in listOf("","0","false","off","private-value"))assertFalse(diagnosticKillSwitch(value))
        assertFalse(diagnosticKillSwitch(null))
    }

    @Test fun quotedConsentScalarsNeverBecomePermission()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.close()
        val original=store.json("consent.json").jsonObject
        val variants=listOf(
            original+("enabled" to JsonPrimitive("true")),
            original+("version" to JsonPrimitive("1")),
            original+("epoch" to JsonPrimitive(java.math.BigInteger("1".repeat(32)))),
            original+("enabled_at_ms" to JsonPrimitive(clock.now.toString())),
        )
        for(value in variants) {
            store.files["consent.json"]=JsonObject(value).toString().toByteArray()
            val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher)
            next.load();runCurrent();assertFalse(next.status.enabled);assertFalse(next.capture(event()));assertTrue(transport.bodies.isEmpty());next.close()
        }
    }
    @Test fun invalidRecoveryEnableTimeDoesNotInventHistory()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.close()
        val original=store.json("consent.json").jsonObject
        for(stamp in listOf(0L,clock.now+60001)) {
            store.files["consent.json"]=JsonObject(original+("enabled_at_ms" to JsonPrimitive(stamp))).toString().toByteArray()
            val next=makeReporter(store,Transport(),clock,dispatcher);next.load()
            assertTrue(next.status.enabled);assertNull(next.recoveryContext);next.close()
        }
    }
    @Test fun malformedRecordScalarTypesAreDiscardedBeforeNetwork()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.capture(event());runCurrent();first.close()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject
        val variants=listOf(saved+("version" to JsonPrimitive("1")),saved+("timestamp_ms" to JsonPrimitive(clock.now.toString())),saved+("id" to JsonPrimitive(java.math.BigInteger("1".repeat(32)))))
        for(record in variants) {
            store.files["outbox.json"]=JsonArray(listOf(JsonObject(record))).toString().toByteArray()
            val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher);next.load();runCurrent()
            assertTrue(transport.bodies.isEmpty());assertFalse(store.files.containsKey("outbox.json"));next.close()
        }
    }
    @Test fun supersededGrantCannotPublishDuringBlockedWithdrawal()=runBlocking {
        val firstEntered=java.util.concurrent.CountDownLatch(1);val secondEntered=java.util.concurrent.CountDownLatch(1)
        val releaseFirst=java.util.concurrent.CountDownLatch(1);val releaseSecond=java.util.concurrent.CountDownLatch(1)
        val writes=java.util.concurrent.atomic.AtomicInteger()
        val store=object:DiagnosticStorage {
            private val files=java.util.concurrent.ConcurrentHashMap<String,ByteArray>()
            override fun read(name:String,maxBytes:Int)=files[name]
            override fun remove(name:String){files.remove(name)}
            override fun write(name:String,bytes:ByteArray){
                if(name=="consent.json") {
                    when(writes.incrementAndGet()) {
                        1->{firstEntered.countDown();check(releaseFirst.await(5,java.util.concurrent.TimeUnit.SECONDS))}
                        2->{secondEntered.countDown();check(releaseSecond.await(5,java.util.concurrent.TimeUnit.SECONDS))}
                    }
                }
                files[name]=bytes
            }
        }
        val reporter=makeReporter(store,Transport(),Clock(),Dispatchers.IO)
        val grant=async(Dispatchers.IO){reporter.setConsent(true)}
        var withdrawal:Deferred<DiagnosticStatus>?=null
        try {
            assertTrue(firstEntered.await(5,java.util.concurrent.TimeUnit.SECONDS))
            withdrawal=async(start=CoroutineStart.UNDISPATCHED){reporter.setConsent(false)}
            releaseFirst.countDown();assertTrue(secondEntered.await(5,java.util.concurrent.TimeUnit.SECONDS))
            assertFalse(reporter.status.enabled);assertFalse(reporter.capture(event()))
        }finally{
            releaseFirst.countDown();releaseSecond.countDown();grant.await();withdrawal?.await();reporter.close()
        }
    }
    @Test fun failedRecordHasBoundedRetryEligibilityAcrossNewFailures()=runTest {
        val store=Store();val transport=Transport();val clock=Clock();val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);reporter.capture(event());runCurrent()
        val firstID=transport.bodies.first()["id"];assertEquals(3,transport.bodies.count{it["id"]==firstID})
        reporter.capture(DiagnosticInput("model_load_failed","model_load"));runCurrent()
        assertEquals(3,transport.bodies.count{it["id"]==firstID});reporter.close()
    }

    @Test fun failedWithdrawalCannotReloadAnOldEnabledGrant()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);assertTrue(first.status.enabled);store.failWrite=true
        try { first.setConsent(false);fail("Expected consent save failure") }catch(_:IOException){}
        assertFalse(first.status.enabled);first.close();store.failWrite=false
        val next=makeReporter(store,Transport(),clock,dispatcher);next.load()
        assertFalse(next.status.enabled);assertFalse(next.capture(event()));next.close()
    }

    @Test fun recoveryGroupsPreserveIdentityCountsAndVersionAcrossUpgrade()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true)
        val context=first.recoveryContext!!;clock.now+=1000;val key="android:${clock.now}:crashed"
        val input=DiagnosticInput("app_crash",reason="crashed",signal="SIGSEGV")
        assertTrue(first.captureRecovered(input,clock.now,context.epoch,key,"0.5.9-beta.2",3));runCurrent();first.close()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject
        val next=makeReporter(store,Transport(),clock,dispatcher,"0.5.10-beta.1","b".repeat(40));next.load();runCurrent()
        assertTrue(next.captureRecovered(input,clock.now,context.epoch,key,"0.5.9-beta.2",3));runCurrent();next.close()
        val replayed=store.json("outbox.json").jsonArray.single().jsonObject
        assertEquals(saved["id"],replayed["id"]);assertEquals(saved["timestamp_ms"],replayed["timestamp_ms"])
        assertEquals(3,replayed["props"]!!.jsonObject["occurrences"]!!.jsonPrimitive.int)
        assertFalse(replayed["props"]!!.jsonObject.containsKey("build_commit"))
    }
    @Test fun recoverySignalsAndVersionsWithinSameWindowDoNotCollide()=runTest {
        val store=Store();val clock=Clock();val reporter=makeReporter(store,Transport(),clock,StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        val epoch=reporter.recoveryContext!!.epoch;clock.now+=1000;val key="android:${clock.now}:crashed"
        val input=DiagnosticInput("app_crash",reason="crashed",signal="SIGSEGV")
        assertFalse(reporter.captureRecovered(input,clock.now,epoch,key,"0.5.8-beta.1",0))
        assertTrue(reporter.captureRecovered(input,clock.now,epoch,key,"0.5.8-beta.1",2))
        assertTrue(reporter.captureRecovered(input.copy(signal="SIGILL"),clock.now,epoch,key,"0.5.8-beta.1"))
        assertTrue(reporter.captureRecovered(input,clock.now,epoch,key,"0.5.9-beta.2"))
        runCurrent();val records=store.json("outbox.json").jsonArray
        assertEquals(3,records.map { it.jsonObject["id"] }.toSet().size)
        assertEquals(listOf(1,1,2),records.map { it.jsonObject["props"]!!.jsonObject["occurrences"]!!.jsonPrimitive.int }.sorted());reporter.close()
    }

    @Test fun hardKillRevokesOldGrantAndPreventsUnkillHistoryReplay()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val kill=java.util.concurrent.atomic.AtomicBoolean(false)
        val first=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()})
        first.setConsent(true);val oldEpoch=first.recoveryContext!!.epoch
        first.capture(event());runCurrent();first.close()
        kill.set(true)
        val killed=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()});killed.load();assertFalse(killed.status.available)
        try { killed.setConsent(true) }catch(_:Exception){}
        killed.close();kill.set(false)
        val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher,disabled={kill.get()});next.load();runCurrent()
        assertFalse(next.status.enabled);assertFalse(next.capture(event()));assertNull(next.recoveryContext);assertTrue(transport.bodies.isEmpty())
        next.setConsent(true);assertNotEquals(oldEpoch,next.recoveryContext!!.epoch);next.close()
    }

    @Test fun hardKillWithoutPriorGrantCreatesNoIdentityOrFiles()=runTest {
        val store=Store();val reporter=makeReporter(store,Transport(),Clock(),StandardTestDispatcher(testScheduler),disabled={true})
        reporter.load();assertFalse(reporter.status.available);assertFalse(reporter.capture(event()));assertTrue(store.files.isEmpty());reporter.close()
    }
    @Test fun hardKillSaveFailureIsVisibleAndRemovedGrantStaysOffOnRestart()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler);val kill=java.util.concurrent.atomic.AtomicBoolean(false)
        val first=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()});first.setConsent(true);first.close()
        kill.set(true);store.failWrite=true
        val killed=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()});killed.load()
        assertTrue(killed.status.consentSaveFailed);assertFalse(killed.status.enabled);killed.close()
        store.failWrite=false;kill.set(false)
        val next=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()});next.load();assertFalse(next.status.enabled);next.close()
    }
    @Test fun recoveredQueueCapacityCountsDroppedReports()=runTest {
        val store=Store();val clock=Clock();val reporter=makeReporter(store,Transport(),clock,StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        val epoch=reporter.recoveryContext!!.epoch
        repeat(100){ offset -> reporter.captureRecovered(DiagnosticInput("app_crash",reason="crashed"),clock.now+offset,epoch,"android:${clock.now+offset}:crashed","0.5.9-beta.2") }
        assertEquals(36,reporter.status.droppedCount);reporter.close()
    }

    @Test fun bootstrapRevocationNeedsNoPolicyOrDestination()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.capture(event());runCurrent();first.close()
        assertTrue(DiagnosticReports.revokeStoredConsent(File("unused-test-store"),store))
        assertFalse(store.json("consent.json").jsonObject["enabled"]!!.jsonPrimitive.boolean);assertFalse(store.files.containsKey("outbox.json"))
        val next=makeReporter(store,Transport(),clock,dispatcher);next.load();assertFalse(next.status.enabled);assertNull(next.recoveryContext);next.close()
        val empty=Store();assertTrue(DiagnosticReports.revokeStoredConsent(File("unused-test-store"),empty));assertTrue(empty.files.isEmpty())
    }

@Test fun withdrawalTombstoneBlocksLockedOldGrantAfterRestartAndRequiresFreshOptIn()=runTest {
        val store=Store();val clock=ScheduledClock(testScheduler);val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true)
        val old=first.recoveryContext!!.epoch
        first.capture(event());runCurrent();advanceTimeBy(1500);runCurrent()
        store.blockedWrites.add("consent.json");store.blockedRemoves.add("consent.json")
        try { first.setConsent(false);fail("Expected locked consent save") }catch(_:IOException){}
        assertFalse(first.status.enabled);assertFalse(first.capture(event()))
        assertTrue("Withdrawal needs a separate durable revocation",store.files.containsKey("revoked.json"))
        first.close()
        val transport=Transport().also{it.code=201};val next=makeReporter(store,transport,clock,dispatcher)
        try {
            next.load();runCurrent();assertFalse(next.status.enabled);assertTrue(transport.bodies.isEmpty())
            store.blockedWrites.clear();store.blockedRemoves.clear()
            advanceTimeBy(60000);runCurrent()
            assertFalse(next.status.consentSaveFailed)
            assertFalse(store.json("consent.json").jsonObject["enabled"]!!.jsonPrimitive.boolean)
            assertTrue(transport.bodies.isEmpty())
            next.setConsent(true);assertNotEquals(old,next.recoveryContext!!.epoch)
            assertFalse(store.files.containsKey("revoked.json"))
            next.capture(event());runCurrent();assertEquals(1,transport.bodies.size)
        }finally{store.blockedWrites.clear();store.blockedRemoves.clear();next.close()}
    }

    @Test fun failedRevocationRetriesWhileOffWithoutBusyLoopOrOldEpochDelivery()=runTest {
        val store=Store();val clock=ScheduledClock(testScheduler);val transport=Transport().also{it.code=201}
        val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        store.blockedWrites.addAll(listOf("consent.json","revoked.json"));store.blockedRemoves.add("consent.json")
        try {
            try{reporter.setConsent(false);fail("Expected unavailable revocation store")}catch(_:IOException){}
            assertFalse(reporter.capture(event()));val attempts=store.revocationWriteAttempts
            advanceTimeBy(59999);runCurrent();assertEquals(attempts,store.revocationWriteAttempts)
            store.blockedWrites.clear();store.blockedRemoves.clear();advanceTimeBy(1);runCurrent()
            assertTrue("OFF retry must persist after storage recovers",store.files.containsKey("revoked.json"))
            assertFalse(reporter.status.enabled);assertFalse(reporter.status.consentSaveFailed);assertTrue(transport.bodies.isEmpty())
            val healthy=store.revocationWriteAttempts;advanceTimeBy(180000);runCurrent();assertEquals(healthy,store.revocationWriteAttempts)
        }finally{store.blockedWrites.clear();store.blockedRemoves.clear();reporter.close()}
    }

    @Test fun presentCorruptOrUnreadableRevocationMarkerAlwaysClosesTheGrant()=runTest {
        for(unreadable in listOf(false,true)) {
            val store=Store();val clock=ScheduledClock(testScheduler);val dispatcher=StandardTestDispatcher(testScheduler)
            val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.close()
            store.files["revoked.json"]="{corrupt".toByteArray();store.unreadableRevocation=unreadable
            val transport=Transport().also{it.code=201};val reporter=makeReporter(store,transport,clock,dispatcher)
            try{
                if(unreadable) {try{reporter.load();fail("Unread permission must remain a retryable IO failure")}catch(_:IOException){}}
                else reporter.load()
                assertFalse(reporter.status.enabled);assertFalse(reporter.capture(event()));runCurrent();assertTrue(transport.bodies.isEmpty())
            }
            finally{store.unreadableRevocation=false;reporter.close()}
        }
    }

    @Test fun expectedConsentEpochCannotBorrowAnotherOwnerGrant()=runTest {
        val store=Store();val transport=Transport().also{it.code=201};val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        try{
            reporter.setConsent(true);val old=reporter.recoveryContext!!.epoch
            reporter.setConsent(false);reporter.setConsent(true);val fresh=reporter.recoveryContext!!.epoch
            assertFalse("A stale UI observation must not acquire the new owner grant",reporter.capture(event(),expectedEpoch=old))
            assertTrue(reporter.capture(event(),expectedEpoch=fresh));runCurrent();assertEquals(1,transport.bodies.size)
        }finally{reporter.close()}
    }

@Test fun freshOffPersistsOnlyAnonymousRevocationAndNeverAnIdentity()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,Transport(),clock,dispatcher);reporter.setConsent(false)
        assertTrue(reporter.status.prompted);assertEquals(setOf("revoked.json"),store.files.keys);reporter.close()
        val next=makeReporter(store,Transport(),clock,dispatcher)
        try{next.load();assertFalse(next.status.enabled);assertTrue(next.status.prompted);assertEquals(setOf("revoked.json"),store.files.keys)}
        finally{next.close()}
    }

    @Test fun revocationPublishedBeforeSendAndFailedMarkerRemovalKeepCollectionClosed()=runTest {
        val store=Store();val transport=Transport().also{it.code=201};val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        try{
            reporter.setConsent(true);assertTrue(reporter.capture(event()))
            store.files["revoked.json"]="{}".toByteArray();runCurrent()
            assertFalse(reporter.status.enabled);assertTrue(transport.bodies.isEmpty())
            store.blockedRemoves.add("revoked.json")
            try{reporter.setConsent(true);fail("Expected uncleared revocation marker")}catch(_:IOException){}
            assertFalse(reporter.capture(event()));assertFalse(reporter.status.enabled)
            store.blockedRemoves.clear();reporter.setConsent(true);reporter.capture(event());runCurrent();assertEquals(1,transport.bodies.size)
        }finally{store.blockedRemoves.clear();reporter.close()}
    }

    private class FileCommitProbe(private val directory:File) : DiagnosticFileSynchronizer {
        val commits=mutableListOf<String>()
        var failing:String?=null
        override fun synchronize(path:java.nio.file.Path) {
            commits.add(path.fileName.toString())
            if(failing==path.fileName.toString())throw IOException("Commit barrier failed")
            NioDiagnosticFileSynchronizer.synchronize(path)
        }
    }
    @Test fun fileStorageChecksPublishedFileAndParentBeforeAcknowledgingWrite() {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-commit-").toFile()
        try {
            val probe=FileCommitProbe(directory);val storage=FileDiagnosticStorage(directory,probe)
            val marker="{\"version\":1}".toByteArray()
            storage.write("revoked.json",marker)
            assertEquals(listOf("revoked.json",directory.name),probe.commits)
            assertArrayEquals(marker,storage.read("revoked.json",4096))
        }finally{directory.deleteRecursively()}
    }
    private fun fileStoreBarrierFailureCannotEnableAFormerlyWithdrawnGrant(directoryFailure:Boolean)=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-commit-").toFile()
        val probe=FileCommitProbe(directory);val storage=FileDiagnosticStorage(directory,probe)
        val transport=Transport();val reporter=makeReporter(storage,transport,Clock(),StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(false)
            probe.failing=if(directoryFailure)directory.name else "consent.json"
            try{reporter.setConsent(true);fail("Uncommitted opt-in must fail")}catch(_:IOException){}
            assertFalse(reporter.status.enabled);assertTrue(reporter.status.consentSaveFailed)
            assertFalse(reporter.capture(event()));assertNotNull(storage.read("revoked.json",4096))
            probe.failing=null;reporter.close()
            val next=makeReporter(FileDiagnosticStorage(directory),transport,Clock(),StandardTestDispatcher(testScheduler))
            try{next.load();next.flush();assertFalse(next.status.enabled);assertTrue(transport.bodies.isEmpty())}finally{next.close()}
        }finally{probe.failing=null;reporter.close();directory.deleteRecursively()}
    }
    @Test fun fileStoreFileBarrierFailureCannotEnableAFormerlyWithdrawnGrant()=fileStoreBarrierFailureCannotEnableAFormerlyWithdrawnGrant(false)
    @Test fun fileStoreDirectoryBarrierFailureCannotEnableAFormerlyWithdrawnGrant()=fileStoreBarrierFailureCannotEnableAFormerlyWithdrawnGrant(true)
    private fun firstOptInPublicationFailureIsOffBeforeRetryOrRestart(directoryFailure:Boolean)=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-first-grant-").toFile()
        var failOnce=true
        var publishedEpoch:String?=null
        val synchronizer=object:DiagnosticFileSynchronizer {
            override fun synchronize(path:java.nio.file.Path) {
                if(failOnce && path.fileName.toString()==(if(directoryFailure)directory.name else "consent.json")) {
                    failOnce=false
                    val published=Json.parseToJsonElement(File(directory,"consent.json").readText(Charsets.UTF_8)).jsonObject
                    assertTrue(published["enabled"]!!.jsonPrimitive.boolean)
                    publishedEpoch=published["epoch"]!!.jsonPrimitive.content
                    throw IOException("Controlled post-publication consent barrier failure")
                }
                NioDiagnosticFileSynchronizer.synchronize(path)
            }
        }
        val storage=FileDiagnosticStorage(directory,synchronizer)
        val dispatcher=StandardTestDispatcher(testScheduler);val clock=ScheduledClock(testScheduler)
        val transport=Transport().apply{code=201};val reporter=makeReporter(storage,transport,clock,dispatcher)
        try {
            assertNull(storage.read("consent.json",4096));assertNull(storage.read("revoked.json",4096))
            try{reporter.setConsent(true);fail("Uncommitted first opt-in must fail")}catch(_:IOException){}
            assertNotNull("The enabled grant was renamed before the checked failure",publishedEpoch)
            assertFalse(reporter.status.enabled);assertTrue(reporter.status.consentSaveFailed)
            assertFalse(reporter.capture(event()))
            // Restart immediately, before any delayed revocation wake or orderly close.
            val restarted=makeReporter(FileDiagnosticStorage(directory),transport,clock,dispatcher)
            try {
                restarted.load()
                assertFalse("A failed first ON must not become an enabled grant on restart",restarted.status.enabled)
                assertFalse(restarted.capture(event()));restarted.flush();assertTrue(transport.bodies.isEmpty())
            }finally{restarted.close()}
            assertTrue("Failure must leave a durable OFF or independent revocation",storage.read("revoked.json",4096)!=null ||
                Json.parseToJsonElement(storage.read("consent.json",4096)!!.toString(Charsets.UTF_8)).jsonObject["enabled"]!!.jsonPrimitive.boolean==false)
            // Retry preserves the desired ON choice, but creates a fresh grant.
            reporter.setConsent(true)
            assertTrue(reporter.status.enabled);assertFalse(reporter.status.consentSaveFailed)
            assertNotEquals(publishedEpoch,reporter.recoveryContext!!.epoch)
            assertFalse(reporter.capture(event(),expectedEpoch=publishedEpoch))
            assertTrue(reporter.capture(event(),expectedEpoch=reporter.recoveryContext!!.epoch));runCurrent()
            assertEquals(1,transport.bodies.size)
        }finally{reporter.close();directory.deleteRecursively()}
    }
    @Test fun firstOptInFileBarrierFailureIsOffBeforeRetryOrRestart()=firstOptInPublicationFailureIsOffBeforeRetryOrRestart(false)
    @Test fun firstOptInDirectoryBarrierFailureIsOffBeforeRetryOrRestart()=firstOptInPublicationFailureIsOffBeforeRetryOrRestart(true)
    @Test fun failedOffDirectoryBarrierRemainsClosedAndOffOnRestart()=runTest {
        val directory=java.nio.file.Files.createTempDirectory("diagnostic-commit-").toFile()
        val probe=FileCommitProbe(directory);val storage=FileDiagnosticStorage(directory,probe)
        val transport=Transport();val reporter=makeReporter(storage,transport,Clock(),StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true);probe.failing=directory.name
            try{reporter.setConsent(false);fail("Uncommitted OFF must expose the storage failure")}catch(_:IOException){}
            assertFalse(reporter.status.enabled);assertFalse(reporter.capture(event()))
            probe.failing=null;reporter.close()
            val next=makeReporter(FileDiagnosticStorage(directory),transport,Clock(),StandardTestDispatcher(testScheduler))
            try{next.load();next.flush();assertFalse(next.status.enabled);assertTrue(transport.bodies.isEmpty())}finally{next.close()}
        }finally{probe.failing=null;reporter.close();directory.deleteRecursively()}
    }

    @Test fun fileStorageAllowsThePrivateAnonymousRevocationMarker() {
        val directory=java.nio.file.Files.createTempDirectory("voice-revocation-test").toFile()
        try {
            val storage=FileDiagnosticStorage(directory);val bytes="{\"version\":1}".toByteArray()
            storage.write("revoked.json",bytes);assertArrayEquals(bytes,storage.read("revoked.json",4096))
            storage.remove("revoked.json");assertNull(storage.read("revoked.json",4096))
        }finally{directory.deleteRecursively()}
    }

@Test fun supersededOnCannotErasePreviousOffMarkerBeforeAFailingNewerOff()=runTest {
        val files=java.util.concurrent.ConcurrentHashMap<String,ByteArray>()
        val entered=java.util.concurrent.CountDownLatch(1);val release=java.util.concurrent.CountDownLatch(1)
        val armed=java.util.concurrent.atomic.AtomicBoolean(false)
        val denied=java.util.concurrent.atomic.AtomicBoolean(false)
        val store=object:DiagnosticStorage {
            override fun read(name:String,maxBytes:Int)=files[name]?.clone()
            override fun remove(name:String){
                if(denied.get() && name=="consent.json")throw IOException("locked consent")
                files.remove(name)
            }
            override fun write(name:String,bytes:ByteArray){
                if(denied.get() && name in setOf("consent.json","revoked.json"))throw IOException("unavailable consent")
                files[name]=bytes.clone()
                if(name=="consent.json" && armed.compareAndSet(true,false)) {
                    entered.countDown();check(release.await(5,java.util.concurrent.TimeUnit.SECONDS))
                }
            }
        }
        val dispatcher=Dispatchers.IO
        val reporter=makeReporter(store,Transport(),Clock(),dispatcher)
        reporter.setConsent(false);assertTrue(files.containsKey("revoked.json"));armed.set(true)
        val on=async(Dispatchers.IO){reporter.setConsent(true)}
        try {
            assertTrue(entered.await(5,java.util.concurrent.TimeUnit.SECONDS))
            denied.set(true)
            val off=async(start=CoroutineStart.UNDISPATCHED){runCatching{reporter.setConsent(false)}}
            release.countDown();on.await();assertTrue(off.await().isFailure)
            reporter.close();denied.set(false)
            val next=makeReporter(store,Transport(),Clock(),dispatcher)
            try {next.load();assertFalse("Stale ON removed the only durable OFF marker",next.status.enabled);assertTrue(files.containsKey("revoked.json"))}
            finally{next.close()}
        }finally{release.countDown();denied.set(false);reporter.close()}
    }

    @Test fun publicPlaintextDestinationsAreRejectedWhileLoopbackAndHTTPSWork() {
        for (host in listOf("http://collector.example", "http://8.8.8.8", "http://192.168.1.2", "http://127.23.4.5", "http://localhost.example")) {
            try { DiagnosticDestination("localtest123", host, "https://app.test.invalid");fail("Public or LAN plaintext collector accepted: $host") } catch (_:IllegalArgumentException) {}
            try { DiagnosticDestination("localtest123", "https://collector.example", host);fail("Public or LAN plaintext app URL accepted: $host") } catch (_:IllegalArgumentException) {}
        }
        for (host in listOf("http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000", "https://collector.example")) {
            assertNotNull(DiagnosticDestination("localtest123", host, host).endpoint)
        }
    }

    @Test fun normalizedHTTPAliasesNeverBecomeLoopbackExceptions() {
        for (alias in listOf("http://%6cocalhost", "http://127.1", "http://2130706433", "http://[0:0:0:0:0:0:0:1]")) {
            try { DiagnosticDestination("localtest123", alias, "https://app.test.invalid");fail("Normalized collector alias accepted: $alias") } catch (_:IllegalArgumentException) {}
            try { DiagnosticDestination("localtest123", "https://collector.example", alias);fail("Normalized app URL alias accepted: $alias") } catch (_:IllegalArgumentException) {}
        }
    }

    @Test fun validLoadedOutboxSurvivesRewriteFailureAndRetriesBeforeNetwork()=runTest {
        val store=Store();val clock=ScheduledClock(testScheduler);val dispatcher=StandardTestDispatcher(testScheduler)
        val first=makeReporter(store,Transport(),clock,dispatcher)
        first.setConsent(true);first.capture(event());runCurrent();advanceTimeBy(1500);runCurrent();first.close()
        val original=store.files.getValue("outbox.json").clone()
        store.failOutboxWrite=true
        val transport=Transport().also { it.code=201 };val reporter=makeReporter(store,transport,clock,dispatcher)
        try {
            reporter.load();runCurrent()
            assertTrue("A valid outbox was deleted after a replacement write failure",store.files.containsKey("outbox.json"))
            assertArrayEquals(original,store.files.getValue("outbox.json"))
            assertEquals(1,reporter.status.pendingCount);assertTrue(transport.bodies.isEmpty())
            val attempts=store.outboxWriteAttempts
            advanceTimeBy(59999);runCurrent();assertEquals(attempts,store.outboxWriteAttempts);assertTrue(transport.bodies.isEmpty())
            store.failOutboxWrite=false
            advanceTimeBy(1);runCurrent()
            assertEquals(1,transport.bodies.size)
            val saved=Json.parseToJsonElement(original.toString(Charsets.UTF_8)).jsonArray.single().jsonObject
            assertEquals(saved["id"],transport.bodies.single()["id"])
            assertEquals(0,reporter.status.pendingCount);assertFalse(store.files.containsKey("outbox.json"))
        } finally { store.failOutboxWrite=false;reporter.close() }
    }

    @Test fun failedNewOutboxWriteRetriesWithoutNewCaptureOrBusyLoop()=runTest {
        val store=Store();val clock=ScheduledClock(testScheduler);val transport=Transport().also { it.code=201 }
        val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true);store.failOutboxWrite=true
            assertTrue(reporter.capture(event()));runCurrent()
            assertTrue("Uncommitted records must not reach the transport",transport.bodies.isEmpty())
            assertEquals(1,reporter.status.pendingCount)
            val attempts=store.outboxWriteAttempts
            advanceTimeBy(59999);runCurrent();assertEquals(attempts,store.outboxWriteAttempts)
            assertTrue(transport.bodies.isEmpty())
            store.failOutboxWrite=false;advanceTimeBy(1);runCurrent()
            assertEquals("Storage recovery must wake without another capture",1,transport.bodies.size)
            assertEquals(0,reporter.status.pendingCount)
            advanceTimeBy(180000);runCurrent();assertEquals(1,transport.bodies.size)
        } finally { store.failOutboxWrite=false;reporter.close() }
    }

    @Test fun storageRetryCannotSurviveOffCloseOrConsentEpochReplacement()=runTest {
        for(action in listOf("off","close","epoch")) {
            val store=Store();val clock=ScheduledClock(testScheduler);val transport=Transport().also { it.code=201 }
            val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
            try {
                reporter.setConsent(true);store.failOutboxWrite=true
                reporter.capture(event());runCurrent();assertTrue(transport.bodies.isEmpty())
                if(action=="close") reporter.close()
                else {
                    store.failOutboxWrite=false
                    reporter.setConsent(false)
                    if(action=="epoch") reporter.setConsent(true)
                }
                store.failOutboxWrite=false;advanceTimeBy(180000);runCurrent()
                assertTrue("A retired consent epoch delivered old pending work",transport.bodies.isEmpty())
                if(action=="epoch") {
                    assertTrue(reporter.capture(event()));runCurrent();assertEquals(1,transport.bodies.size)
                } else assertFalse(reporter.capture(event()))
            } finally { store.failOutboxWrite=false;reporter.close() }
        }
    }

    @Test fun scheduledRetryRecoversWithoutAnotherFailureAndCoalescesNewIntake()=runTest {
        val store=Store();val transport=Transport();val clock=ScheduledClock(testScheduler)
        val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true);reporter.capture(event());runCurrent()
            assertEquals(1,transport.bodies.size)
            advanceTimeBy(500);runCurrent();assertEquals(2,transport.bodies.size)
            advanceTimeBy(1000);runCurrent();assertEquals(3,transport.bodies.size)
            val first=transport.bodies.first()["id"]
            reporter.capture(DiagnosticInput("model_load_failed","model_load"));runCurrent()
            advanceTimeBy(1500);runCurrent()
            assertEquals(3,transport.bodies.count{it["id"]==first})
            assertEquals(6,transport.bodies.size)
            transport.code=201
            advanceTimeBy(58499);runCurrent();assertEquals(6,transport.bodies.size)
            advanceTimeBy(1);runCurrent()
            assertEquals(7,transport.bodies.size);assertEquals(first,transport.bodies.last()["id"])
            advanceTimeBy(1500);runCurrent();assertEquals(8,transport.bodies.size)
            assertEquals(0,reporter.status.pendingCount);assertFalse(store.files.containsKey("outbox.json"))
            advanceTimeBy(180000);runCurrent();assertEquals(8,transport.bodies.size)
        }finally{reporter.close()}
    }

    @Test fun scheduledRetryCannotSurviveOffCloseOrConsentEpochReplacement()=runTest {
        for(action in listOf("off","close","epoch")) {
            val store=Store();val transport=Transport();val clock=ScheduledClock(testScheduler)
            val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
            try {
                reporter.setConsent(true);val epoch=reporter.recoveryContext!!.epoch
                reporter.capture(event());runCurrent();advanceTimeBy(1500);runCurrent()
                assertEquals(3,transport.bodies.size)
                when(action){
                    "close"->reporter.close()
                    "off"->reporter.setConsent(false)
                    else->{reporter.setConsent(false);reporter.setConsent(true);assertNotEquals(epoch,reporter.recoveryContext!!.epoch)}
                }
                transport.code=201;advanceTimeBy(180000);runCurrent()
                assertEquals(3,transport.bodies.size)
                if(action!="close")assertFalse(store.files.containsKey("outbox.json"))
            }finally{reporter.close()}
        }
    }


    @Test fun sameGrantConsentReadRecoveryPreservesUnpublishedAcceptedReports()=runTest {
        val store=Store();val transport=Transport().apply{code=204}
        val clock=ScheduledClock(testScheduler)
        val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true)
            store.failOutboxWrite=true
            assertTrue(reporter.capture(event()))
            assertTrue(reporter.capture(event()))
            runCurrent()
            assertEquals(1,reporter.status.pendingCount)
            assertTrue(transport.bodies.isEmpty())
            store.unreadableRevocation=true
            assertTrue(runCatching{reporter.load(forceRead=true)}.isFailure)
            assertFalse(reporter.capture(event()))
            store.unreadableRevocation=false;store.failOutboxWrite=false
            reporter.load(forceRead=true);reporter.flush()
            assertEquals("An accepted same-grant report must survive an unread consent pause",1,transport.bodies.size)
            assertEquals(2,transport.bodies.single().getValue("props").jsonObject.getValue("occurrences").jsonPrimitive.int)
            assertEquals(0,reporter.status.pendingCount)
        } finally {reporter.close()}
    }
    @Test fun sameGrantConsentReadRecoveryDoesNotResurrectAcknowledgedDiskRecords()=runTest {
        val store=Store();val transport=Transport().apply{code=204}
        val reporter=makeReporter(store,transport,ScheduledClock(testScheduler),StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true)
            store.blockedRemoves.add("outbox.json")
            assertTrue(reporter.capture(event()));runCurrent()
            assertEquals(1,transport.bodies.size)
            assertTrue(store.files.containsKey("outbox.json"))
            store.unreadableRevocation=true
            assertTrue(runCatching{reporter.load(forceRead=true)}.isFailure)
            store.unreadableRevocation=false;store.blockedRemoves.clear()
            reporter.load(forceRead=true);reporter.flush()
            assertEquals("An ACK must remain authoritative when stale outbox cleanup failed",1,transport.bodies.size)
            assertFalse(store.files.containsKey("outbox.json"))
        } finally {store.blockedRemoves.clear();reporter.close()}
    }
    @Test fun unreadConsentRecoveryNeverCarriesMemoryIntoAnOffOrDifferentGrant()=runTest {
        for(enabled in listOf(false,true)) {
            val store=Store();val transport=Transport().apply{code=204}
            val reporter=makeReporter(store,transport,ScheduledClock(testScheduler),StandardTestDispatcher(testScheduler))
            try {
                reporter.setConsent(true);store.failOutboxWrite=true
                assertTrue(reporter.capture(event()));runCurrent()
                store.unreadableRevocation=true
                assertTrue(runCatching{reporter.load(forceRead=true)}.isFailure)
                val consent=store.json("consent.json").jsonObject
                store.files["consent.json"]=JsonObject(consent+mapOf("enabled" to JsonPrimitive(enabled),"epoch" to JsonPrimitive("b".repeat(32)),"profile_id" to JsonPrimitive("install_"+"b".repeat(32)))).toString().toByteArray(Charsets.UTF_8)
                store.unreadableRevocation=false;store.failOutboxWrite=false
                reporter.load(forceRead=true);reporter.flush()
                assertTrue("Old accepted work cannot cross a changed or withdrawn grant",transport.bodies.isEmpty())
                assertEquals(enabled,reporter.status.enabled)
            } finally {reporter.close()}
        }
    }
    @Test fun closeAfterUnreadConsentPreservesAcceptedUnpublishedReports()=runTest {
        val store=Store();val transport=Transport().apply {code=204}
        val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,transport,ScheduledClock(testScheduler),dispatcher)
        try {
            reporter.setConsent(true);store.failOutboxWrite=true
            assertTrue(reporter.capture(event()));assertTrue(reporter.capture(event()));runCurrent()
            assertEquals(1,reporter.status.pendingCount);assertTrue(transport.bodies.isEmpty())
            store.unreadableRevocation=true
            assertTrue(runCatching {reporter.load(forceRead=true)}.isFailure)
            store.unreadableRevocation=false;store.failOutboxWrite=false
            reporter.close()
            val reopened=makeReporter(store,transport,ScheduledClock(testScheduler),dispatcher)
            try {
                reopened.load();reopened.flush()
                assertEquals("Closing an unread same-grant owner must publish its accepted memory",1,transport.bodies.size)
                assertEquals(2,transport.bodies.single().getValue("props").jsonObject.getValue("occurrences").jsonPrimitive.int)
            } finally {reopened.close()}
        } finally {store.unreadableRevocation=false;store.failOutboxWrite=false;reporter.close()}
    }
    @Test fun closeAfterUnreadConsentCannotResurrectAcknowledgedDiskRecords()=runTest {
        val store=Store();val transport=Transport().apply {code=204}
        val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,transport,ScheduledClock(testScheduler),dispatcher)
        try {
            reporter.setConsent(true);store.blockedRemoves.add("outbox.json")
            assertTrue(reporter.capture(event()));runCurrent()
            assertEquals(1,transport.bodies.size);assertTrue(store.files.containsKey("outbox.json"))
            store.unreadableRevocation=true
            assertTrue(runCatching {reporter.load(forceRead=true)}.isFailure)
            store.unreadableRevocation=false;store.blockedRemoves.clear()
            reporter.close()
            val reopened=makeReporter(store,transport,ScheduledClock(testScheduler),dispatcher)
            try {
                reopened.load();reopened.flush()
                assertEquals("Shutdown must preserve the authoritative ACK removal",1,transport.bodies.size)
                assertFalse(store.files.containsKey("outbox.json"))
            } finally {reopened.close()}
        } finally {store.unreadableRevocation=false;store.blockedRemoves.clear();reporter.close()}
    }

    @Test fun automaticConsentReadRecoveryDrainsAcceptedIntakeWithoutAnotherAction()=runTest {
        val store=Store();val transport=Transport().apply {code=204}
        val reporter=makeReporter(store,transport,ScheduledClock(testScheduler),StandardTestDispatcher(testScheduler))
        try {
            reporter.setConsent(true)
            store.unreadableRevocation=true
            // Cached consent accepts both failures; the first actual flush
            // loses marker readability before it can drain that intake.
            assertTrue(reporter.capture(event()));assertTrue(reporter.capture(event()))
            runCurrent()
            assertFalse(reporter.status.enabled);assertTrue(transport.bodies.isEmpty())
            store.unreadableRevocation=false
            advanceTimeBy(59999);runCurrent();assertTrue(transport.bodies.isEmpty())
            advanceTimeBy(1);runCurrent()
            assertTrue(reporter.status.enabled)
            assertEquals("The automatic read-recovery wake must also drain accepted intake",1,transport.bodies.size)
            assertEquals(2,transport.bodies.single().getValue("props").jsonObject.getValue("occurrences").jsonPrimitive.int)
            assertEquals(0,reporter.status.pendingCount)
            advanceTimeBy(180000);runCurrent();assertEquals(1,transport.bodies.size)
        } finally {reporter.close()}
    }

    @Test fun automaticConsentReadRecoveryRetriesAnAlreadyCreatedOptionalSession()=runTest {
        val store=Store();val bodies=mutableListOf<JsonObject>()
        val transport=object:DiagnosticTransport {
            override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int=error("App session must not use legacy transport")
            override suspend fun sendApp(destination:DiagnosticDestination,body:ByteArray,userAgent:String,allowed:()->Boolean):DiagnosticTransportReply {
                check(allowed())
                val parsed=Json.parseToJsonElement(body.toString(Charsets.UTF_8)).jsonObject
                bodies.add(parsed)
                return DiagnosticTransportReply(202,"{\"accepted\":${parsed.getValue("events").jsonArray.size},\"dropped\":[]}".toByteArray(Charsets.UTF_8))
            }
            override fun cancelAll()=Unit
        }
        val destination=DiagnosticDestination("localtest123","https://legacy.invalid","https://app.invalid",
            DiagnosticAppConfiguration("native01","smk_"+"a".repeat(39),"https://intake.invalid",sessionPings=true))
        val reporter=DiagnosticReports(File("unused-test-store"),policy(),destination,"0.5.9-beta.3",
            transport=transport,clock=ScheduledClock(testScheduler),storage=store,dispatcher=StandardTestDispatcher(testScheduler),disabled={false})
        try {
            reporter.setConsent(true)
            // The initial flush marker read succeeds. The delivery-time marker
            // read then fails, after the opt-in session already exists.
            store.failRevocationReadAt=store.revocationReads+2
            reporter.flush();runCurrent()
            assertFalse(reporter.status.enabled);assertEquals(0,reporter.status.pendingCount);assertTrue(bodies.isEmpty())
            advanceTimeBy(59999);runCurrent();assertTrue(bodies.isEmpty())
            advanceTimeBy(1);runCurrent()
            assertTrue(reporter.status.enabled)
            assertEquals("An accepted session-only ping needs the same recovery wake",1,bodies.size)
            val events=bodies.single().getValue("events").jsonArray
            assertEquals(1,events.size);assertEquals("session",events.single().jsonObject.getValue("kind").jsonPrimitive.content)
            advanceTimeBy(180000);runCurrent();assertEquals(1,bodies.size)
        } finally {reporter.close()}
    }

}
