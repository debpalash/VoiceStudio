package com.voicestudio.mobile.data

import com.voicestudio.mobile.network.*
import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.json.*
import kotlinx.serialization.decodeFromString

class CompanionFailureObservationTest {
    private val a=CompanionDiagnosticObservation(1,"a".repeat(32))
    private val b=CompanionDiagnosticObservation(2,"b".repeat(32))
    private fun page(sequence: Int,time: Double?,status: String="running") = JobEvents(
        Job(status=status,createdAt=100.0,updatedAt=100.0),
        listOf(JobEvent(sequence,"data: {\"type\":\"error\"}",time)))
    @Test fun unknownOrChangedConsentCannotAuthorizeTheResponseOrArmANewGrantFromAnOldRequest() {
        val gate=CompanionTaskFailureGate()
        assertNull(gate.observe("task",page(7,100.0),a,b,null))
        assertNull(gate.observe("task",page(8,101.0,"failed"),b,b,null))
        assertNull(CompanionTaskFailureGate().observe("task",page(1,101.0,"failed"),null,b,null))
    }
    @Test fun submissionAuthorityIsLocalToItsOriginalGrantAndTask() {
        assertEquals(a,CompanionTaskFailureGate().observe("new-task",page(1,null,"failed"),a,a,a))
        assertNull(CompanionTaskFailureGate().observe("new-task",page(1,null,"failed"),b,b,a))
    }
    private fun wirePage(first:Int,last:Int,failure:Int?=null,eventTime:Double=100.0,
        watermark:Int?=2001,tailTime:Double?=100.0):JobEvents {
        val body=buildJsonObject {
            putJsonObject("job") {put("id","task");put("status","running");put("created_at",100.0);put("updated_at",100.0)}
            putJsonArray("events") {for(sequence in first..last) addJsonObject {
                put("seq",sequence);put("created_at",eventTime)
                put("payload", "data: {\"type\":\"${if(sequence==failure) "error" else "progress"}\"}")
            }}
            if(watermark!=null) putJsonObject("event_watermark") {
                put("seq",watermark);put("created_at",tailTime?.let(::JsonPrimitive)?:JsonNull)
            }
        }
        return Json {ignoreUnknownKeys=true}.decodeFromString(body.toString())
    }
    @Test fun trueHistoryTailExcludesAnArchivedErrorBeyondTheFirstPageButKeepsFreshFailures() {
        val gate=CompanionTaskFailureGate()
        assertNull(gate.observe("task",wirePage(1,500),a,a,null))
        assertNull(gate.observe("task",wirePage(501,1000,failure=501),a,a,null))
        assertNull(gate.observe("task",wirePage(2002,2002,failure=2002,eventTime=99.0,watermark=2002),a,a,null))
        assertEquals(a,gate.observe("task",wirePage(2003,2003,failure=2003,eventTime=101.0,watermark=2003,tailTime=101.0),a,a,null))
    }
    @Test fun unknownOrInvalidHistoryTailCannotAuthorizeAnotherPage() {
        for(scenario in listOf("missing","behind-page","missing-time")) {
            val gate=CompanionTaskFailureGate()
            val first=wirePage(1,500,watermark=if(scenario=="missing")null else if(scenario=="behind-page")499 else 2001,
                tailTime=if(scenario=="missing-time")null else 100.0)
            assertNull(gate.observe("task",first,a,a,null))
            assertNull(scenario,gate.observe("task",wirePage(501,1000,failure=501),a,a,null))
        }
    }
    @Test fun historyTailPreservesChangedGrantAndNewSubmissionAuthority() {
        val gate=CompanionTaskFailureGate()
        assertNull(gate.observe("task",wirePage(1,500),a,a,null))
        assertNull(gate.observe("task",wirePage(501,1000,failure=501),a,b,null))
        assertNull(gate.observe("task",wirePage(501,1000,failure=501),b,b,null))
        assertEquals(b,gate.observe("task",wirePage(2002,2002,failure=2002,eventTime=101.0,watermark=2002,tailTime=101.0),b,b,null))
        assertEquals(b,CompanionTaskFailureGate().observe("new-task",wirePage(1,1,failure=1,watermark=null),b,b,b))
    }

}
