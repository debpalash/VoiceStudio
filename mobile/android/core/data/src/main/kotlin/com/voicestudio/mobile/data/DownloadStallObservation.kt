package com.voicestudio.mobile.data

import com.voicestudio.mobile.local.DownloadProgress

/** Observe a stalled active transfer; never cancel or change the transfer itself. */
internal class DownloadStallObservation {
    private var previous: Pair<String, Long>? = null
    private var changedAt = 0L
    private var reported = false

    fun observe(progress: DownloadProgress?, nowMs: Long): Boolean {
        if (progress == null || !progress.running || progress.retrySeconds > 0 ||
            progress.stage != "downloading") {
            previous = null
            reported = false
            return false
        }
        val current = progress.stage to progress.received
        if (current != previous || nowMs < changedAt) {
            previous = current
            changedAt = nowMs
            reported = false
        }
        if (reported || nowMs - changedAt < 600_000) return false
        reported = true
        return true
    }
}
