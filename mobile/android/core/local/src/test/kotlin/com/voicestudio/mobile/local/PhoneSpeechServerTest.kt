package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test
import java.net.Socket
import java.io.File

class PhoneSpeechServerTest {
    private fun request(server:PhoneSpeechServer,body:String="{\"input\":\"Hello\"}",headers:Map<String,String> = emptyMap(),path:String="/v1/audio/speech",method:String="POST") = PhoneSpeechServer.Request(method,path,mapOf("authorization" to "Bearer ${server.token}","host" to "localhost:${server.port}","content-type" to "application/json")+headers,body.toByteArray())
    private fun server()=PhoneSpeechServer("voice") {_,_ ->
        val file=File.createTempFile("server-test-",".wav")
        try {markSynthetic(LocalAudio(floatArrayOf(.1f,.2f),24000),file);file.readBytes()} finally {file.delete()}
    }.apply {start()}
    @Test fun authOriginAndHostAreRequired()=runBlocking {
        val server=server()
        try {
            assertEquals(401,server.respond(request(server,headers=mapOf("authorization" to ""))).status)
            assertEquals(403,server.respond(request(server,headers=mapOf("host" to "evil:123"))).status)
            assertEquals(403,server.respond(request(server,headers=mapOf("origin" to "null"))).status)
            assertEquals(403,server.respond(request(server,headers=mapOf("origin" to "https://evil.example"))).status)
            val reply=server.respond(request(server,headers=mapOf("origin" to "http://localhost:${server.port}")))
            assertEquals(200,reply.status);assertEquals("audio/wav",reply.type)
            assertTrue(reply.body.toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        } finally {server.close()}
    }
    @Test fun rejectsInvalidSpeechAndSupportsMCP()=runBlocking {
        val server=server()
        try {
            listOf("{}","{\"input\":\"\"}","{\"input\":\"hi\",\"speed\":3}","{\"input\":\"hi\",\"model\":\"download-me\"}","{\"input\":\"hi\",\"file\":\"/etc/passwd\"}").forEach {assertEquals(400,server.respond(request(server,it)).status)}
            val init=server.respond(request(server,"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\"}}",path="/mcp"))
            assertTrue(init.body.toString(Charsets.UTF_8).contains("2025-11-25"))
            val wav=server.respond(request(server,"{\"jsonrpc\":\"2.0\",\"id\":\"speak\",\"method\":\"tools/call\",\"params\":{\"name\":\"speak\",\"arguments\":{\"text\":\"Hi\"}}}",path="/mcp"))
            assertTrue(wav.body.toString(Charsets.UTF_8).contains("audio/wav"))
        } finally {server.close()}
    }
    @Test fun actualSocketChecksFramingAndClosesOnStop() {
        val server=server()
        try {
            Socket("127.0.0.1",server.port).use {socket ->
                socket.soTimeout=5000
                socket.getOutputStream().write("POST /mcp HTTP/1.1\r\nHost: localhost:${server.port}\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}".toByteArray())
                assertTrue(socket.getInputStream().bufferedReader().readLine().startsWith("HTTP/1.1 400"))
            }
            val socket=Socket("127.0.0.1",server.port);socket.soTimeout=5000
            server.close()
            assertTrue(runCatching {socket.getInputStream().read()==-1}.getOrDefault(true));socket.close()
        } finally {server.close()}
    }
    @Test fun onlyOneInferenceButMetadataStillWorks()=runBlocking {
        val entered=CompletableDeferred<Unit>();val release=CompletableDeferred<Unit>()
        val server=PhoneSpeechServer("voice") {_,_ -> entered.complete(Unit);release.await();byteArrayOf(1)}.apply {start()}
        try {
            val first=async {server.respond(request(server))};entered.await()
            assertEquals(429,server.respond(request(server)).status)
            assertEquals(200,server.respond(request(server,path="/health",method="GET")).status)
            first.cancelAndJoin()
            release.complete(Unit)
            assertEquals(200,server.respond(request(server)).status)
        } finally {server.close()}
    }
    @Test fun mcpCancellationIsScopedToRequestID()=runBlocking {
        val entered=CompletableDeferred<Unit>()
        val server=PhoneSpeechServer("voice") {_,_ -> entered.complete(Unit);delay(10000);byteArrayOf(1)}.apply {start()}
        try {
            val first=async {server.respond(request(server,"""{"jsonrpc":"2.0","id":"active","method":"tools/call","params":{"name":"speak","arguments":{"text":"Hello"}}}""",path="/mcp"))}
            withTimeout(2000) {entered.await()}
            assertEquals(202,server.respond(request(server,"""{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":"different"}}""",path="/mcp")).status)
            assertEquals(429,server.respond(request(server)).status)
            server.respond(request(server,"""{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":"active"}}""",path="/mcp"))
            withTimeout(2000) {try {first.await();fail("Expected cancellation")} catch(_:CancellationException) {}}
        } finally {server.close()}
    }

}
