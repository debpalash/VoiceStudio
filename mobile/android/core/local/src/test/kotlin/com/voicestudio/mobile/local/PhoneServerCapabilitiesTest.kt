package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Test
import org.junit.Assert.*
import java.io.File
import java.util.Base64

class PhoneServerCapabilitiesTest {
    private fun wave():ByteArray {
        val file=File.createTempFile("capabilities-",".wav")
        return try {markSynthetic(LocalAudio(floatArrayOf(0f,.5f,-.5f,0f),24000),file);file.readBytes()} finally {file.delete()}
    }
    private fun server()=PhoneSpeechServer("voice",PhoneServerCapabilities(transcriptionModel="whisper",translationModel="translator",translationLanguages=listOf("fr","de"),
        transcribe={audio -> require(audio.sampleRate==24000&&audio.samples.size==4);"Hello"},translate={text,language -> "$text $language"},encodeMP3={"mp3 fixture".toByteArray()})) {_,_ -> wave()}.apply {start()}
    private fun request(server:PhoneSpeechServer,path:String,body:JsonObject,token:String=server.token)=PhoneSpeechServer.Request("POST",path,mapOf("host" to "localhost:${server.port}","authorization" to "Bearer $token","content-type" to "application/json"),body.toString().toByteArray())
    @Test fun enabledHandlersTranscribeTranslateAndEncode()=runBlocking {
        val server=server()
        try {
            val audio=Base64.getEncoder().encodeToString(wave())
            val result=server.respond(request(server,"/v1/audio/transcriptions",buildJsonObject {put("audio",audio);put("model","whisper")}))
            assertEquals(200,result.status);assertTrue(result.body.toString(Charsets.UTF_8).contains("Hello"))
            assertEquals(400,server.respond(request(server,"/v1/audio/transcriptions",buildJsonObject {put("audio",audio);put("model","download-me")})).status)
            val translated=server.respond(request(server,"/v1/text/translations",buildJsonObject {put("input","Hello");put("language","FR")}))
            assertEquals(200,translated.status);assertTrue(translated.body.toString(Charsets.UTF_8).contains("fr"))
            assertEquals(400,server.respond(request(server,"/v1/text/translations",buildJsonObject {put("input","Hello");put("language","unknown")})).status)
            assertEquals(401,server.respond(request(server,"/v1/text/translations",buildJsonObject {put("input","Hello");put("language","fr")},token="bad")).status)
            val mp3=server.respond(request(server,"/v1/audio/speech",buildJsonObject {put("input","Hello");put("response_format","mp3")}))
            assertEquals("audio/mpeg",mp3.type);assertEquals("mp3 fixture",mp3.body.toString(Charsets.UTF_8))
            val tools=server.respond(request(server,"/mcp",buildJsonObject {put("jsonrpc","2.0");put("id",1);put("method","tools/list")})).body.toString(Charsets.UTF_8)
            assertTrue(tools.contains("transcribe")&&tools.contains("translate")&&tools.contains("speak"))
        } finally {server.close()}
    }
    @Test fun waveParsingIsStrictAndBounded() {
        assertEquals(4,PhoneServerAudio.decodeWave(wave()).samples.size)
        val malformed=wave().apply {this[4]=0};assertTrue(runCatching {PhoneServerAudio.decodeWave(malformed)}.isFailure)
        val stereo=wave().apply {this[22]=2};assertTrue(runCatching {PhoneServerAudio.decodeWave(stereo)}.isFailure)
    }
    @Test fun inferenceSlotIsSharedAndDisabledSpeechStaysDisabled()=runBlocking {
        val entered=CompletableDeferred<Unit>()
        val server=PhoneSpeechServer("unused",PhoneServerCapabilities(speech=false,transcriptionModel="whisper",transcribe={entered.complete(Unit);delay(20000);"Hello"})) {_,_ -> error("Speech disabled")}.apply {start()}
        try {
            val snapshot=request(server,"/v1/audio/transcriptions",buildJsonObject {put("audio",Base64.getEncoder().encodeToString(wave()))})
            val first=async {server.respond(snapshot)};entered.await()
            assertEquals(429,server.respond(snapshot).status)
            assertEquals(404,server.respond(request(server,"/v1/audio/speech",buildJsonObject {put("input","Hello")})).status)
            first.cancelAndJoin()
        } finally {server.close()}
    }
}
