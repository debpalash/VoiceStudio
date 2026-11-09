package com.voicestudio.mobile.network

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.*
import org.junit.Assert.*

class GalleryTest {
    @Test fun browsingUsesPairedCredentialsAndImportIsExplicit()=runBlocking {
        val server=MockWebServer();server.start()
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/studio/").toString(),"123456"),token="session")
            server.enqueue(MockResponse().setBody("""{"total":1,"items":[{"id":"voice-1","name":"Voice","license":"CC0","author":"Author","language":"English"}]}"""))
            assertEquals("CC0",api.community(100).items.single().license)
            assertEquals(1,server.requestCount)
            val browse=server.takeRequest()
            assertEquals("/studio/community/items?limit=100&offset=100",browse.path)
            assertEquals("Bearer session",browse.getHeader("Authorization"));assertEquals("123456",browse.getHeader("X-OmniVoice-Pin"))
            server.enqueue(MockResponse().setBody("""{"profile_id":"saved-voice","name":"Voice"}"""))
            assertEquals("saved-voice",api.useCommunity("voice-1").profile_id)
            val save=server.takeRequest();assertEquals("POST",save.method);assertEquals("/studio/community/items/voice-1/use",save.path)
        } finally {server.shutdown()}
    }
}
