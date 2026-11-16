package com.voicestudio.mobile.features.dub

import com.voicestudio.mobile.data.CompanionDiagnosticObservation
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.TimeUnit

class ComputerDubMonitorConsentTest {
    private val a=CompanionDiagnosticObservation(1,"a".repeat(32))
    private val b=CompanionDiagnosticObservation(2,"b".repeat(32))
    private fun page(seq:Int,type:String="progress",status:String="running",watermark:Boolean=true,time:Double=100.0,watermarkSeq:Int=seq)=buildJsonObject {
        putJsonObject("job") {put("id","task");put("status",status);put("created_at",100.0);put("updated_at",time)}
        putJsonArray("events") {addJsonObject {put("seq",seq);put("created_at",time);put("payload","data: {\"type\":\"$type\"}")}}
        if(watermark) putJsonObject("event_watermark") {put("seq",watermarkSeq);put("created_at",time)}
    }.toString()

    private suspend fun observedFailure(server:MockWebServer,submitted:CompanionDiagnosticObservation?=null,
        observe:()->CompanionDiagnosticObservation?={a},pause:suspend()->Unit={}):List<CompanionDiagnosticObservation> {
        val reported=mutableListOf<CompanionDiagnosticObservation>()
        var cleared=false
        val operation=observe()
        val error=try {
            withTimeout(5000) {
                monitorComputerDubTask(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),"task",
                    {assertNull(it);cleared=true},{fail("Failed task must not complete")},submitted,observe,pause)
            }
            error("Expected terminal failure")
        } catch(error:Throwable) {if(error is AssertionError)throw error;error}
        if(error is CancellationException) throw error
        assertTrue("Terminal task failure must not be an assertion/API fallback",error is ComputerDubRemoteTaskFailure)
        deliverComputerDubFailure(error,{if(operation!=null && operation==observe()) reported+=operation},
            {if(it!=null && it==observe()) reported+=it})
        assertTrue("Terminal failure retires its saved receipt",cleared)
        return reported
    }

    @Test fun restoredTerminalAndUnknownHistoryNeverAcquireTheResumeGrant():Unit=runBlocking {
        for(known in listOf(true,false)) {
            val server=MockWebServer().apply {start()}
            try {
                server.enqueue(MockResponse().setBody(page(7,"error","failed",known)))
                assertTrue(observedFailure(server).isEmpty())
                assertEquals("/jobs/task/events?after_seq=0",server.takeRequest(1,TimeUnit.SECONDS)?.path)
            } finally {server.shutdown()}
        }
    }
    @Test fun cancelledRemoteTaskStaysLocalEvenWithSubmissionAuthority():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {server.enqueue(MockResponse().setBody(page(1,"cancelled","cancelled")));assertTrue(observedFailure(server,a).isEmpty())}
        finally {server.shutdown()}
    }
    @Test fun originalSubmissionAndFreshPostBaselineFailuresRemainReportable():Unit=runBlocking {
        for(submitted in listOf(a,null)) {
            val server=MockWebServer().apply {start()}
            try {
                if(submitted==null) server.enqueue(MockResponse().setBody(page(7)))
                server.enqueue(MockResponse().setBody(page(8,"error","failed",time=101.0)))
                assertEquals(listOf(a),observedFailure(server,submitted))
                if(submitted==null) {
                    server.takeRequest(1,TimeUnit.SECONDS)
                    assertEquals("/jobs/task/events?after_seq=7",server.takeRequest(1,TimeUnit.SECONDS)?.path)
                }
            } finally {server.shutdown()}
        }
    }
    @Test fun regrantCannotBorrowTheOriginalSubmission():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            server.enqueue(MockResponse().setBody(page(1,"error","failed")))
            assertTrue(observedFailure(server,a,observe={b}).isEmpty())
        } finally {server.shutdown()}
    }
    @Test fun aLaterGrantRequiresItsOwnBaselineButCanReportANewFollowingEvent():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        var current:CompanionDiagnosticObservation?=null
        try {
            server.enqueue(MockResponse().setBody(page(6)))
            server.enqueue(MockResponse().setBody(page(7)))
            server.enqueue(MockResponse().setBody(page(8,"error","failed",time=101.0)))
            assertEquals(listOf(b),observedFailure(server,observe={current},pause={current=b}))
            assertEquals(3,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun directApiFailuresRetainTheOperationHandlerAndTheSavedReceipt():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        var cleared=false
        var fallback:Throwable?=null
        var remote=false
        try {
            server.enqueue(MockResponse().setResponseCode(500).setBody("{}"))
            val error=try {
                withTimeout(5000) {monitorComputerDubTask(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),"task",
                    {cleared=true},{fail("Unexpected completion")},observe={a})}
                error("Expected API failure")
            } catch(error:Throwable) {error}
            assertTrue(error is ClientFailure)
            deliverComputerDubFailure(error,{fallback=it},{remote=true})
            assertSame(error,fallback)
            assertFalse(remote)
            assertFalse(cleared)
        } finally {server.shutdown()}
    }
    @Test fun callerCancellationRetainsTheReceiptAndDoesNotBecomeARemoteFailure():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        var cleared=false
        val identity=IllegalStateException("controlled caller identity")
        val cancellation=CancellationException("controlled caller cancellation").apply {initCause(identity)}
        try {
            server.enqueue(MockResponse().setBody(page(6)))
            val error=runCatching {withTimeout(5000) {
                monitorComputerDubTask(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),"task",
                    {cleared=true},{fail("Unexpected completion")},observe={a},pause={throw cancellation})
            }}.exceptionOrNull()
            assertTrue("Watchdog timeout is not caller cancellation",error is CancellationException && error !is TimeoutCancellationException)
            assertTrue("Coroutine exception copies must preserve the caller identity",
                generateSequence(error) {it.cause}.any {it===cancellation || it===identity})
            assertFalse(cleared)
        } finally {server.shutdown()}
    }
    @Test fun terminalStatusDrainsThroughItsWatermarkBeforeRetiringTheReceipt():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            server.enqueue(MockResponse().setBody(page(7)))
            server.enqueue(MockResponse().setBody(page(8,status="failed",time=101.0,watermarkSeq=10)))
            server.enqueue(MockResponse().setBody(page(10,"error","failed",time=102.0)))
            assertEquals(listOf(a),observedFailure(server))
            server.takeRequest(1,TimeUnit.SECONDS);server.takeRequest(1,TimeUnit.SECONDS)
            assertEquals("/jobs/task/events?after_seq=8",server.takeRequest(1,TimeUnit.SECONDS)?.path)
        } finally {server.shutdown()}
    }

    @Test fun stalledTerminalPaginationKeepsTheReceipt():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        var cleared=false
        try {
            server.enqueue(MockResponse().setBody(page(7)))
            server.enqueue(MockResponse().setBody(page(8,status="failed",watermarkSeq=10)))
            server.enqueue(MockResponse().setBody(page(8,status="failed",watermarkSeq=10)))
            val error=runCatching {withTimeout(5000) {monitorComputerDubTask(
                VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),"task",{cleared=true},{fail("Unexpected completion")},observe={a},pause={})}}.exceptionOrNull()
            assertTrue(error is ClientFailure)
            assertEquals("mobile.android.invalid_response",(error as ClientFailure).key)
            assertFalse(cleared)
            assertEquals(3,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun historicalTerminalPrefixUsesWatermarkWithoutScanningAllHistory():Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            server.enqueue(MockResponse().setBody(page(1,"error","failed",watermarkSeq=10000)))
            assertTrue(observedFailure(server).isEmpty())
            assertEquals(1,server.requestCount)
        } finally {server.shutdown()}
    }
}
