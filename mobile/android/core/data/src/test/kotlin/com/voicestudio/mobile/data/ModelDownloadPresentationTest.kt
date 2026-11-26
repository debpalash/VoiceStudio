package com.voicestudio.mobile.data

import com.voicestudio.mobile.local.DownloadProgress
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelDownloadPresentationTest {
    @Test fun everyWifiPolicyWaitOffersAWayForward() {
        assertTrue(canUseThisConnection(DownloadProgress(running=true,stage="waiting")))
        assertTrue(canUseThisConnection(DownloadProgress(running=true,stage="waiting_metered")))
    }

    @Test fun otherStatesDoNotOfferIt() {
        for(stage in listOf("downloading","connecting","verifying","retrying","waiting_network","paused"))
            assertFalse(stage,canUseThisConnection(DownloadProgress(running=true,stage=stage)))
        assertFalse(canUseThisConnection(DownloadProgress(running=false,stage="waiting")))
    }
}
