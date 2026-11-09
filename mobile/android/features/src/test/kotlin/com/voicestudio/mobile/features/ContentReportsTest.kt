package com.voicestudio.mobile.features

import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ContentReport
import com.voicestudio.mobile.network.ContentReportClient
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.*
import org.junit.Assert.*

class ContentReportsTest {
    private lateinit var server: MockWebServer
    private lateinit var client: ContentReportClient
    private val subject = ReportSubject("speak:phone:1", "speak", "phone", "org/model", "a".repeat(40), "Typed words")
    @Before fun setup() { server = MockWebServer(); server.start(); client = ContentReportClient(server.url("/").toString(), "1.2.3") }
    @After fun teardown() { server.shutdown() }
    private fun model(id: String, capability: String, revision: String = "a".repeat(40)) = PhoneModel(id, capability, "omnivoice.cpp", "org/$id", revision,
        emptyList(), 1, listOf("en"), 1.0, ModelLicence("mit", "commercial-ok", false, "", "", "b".repeat(64)))

    @Test fun draftStartsEmptyAndSurvivesClosingTheSheet() {
        runBlocking {
            val reports = ContentReports(this)
            reports.open(subject)
            val opened = reports.sheet.value!!
            assertNull(opened.draft.reason)
            assertFalse(opened.draft.includeText)
            assertFalse("Send is disabled until a reason is chosen", opened.canSend)
            reports.edit { it.copy(reason = "hateful", note = "n".repeat(1500), includeText = true) }
            reports.close()
            assertNull(reports.sheet.value)
            reports.open(subject)
            val reopened = reports.sheet.value!!
            assertEquals("hateful", reopened.draft.reason)
            assertEquals(ContentReport.NOTE_LIMIT, reopened.draft.note.length)
            assertTrue(reopened.draft.includeText)
            assertEquals(opened.draft.idempotencyKey, reopened.draft.idempotencyKey)
            assertTrue(reopened.canSend)
            // Another result has its own draft.
            reports.open(subject.copy(id = "speak:phone:2"))
            assertNull(reports.sheet.value!!.draft.reason)
        }
    }
    @Test fun includeTextNeedsTypedText() {
        runBlocking {
            val reports = ContentReports(this)
            reports.open(ReportSubject("clone:computer:p", "clone", "computer", "unknown"))
            reports.edit { it.copy(includeText = true) }
            assertFalse(reports.sheet.value!!.draft.includeText)
        }
    }
    @Test fun failureKeepsDraftAndKeyForRetryThenSuccessDiscardsIt() {
        runBlocking {
            val reports = ContentReports(this)
            reports.open(subject)
            reports.edit { it.copy(reason = "other") }
            val key = reports.sheet.value!!.draft.idempotencyKey
            server.enqueue(MockResponse().setResponseCode(503))
            reports.send(client)!!.join()
            assertEquals(ReportStatus.FAILED, reports.sheet.value!!.status)
            assertEquals(key, reports.draft(subject.id)!!.idempotencyKey)
            var sent = 0
            val collector = launch(kotlinx.coroutines.Dispatchers.Unconfined) { reports.sent.collect { sent++ } }
            server.enqueue(MockResponse().setResponseCode(201).setBody("{}"))
            reports.send(client)!!.join()
            assertEquals(key, server.takeRequest().getHeader("Idempotency-Key"))
            assertEquals(key, server.takeRequest().getHeader("Idempotency-Key"))
            assertNull("The sheet closes", reports.sheet.value)
            assertNull("The draft is discarded", reports.draft(subject.id))
            yield()
            assertEquals(1, sent)
            collector.cancel()
        }
    }
    @Test fun conflictKeepsTheNewKey() {
        runBlocking {
            val reports = ContentReports(this)
            reports.open(subject)
            reports.edit { it.copy(reason = "other") }
            val key = reports.sheet.value!!.draft.idempotencyKey
            server.enqueue(MockResponse().setResponseCode(409)); server.enqueue(MockResponse().setResponseCode(503))
            reports.send(client)!!.join()
            server.takeRequest()
            val second = server.takeRequest().getHeader("Idempotency-Key")
            assertNotEquals(key, second)
            assertEquals(second, reports.draft(subject.id)!!.idempotencyKey)
        }
    }
    @Test fun rateLimitedAndInvalidKeepTheSheet() {
        runBlocking {
            val reports = ContentReports(this)
            reports.open(subject)
            reports.edit { it.copy(reason = "other") }
            server.enqueue(MockResponse().setResponseCode(429))
            reports.send(client)!!.join()
            assertEquals(ReportStatus.RATE_LIMITED, reports.sheet.value!!.status)
            assertEquals("other", reports.sheet.value!!.draft.reason)
            server.enqueue(MockResponse().setResponseCode(400))
            reports.send(client)!!.join()
            assertEquals(ReportStatus.INVALID, reports.sheet.value!!.status)
            assertFalse("A rejected report is not retried", reports.sheet.value!!.canSend)
            assertNull(reports.send(client))
            assertEquals(2, server.requestCount)
        }
    }
    @Test fun subjectsCarryTaskPlaceAndModel() {
        val take = ReportSubjects.phoneSpeak(PhoneGeneration("t", "Hello", 1, "org/tts", "c".repeat(40)))
        assertEquals(listOf("speak", "phone", "org/tts", "c".repeat(40), "Hello"), listOf(take.task, take.runsOn, take.modelId, take.modelRevision, take.typedText))
        val older = ReportSubjects.phoneSpeak(PhoneGeneration("t", "Hello", 1))
        assertEquals("unknown", older.modelId); assertNull(older.modelRevision)
        val voice = ReportSubjects.phoneClone(PhoneVoice("v", "Name", "Transcript", 1, 1, modelRepo = "org/asr", modelRevision = "d".repeat(40)))
        assertEquals(listOf("clone", "phone", "org/asr", "d".repeat(40)), listOf(voice.task, voice.runsOn, voice.modelId, voice.modelRevision))
        assertFalse(voice.hasTypedText)
        val host = ReportSubjects.computerClone(com.voicestudio.mobile.network.Profile("p", "Name"))
        assertEquals(listOf("clone", "computer", "unknown"), listOf(host.task, host.runsOn, host.modelId))
        val hostTake = ReportSubjects.computerSpeak("/audio/a.wav", "Typed", "omni voice!")
        assertEquals(listOf("speak", "computer", "omnivoice", "Typed"), listOf(hostTake.task, hostTake.runsOn, hostTake.modelId, hostTake.typedText))
        assertEquals("unknown", ReportSubjects.computerSpeak("/audio/a.wav", "Typed", null).modelId)
        assertEquals(listOf("dub", "computer", "unknown"), ReportSubjects.computerDub("job").let { listOf(it.task, it.runsOn, it.modelId) })
    }
    @Test fun phoneDubReportsThePinnedVoiceModel() {
        val clone = model("clone-a", "clone"); val asr = model("asr-a", "asr")
        val request = PhoneDubRequest("id", "Title", "source.mp4", emptyList(), listOf(modelPin(asr), modelPin(clone)), 1, 1)
        val exact = ReportSubjects.phoneDub(request, listOf(asr, clone))
        assertEquals(listOf("dub", "phone", "org/clone-a", "a".repeat(40)), listOf(exact.task, exact.runsOn, exact.modelId, exact.modelRevision))
        // The catalogue moved to a new revision: keep the repo, never claim the new revision.
        val moved = ReportSubjects.phoneDub(request, listOf(asr, model("clone-a", "clone", "e".repeat(40))))
        assertEquals("org/clone-a", moved.modelId); assertNull(moved.modelRevision)
        assertEquals("unknown", ReportSubjects.phoneDub(request, listOf(asr)).modelId)
    }
    @Test fun reasonsMatchTheContractOrder() {
        assertEquals(ContentReport.REASONS, reportReasons.map { it.first })
        reportReasons.forEach { (id, key) -> assertEquals("mobile.report.reasons.$id", key) }
    }
}
