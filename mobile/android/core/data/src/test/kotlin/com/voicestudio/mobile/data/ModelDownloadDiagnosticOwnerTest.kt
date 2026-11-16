package com.voicestudio.mobile.data

import com.voicestudio.mobile.local.DownloadProgress
import com.voicestudio.mobile.network.DiagnosticInput
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ModelDownloadDiagnosticOwnerTest {
    private fun stalledTransfer(original:CompanionDiagnosticObservation?,replace:Boolean)=runTest {
        var current=original
        val reports=mutableListOf<CompanionDiagnosticObservation?>()
        val progress=DownloadProgress(received=10,total=100,running=true,stage="downloading")
        fun monitor(owner:CompanionDiagnosticObservation?)=backgroundScope.launch {
            monitorModelDownloadStalls(owner,{progress},{current},{input,observation ->
                assertEquals("install_stalled",input.category);reports.add(observation)
            },{testScheduler.currentTime},{delay(30_000)})
        }
        val old=monitor(original)
        runCurrent()
        if(replace) {
            advanceTimeBy(30_000);runCurrent()
            current=CompanionDiagnosticObservation(2,"b".repeat(32))
        }
        advanceTimeBy(660_000);runCurrent()
        assertEquals("Only the original transfer grant may authorize a stalled-transfer report",if(replace||original==null)0 else 1,reports.size)
        old.cancelAndJoin()
        if(replace) {
            val fresh=monitor(current)
            runCurrent();advanceTimeBy(600_000);runCurrent()
            assertEquals("A genuinely new transfer may observe the current grant",listOf(current),reports)
            fresh.cancelAndJoin()
        }
    }
    @Test fun offStartedTransferCannotBorrowOptIn()=stalledTransfer(null,true)
    @Test fun oldTransferCannotBorrowReplacementGrant()=stalledTransfer(CompanionDiagnosticObservation(1,"a".repeat(32)),true)
    @Test fun sameGrantTransferReportsOnce()=stalledTransfer(CompanionDiagnosticObservation(1,"a".repeat(32)),false)
    @Test fun unchangedOffTransferStaysLocal()=stalledTransfer(null,false)
}
