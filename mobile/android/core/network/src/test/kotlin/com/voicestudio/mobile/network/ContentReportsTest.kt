package com.voicestudio.mobile.network

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.*
import org.junit.*
import org.junit.Assert.*

class ContentReportsTest {
    private lateinit var server: MockWebServer
    private lateinit var client: ContentReportClient
    private val phone = ReportSubject("speak:phone:1", "speak", "phone", "org/model", "a".repeat(40), "Hello there")
    private val computer = ReportSubject("clone:computer:p1", "clone", "computer", "unknown")
    @Before fun setup() { server = MockWebServer(); server.start(); client = ContentReportClient(server.url("/").toString(), "1.2.3") }
    @After fun teardown() { server.shutdown() }
    private fun created() = MockResponse().setResponseCode(201).setBody("""{"report_id":"ABCDEFGHIJKLMNOP","received_at":"2026-10-24T00:00:00Z"}""")
    private fun RecordedRequest.json() = Json.parseToJsonElement(body.readUtf8()).jsonObject

    @Test fun bodyHasExactlyTheDocumentedFieldsAndOmitsEmptyOptionals() = runBlocking {
        server.enqueue(created())
        val result = client.send(phone, ReportDraft(reason = "hateful", note = "   "))
        assertEquals(ReportOutcome.SENT, result.outcome)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/v1/content-reports", request.path)
        assertEquals(buildJsonObject {
            put("reason", "hateful"); put("platform", "android"); put("app_version", "1.2.3"); put("task", "speak")
            put("model_id", "org/model"); put("model_revision", "a".repeat(40)); put("runs_on", "phone")
        }, request.json())
    }
    @Test fun promptTextOnlyWhenTickedAndCapped() = runBlocking {
        val long = phone.copy(typedText = "x".repeat(6000))
        server.enqueue(created()); client.send(long, ReportDraft(reason = "other"))
        assertFalse(server.takeRequest().json().containsKey("prompt_text"))
        server.enqueue(created()); client.send(long, ReportDraft(reason = "other", includeText = true))
        assertEquals("x".repeat(5000), server.takeRequest().json()["prompt_text"]!!.jsonPrimitive.content)
        // A ticked box on a result without typed text sends nothing extra.
        server.enqueue(created()); client.send(computer, ReportDraft(reason = "other", includeText = true))
        assertFalse(server.takeRequest().json().containsKey("prompt_text"))
    }
    @Test fun noteIsTrimmedAndCapped() = runBlocking {
        server.enqueue(created()); client.send(phone, ReportDraft(reason = "sexual", note = "  sounds wrong \n"))
        assertEquals("sounds wrong", server.takeRequest().json()["note"]!!.jsonPrimitive.content)
        server.enqueue(created()); client.send(phone, ReportDraft(reason = "sexual", note = "n".repeat(1200)))
        assertEquals(1000, server.takeRequest().json()["note"]!!.jsonPrimitive.content.length)
    }
    @Test fun computerReportHasNoRevisionAndSanitizedModel() = runBlocking {
        server.enqueue(created())
        client.send(computer.copy(modelId = "my engine/v2:ö#1", modelRevision = "abc"), ReportDraft(reason = "impersonation"))
        val body = server.takeRequest().json()
        assertEquals("myengine/v2:1", body["model_id"]!!.jsonPrimitive.content)
        assertEquals("computer", body["runs_on"]!!.jsonPrimitive.content)
        assertEquals("clone", body["task"]!!.jsonPrimitive.content)
        assertFalse(body.containsKey("model_revision"))
        assertEquals(setOf("reason", "platform", "app_version", "task", "model_id", "runs_on"), body.keys)
    }
    @Test fun modelIdSanitizing() {
        assertEquals("unknown", ContentReport.modelId(null))
        assertEquals("unknown", ContentReport.modelId("  ##  "))
        assertEquals("a.b_c/d:e-f", ContentReport.modelId("a.b_c/d:e-f"))
        assertEquals(128, ContentReport.modelId("m".repeat(300)).length)
    }
    @Test fun headersCarryAKeyAndNoCredentials() = runBlocking {
        server.enqueue(created())
        val draft = ReportDraft(reason = "misleading")
        client.send(phone, draft)
        val request = server.takeRequest()
        assertTrue(request.getHeader("Content-Type")!!.startsWith("application/json"))
        assertEquals(draft.idempotencyKey, request.getHeader("Idempotency-Key"))
        assertTrue(Regex("[A-Za-z0-9]{32}").matches(draft.idempotencyKey))
        assertNull(request.getHeader("Authorization"))
        assertNull(request.getHeader("X-OmniVoice-Pin"))
        assertNull(request.getHeader("Cookie"))
    }
    @Test fun keysAreRandom() {
        val keys = (1..50).map { ContentReport.newKey() }
        assertEquals(50, keys.toSet().size)
        keys.forEach { assertTrue(it, Regex("[A-Za-z0-9]{32}").matches(it)) }
    }
    @Test fun retryReusesTheKey() = runBlocking {
        val draft = ReportDraft(reason = "other")
        server.enqueue(MockResponse().setResponseCode(503))
        val failed = client.send(phone, draft)
        assertEquals(ReportOutcome.FAILED, failed.outcome)
        assertEquals(draft.idempotencyKey, failed.idempotencyKey)
        server.enqueue(created())
        assertEquals(ReportOutcome.SENT, client.send(phone, draft.copy(idempotencyKey = failed.idempotencyKey)).outcome)
        assertEquals(server.takeRequest().getHeader("Idempotency-Key"), server.takeRequest().getHeader("Idempotency-Key"))
    }
    @Test fun conflictMakesANewKeyAndResendsOnce() = runBlocking {
        val draft = ReportDraft(reason = "other")
        server.enqueue(MockResponse().setResponseCode(409)); server.enqueue(created())
        val result = client.send(phone, draft)
        assertEquals(ReportOutcome.SENT, result.outcome)
        val first = server.takeRequest(); val second = server.takeRequest()
        assertEquals(draft.idempotencyKey, first.getHeader("Idempotency-Key"))
        assertNotEquals(first.getHeader("Idempotency-Key"), second.getHeader("Idempotency-Key"))
        assertEquals(second.getHeader("Idempotency-Key"), result.idempotencyKey)
        assertEquals(first.body.readUtf8(), second.body.readUtf8())
        // A second conflict is not resent again.
        server.enqueue(MockResponse().setResponseCode(409)); server.enqueue(MockResponse().setResponseCode(409))
        assertEquals(ReportOutcome.FAILED, client.send(phone, draft).outcome)
        assertEquals(4, server.requestCount)
    }
    @Test fun statusMapping() = runBlocking {
        val draft = ReportDraft(reason = "other")
        mapOf(429 to ReportOutcome.RATE_LIMITED, 400 to ReportOutcome.INVALID, 503 to ReportOutcome.FAILED,
            404 to ReportOutcome.FAILED, 500 to ReportOutcome.FAILED, 200 to ReportOutcome.FAILED, 302 to ReportOutcome.FAILED).forEach { (code, outcome) ->
            server.enqueue(MockResponse().setResponseCode(code).setHeader("Location", "https://outside.example/"))
            assertEquals("HTTP $code", outcome, client.send(phone, draft).outcome)
        }
        assertEquals(7, server.requestCount)
    }
    @Test fun noConnectionIsFailed() = runBlocking {
        val closed = MockWebServer().apply { start() }
        val offline = ContentReportClient(closed.url("/").toString(), "1.2.3")
        closed.shutdown()
        assertEquals(ReportOutcome.FAILED, offline.send(phone, ReportDraft(reason = "other")).outcome)
    }
    @Test fun endpointsUseHttpsAndThePath() {
        assertEquals("https://appapi.voicestudio.sh/v1/content-reports", ContentReportClient("https://appapi.voicestudio.sh", "1").endpoint.toString())
        assertEquals("https://dev-appapi.voicestudio.sh/v1/content-reports", ContentReportClient("https://dev-appapi.voicestudio.sh/", "1").endpoint.toString())
        assertThrows(ClientFailure::class.java) { ContentReportClient("http://appapi.voicestudio.sh", "1") }
    }
}
