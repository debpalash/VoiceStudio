package com.voicestudio.mobile.network

import java.io.File
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DiagnosticAppIntakeTest {
    private val root=generateSequence(File(System.getProperty("user.dir"))) {it.parentFile}.first {File(it,"config/diagnostics.json").isFile}
    private val policy=DiagnosticPolicy(File(root,"config/diagnostics.json").readText(Charsets.UTF_8))
    private class Store:DiagnosticStorage {
        val files=mutableMapOf<String,ByteArray>()
        override fun read(name:String,maxBytes:Int)=files[name]?.clone()?.also {require(it.size<=maxBytes)}
        override fun write(name:String,bytes:ByteArray) {files[name]=bytes.clone()}
        override fun remove(name:String) {files.remove(name)}
    }
    private class Clock(private val scheduler:TestCoroutineScheduler):DiagnosticClock {
        override fun nowMs()=1_794_640_000_000L+scheduler.currentTime
        override suspend fun sleep(ms:Long)=delay(ms)
    }
    private class Transport:DiagnosticTransport {
        val bodies=mutableListOf<JsonObject>();val legacy=mutableListOf<String>();val replies=ArrayDeque<DiagnosticTransportReply>()
        var held:CompletableDeferred<Unit>?=null;var cancelled=0
        override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int {legacy.add(body.toString(Charsets.UTF_8));return 202}
        override suspend fun sendApp(destination:DiagnosticDestination,body:ByteArray,userAgent:String,allowed:()->Boolean):DiagnosticTransportReply {
            check(allowed());val parsed=Json.parseToJsonElement(body.toString(Charsets.UTF_8)).jsonObject;bodies.add(parsed)
            held?.await();check(allowed())
            return replies.removeFirstOrNull() ?: DiagnosticTransportReply(202,"{\"accepted\":${parsed.getValue("events").jsonArray.size},\"dropped\":[]}".toByteArray())
        }
        override fun cancelAll() {cancelled++;held?.cancel()}
    }
    private fun destination(pings:Boolean=false)=DiagnosticDestination("legacy01","https://legacy.example","https://voicestudio.sh",DiagnosticAppConfiguration("native01","smk_"+"a".repeat(39),"https://intake.example",pings))
    private fun reports(store:Store,transport:Transport,scheduler:TestCoroutineScheduler,pings:Boolean=false,app:Boolean=true,results:MutableList<DiagnosticDeliveryResult> = mutableListOf())=DiagnosticReports(File("/unused"),policy,if(app)destination(pings) else DiagnosticDestination("legacy01","https://legacy.example","https://voicestudio.sh"),"0.5.9-beta.3",arch="arm64",transport=transport,clock=Clock(scheduler),storage=store,dispatcher=StandardTestDispatcher(scheduler),disabled={false},onDeliveryResult={results.add(it)})
    @Test fun commonGoldenMappingAndRawTagOrder() {
        val fixture=Json.parseToJsonElement(File(root,"tests/fixtures/diagnostic-app-events.json").readText(Charsets.UTF_8)).jsonObject
        for(case in fixture.getValue("cases").jsonArray) {
            val row=case.jsonObject;val actual=diagnosticAppEvent(row.getValue("props").jsonObject,"id",10,"profile")
            assertEquals(row.getValue("name").jsonPrimitive.content,row.getValue("expected"),JsonObject(actual-filterKeys))
            val expectedKeys=row.getValue("expected").jsonObject["tags"]?.jsonObject?.keys?.toList().orEmpty()
            assertEquals(expectedKeys,actual["tags"]?.jsonObject?.keys?.toList().orEmpty())
            val body=diagnosticAppBody("native01",listOf(actual)).toString(Charsets.UTF_8)
            var index=body.indexOf("\"tags\"")
            expectedKeys.forEach {key->val next=body.indexOf("\"$key\":",index+1);assertTrue("tag $key order",next>index);index=next}
        }
    }
    private val filterKeys=setOf("id","ts","runtime","install_id")
    @Test fun invalidSelectedCredentialsDoNotUseGeneratedFallbackAndInvalidHostUsesAnalytics() {
        val generated="""{"app_id":"native01","ingest_key":"${"smk_"+"a".repeat(39)}","host":"https://generated.example"}"""
        assertNull(DiagnosticAppConfiguration.resolve(generated,mapOf("SUPERMETRY_APP_ID" to "invalid!"),"https://fallback.example"))
        val config=requireNotNull(DiagnosticAppConfiguration.resolve(generated,mapOf("SUPERMETRY_APP_HOST" to "http://public.example"),"https://fallback.example"))
        assertEquals("https://fallback.example",config.host);assertFalse(config.sessionPings)
    }
    private fun validAppConfigurationIsIndependentOfLegacyOverrides(invalid:String) {
        val site=if(invalid in listOf("site","all")) "" else "legacy01"
        val host=if(invalid in listOf("host","all")) "http://public.example" else "https://legacy.example"
        val appURL=if(invalid in listOf("app_url","all")) "not-a-url" else "https://voicestudio.sh"
        assertTrue("Legacy-only validation must remain strict",runCatching {DiagnosticDestination(site,host,appURL)}.isFailure)
        val app=DiagnosticAppConfiguration("native01","smk_"+"a".repeat(39),"https://intake.example")
        val destination=DiagnosticDestination(site,host,appURL,app)
        assertEquals("https://intake.example/api/public/v1/app-events",destination.endpoint.toString())
        assertEquals("native01",destination.app?.appID)
    }
    @Test fun validAppIgnoresInvalidLegacySite()=validAppConfigurationIsIndependentOfLegacyOverrides("site")
    @Test fun validAppIgnoresInvalidLegacyHost()=validAppConfigurationIsIndependentOfLegacyOverrides("host")
    @Test fun validAppIgnoresInvalidLegacyAppURL()=validAppConfigurationIsIndependentOfLegacyOverrides("app_url")
    @Test fun validAppIgnoresAllInvalidLegacyFields()=validAppConfigurationIsIndependentOfLegacyOverrides("all")
    @Test fun mixed202AndAmbiguousAndBooleanAcknowledgements() {
        fun completed(text:String)=diagnosticAppCompleted(DiagnosticTransportReply(202,text.toByteArray()),4)
        assertEquals(setOf(0,1,2),completed("""{"accepted":1,"dropped":[{"index":1,"reason":"invalid"},{"index":2,"reason":"duplicate"},{"index":3,"reason":"rate_limited"}]}"""))
        assertTrue(completed("""{"accepted":1,"dropped":[]}""").isEmpty())
        assertTrue(completed("""{"accepted":true,"dropped":[]}""").isEmpty())
        assertTrue(completed("""{"accepted":3,"dropped":[{"index":true,"reason":"invalid"}]}""").isEmpty())
        assertEquals(setOf(0,1,2,3),diagnosticAppCompleted(DiagnosticTransportReply(400),4))
    }
    @Test fun actualWorkerBatchesBoundedAndRecordsNoCredentialsOrSessionByDefault()=runTest {
        val store=Store();val transport=Transport();val reports=reports(store,transport,testScheduler)
        reports.setConsent(true)
        repeat(45) {i->assertTrue(reports.capture(DiagnosticInput("engine_failed",frames=listOf(DiagnosticFrame("backend/main.py",i+1))))) }
        reports.flush()
        assertEquals(45,transport.bodies.sumOf {it.getValue("events").jsonArray.size})
        transport.bodies.forEach {assertTrue(it.getValue("events").jsonArray.size<=20);assertTrue(it.toString().toByteArray().size<=16384)}
        assertTrue(transport.bodies.flatMap {it.getValue("events").jsonArray}.none {it.jsonObject["kind"]==JsonPrimitive("session") || "session_id" in it.jsonObject})
        assertEquals(0,reports.status.pendingCount);assertFalse(store.files.values.any {it.toString(Charsets.UTF_8).contains("smk_")})
        reports.close()
    }
    @Test fun unauthorizedPausesEntireRunAndOffClearsQueue()=runTest {
        val store=Store();val transport=Transport();transport.replies.add(DiagnosticTransportReply(401));val results=mutableListOf<DiagnosticDeliveryResult>();val reports=reports(store,transport,testScheduler,results=results)
        reports.setConsent(true);reports.capture(DiagnosticInput("engine_failed"));reports.flush()
        assertEquals(1,transport.bodies.size);assertEquals(1,reports.status.pendingCount);assertEquals("unauthorized",results.single().reason)
        advanceTimeBy(60001);runCurrent();reports.capture(DiagnosticInput("download_failed"));reports.flush();assertEquals(1,transport.bodies.size)
        reports.setConsent(false);assertEquals(0,reports.status.pendingCount);assertFalse(store.files.containsKey("outbox.json"))
        reports.setConsent(true);reports.capture(DiagnosticInput("model_load_failed"));reports.flush();assertEquals(1,transport.bodies.size)
        reports.close()
    }
    @Test fun retryKeepsIDsAndMixedDropWarningRemainsLocal()=runTest {
        val store=Store();val transport=Transport();val results=mutableListOf<DiagnosticDeliveryResult>()
        transport.replies.add(DiagnosticTransportReply(429));transport.replies.add(DiagnosticTransportReply(503))
        transport.replies.add(DiagnosticTransportReply(202,"""{"accepted":1,"dropped":[{"index":1,"reason":"rate_limited"},{"index":2,"reason":"too_old"}],"warnings":[{"index":0,"reason":"tag_not_allowed","tag":"stage"}]}""".toByteArray()))
        val reports=reports(store,transport,testScheduler,results=results);reports.setConsent(true)
        listOf("engine_failed","download_failed","model_load_failed").forEach {reports.capture(DiagnosticInput(it))}
        reports.flush()
        val ids=transport.bodies.map {it.getValue("events").jsonArray.map {it.jsonObject.getValue("id")}}
        assertEquals(3,ids.size);assertEquals(ids[0],ids[1]);assertEquals(ids[1],ids[2]);assertEquals(1,reports.status.pendingCount)
        assertTrue(results.any {it.reason=="tag_not_allowed" && it.warningTag=="stage"});assertTrue(results.any {it.reason=="too_old"})
        advanceTimeBy(60001);runCurrent();reports.flush();assertEquals(0,reports.status.pendingCount)
        reports.close()
    }
    @Test fun optionalSessionIsOncePerRunAndWithdrawalEndsCorrelation()=runTest {
        val store=Store();val transport=Transport();val reports=reports(store,transport,testScheduler,pings=true)
        reports.setConsent(true);reports.capture(DiagnosticInput("engine_failed"));reports.flush()
        val events=transport.bodies.flatMap {it.getValue("events").jsonArray}.map {it.jsonObject}
        assertEquals(1,events.count {it["kind"]==JsonPrimitive("session")});assertEquals(1,events.map {it.getValue("session_id")}.toSet().size)
        assertFalse(store.files.values.any {it.toString(Charsets.UTF_8).contains("session_id")})
        reports.setConsent(false);reports.setConsent(true);reports.capture(DiagnosticInput("model_load_failed"));reports.flush()
        assertTrue(transport.bodies.last().getValue("events").jsonArray.all {"session_id" !in it.jsonObject && it.jsonObject["kind"]!=JsonPrimitive("session")})
        reports.close()
    }
    @Test fun actualTransportUsesAppEndpointKeyAgentAndNoOrigin()=runTest {
        val server=MockWebServer();server.start()
        val transport=OkHttpDiagnosticTransport()
        try {
            server.enqueue(MockResponse().setResponseCode(202).setBody("""{"accepted":1,"dropped":[]}"""))
            val config=DiagnosticAppConfiguration("native01","smk_"+"a".repeat(39),server.url("/").toString())
            val reply=transport.sendApp(DiagnosticDestination("legacy01","https://legacy.example","https://voicestudio.sh",config),"{}".toByteArray(),"VoiceStudio/0.5.9-beta.3 (android; arm64)") {true}
            assertEquals(202,reply.status);val request=server.takeRequest()
            assertEquals("/api/public/v1/app-events",request.path);assertEquals(config.ingestKey,request.getHeader("X-Supermetry-Key"));assertNull(request.getHeader("Origin"));assertEquals("VoiceStudio/0.5.9-beta.3 (android; arm64)",request.getHeader("User-Agent"))
        }finally {transport.cancelAll();server.shutdown()}
    }
    @Test fun initialDeclineDoesNotRetireFirstSessionGrant()=runTest {
        for(pings in listOf(false,true)) {
            val store=Store();val transport=Transport();val reports=reports(store,transport,testScheduler,pings=pings)
            reports.load();reports.setConsent(false);reports.setConsent(false)
            assertTrue(transport.bodies.isEmpty());assertFalse(store.files.containsKey("consent.json"))
            reports.setConsent(true);assertTrue(reports.capture(DiagnosticInput("engine_failed")))
            reports.flush();reports.flush()
            val events=transport.bodies.flatMap {it.getValue("events").jsonArray}.map {it.jsonObject}
            assertEquals(if(pings)1 else 0,events.count {it["kind"]==JsonPrimitive("session")})
            assertTrue(events.filter {it["kind"]!=JsonPrimitive("session")}.all {("session_id" in it)==pings})
            reports.close()
        }
    }
    @Test fun withdrawalBeforeFirstFlushStillRetiresRunSession()=runTest {
        val store=Store();val transport=Transport();val reports=reports(store,transport,testScheduler,pings=true)
        reports.setConsent(true);reports.setConsent(false);reports.setConsent(true)
        assertTrue(reports.capture(DiagnosticInput("engine_failed")));reports.flush()
        val events=transport.bodies.flatMap {it.getValue("events").jsonArray}.map {it.jsonObject}
        assertEquals(1,events.size)
        assertTrue(events.all {it["kind"]!=JsonPrimitive("session") && "session_id" !in it})
        reports.close()
    }
    @Test fun priorRunOffChoiceDoesNotRetireANewRunsFirstGrant()=runTest {
        val store=Store();val firstTransport=Transport();val first=reports(store,firstTransport,testScheduler,pings=true)
        first.setConsent(true);first.setConsent(false);first.close()
        val transport=Transport();val reports=reports(store,transport,testScheduler,pings=true)
        reports.load();reports.setConsent(true);assertTrue(reports.capture(DiagnosticInput("engine_failed")));reports.flush()
        val events=transport.bodies.flatMap {it.getValue("events").jsonArray}.map {it.jsonObject}
        assertEquals(1,events.count {it["kind"]==JsonPrimitive("session")})
        assertTrue(events.filter {it["kind"]!=JsonPrimitive("session")}.all {"session_id" in it})
        reports.close()
    }

}
