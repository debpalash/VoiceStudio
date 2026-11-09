package com.voicestudio.mobile.network

import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.*
import okio.buffer
import org.junit.*
import org.junit.Assert.*

class AdvancedDubApiTest {
    private val stream="event: warning\ndata: {\"detail\":\"Check labels\"}\n\nevent: final\ndata: {\"segments\":[{\"id\":0,\"start\":0,\"end\":1,\"text\":\"Previous translation\",\"text_original\":\"Original speech\",\"speaker_id\":\"Speaker 1\"}],\"cast_sources\":{\"Speaker 1\":{\"kind\":\"speaker\",\"duration\":4}}}\n\nevent: done\ndata: {}\n\n"
    private val engine="""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}"""
    @Test fun installedDiarizerStreamsRealWarningsAndRecoversSourceRatherThanPreviousTranslation(): Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()),token="session")
            server.enqueue(MockResponse().setBody("""{"installed":true}"""));server.enqueue(MockResponse().setBody(stream))
            val analysis=api.computerDubAnalysis("job",2)
            assertEquals("Original speech",analysis.segments.single().text);assertEquals("Speaker 1",analysis.segments.single().speakerId);assertEquals(listOf("Check labels"),analysis.warnings)
            assertEquals("/engines/diarisation",server.takeRequest().path)
            val request=server.takeRequest();assertEquals("/dub/transcribe-stream/job?reanalyze_speakers=true&num_speakers=2",request.path);assertEquals("Bearer session",request.getHeader("Authorization"))
            server.enqueue(MockResponse().setBody("""{"installed":false}"""))
            assertTrue(runCatching {api.computerDubAnalysis("job")}.exceptionOrNull() is ClientFailure)
            assertEquals(3,server.requestCount)
            assertThrows(ClientFailure::class.java) {ComputerDubAnalysis.parse(stream.substringBefore("event: done").toByteArray())}
            assertThrows(ClientFailure::class.java) {ComputerDubAnalysis.parse(stream.replace("Original speech","   ").toByteArray())}
            assertThrows(ClientFailure::class.java) {ComputerDubAnalysis.parse(stream.replace("Original speech","x".repeat(4001)).toByteArray())}
        } finally {server.shutdown()}
    }
    @Test fun repeatedSpeakerDetectionRequestsFreshAnalysisIncludingAutomaticCount(): Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()),token="session")
            for(speakers in listOf(0,3,0)) {
                server.enqueue(MockResponse().setBody("""{"installed":true}"""));server.enqueue(MockResponse().setBody(stream))
                assertEquals("Original speech",api.computerDubAnalysis("job",speakers).segments.single().text)
                assertEquals("/engines/diarisation",server.takeRequest().path)
                val request=server.takeRequest()
                assertEquals("GET",request.method)
                assertEquals("true",request.requestUrl!!.queryParameter("reanalyze_speakers"))
                assertEquals(if(speakers==0) null else speakers.toString(),request.requestUrl!!.queryParameter("num_speakers"))
                assertEquals("Bearer session",request.getHeader("Authorization"))
            }
        } finally {server.shutdown()}
    }
    @Test fun castingRetranslatesSourceAndBindsStableIdsVoiceAndTimingToSameProvider(): Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
            val authorization=TranslationEngine("argos",category="offline",installed=true,ready=true).authorize()!!
            server.enqueue(MockResponse().setBody(engine));server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Bonjour"}]}"""));server.enqueue(MockResponse().setBody("""{"task_id":"render"}"""))
            val result=api.computerDubCast("job",ComputerDubAnalysis.parse(stream.toByteArray()),"fr","French",mapOf("Speaker 1" to "saved-voice"),authorization,"smart_fit")
            assertEquals("render",result.taskId);server.takeRequest()
            val translation=server.takeRequest();assertEquals("/dub/translate",translation.path);val translatedBody=translation.body.readUtf8();assertTrue(translatedBody.contains("Original speech"));assertFalse(translatedBody.contains("Previous translation"))
            val render=Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            assertEquals("0",render.getValue("segment_ids").jsonArray.single().jsonPrimitive.content);assertEquals("smart_fit",render.getValue("timing_strategy").jsonPrimitive.content)
            val cue=render.getValue("segments").jsonArray.single().jsonObject
            assertEquals("Bonjour",cue.getValue("text").jsonPrimitive.content);assertEquals("saved-voice",cue.getValue("profile_id").jsonPrimitive.content)
            server.enqueue(MockResponse().setBody(engine.replace("offline","online")))
            assertTrue(runCatching {api.computerDubCast("job",ComputerDubAnalysis.parse(stream.toByteArray()),"fr","French",emptyMap(),authorization)}.exceptionOrNull() is ClientFailure)
            assertEquals(4,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun computerCastPreservesSavedRenderSettingsWhileLegacySegmentsOmitOverrides(): Unit=runBlocking {
        val server=MockWebServer().apply {start()}
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
            val authorization=TranslationEngine("argos",category="offline",installed=true,ready=true).authorize()!!
            fun assertSettings(cue: JsonObject,gain: Double?) {
                val keys=setOf("gain","speed","direction","effect_preset","instruct")
                val expected=if(gain==null) emptyMap() else mapOf("gain" to JsonPrimitive(gain),"speed" to JsonPrimitive(0.85),"direction" to JsonPrimitive("Whisper urgently"),"effect_preset" to JsonPrimitive("raw"),"instruct" to JsonPrimitive("Calm delivery"))
                assertEquals(expected,cue.filterKeys {it in keys})
            }
            for(gain in listOf(0.0,0.35,1.0,null)) {
                val response=gain?.let {stream.replace("\"speaker_id\":","\"gain\":$it,\"speed\":0.85,\"direction\":\"Whisper urgently\",\"effect_preset\":\"raw\",\"instruct\":\"Calm delivery\",\"speaker_id\":")} ?: stream
                server.enqueue(MockResponse().setBody("""{"installed":true}"""));server.enqueue(MockResponse().setBody(response))
                val analysis=api.computerDubAnalysis("job")
                server.takeRequest();server.takeRequest()
                assertSettings(Json.encodeToJsonElement(analysis.segments.single()).jsonObject,gain)
                server.enqueue(MockResponse().setBody(engine));server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Bonjour"}]}"""));server.enqueue(MockResponse().setBody("""{"task_id":"render"}"""))
                api.computerDubCast("job",analysis,"fr","French",emptyMap(),authorization)
                server.takeRequest();server.takeRequest()
                val cue=Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject.getValue("segments").jsonArray.single().jsonObject
                assertSettings(cue,gain);assertEquals("Bonjour",cue.getValue("text").jsonPrimitive.content)
                server.enqueue(MockResponse().setBody("""{"task_id":"render"}"""))
                api.startDub("job","fr","French","",analysis.segments.map {it.copy(text="Bonjour")})
                val ordinary=Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject.getValue("segments").jsonArray.single().jsonObject
                assertSettings(ordinary,gain)
            }
        } finally {server.shutdown()}
    }
    @Test fun exportOptionsExplicitlySelectTracksBackgroundAndRealBurnWithoutPathInjection() {
        val options=ComputerDubExport(language="fr",tracks=listOf("fr","de"),original=true,preserveBackground=true,burn=true,karaoke=true)
        val url=okhttp3.HttpUrl.Builder().scheme("http").host("localhost").build().resolve(options.path("job-1"))!!
        assertEquals("fr,de,original",url.queryParameter("include_tracks"));assertEquals("fr",url.queryParameter("default_track"));assertEquals("1",url.queryParameter("preserve_bg"));assertEquals("1",url.queryParameter("burn_subs"));assertEquals("1",url.queryParameter("karaoke"))
        assertThrows(IllegalArgumentException::class.java) {options.path("../../private")}
        assertThrows(IllegalArgumentException::class.java) {options.copy(dual=true).path("job")}
        assertTrue(options.copy(kind="ass").path("job").startsWith("/dub/ass/job?"))
    }
    @Test fun cancellingAfterHeadersClosesAStalledSSEBodyWithoutWaitingForSocketTimeout(): Unit=runBlocking {
        val entered=java.util.concurrent.CountDownLatch(1);val closed=java.util.concurrent.CountDownLatch(1)
        val source=object: okio.Source {
            override fun timeout()=okio.Timeout.NONE
            override fun close() {closed.countDown()}
            override fun read(sink: okio.Buffer,byteCount: Long): Long {
                entered.countDown()
                check(closed.await(10,java.util.concurrent.TimeUnit.SECONDS))
                throw java.io.IOException("closed by cancellation")
            }
        }
        val buffered=source.buffer()
        val body=object: okhttp3.ResponseBody() {
            override fun contentType()=null
            override fun contentLength()=-1L
            override fun source()=buffered
        }
        val response=okhttp3.Response.Builder().request(okhttp3.Request.Builder().url("http://localhost/dub/transcribe-stream/job").build()).protocol(okhttp3.Protocol.HTTP_1_1).code(200).message("OK").body(body).build()
        val reading=launch(Dispatchers.IO) {readComputerDubAnalysis(response)}
        assertTrue(withContext(Dispatchers.IO) {entered.await(3,java.util.concurrent.TimeUnit.SECONDS)})
        withTimeout(3000) {reading.cancelAndJoin()}
        assertEquals(0L,closed.count);assertTrue(reading.isCancelled)
    }
    @Test fun sourceSpeakerVoiceIDsMatchBackendLowercasePortableNames() {
        assertEquals("auto:speaker_1",ComputerDubAnalysis.sourceVoiceID("Speaker 1"))
        assertEquals("auto:speaker_2",ComputerDubAnalysis.sourceVoiceID("SPEAKER-2!"))
        assertEquals("auto:speaker",ComputerDubAnalysis.sourceVoiceID("___"))
    }
}
