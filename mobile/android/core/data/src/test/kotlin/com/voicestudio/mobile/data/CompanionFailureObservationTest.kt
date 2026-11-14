package com.voicestudio.mobile.data

import com.voicestudio.mobile.network.*
import org.junit.Assert.*
import org.junit.Test

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
}
