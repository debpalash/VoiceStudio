package com.voicestudio.mobile.data

import com.voicestudio.mobile.local.DownloadProgress
import org.junit.Assert.*
import org.junit.Test

class DownloadStallObservationTest {
    @Test fun reportsOnlyAfterTenMinutesAndOncePerUnchangedTransfer() {
        val observation = DownloadStallObservation()
        val progress = DownloadProgress(received=10,total=100,running=true)
        assertFalse(observation.observe(progress,0))
        assertFalse(observation.observe(progress,599_999))
        assertTrue(observation.observe(progress,600_000))
        assertFalse(observation.observe(progress,1_200_000))
        assertFalse(observation.observe(progress.copy(received=20),1_200_000))
        assertTrue(observation.observe(progress.copy(received=20),1_800_000))
    }

    @Test fun waitingBackoffPauseAndCompletionNeverCountAsAStall() {
        for(stage in listOf("waiting","waiting_network","paused","retrying","failed","verifying")) {
            val observation = DownloadStallObservation()
            val progress = DownloadProgress(running=true,stage=stage)
            assertFalse(observation.observe(progress,0))
            assertFalse(observation.observe(progress,900_000))
        }
        val observation = DownloadStallObservation()
        val paused = DownloadProgress(running=false)
        assertFalse(observation.observe(paused,0))
        assertFalse(observation.observe(paused,900_000))
        val retry = DownloadProgress(running=true,retrySeconds=10)
        assertFalse(observation.observe(retry,0))
        assertFalse(observation.observe(retry,900_000))
    }

    @Test fun aStateTransitionRestartsObservationWithoutAbortingWork() {
        val observation = DownloadStallObservation()
        val progress = DownloadProgress(received=100,total=100,running=true)
        assertFalse(observation.observe(progress,0))
        assertFalse(observation.observe(progress.copy(stage="verifying"),500_000))
        assertFalse(observation.observe(progress.copy(stage="verifying"),1_099_999))
        assertFalse(observation.observe(progress.copy(stage="verifying"),1_100_000))
        assertFalse(observation.observe(null,1_200_000))
    }
}
