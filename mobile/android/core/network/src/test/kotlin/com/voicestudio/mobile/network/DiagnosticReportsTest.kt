package com.voicestudio.mobile.network

import java.io.File
import java.io.IOException
import java.lang.ref.WeakReference
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
        override fun read(name:String,maxBytes:Int):ByteArray? { reads++;return files[name]?.clone()?.also { require(it.size<=maxBytes) } }
        override fun write(name:String,bytes:ByteArray) { if(failWrite)throw IOException("private path");writes++;files[name]=bytes.clone() }
        override fun remove(name:String) { if(failRemove)throw IOException("private path");files.remove(name) }
        fun json(name:String)=Json.parseToJsonElement(files.getValue(name).toString(Charsets.UTF_8))
    }
    private class Clock : DiagnosticClock {
        var now=1_794_640_000_000L
        override fun nowMs()=now
        override suspend fun sleep(ms:Long) { yield() }
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
    private fun policy():DiagnosticPolicy {
        var root=File(System.getProperty("user.dir"))
        repeat(10) {
            val file=File(root,"config/diagnostics.json")
            if(file.isFile)return DiagnosticPolicy(file.readText(Charsets.UTF_8))
            root=root.parentFile?:root
        }
        error("Canonical diagnostic policy missing")
    }
    private fun makeReporter(store:DiagnosticStorage,transport:DiagnosticTransport,clock:Clock,dispatcher:CoroutineDispatcher,appVersion:String="0.5.9-beta.2",buildCommit:String="a".repeat(40),disabled:()->Boolean={false})=DiagnosticReports(
        File("unused-test-store"),policy(),DiagnosticDestination("localtest123","http://127.0.0.1:1","https://app.test.invalid"),appVersion,
        buildCommit=buildCommit,transport=transport,clock=clock,storage=store,dispatcher=dispatcher,disabled=disabled)
    private fun event(errorType:String="TypeError")=DiagnosticInput("generation_failed","generation",errorType=errorType)

    @Test fun independentDefaultOffAndNoCaptureIO()=runTest {
        val store=Store();val transport=Transport();val clock=Clock();val reporter=makeReporter(store,transport,clock,StandardTestDispatcher(testScheduler))
        assertFalse(reporter.capture(event(),IllegalStateException("secret transcript")))
        assertEquals(0,store.reads);assertEquals(0,store.writes)
        reporter.load();assertFalse(reporter.status.enabled);assertTrue(store.files.isEmpty());assertTrue(transport.bodies.isEmpty())
        reporter.setConsent(true);val reads=store.reads;val writes=store.writes
        assertTrue(reporter.capture(event()));assertEquals(reads,store.reads);assertEquals(writes,store.writes)
        advanceUntilIdle();assertTrue(transport.bodies.isNotEmpty());reporter.close()
    }
    @Test fun projectsOnlyClosedMetadataAndNeverReadsOrRetainsThrowable()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        class SecretError(val retained:ByteArray):IllegalArgumentException() {
            override val message:String get()=error("Message read")
            override fun toString():String=error("Throwable rendered")
        }
        fun fail():WeakReference<ByteArray> {
            val payload=ByteArray(1024*1024);val ref=WeakReference(payload)
            assertTrue(reporter.capture(DiagnosticInput("hf_secret","/home/private",errorType="user voice",engineID="secret model",frames=listOf(DiagnosticFrame("/home/secret.kt",1)),reason="private"),SecretError(payload)))
            return ref
        }
        val ref=fail();repeat(10) { System.gc();if(ref.get()==null)return@repeat }
        assertNull(ref.get())
        advanceUntilIdle()
        val body=store.json("outbox.json").jsonArray.single().jsonObject
        val props=body["props"]!!.jsonObject
        assertEquals("operation_failed",props["category"]!!.jsonPrimitive.content)
        assertEquals("ValueError",props["error_type"]!!.jsonPrimitive.content)
        assertEquals("unknown",props["engine_id"]!!.jsonPrimitive.content)
        assertFalse(body.toString().contains("secret"));assertFalse(props.containsKey("frames"));reporter.close()
    }
    @Test fun offlineReplayUsesStableIdentityTimestampAndSanitizesAgain()=runTest {
        val store=Store();val first=Transport();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,first,clock,dispatcher);reporter.setConsent(true);reporter.capture(event());advanceUntilIdle()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject;reporter.close()
        val nextTransport=Transport().also { it.code=201 };val next=makeReporter(store,nextTransport,clock,dispatcher)
        next.load();advanceUntilIdle()
        assertEquals(saved["id"],nextTransport.bodies.single()["id"]);assertEquals(saved["timestamp_ms"],nextTransport.bodies.single()["ts"])
        assertFalse(store.files.containsKey("outbox.json"));next.close()
    }
    @Test fun withdrawalCancelsInFlightPurgesAndRotatesEpoch()=runTest {
        val store=Store();val transport=Transport().also { it.hold=true };val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);val old=reporter.recoveryContext!!.epoch
        reporter.capture(event());runCurrent();assertEquals(1,transport.bodies.size)
        reporter.setConsent(false);advanceUntilIdle()
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
        repeat(2) { assertTrue(reporter.captureRecovered(input,clock.now,context.epoch,key,"0.5.8-beta.1"));advanceUntilIdle() }
        assertEquals(1,transport.bodies.map { it["id"] }.toSet().size)
        assertEquals("0.5.8-beta.1",transport.bodies.first()["props"]!!.jsonObject["app_version"]!!.jsonPrimitive.content)
        assertFalse(transport.bodies.first()["props"]!!.jsonObject.containsKey("build_commit"))
        assertTrue(reporter.captureRecovered(input.copy(reason="killed"),clock.now,context.epoch,"android:${clock.now}:killed","0.5.8b1"));advanceUntilIdle()
        assertEquals("0.5.8b1",transport.bodies.last()["props"]!!.jsonObject["app_version"]!!.jsonPrimitive.content)
        assertTrue(reporter.captureRecovered(input,clock.now,context.epoch,"android:${clock.now}:oom"));advanceUntilIdle()
        assertFalse(transport.bodies.last()["props"]!!.jsonObject.containsKey("app_version"));reporter.close()
    }
    @Test fun corruptExpiredAndLegacyConsentFailClosedForHistory()=runTest {
        val store=Store();val transport=Transport();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val reporter=makeReporter(store,transport,clock,dispatcher);reporter.setConsent(true);reporter.capture(event());advanceUntilIdle();reporter.close()
        val consent=store.json("consent.json").jsonObject;store.files["consent.json"]=JsonObject(consent-"enabled_at_ms").toString().toByteArray()
        val legacy=makeReporter(store,Transport(),clock,dispatcher);legacy.load();assertNull(legacy.recoveryContext);legacy.close()
        clock.now+=604800001L
        val expired=makeReporter(store,Transport(),clock,dispatcher);expired.load();advanceUntilIdle();assertFalse(store.files.containsKey("outbox.json"));expired.close()
        store.files["consent.json"]="{broken".toByteArray();store.files["outbox.json"]="[]".toByteArray()
        val broken=makeReporter(store,Transport(),clock,dispatcher);broken.load();assertFalse(broken.status.enabled);assertFalse(store.files.containsKey("outbox.json"));broken.close()
    }
    @Test fun boundedQueueAndCancellationControl()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler));reporter.setConsent(true)
        assertFalse(reporter.capture(event(),CancellationException("private")))
        repeat(100) { reporter.capture(DiagnosticInput("generation_failed",frames=listOf(DiagnosticFrame("backend/core/tasks.py",it+1)))) }
        assertEquals(36,reporter.status.droppedCount);advanceUntilIdle()
        val records=store.json("outbox.json").jsonArray;assertEquals(64,records.size);assertTrue(store.files.getValue("outbox.json").size<=524288)
        reporter.close()
    }
    @Test fun writeFailureAndLockedOutboxCannotRestoreOldConsent()=runTest {
        val store=Store();val transport=Transport();val reporter=makeReporter(store,transport,Clock(),StandardTestDispatcher(testScheduler))
        reporter.setConsent(true);reporter.capture(event());advanceUntilIdle()
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
            next.load();advanceUntilIdle();assertFalse(next.status.enabled);assertFalse(next.capture(event()));assertTrue(transport.bodies.isEmpty());next.close()
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
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.capture(event());advanceUntilIdle();first.close()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject
        val variants=listOf(saved+("version" to JsonPrimitive("1")),saved+("timestamp_ms" to JsonPrimitive(clock.now.toString())),saved+("id" to JsonPrimitive(java.math.BigInteger("1".repeat(32)))))
        for(record in variants) {
            store.files["outbox.json"]=JsonArray(listOf(JsonObject(record))).toString().toByteArray()
            val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher);next.load();advanceUntilIdle()
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
        reporter.setConsent(true);reporter.capture(event());advanceUntilIdle()
        val firstID=transport.bodies.first()["id"];assertEquals(3,transport.bodies.count{it["id"]==firstID})
        reporter.capture(DiagnosticInput("model_load_failed","model_load"));advanceUntilIdle()
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
        assertTrue(first.captureRecovered(input,clock.now,context.epoch,key,"0.5.9-beta.2",3));advanceUntilIdle();first.close()
        val saved=store.json("outbox.json").jsonArray.single().jsonObject
        val next=makeReporter(store,Transport(),clock,dispatcher,"0.5.10-beta.1","b".repeat(40));next.load();advanceUntilIdle()
        assertTrue(next.captureRecovered(input,clock.now,context.epoch,key,"0.5.9-beta.2",3));advanceUntilIdle();next.close()
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
        advanceUntilIdle();val records=store.json("outbox.json").jsonArray
        assertEquals(3,records.map { it.jsonObject["id"] }.toSet().size)
        assertEquals(listOf(1,1,2),records.map { it.jsonObject["props"]!!.jsonObject["occurrences"]!!.jsonPrimitive.int }.sorted());reporter.close()
    }

    @Test fun hardKillRevokesOldGrantAndPreventsUnkillHistoryReplay()=runTest {
        val store=Store();val clock=Clock();val dispatcher=StandardTestDispatcher(testScheduler)
        val kill=java.util.concurrent.atomic.AtomicBoolean(false)
        val first=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()})
        first.setConsent(true);val oldEpoch=first.recoveryContext!!.epoch
        first.capture(event());advanceUntilIdle();first.close()
        kill.set(true)
        val killed=makeReporter(store,Transport(),clock,dispatcher,disabled={kill.get()});killed.load();assertFalse(killed.status.available)
        try { killed.setConsent(true) }catch(_:Exception){}
        killed.close();kill.set(false)
        val transport=Transport();val next=makeReporter(store,transport,clock,dispatcher,disabled={kill.get()});next.load();advanceUntilIdle()
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
        val first=makeReporter(store,Transport(),clock,dispatcher);first.setConsent(true);first.capture(event());advanceUntilIdle();first.close()
        assertTrue(DiagnosticReports.revokeStoredConsent(File("unused-test-store"),store))
        assertFalse(store.json("consent.json").jsonObject["enabled"]!!.jsonPrimitive.boolean);assertFalse(store.files.containsKey("outbox.json"))
        val next=makeReporter(store,Transport(),clock,dispatcher);next.load();assertFalse(next.status.enabled);assertNull(next.recoveryContext);next.close()
        val empty=Store();assertTrue(DiagnosticReports.revokeStoredConsent(File("unused-test-store"),empty));assertTrue(empty.files.isEmpty())
    }

}
