package com.voicestudio.mobile.features.dictate

import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.mockwebserver.*
import org.junit.Test
import org.junit.Assert.*

class DictationProtocolTest {
    // Every wait here is for something that should happen: a generous limit keeps a
    // busy two-worker CI runner (MockWebServer + socket start-up) from flaking, and a
    // real hang still fails in seconds.
    private val waitMs=10_000L
    private suspend fun awaitCondition(condition: () -> Boolean) {
        withTimeout(waitMs) { while(!condition()) delay(5) }
    }
    @Test fun utteranceFinalsKeepRecordingUntilSummaryWithoutSocketClose() = runBlocking {
        val server=MockWebServer()
        val opened=CompletableDeferred<WebSocket>()
        server.enqueue(MockResponse().setBody("""{"ready":true}"""))
        server.enqueue(MockResponse().withWebSocketUpgrade(object:WebSocketListener() {
            override fun onOpen(webSocket:WebSocket,response:Response) {opened.complete(webSocket)}
            override fun onClosing(webSocket:WebSocket,code:Int,reason:String) {webSocket.close(code,null)}
        }))
        server.start()
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
        val errors=java.util.concurrent.CopyOnWriteArrayList<Throwable>()
        val captureCycles=java.util.concurrent.atomic.AtomicInteger()
        val saved=java.util.concurrent.CopyOnWriteArrayList<String>()
        val controller=DictationController(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),scope,{{error ->errors.add(error)}},
            DictationAudioSource {keepRecording,_ -> while(keepRecording()) {captureCycles.incrementAndGet();delay(5)}},completed={saved.add(it)})
        try {
            controller.start()
            val socket=withTimeout(waitMs) {opened.await()}
            socket.send("""{"type":"partial","text":"First"}""")
            awaitCondition {controller.text.value=="First"}
            socket.send("""{"type":"final","text":"First phrase.","final_kind":"utterance"}""")
            awaitCondition {controller.text.value=="First phrase."}
            assertTrue(controller.active.value)
            socket.send("""{"type":"final","text":"Ignore this","final_kind":"unknown"}""")
            socket.send("""{"type":"final","text":"","final_kind":"utterance"}""")
            socket.send("""{"type":"partial","text":"Second"}""")
            awaitCondition {controller.text.value=="First phrase. Second"}
            socket.send("""{"type":"final","text":"Second phrase.","final_kind":"utterance"}""")
            awaitCondition {controller.text.value=="First phrase. Second phrase."}
            val cycles=captureCycles.get()
            awaitCondition {captureCycles.get()>=cycles+2}
            assertTrue(controller.active.value)
            socket.send("""{"type":"final","text":"First phrase. Second phrase.","final_kind":"summary","refined_text":"First phrase; second phrase."}""")
            awaitCondition {!controller.active.value}
            assertEquals("First phrase; second phrase.",controller.text.value)
            controller.close();assertEquals(listOf("First phrase; second phrase."),saved)
            assertTrue(errors.toString(),errors.isEmpty())
        } finally {controller.close();scope.cancel();server.shutdown()}
    }
    @Test fun emptySummaryAndLegacyEofPreserveCommittedText() = runBlocking {
        for(summary in listOf("""{"type":"final","text":"","final_kind":"summary"}""",
            """{"type":"final","text":"Whole session.","final_kind":"summary"}""", """{"type":"final","text":"Whole session."}""")) {
            val server=MockWebServer()
            val opened=CompletableDeferred<WebSocket>()
            val eof=CompletableDeferred<Unit>()
            server.enqueue(MockResponse().setBody("""{"ready":true}"""))
            server.enqueue(MockResponse().withWebSocketUpgrade(object:WebSocketListener() {
                override fun onOpen(webSocket:WebSocket,response:Response) {opened.complete(webSocket)}
                override fun onMessage(webSocket:WebSocket,text:String) {if(text.contains("input_audio.end")) eof.complete(Unit)}
                override fun onClosing(webSocket:WebSocket,code:Int,reason:String) {webSocket.close(code,null)}
            }))
            server.start()
            val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
            val controller=DictationController(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),scope,{{_ ->}},
                DictationAudioSource {keepRecording,_ -> while(keepRecording()) delay(5)})
            try {
                controller.start()
                val socket=withTimeout(waitMs) {opened.await()}
                socket.send("""{"type":"final","text":"Whole session.","final_kind":"utterance"}""")
                awaitCondition {controller.text.value=="Whole session."}
                controller.finish()
                withTimeout(waitMs) {eof.await()}
                socket.send(summary)
                awaitCondition {!controller.active.value}
                assertEquals("Whole session.",controller.text.value)
            } finally {controller.close();scope.cancel();server.shutdown()}
        }
    }
    @Test fun oldCaptureTerminationCannotCloseTheNextDictationSocket() = runBlocking {
        val server=MockWebServer()
        val sockets=java.util.concurrent.LinkedBlockingQueue<WebSocket>()
        server.dispatcher=object:okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request:RecordedRequest)=if(request.path=="/dictation/readiness")
                MockResponse().setBody("""{"ready":true}""")
            else MockResponse().withWebSocketUpgrade(object:WebSocketListener() {
                override fun onOpen(webSocket:WebSocket,response:Response) {sockets.add(webSocket)}
                override fun onClosing(webSocket:WebSocket,code:Int,reason:String) {webSocket.close(code,null)}
            })
        };server.start()
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
        val oldCaptureTerminating=CompletableDeferred<Unit>()
        val releaseOldCapture=CompletableDeferred<Unit>()
        val oldCaptureEnded=CompletableDeferred<Unit>()
        val captures=java.util.concurrent.atomic.AtomicInteger()
        val errors=java.util.concurrent.CopyOnWriteArrayList<Throwable>()
        val controller=DictationController(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),scope,{{error ->errors.add(error)}},
            DictationAudioSource {keepRecording,_ ->
                val first=captures.incrementAndGet()==1
                try {while(keepRecording()) delay(5)}
                finally {if(first) withContext(NonCancellable) {
                    oldCaptureTerminating.complete(Unit);releaseOldCapture.await();oldCaptureEnded.complete(Unit)
                }}
            })
        try {
            controller.start()
            withContext(Dispatchers.IO) {requireNotNull(sockets.poll(2,java.util.concurrent.TimeUnit.SECONDS))}
            awaitCondition {captures.get()==1}
            controller.close()
            withTimeout(waitMs) {oldCaptureTerminating.await()}
            controller.start()
            val next=withContext(Dispatchers.IO) {requireNotNull(sockets.poll(2,java.util.concurrent.TimeUnit.SECONDS))}
            awaitCondition {captures.get()==2}
            next.send("""{"type":"partial","text":"Next session"}""")
            awaitCondition {controller.text.value=="Next session"}
            releaseOldCapture.complete(Unit)
            withTimeout(waitMs) {oldCaptureEnded.await()}
            // A message sent after the old capture exits must still reach the new stream.
            next.send("""{"type":"partial","text":"Still recording"}""")
            awaitCondition {controller.text.value=="Still recording"}
            assertTrue(controller.active.value)
            assertTrue(errors.toString(),errors.isEmpty())
        } finally {releaseOldCapture.complete(Unit);controller.close();scope.cancel();server.shutdown()}
    }

    @Test fun permissionOwnerSurvivesRemoteReadinessAndExplicitLaterStartIsFresh() = runBlocking {
        for(original in listOf<String?>(null,"grant-a")) {
            val server=MockWebServer()
            repeat(2) {server.enqueue(MockResponse().setResponseCode(503).setBody("{}"))}
            server.start()
            val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
            val current=java.util.concurrent.atomic.AtomicReference<String?>(original)
            val observations=java.util.concurrent.atomic.AtomicInteger()
            val feedback=java.util.concurrent.CopyOnWriteArrayList<String?>()
            val reports=java.util.concurrent.CopyOnWriteArrayList<String>()
            val observe:()->(Throwable)->Unit={
                observations.incrementAndGet()
                val owner=current.get();
                {_:Throwable ->
                    feedback.add(owner)
                    if(owner!=null && owner==current.get()) reports.add(owner)
                }
            }
            val originalFailure=observe()
            current.set("grant-b")
            val controller=DictationController(VoiceStudioApi(PairingAddress.parse(server.url("/").toString())),scope,observe,
                DictationAudioSource {_,_ ->error("unready backend must not record")})
            try {
                controller.start(originalFailure)
                awaitCondition {!controller.active.value && feedback.size==1}
                assertEquals(1,observations.get())
                assertEquals(listOf(original),feedback.toList())
                assertTrue(reports.isEmpty())
                controller.start()
                awaitCondition {!controller.active.value && feedback.size==2}
                assertEquals(2,observations.get())
                assertEquals(listOf(original,"grant-b"),feedback.toList())
                assertEquals(listOf("grant-b"),reports.toList())
                assertEquals(2,server.requestCount)
            } finally {controller.close();scope.cancel();server.shutdown()}
        }
    }

}
