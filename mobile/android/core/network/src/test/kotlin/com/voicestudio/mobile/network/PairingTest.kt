package com.voicestudio.mobile.network

import org.junit.Assert.*
import org.junit.Test

class PairingTest {
    @Test fun qrAndManualAddresses() {
        val qr=PairingAddress.parse("http://192.168.1.20:3900/?pin=123456")
        assertEquals("123456",qr.pin);assertEquals("http://192.168.1.20:3900/",qr.baseUrl.toString())
        assertNull(PairingAddress.parse("https://voice.example/studio").pin)
        assertEquals("/studio/",PairingAddress.parse("https://voice.example/studio").baseUrl.encodedPath)
        assertEquals("[::1]", "["+PairingAddress.parse("http://[::1]:3900/?pin=123456").baseUrl.host+"]")
        assertEquals("654321",PairingAddress.parse("http://10.0.0.2/?pin=123456","654321").pin)
    }
    @Test fun privateCleartextBoundaries() {
        listOf("127.0.0.1","127.254.1.2","10.0.0.1","172.16.0.1","172.31.255.255","192.168.2.1","100.64.0.0","100.127.255.255","169.254.0.1","::1","fc00::1","fd22::2","fe80::1","host.local","box.tail.ts.net","localhost").forEach {assertTrue(it,HostPolicy.cleartextAllowed(it))}
        listOf("8.8.8.8","172.15.255.255","172.32.0.1","100.63.255.255","100.128.0.0","169.253.0.1","192.169.0.1","0.0.0.0","::","2001:4860:4860::8888","example.com","notlocal","evilts.net").forEach {assertFalse(it,HostPolicy.cleartextAllowed(it))}
    }
    @Test fun rejectAmbiguousOrCredentialBearingUrls() {
        listOf("ftp://10.0.0.1","http://8.8.8.8","https://key@example.com","https://example.com/#api_key=x","https://example.com/?api_key=x","http://10.0.0.1/?pin=12","http://10.0.0.1/?pin=123456&pin=654321","invalid").forEach {input ->
            assertThrows(input,ClientFailure::class.java) {PairingAddress.parse(input)}
        }
    }
}
