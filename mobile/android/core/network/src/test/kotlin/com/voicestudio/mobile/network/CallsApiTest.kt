package com.voicestudio.mobile.network

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Test
import org.junit.Assert.*

class CallsApiTest {
    @Test fun eligibleVoicesAndExplicitCallsKeepPairedCredentials()=runBlocking {
        val server=MockWebServer();server.start()
        try {
            val calls=CallsApi(VoiceStudioApi(PairingAddress.parse(server.url("/studio/").toString()),token="session"))
            server.enqueue(MockResponse().setBody("""[{"id":"raw","name":"Raw","kind":"clone"},{"id":"own","name":"Own","kind":"clone","verified_own_voice":1},{"id":"design","name":"Design","kind":"design"}]"""))
            assertEquals(listOf("own","design"),calls.voices().filter {it.eligible}.map {it.id})
            assertEquals(1,server.requestCount)
            assertEquals("Bearer session",server.takeRequest().getHeader("Authorization"))
            server.enqueue(MockResponse().setBody("""{"call":{"id":"call-1","to_masked":"+1***1111","status":"ringing"}}"""))
            assertTrue(calls.start("+15550001111","Ask opening hours","own","AI assistant",5).live)
            val start=server.takeRequest();assertEquals("POST",start.method);assertEquals("/studio/calls",start.path)
            server.enqueue(MockResponse().setBody("{}"));calls.control("call-1","hangup")
            assertEquals("/studio/calls/call-1/hangup",server.takeRequest().path)
            assertTrue(runCatching {calls.control("../settings","hangup")}.isFailure)
            assertEquals(3,server.requestCount)
        } finally {server.shutdown()}
    }
}
