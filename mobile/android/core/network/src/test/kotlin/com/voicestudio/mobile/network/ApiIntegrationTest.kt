package com.voicestudio.mobile.network

import kotlinx.coroutines.*
import okhttp3.mockwebserver.*
import okhttp3.Request
import org.junit.*
import org.junit.Assert.*
import java.io.File

class ApiIntegrationTest {
    private lateinit var server:MockWebServer
    private lateinit var api:VoiceStudioApi
    private var expired=false
    @Before fun setup() {server=MockWebServer();server.start();api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString(),"123456"),expired={expired=true})}
    @After fun teardown() {server.shutdown()}
    @Test fun healthRequiresBackendMarker()=runBlocking {
        server.enqueue(MockResponse().setHeader("x-omnivoice-backend","0.0.0").setBody("""{"status":"ok","version":"0.0.0","device":"cpu"}"""))
        assertEquals("cpu",api.health().device);assertEquals("/health",server.takeRequest().path)
        server.enqueue(MockResponse().setBody("""{"status":"ok","version":"0.0.0"}"""))
        assertEquals("mobile.connect.error_not_voicestudio",failure {api.health()}.key)
    }
    @Test fun pinOnlyDisconnectNeverCallsSessionRevocation()=runBlocking {
        // A server would reject session deletion without an administrator session.
        server.enqueue(MockResponse().setResponseCode(403))
        api.revoke()
        assertEquals(0,server.requestCount)
        assertNull(api.sessionToken)
        val authenticated=VoiceStudioApi(api.address,token="session")
        server.enqueue(MockResponse().setResponseCode(204))
        // The first queued response remains unused until a session exists.
        assertEquals(403,failure {authenticated.revoke()}.status)
        assertEquals(1,server.requestCount)
    }
    @Test fun pinSessionExchangeAndRevoke()=runBlocking {
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"token":"session-secret","expires_at":1999999999,"expires_in":3600}"""))
        api.exchange("master-once")
        val exchange=server.takeRequest()
        assertEquals("Bearer master-once",exchange.getHeader("Authorization"));assertEquals("123456",exchange.getHeader("X-OmniVoice-Pin"))
        assertEquals("{\"transport\":\"bearer\"}",exchange.body.readUtf8())
        server.enqueue(MockResponse().setBody("[]"));api.profiles()
        assertEquals("Bearer session-secret",server.takeRequest().getHeader("Authorization"))
        server.enqueue(MockResponse().setResponseCode(204));api.revoke()
        val revoke=server.takeRequest();assertEquals("DELETE",revoke.method);assertEquals("/api/auth/session",revoke.path);assertNull(api.sessionToken)
    }
    @Test fun unauthorizedIsActionableAndInvalidatesSession()=runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"API key required"}"""))
        assertEquals("mobile.connect.error_session_expired",failure {api.profiles()}.key);assertTrue(expired)
        server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"PIN required"}"""))
        assertEquals("mobile.connect.error_pin",failure {api.profiles()}.key)
    }
    @Test fun generateUsesMultipartAndWavMetadata()=runBlocking {
        server.enqueue(MockResponse().setHeader("Content-Type","audio/wav").setHeader("X-Audio-Id","a1").setHeader("X-Audio-Path","a1.wav").setHeader("X-Audio-Duration","1.5").setBody("RIFF"))
        val generated=api.generate("A test sentence","voice-id","engine-id","English")
        assertEquals("a1.wav",generated.audioPath);assertEquals(1.5,generated.duration,0.0)
        val request=server.takeRequest();assertEquals("/generate",request.path)
        val body=request.body.readUtf8();listOf("name=\"text\"","A test sentence","name=\"profile_id\"","voice-id","name=\"engine\"","engine-id","name=\"language\"","English").forEach {assertTrue(it,body.contains(it))}
    }
    @Test fun dubUploadAndPersistedProgress()=runBlocking {
        val file=File.createTempFile("voice-test-",".mp4").apply {writeBytes(ByteArray(200000) {1})}
        try {
            val progress=mutableListOf<Int>()
            server.enqueue(MockResponse().setResponseCode(202).setBody("""{"job_id":"j1","task_id":"prep_j1","filename":"clip.mp4"}"""))
            assertEquals("prep_j1",api.upload(file,"video","j1") {progress.add(it)}.taskId)
            val upload=server.takeRequest();assertEquals("/dub/upload",upload.path)
            assertTrue(upload.body.readUtf8().contains("name=\"video\""));assertEquals(100,progress.last())
            server.enqueue(MockResponse().setBody("""{"job":{"id":"prep_j1","status":"running"},"events":[{"seq":3,"payload":"data: {\"type\":\"progress\",\"percent\":0.5}"}]}"""))
            val events=api.jobEvents("prep_j1",2)
            assertEquals(3,events.events.single().seq);assertEquals("running",events.job.status)
            assertEquals("/jobs/prep_j1/events?after_seq=2",server.takeRequest().path)
        } finally {file.delete()}
    }
    @Test fun mediaCannotEscapePairedOriginAndRedirectIsNotFollowed()=runBlocking {
        assertThrows(ClientFailure::class.java) {api.url("https://outside.example/audio.wav")}
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location","https://outside.example/audio.wav"))
        assertEquals(302,failure {api.raw("/audio/one.wav")}.status)
        assertEquals(1,server.requestCount)
    }
    @Test fun cancellationClosesInFlightRequest()=runBlocking {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val job=launch {api.profiles()}
        withContext(Dispatchers.IO) {server.takeRequest()}
        job.cancelAndJoin();assertTrue(job.isCancelled)
    }
    @Test fun dictationUsesScopedTicketWithoutMasterInUrl()=runBlocking {
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"token":"session-secret","expires_at":1999999999,"expires_in":3600}"""))
        api.exchange("master-once");server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"ticket":"single-use"}"""))
        val handshake=api.dictationRequest()
        val ticket=server.takeRequest()
        assertEquals("/api/auth/ws-ticket",ticket.path)
        assertEquals("Bearer session-secret",ticket.getHeader("Authorization"))
        assertEquals("{\"path\":\"/ws/transcribe\"}",ticket.body.readUtf8())
        assertEquals("single-use",handshake.url.queryParameter("ws_ticket"))
        assertEquals("1",handshake.url.queryParameter("pcm"))
        assertNull(handshake.url.queryParameter("api_key"))
        assertFalse(handshake.url.toString().contains("session-secret"))
    }
    @Test fun serverDetailsSurviveRestFailures()=runBlocking {
        for(path in listOf("/generate", "/profiles", "/dictation/readiness", "/dub/upload")) {
            server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"KittenTTS cannot use reference audio; choose an engine that supports voice cloning."}"""))
            val error=failure {api.raw(path)}
            assertEquals("mobile.errors.server",error.key)
            assertTrue(error.params.getValue("message").contains("KittenTTS"))
            server.takeRequest()
        }
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":{"code":"model_licence_required","message":"Accept the model licence.","models":[{"repo_id":"org/model","license":"Example licence"}]}}"""))
        val licence=failure {api.profiles()}
        assertEquals("mobile.errors.model_licence_required",licence.key)
        assertTrue(licence.params.getValue("models").contains("Example licence"))
        assertEquals("Accept the model licence.",licence.params["message"])
        server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(422).setBody("""{"detail":{"code":"unsupported_voice","message":"Choose an engine that supports voice cloning."}}"""))
        val objectMessage=failure {api.profiles()}
        assertEquals("Choose an engine that supports voice cloning.",objectMessage.params["message"])
        server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"Media access denied; reconnect."}"""))
        withContext(Dispatchers.IO) {
            try {api.client.newCall(Request.Builder().url(api.url("/audio/file.wav")).build()).execute().close();fail("Expected media failure")}
            catch(e:ClientFailure) {assertEquals("Media access denied; reconnect.",e.params["message"])}
        }
        server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(500).setBody("not json"))
        assertEquals("mobile.android.request_failed",failure {api.profiles()}.key)
    }
    @Test fun defaultVoiceOmitsProfileId()=runBlocking {
        server.enqueue(MockResponse().setHeader("X-Audio-Id","a1").setHeader("X-Audio-Path","a1.wav").setBody("RIFF"))
        api.generate("Preset voice", "", "kittentts", "Auto")
        val body=server.takeRequest().body.readUtf8()
        assertFalse(body.contains("name=\"profile_id\""))
        assertTrue(body.contains("Auto"))
    }
    @Test fun engineAndProfileMetadataMatchHostShape()=runBlocking {
        server.enqueue(MockResponse().setBody("""{"tts":{"active":"kittentts","backends":[{"id":"kittentts","display_name":"KittenTTS","available":true,"supports_cloning":false,"hint":"Preset voices only"},{"id":"omnivoice","display_name":"VoiceStudio","available":false,"supports_cloning":true,"reason":"Install the model"}]}}"""))
        val engines=api.engines().tts
        assertEquals("kittentts",engines.active)
        assertEquals("KittenTTS",engines.backends.first().displayName)
        assertEquals(false,engines.backends.first().supportsCloning)
        assertEquals("Preset voices only",engines.backends.first().hint)
        assertFalse(engines.backends.last().available)
        assertEquals("Install the model",engines.backends.last().reason)
        server.takeRequest()
        server.enqueue(MockResponse().setBody("""[{"id":"v1","name":"Clone","language":"English","ref_audio_path":"reference.wav","audio_url":"/profiles/v1/audio"}]"""))
        val profile=api.profiles().single()
        assertTrue(profile.hasReferenceAudio);assertEquals("English",profile.language)
    }
    @Test fun readinessPreservesMissingModelRecommendation()=runBlocking {
        server.enqueue(MockResponse().setBody("""{"ready":false,"missing":{"error":"asr_model_missing","missing_repo_id":"org/model","recommended":{"label":"Small ASR","size_gb":0.6}}}"""))
        val readiness=api.readiness()
        assertFalse(readiness.ready)
        assertEquals("asr_model_missing",readiness.missing?.error)
        assertEquals("Small ASR",readiness.missing?.recommended?.label)
        assertEquals(0.6,readiness.missing?.recommended?.sizeGb ?: 0.0,0.0)
    }
    @Test fun websocketUpgradeSurvivesSharedErrorInterceptor()=runBlocking {
        val opened=CompletableDeferred<Unit>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object:okhttp3.WebSocketListener() {}))
        val socket=api.client.newWebSocket(Request.Builder().url(server.url("/ws/transcribe")).build(),object:okhttp3.WebSocketListener() {
            override fun onOpen(webSocket:okhttp3.WebSocket,response:okhttp3.Response) {opened.complete(Unit)}
            override fun onFailure(webSocket:okhttp3.WebSocket,t:Throwable,response:okhttp3.Response?) {opened.completeExceptionally(t)}
        })
        try {withTimeout(5000) {opened.await()}} finally {socket.cancel()}
    }
    @Test fun dubTranslationAlwaysCarriesExplicitProvider()=runBlocking {
        server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Translated"}]}"""))
        api.translate("job", "fr", "en", listOf(Segment(start=0.0,end=1.0,text="Original")),
            TranslationEngine("argos",category="offline",installed=true,ready=true).authorize()!!)
        val body=server.takeRequest().body.readUtf8()
        assertTrue("Translation must never use the backend's implicit Google fallback",body.contains("\"provider\":\"argos\""))
    }
    private suspend fun failure(block:suspend ()->Unit):ClientFailure {
        try {block();throw AssertionError("Expected ClientFailure")} catch(e:ClientFailure) {return e}
    }
}
