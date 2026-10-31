package com.voicestudio.mobile.local

import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.test.runTest
import okhttp3.*
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder

class CommunityGalleryTest {
    @get:Rule val temporary=TemporaryFolder()
    private fun seed():ByteArray=File("../../../../contract/community-gallery.json").let {if(it.isFile) it else File("../../../contract/community-gallery.json")}.readBytes()
    private fun client(replies:MutableList<Pair<Int,ByteArray>>,calls:MutableList<String>,location:String?=null)=OkHttpClient.Builder().followRedirects(false).addInterceptor {chain ->
        calls+=chain.request().url.toString();assertNull(chain.request().header("Authorization"))
        val (code,bytes)=replies.removeAt(0)
        Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code).message("fixture").body(ResponseBody.create(null,bytes)).apply {if(location!=null) header("Location",location)}.build()
    }.build()
    @Test fun realCatalogueWorksWithoutNetworkAndRetainsCacheWhenRefreshFails()=runTest {
        val calls=mutableListOf<String>();val service=CommunityGallery(temporary.root,client(mutableListOf(200 to seed(),500 to byteArrayOf()),calls))
        val bundled=service.cached(seed());assertEquals(50,bundled.items.size);assertTrue(bundled.items.any {it.name=="The Librarian"});assertTrue(bundled.items.any {it.name=="Thalia"&&it.audio!=null});assertTrue(calls.isEmpty())
        service.refresh();assertEquals(1,calls.size)
        try {service.refresh();fail()} catch(_:com.voicestudio.mobile.network.ClientFailure) {}
        assertEquals(50,service.cached(byteArrayOf()).items.size)
    }
    @Test fun refusesUnsafeCatalogueAndRedirectBeforeSecondRequest()=runTest {
        val unsafe=seed().toString(Charsets.UTF_8).replace("https://github.com/debpalash/omnivoice-gallery/releases","http://127.0.0.1/releases")
        try {CommunityCatalogue.decode(unsafe.toByteArray());fail()} catch(_:IllegalArgumentException) {}
        val calls=mutableListOf<String>();val service=CommunityGallery(temporary.root,client(mutableListOf(302 to byteArrayOf()),calls,"https://untrusted.example/voice"))
        try {service.refresh();fail()} catch(_:IllegalArgumentException) {}
        assertEquals(1,calls.size)
    }
    @Test fun hashCacheAndImportRequireIntegrityAndConsent()=runTest {
        val source=File(temporary.root,"source.wav");markSynthetic(LocalAudio(FloatArray(4800) {.2f},24000),source)
        val wave=source.readBytes();val hash=MessageDigest.getInstance("SHA-256").digest(wave).joinToString("") {"%02x".format(it)}
        val entry=GalleryEntry("test","voice","Test","English","creator","CC-BY-4.0",audio=GalleryRecording("https://github.com/debpalash/omnivoice-gallery/releases/download/voices-v1/test.wav",hash,"Actual reference words"))
        val calls=mutableListOf<String>();val service=CommunityGallery(File(temporary.root,"cache"),client(mutableListOf(200 to "bad".toByteArray(),200 to wave,200 to wave),calls))
        try {service.recording(entry);fail()} catch(_:com.voicestudio.mobile.network.ClientFailure) {}
        assertFalse(File(temporary.root,"cache/$hash.wav").exists())
        val file=service.recording(entry);service.recording(entry);assertEquals(2,calls.size)
        file.writeText("tampered");service.recording(entry);assertEquals(3,calls.size)
        val store=PhoneVoiceStore(File(temporary.root,"voices"))
        val decoder=object:AudioDecoder {override suspend fun decode(source:File,output:File,maxSeconds:Int,progress:(Float)->Unit):PcmFile {assertEquals(30,maxSeconds);writePcm(LocalAudio(FloatArray(4800) {.2f},24000),output);return PcmFile(output)}}
        try {store.importCommunity(entry,file,false,decoder);fail()} catch(_:IllegalArgumentException) {}
        assertTrue(store.list().isEmpty())
        val voice=store.importCommunity(entry,file,true,decoder)
        assertEquals("Actual reference words",voice.transcript);assertEquals("CC-BY-4.0",voice.sourceAttribution?.get("license"));assertTrue(PhoneAudioProcessing.containsSyntheticMarker(store.preview(voice)))
    }
}
