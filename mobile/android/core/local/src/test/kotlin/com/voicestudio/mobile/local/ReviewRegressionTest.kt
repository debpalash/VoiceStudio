package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ReviewRegressionTest {
    @get:Rule val temporary=TemporaryFolder()
    private val content="verified weights".toByteArray()
    private val hash=java.security.MessageDigest.getInstance("SHA-256").digest(content).joinToString("") {"%02x".format(it)}
    private val model=fixtureModel("tts").copy(id="fixture",files=listOf(ModelFile("weights",content.size.toLong(),hash,"model")),totalBytes=content.size.toLong())
    private fun target()=File(temporary.root,"models/fixture/weights")
    private var hashes=0
    private fun downloads(server: MockWebServer, free: ()->Long={Long.MAX_VALUE})=ModelDownloads(
        File(temporary.root,"models"),LicenceGate(File(temporary.root,"licences"),"test"),8_000_000_000,{true},free,
        OkHttpClient(),listOf(server.url("/").toString()),true,digest={ file,check -> hashes++;sha256(file,check) })
    @Test fun resumeCountsOnlyRemainingBytesAndDoesNotTrustCorruptCompletedFiles()=runTest {
        MockWebServer().use { server -> server.start()
            target().apply {parentFile!!.mkdirs();writeBytes(content)}
            val selection=model.copy(files=model.files+ModelFile("remaining",content.size.toLong(),hash,"tokens"),totalBytes=content.size*2L)
            server.enqueue(MockResponse().setBody(String(content)))
            var available=16*1024*1024+content.size.toLong()
            val downloads=downloads(server) {available}
            downloads.download(selection,false)
            assertTrue(downloads.installed(selection));assertEquals(1,server.requestCount)
            target().writeBytes(ByteArray(content.size));target().setLastModified(target().lastModified()+2000)
            available=16*1024*1024L
            try {downloads.download(selection,false);fail("Damaged completed bytes need storage too")}
            catch(e:ClientFailure) {assertEquals("mobile.local.storage_required",e.key)}
        }
    }
    @Test fun modelHashesAreCachedUntilStampCatalogueOrNativeFailureChanges()=runTest {
        MockWebServer().use {server -> server.start();server.enqueue(MockResponse().setBody(String(content)))
            val downloads=downloads(server);downloads.download(model,false);assertEquals(1,hashes)
            repeat(3) {downloads.withModel(model) {}}
            assertEquals("Inference must reuse install verification",1,hashes)
            target().setLastModified(target().lastModified()+2000)
            downloads.withModel(model) {};assertEquals(2,hashes)
            val changed=model.copy(config=kotlinx.serialization.json.buildJsonObject {put("threads",kotlinx.serialization.json.JsonPrimitive(2))})
            downloads.withModel(changed) {};assertEquals(3,hashes)
            try {downloads.withModel(changed) {throw IllegalStateException("native load")}} catch(_:IllegalStateException) {}
            downloads.withModel(changed) {};assertEquals(4,hashes)
            target().writeBytes(ByteArray(content.size));target().setLastModified(target().lastModified()+2000)
            try {downloads.withModel(changed) {};fail("Changed bytes accepted")}
            catch(e:ClientFailure) {assertEquals("mobile.local.checksum_failed",e.key)}
            assertFalse(downloads.installed(model))
        }
    }
    @Test fun nativeLeaseSurvivesCancellationUntilNativeWorkReturns()=runTest {
        MockWebServer().use {server -> server.start();server.enqueue(MockResponse().setBody(String(content)))
            val downloads=downloads(server);downloads.download(model,false);val engine=PhoneLocalEngine(downloads)
            val entered=CountDownLatch(1);val release=CountDownLatch(1);val secondEntered=CountDownLatch(1)
            val first=launch(Dispatchers.Default) {engine.execute(model,"tts",{}) {_,_ -> entered.countDown();check(release.await(10,TimeUnit.SECONDS))}}
            try {
                assertTrue(entered.await(10,TimeUnit.SECONDS));first.cancel()
                val second=launch(Dispatchers.Default) {engine.execute(model,"tts",{}) {_,_ -> secondEntered.countDown()}}
                assertFalse("Cancelled native work still owns the lease",secondEntered.await(200,TimeUnit.MILLISECONDS))
                release.countDown();first.join();second.join();assertEquals(0L,secondEntered.count)
            } finally {release.countDown();first.cancelAndJoin()}
        }
    }
}
