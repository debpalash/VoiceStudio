package com.voicestudio.mobile.network

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.*
import org.junit.*
import org.junit.Assert.*

class TranslationPolicyTest {
    private val json=Json {ignoreUnknownKeys=true}
    @Test fun activeEngineMustExistBeInstalledAndReady() {
        for(body in listOf(
            """{"active":"missing","engines":[]}""",
            """{"active":"argos","engines":[{"id":"argos","category":"offline","installed":false,"ready":true}]}""",
            """{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":false}]}""",
            """{"active":"argos","engines":[{"id":"argos"}]}"""
        )) {
            val error=assertThrows(ClientFailure::class.java) {json.decodeFromString<TranslationEngines>(body).activeEngine()}
            assertEquals("mobile.dub.translation_setup",error.key)
        }
    }
    @Test fun onlineConsentIsPerJobAndDecliningNeverTranslates()=runBlocking {
        val server=MockWebServer();server.start()
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
            val body="""{"active":"deepl","engines":[{"id":"argos","category":"offline","installed":true,"ready":true},{"id":"deepl","display_name":"DeepL","category":"online","installed":true,"ready":true}]}"""
            server.enqueue(MockResponse().setBody(body))
            val engine=api.translationEngine()
            assertEquals("deepl",engine.id);assertEquals("DeepL",engine.displayName)
            assertEquals("/engines/translation",server.takeRequest().path)
            assertNull(engine.authorize())
            assertNull(engine.authorize(false))
            assertEquals(1,server.requestCount)
            val approval=engine.authorize(true)!!
            assertTrue(approval.onlineConfirmed)
            server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Bonjour"}]}"""))
            api.translate("j","fr","en",listOf(Segment(start=0.0,end=1.0,text="Hello")),approval)
            val request=server.takeRequest()
            assertEquals("/dub/translate",request.path)
            assertEquals("deepl",json.parseToJsonElement(request.body.readUtf8()).jsonObject["provider"]!!.jsonPrimitive.content)
            assertNull("A previous approval must not carry into another job",engine.authorize())
        } finally {server.shutdown()}
    }
    @Test fun dubVoiceIsPresetOrExplicitProfileAndNeverImplicitSourceCloning()=runBlocking {
        val server=MockWebServer();server.start()
        try {
            val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
            try {
                api.startDub("job","fr","French",DubVoiceChoice.UNSELECTED,listOf(Segment(start=0.0,end=1.0,text="Bonjour")))
                fail("A voice must be selected explicitly")
            } catch(e:ClientFailure) {assertEquals("mobile.dub.choose_voice",e.key)}
            assertEquals(0,server.requestCount)
            for(voice in listOf("", "cloned-profile")) {
                server.enqueue(MockResponse().setBody("""{"task_id":"render"}"""))
                api.startDub("job","fr","French",voice,listOf(Segment(start=0.0,end=1.0,text="Bonjour")))
                val request=server.takeRequest()
                assertEquals("/dub/generate/job",request.path)
                val segment=json.parseToJsonElement(request.body.readUtf8()).jsonObject.getValue("segments").jsonArray.single().jsonObject
                assertEquals(voice,segment.getValue("profile_id").jsonPrimitive.content)
                assertFalse(segment.containsKey("speaker_id"))
            }
        } finally {server.shutdown()}
    }
    @Test fun llmDoesNotImplyLocalOrReuseAnotherJobsConsent() {
        val engine=TranslationEngine("openai","LLM (OpenAI-compatible)","llm",true,true)
        assertNull("An unclassified LLM must require per-job consent",engine.authorize())
        assertTrue(engine.authorize(true)!!.onlineConfirmed)
        assertNull(engine.authorize())
    }

    @Test fun llmLocalityUsesResolvedSkillEndpointRatherThanProviderNameOrLocalFlag()=runBlocking {
        val server=MockWebServer().apply {start()}
        val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
        try {
            for(endpoint in listOf("http://127.0.0.1:11434/v1","http://localhost:1234/v1","http://[::1]:11434/v1","https://api.example.test/v1","http://192.168.1.2:11434/v1")) {
                server.enqueue(MockResponse().setBody("""{"active":"openai","engines":[{"id":"openai","category":"llm","configured_via":"Ollama","installed":true,"ready":true}]}"""))
                server.enqueue(MockResponse().setBody("""{"skills":[{"id":"dub_translation","provider":"ollama","enabled":true,"ready":true}]}"""))
                server.enqueue(MockResponse().setBody("""{"providers":[{"id":"other","base_url":"http://localhost/v1"},{"id":"ollama","display_name":"Ollama","local":true,"base_url":"$endpoint"}]}"""))
                val engine=api.translationEngine()
                assertEquals("Ollama",engine.consentName)
                assertEquals(listOf("/engines/translation","/api/settings/llm-skills","/api/settings/llm-providers"),List(3) {server.takeRequest().path})
                val local=endpoint.contains("127.0.0.1") || endpoint.contains("localhost") || endpoint.contains("[::1]")
                assertEquals(!local,engine.requiresConsent)
                assertEquals(local,engine.authorize()!=null)
                val approved=engine.authorize(true)!!
                assertEquals(!local,approved.onlineConfirmed)
                assertTrue(approved.routingIdentity.isNotBlank())
                server.enqueue(MockResponse().setBody("""{"translated":[{"id":"0","text":"Bonjour"}]}"""))
                api.translate("j","fr","en",listOf(Segment(start=0.0,end=1.0,text="Hello")),approved)
                assertEquals("/dub/translate",server.takeRequest().path)
            }
        } finally {server.shutdown()}
    }
    @Test fun forbiddenOrIncompleteLlmMetadataRequiresConsentAndNamesConfiguredProvider()=runBlocking {
        val server=MockWebServer().apply {start()}
        val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
        try {
            for(body in listOf(MockResponse().setResponseCode(403).setBody("""{"detail":"loopback origin required"}"""),MockResponse().setBody("""{"skills":[]}"""))) {
                server.enqueue(MockResponse().setBody("""{"active":"openai","engines":[{"id":"openai","category":"llm","configured_via":"OpenAI","installed":true,"ready":true}]}"""))
                server.enqueue(body)
                val engine=api.translationEngine()
                assertEquals("OpenAI",engine.consentName)
                assertNull(engine.authorize())
                assertTrue(engine.authorize(true)!!.onlineConfirmed)
                assertEquals("/engines/translation",server.takeRequest().path)
                assertEquals("/api/settings/llm-skills",server.takeRequest().path)
            }
            assertEquals(4,server.requestCount)
        } finally {server.shutdown()}
    }
    @Test fun transientLlmLookupFailureIsRetryableNotUnknown()=runBlocking {
        val server=MockWebServer().apply {start()}
        val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString()))
        try {
            server.enqueue(MockResponse().setBody("""{"active":"openai","engines":[{"id":"openai","category":"llm","configured_via":"OpenAI","installed":true,"ready":true}]}"""))
            server.enqueue(MockResponse().setResponseCode(503))
            val failure=runCatching {api.translationEngine()}.exceptionOrNull()
            assertTrue("expected a retryable failure, got $failure",(failure as? ClientFailure)?.isTransient() == true)
        } finally {server.shutdown()}
    }
}
