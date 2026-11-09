package com.voicestudio.mobile.local

import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.Assert.*
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class PhoneServerLifetimeTest {
    @Test fun closeBetweenAcceptAndRegistrationClosesTheSocketAndNeverStartsInference()=runBlocking {
        val accepted=CountDownLatch(1);val release=CountDownLatch(1);val calls=AtomicInteger()
        val server=PhoneSpeechServer("fixture") {_,_ ->calls.incrementAndGet();byteArrayOf()}
        server.beforeClientRegistration={accepted.countDown();check(release.await(2,TimeUnit.SECONDS))}
        server.start()
        val client=Socket("127.0.0.1",server.port).apply {soTimeout=2000}
        try {
            assertTrue(accepted.await(2,TimeUnit.SECONDS))
            val body="{\"input\":\"Hello\"}"
            client.getOutputStream().write(("POST /v1/audio/speech HTTP/1.1\r\nHost: localhost:${server.port}\r\nAuthorization: Bearer ${server.token}\r\nContent-Type: application/json\r\nContent-Length: ${body.toByteArray().size}\r\n\r\n$body").toByteArray())
            server.close();release.countDown()
            val closed=runCatching {client.getInputStream().read()==-1}.getOrElse {it is java.net.SocketException}
            assertTrue("A client accepted before close must not remain unowned",closed)
            assertEquals(0,calls.get())
        } finally {release.countDown();client.close();server.close()}
    }
}
