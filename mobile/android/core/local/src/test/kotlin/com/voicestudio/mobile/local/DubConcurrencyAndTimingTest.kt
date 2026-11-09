package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.UUID

class DubConcurrencyAndTimingTest {
    @get:Rule val temporary=TemporaryFolder()

    @Test fun exclusiveRunsForTheSameJobNeverInterleave() = runBlocking {
        val store=PhoneDubStore(File(temporary.root,"jobs"));val id=UUID.randomUUID().toString()
        val events=java.util.Collections.synchronizedList(mutableListOf<String>())
        val first=launch(Dispatchers.Default) {store.exclusive(id) {events+="a-start";delay(150);events+="a-end"}}
        delay(50)
        val second=launch(Dispatchers.Default) {store.exclusive(id) {events+="b-start";events+="b-end"}}
        joinAll(first,second)
        assertEquals(listOf("a-start","a-end","b-start","b-end"),events)
    }
    @Test fun deleteWaitsForTheRunningJobToFinishSaving() = runBlocking {
        val store=PhoneDubStore(File(temporary.root,"jobs"));val id=UUID.randomUUID().toString()
        store.directory(id).mkdirs()
        var finished=false;var sawDirectory=false
        val running=launch(Dispatchers.Default) {store.exclusive(id) {delay(150);sawDirectory=store.directory(id).exists();finished=true}}
        delay(50)
        store.delete(id)
        assertTrue(finished);assertTrue(sawDirectory);assertFalse(store.directory(id).exists())
        running.join()
    }
    @Test fun differentJobsDoNotBlockEachOther() = runBlocking {
        val store=PhoneDubStore(File(temporary.root,"jobs"))
        val gate=CompletableDeferred<Unit>()
        val holder=launch(Dispatchers.Default) {store.exclusive(UUID.randomUUID().toString()) {gate.await()}}
        delay(50)
        withTimeout(2000) {store.exclusive(UUID.randomUUID().toString()) {}}
        gate.complete(Unit);holder.join()
    }

    @Test fun decodedBuffersPreserveShortGapsAndTrimOverlap() = runBlocking {
        val output=File(temporary.root,"decoded.pcm")
        java.io.FileOutputStream(output).use {stream ->
            var written=appendDecodedPcm(stream,FloatArray(1200) {.25f},50_000,0,1)
            assertEquals(2400L,written) // 50 ms of silence then 50 ms of speech
            written=appendDecodedPcm(stream,FloatArray(1200) {.5f},150_000,written,1)
            assertEquals(4800L,written)
            written=appendDecodedPcm(stream,FloatArray(1200) {.75f},175_000,written,1)
            assertEquals(5400L,written) // overlapping 25 ms is discarded
        }
        val samples=PcmFile(output).read(0,5400).samples
        assertTrue(samples.sliceArray(0 until 1200).all {it==0f})
        assertTrue(samples.sliceArray(1200 until 2400).all {it==.25f})
        assertTrue(samples.sliceArray(2400 until 3600).all {it==0f})
        assertTrue(samples.sliceArray(3600 until 4800).all {it==.5f})
        assertTrue(samples.sliceArray(4800 until 5400).all {it==.75f})
    }

    @Test fun rawTimelineInsertsSilenceForLateStartAndGaps() {
        assertEquals(24000L,timelineShift(1_000_000,0))
        assertEquals(24000L,timelineShift(1_500_000,12000)) // 1.5 s = 36000 samples, 12000 written
    }
    @Test fun rawTimelineDropsOverlapAndIgnoresJitterAndMissingTime() {
        assertEquals(-2400L,timelineShift(900_000,24000))
        assertEquals(0L,timelineShift(1_004_000,24000))
        assertEquals(0L,timelineShift(-1,500))
    }

    @Test fun pausesSplitOneWindowIntoTimedUtterances() {
        val tokens=listOf(" Hello"," there","."," Much"," later",".")
        val ts=listOf(0.5f,0.9f,1.2f,9.0f,9.4f,9.8f)
        val parts=splitUtterances("Hello there. Much later.",tokens,ts,listOf(.3f,.3f,.2f,.3f,.3f,.2f),20.0)
        assertEquals(listOf("Hello there.","Much later."),parts.map {it.text})
        assertEquals(.5,parts[0].start,1e-6);assertEquals(9.0,parts[1].start,1e-6)
        assertTrue(parts[0].end<=9.0&&parts[1].end<=20.0&&parts[1].end>parts[1].start)
    }
    @Test fun continuousSpeechAndMismatchedTimestampsStayOneSegment() {
        val one=splitUtterances("a b c",listOf("a"," b"," c"),listOf(0f,.4f,.8f),emptyList(),20.0)
        assertEquals(1,one.size);assertEquals("a b c",one[0].text)
        val bad=splitUtterances("a b",listOf("a"," b"),listOf(0f),emptyList(),20.0)
        assertEquals(1,bad.size)
    }
}
