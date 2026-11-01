package com.voicestudio.mobile.local

/** Counts unique model bytes, never retries. A resumed prefix is the speed baseline. */
internal class DownloadMeter(private val total: Long, private val clock: ()->Double = {System.nanoTime()/1e9}) {
    private val ranges=mutableMapOf<String,Long>()
    private var sampledAt=clock()
    private var sampledBytes=0L
    private var speed=0.0
    @Synchronized fun seed(key: String, bytes: Long) {ranges[key]=bytes;sampledBytes=ranges.values.sum();sampledAt=clock()}
    @Synchronized fun update(key: String, bytes: Long, stage: String="downloading", retry: Long=0): DownloadProgress {
        ranges[key]=maxOf(ranges[key] ?: 0,bytes)
        val received=ranges.values.sum().coerceAtMost(total)
        val now=clock();val elapsed=now-sampledAt
        if(elapsed>=0.5) {
            val current=(received-sampledBytes).coerceAtLeast(0)/elapsed
            speed=if(speed==0.0) current else speed*0.65+current*0.35
            sampledAt=now;sampledBytes=received
        }
        if(stage!="downloading") {speed=0.0;sampledAt=now;sampledBytes=received}
        return DownloadProgress(received,total,true,bytesPerSecond=speed,etaSeconds=if(speed>0&&stage=="downloading") (total-received)/speed else null,stage=stage,retrySeconds=retry)
    }
}
