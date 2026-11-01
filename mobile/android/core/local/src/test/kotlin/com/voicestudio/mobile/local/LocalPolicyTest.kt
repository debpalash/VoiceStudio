package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okio.Buffer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.TimeUnit

class LocalPolicyTest {
    @get:Rule val temporary=TemporaryFolder()
    private val payload="pinned model data".toByteArray()
    private fun model(gated: Boolean=false, digest: String=hash(payload)): PhoneModel = ModelCatalog.decode("""
        {"schema_version":1,"unknown":"ignored","models":[{"id":"fixture","name":"Fixture","capability":"tts","runtime":"sherpa-onnx",
        "repo":"org/model","revision":"${"a".repeat(40)}","files":[{"path":"weights.bin","size":${payload.size},"sha256":"$digest","role":"model"}],
        "total_bytes":${payload.size},"languages":["en"],"min_ram_gb":2,
        "licence":{"id":"example","category":"${if(gated) "non-commercial" else "commercial-ok"}","requires_acceptance":$gated,"credit":"Credit","rights_holder":"Holder","terms_fingerprint":"${"b".repeat(64)}"},"config":{}}]}
    """).models.single()
    private fun hash(bytes: ByteArray)=java.security.MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") {"%02x".format(it)}
    private fun gate()=LicenceGate(File(temporary.root,"licences.json"),"1.2.3")
    private fun downloads(server: MockWebServer, licence: LicenceGate=gate(), wifi: ()->Boolean={true}, free: ()->Long={Long.MAX_VALUE}, endpoints: List<String> = listOf(server.url("/").toString())) =
        ModelDownloads(File(temporary.root,"models"),licence,8_000_000_000,wifi,free,OkHttpClient.Builder().followRedirects(false).build(),endpoints,true)
    private suspend fun failure(key: String, block: suspend ()->Unit) {
        try {block();fail("Expected $key")} catch(e:ClientFailure) {assertEquals(key,e.key)}
    }
    @Test fun temporaryServerFailureRetriesWithoutLosingTheSavedPrefix() = runTest {
        MockWebServer().use {server ->
            server.start()
            File(temporary.root,"models/fixture/weights.bin.part").apply {parentFile!!.mkdirs();writeBytes(payload.copyOfRange(0,5))}
            server.enqueue(MockResponse().setResponseCode(503))
            server.enqueue(MockResponse().setResponseCode(206).setHeader("Content-Range","bytes 5-${payload.size-1}/${payload.size}").setBody(Buffer().write(payload.copyOfRange(5,payload.size))))
            val d=downloads(server)
            d.download(model(),false)
            assertTrue(d.installed(model()))
            assertEquals("bytes=5-",server.takeRequest().getHeader("Range"))
            assertEquals("bytes=5-",server.takeRequest().getHeader("Range"))
        }
    }
    @Test fun throttlingHonorsRetryAfterAndReportsRetryState() = runTest {
        MockWebServer().use {server ->
            server.start();server.enqueue(MockResponse().setResponseCode(429).setHeader("Retry-After","3"));server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val waits=mutableListOf<Long>()
            val d=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true},{Long.MAX_VALUE},OkHttpClient.Builder().followRedirects(false).build(),listOf(server.url("/").toString()),true,retryDelay={waits+=it})
            d.download(model(),false)
            assertTrue(waits.sum()>=3000L);assertTrue(d.installed(model()))
        }
    }
    @Test fun incorrectRangeEndNeverInstallsEvenWhenTheBodyHashMatches() = runTest {
        MockWebServer().use {server ->
            server.start();server.enqueue(MockResponse().setResponseCode(206).setHeader("Content-Range","bytes 5-6/${payload.size}").setBody(Buffer().write(payload.copyOfRange(5,payload.size))))
            File(temporary.root,"models/fixture/weights.bin.part").apply {parentFile!!.mkdirs();writeBytes(payload.copyOfRange(0,5))}
            val d=downloads(server)
            failure("mobile.local.download_failed") {d.download(model(),false)}
            assertFalse(d.installed(model()));assertEquals(1,server.requestCount)
        }
    }
    @Test fun parallelRangesRecoverCompletedChunksAcrossDownloaderRelaunch() = runTest {
        val bytes=ByteArray(1024) {(it%251).toByte()}
        val m=model().let {it.copy(files=listOf(it.files.single().copy(size=bytes.size.toLong(),sha256=hash(bytes))),totalBytes=bytes.size.toLong())}
        MockWebServer().use {server ->
            val requested=java.util.concurrent.CopyOnWriteArrayList<String>()
            var fail=true
            server.dispatcher=object:okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse {
                    val range=request.getHeader("Range")!!;requested+=range
                    val bounds=range.removePrefix("bytes=").split('-').map {it.toInt()}
                    if(fail&&bounds[0]>=256) {
                        // Fail only after the other connection has durably saved its range.
                        val saved=File(temporary.root,"models/fixture/weights.bin.part.ranges/0-255")
                        val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5)
                        while(!saved.isFile&&System.nanoTime()<deadline) Thread.sleep(5)
                        return MockResponse().setResponseCode(404)
                    }
                    return MockResponse().setResponseCode(206).setHeader("Content-Range","bytes ${bounds[0]}-${bounds[1]}/${bytes.size}").setBody(Buffer().write(bytes.copyOfRange(bounds[0],bounds[1]+1)))
                }
            }
            server.start()
            fun fresh()=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true},{Long.MAX_VALUE},OkHttpClient.Builder().followRedirects(false).build(),listOf(server.url("/").toString()),true,chunkSize=256)
            failure("mobile.local.download_failed") {fresh().download(m,false)}
            assertTrue(File(temporary.root,"models/fixture/weights.bin.part.ranges/0-255").isFile)
            fail=false;requested.clear()
            val restored=fresh();restored.download(m,false)
            assertTrue(restored.installed(m));assertFalse(requested.contains("bytes=0-255"))
            assertArrayEquals(bytes,File(temporary.root,"models/fixture/weights.bin").readBytes())
        }
    }
    @Test fun parallelUnsupportedRangesFallBackToOneWholeStream() = runTest {
        MockWebServer().use {server ->
            val bytes=ByteArray(1024) {(it%251).toByte()}
            val m=model().let {it.copy(files=listOf(it.files.single().copy(size=bytes.size.toLong(),sha256=hash(bytes))),totalBytes=bytes.size.toLong())}
            server.dispatcher=object:okhttp3.mockwebserver.Dispatcher() {override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest)=MockResponse().setBody(Buffer().write(bytes))}
            server.start()
            val d=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true},{Long.MAX_VALUE},OkHttpClient.Builder().followRedirects(false).build(),listOf(server.url("/").toString()),true,chunkSize=256)
            d.download(m,false)
            assertTrue(d.installed(m));assertArrayEquals(bytes,File(temporary.root,"models/fixture/weights.bin").readBytes())
        }
    }
    @Test fun resumedBytesAndRetriesNeverInflateSpeed() {
        var now=0.0;val meter=DownloadMeter(1000) {now}
        meter.seed("weights",500)
        now=1.0
        val start=meter.update("weights",500)
        assertEquals(500L,start.received);assertEquals(0.0,start.bytesPerSecond,0.0);assertNull(start.etaSeconds)
        now=2.0
        val value=meter.update("weights",600)
        assertTrue(value.bytesPerSecond>0);assertTrue(value.bytesPerSecond<=100)
        now=3.0
        assertEquals(600L,meter.update("weights",550).received)
        val retry=meter.update("weights",600,"retrying",8)
        assertEquals(0.0,retry.bytesPerSecond,0.0);assertNull(retry.etaSeconds)
    }
    @Test fun segmentedDownloadReservesAssemblySpaceBeforeContactingTheServer() = runTest {
        MockWebServer().use {server ->
            server.start();server.dispatcher=object:okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest)=MockResponse().setResponseCode(404)
            }
            val bytes=ByteArray(1024)
            val m=model().let {it.copy(files=listOf(it.files.single().copy(size=1024,sha256=hash(bytes))),totalBytes=1024)}
            val d=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true},{1024L+16*1024*1024},OkHttpClient.Builder().followRedirects(false).build(),listOf(server.url("/").toString()),true,chunkSize=256)
            failure("mobile.local.storage_required") {d.download(m,false)}
            assertEquals(0,server.requestCount)
        }
    }
    @Test fun omniSpeechRequiresAReferenceBeforeLoadingAnyModel() = runTest {
        val m=fixtureModel("clone")
        assertTrue(m.supportsSpeech&&m.supportsDesign&&m.requiresReference)
        val d=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true})
        failure("mobile.speak.reference_required") {PhoneLocalEngine(d).synthesize(m,"Hi hello")}
    }
    @Test fun nativeSpeechConfigCannotEscapeItsInstalledFrontendDirectory() {
        val m=model().let {it.copy(files=it.files.map {f->f.copy(role="espeak-data")},config=JsonObject(mapOf("model_type" to JsonPrimitive("vits"),"data_dir" to JsonPrimitive("../outside"))))}
        try {speechDataDirectory(m,temporary.root);fail("Escaping frontend accepted")} catch(e:ClientFailure) {assertEquals("mobile.local.model_incompatible",e.key)}
    }
    @Test fun bundledSpeechFrontendInstallsOfflineAndRejectsCorruption() = runTest {
        val m=model().let {it.copy(files=it.files.map {f->f.copy(bundled="tts/frontend.txt")})}
        var corrupt=true
        val d=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{false},{Long.MAX_VALUE},bundledAssets={ (if(corrupt) ByteArray(payload.size) else payload).inputStream() })
        failure("mobile.local.checksum_failed") {d.download(m,false)}
        assertFalse(d.installed(m));corrupt=false
        d.download(m,false);assertTrue(d.installed(m))
        assertArrayEquals(payload,d.withModel(m) {File(it,"weights.bin").readBytes()})
    }
    @Test fun fixedSpeechComponentsRequireApprovedPinnedSources() {
        assertTrue(ModelAssetSource.validUrl("https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx"))
        assertTrue(ModelAssetSource.validUrl("https://huggingface.co/owner/model/resolve/"+"a".repeat(40)+"/encoder.onnx"))
        for(url in listOf("https://github.com/other/repo/releases/download/tag/model.onnx","https://huggingface.co/owner/model/resolve/main/model.onnx","https://huggingface.co/owner/model/resolve/"+"a".repeat(40)+"/../outside")) assertFalse(ModelAssetSource.validUrl(url))
        assertFalse(ModelAssetSource.validBundled("../outside"))
    }
    @Test fun contractAndRoutingFailClosed() {
        val m=model();assertEquals("fixture",m.id);assertTrue(m.fits(2_000_000_000));assertFalse(m.fits(1_999_999_999))
        assertEquals(RunLocation.PHONE,routeTask(null,true,true))
        assertEquals(RunLocation.COMPUTER,routeTask(null,false,true))
        assertEquals(RunLocation.DOWNLOAD,routeTask(null,false,false))
        assertEquals(RunLocation.PHONE,routeTask("computer",true,false))
        assertEquals(RunLocation.DOWNLOAD,routeTask("phone",false,true))
        assertEquals(RunLocation.COMPUTER,routeTask("computer",true,true))
    }
    @Test fun modelRedirectHostsMatchIosIncludingMirrorSubdomains() {
        val downloads=ModelDownloads(File(temporary.root,"models"),gate(),8_000_000_000,{true},{Long.MAX_VALUE},OkHttpClient.Builder().followRedirects(false).build(),listOf("https://huggingface.co"),false)
        for(host in listOf("huggingface.co","hf-mirror.com","cdn-lfs.hf-mirror.com","cas-bridge.xethub.hf.co"))
            assertTrue(downloads.allowed("https://$host/org/model/resolve/main/weights.bin".toHttpUrl()))
        for(url in listOf("http://huggingface.co/org/model/resolve/main/weights.bin","https://user:pass@huggingface.co/x","https://huggingface.co.evil.test/x","https://cdn-lfs.hf-mirror.com.evil.test/x","https://hf-mirror.com:8443/x","https://example.org/x"))
            assertFalse(downloads.allowed(url.toHttpUrl()))
    }
    @Test fun invalidCatalogueCannotChooseAnUnpinnedOrEscapingFile() {
        val json=kotlinx.serialization.json.Json
        val valid=json.encodeToString(ModelCatalog.serializer(),ModelCatalog(1,listOf(model())))
        for(invalid in listOf(valid.replace("\"name\":\"Fixture\"","\"name\":\"\""),valid.replace("weights.bin","../escape"),valid.replace("weights.bin",".verified"),valid.replace(hash(payload),"missing-checksum"),valid.replace("\"schema_version\":1","\"schema_version\":2"))) {
            try {ModelCatalog.decode(invalid);fail("Invalid catalogue accepted")} catch(_:IllegalArgumentException) {}
        }
    }
    @Test fun unreadableReceiptsAndStaleDialogFingerprintsStayBlocked() {
        val m=model(true);val g=gate()
        try {g.record(m,true,"c".repeat(64));fail()} catch(_:ClientFailure) {}
        assertFalse(g.accepted(m))
        File(temporary.root,"licences.json").writeText("corrupted")
        try {g.requireAccepted(m);fail()} catch(e:ClientFailure) {assertEquals("mobile.local.licence_storage",e.key)}
    }
    @Test fun licencePersistsFingerprintVersionTimeAndWithdrawal() {
        val m=model(true);val gate=gate();assertFalse(gate.accepted(m))
        try {gate.requireAccepted(m);fail()} catch(_:ClientFailure) {}
        gate.record(m,true);assertTrue(gate.accepted(m));assertTrue(gate().accepted(m))
        val text=File(temporary.root,"licences.json").readText();assertTrue(text.contains("1.2.3"));assertTrue(text.contains("time"))
        assertFalse(gate.accepted(m.copy(licence=m.licence.copy(termsFingerprint="c".repeat(64)))))
        gate.record(m,false);assertFalse(gate().accepted(m))
    }
    @Test fun resumeUsesRangeThenChecksDigestBeforeActivation() = runTest {
        MockWebServer().use {server -> server.start()
            val part=File(temporary.root,"models/fixture/weights.bin.part").apply {parentFile!!.mkdirs();writeBytes(payload.copyOfRange(0,5))}
            server.enqueue(MockResponse().setResponseCode(206).setHeader("Content-Range","bytes 5-${payload.size-1}/${payload.size}").setBody(Buffer().write(payload.copyOfRange(5,payload.size))))
            val downloads=downloads(server);val m=model();downloads.download(m,false)
            assertEquals("bytes=5-",server.takeRequest().getHeader("Range"));assertFalse(part.exists());assertTrue(downloads.installed(m))
            assertArrayEquals(payload,downloads.withModel(m) {File(it,"weights.bin").readBytes()})
            File(temporary.root,"models/fixture/weights.bin").apply {
                val previous=lastModified()
                writeBytes(ByteArray(payload.size))
                // Make the metadata change deterministic on filesystems with coarse timestamps.
                assertTrue(setLastModified(previous+2000))
            }
            failure("mobile.local.checksum_failed") {downloads.withModel(m) {fail()}}
            assertFalse(downloads.installed(m))
        }
    }
    @Test fun serverIgnoringRangeRestartsWithoutAppending() = runTest {
        MockWebServer().use {server -> server.start();server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            File(temporary.root,"models/fixture/weights.bin.part").apply {parentFile!!.mkdirs();writeBytes(payload.copyOfRange(0,5))}
            val d=downloads(server);d.download(model(),false);assertTrue(d.installed(model()))
        }
    }
    @Test fun checksumFailureNeverActivatesAndDeleteRemovesFragments() = runTest {
        MockWebServer().use {server -> server.start();server.enqueue(MockResponse().setBody(Buffer().write(ByteArray(payload.size))))
            val d=downloads(server);failure("mobile.local.checksum_failed") {d.download(model(),false)}
            assertFalse(d.installed(model()));d.delete(model());assertEquals(0L,d.usedBytes())
        }
    }
    @Test fun wifiStorageAndLicenceBlockBeforeNetwork() = runTest {
        MockWebServer().use {server ->server.start()
            failure("mobile.local.wifi_required") {downloads(server,wifi={false}).download(model(),false)}
            failure("mobile.local.storage_required") {downloads(server,free={0}).download(model(),false)}
            val m=model(true);val gate=gate();val d=downloads(server,gate)
            failure("mobile.local.licence_required") {d.download(m,false)}
            failure("mobile.local.licence_required") {d.withModel(m) {fail()}}
            assertEquals(0,server.requestCount)
        }
    }
    @Test fun ramRecommendationAllowsVerifiedDownloadAndUseOnSmallerDevice() = runTest {
        MockWebServer().use { server ->
            server.start();server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val m=model(true).copy(minRamGb=32.0)
            assertFalse(m.fits(8_000_000_000))
            val gate=gate();val d=downloads(server,gate)
            failure("mobile.local.licence_required") {d.download(m,false)}
            assertEquals(0,server.requestCount)
            gate.record(m,true)
            d.download(m,false)
            assertTrue(d.installed(m))
            assertArrayEquals(payload,d.withModel(m) {File(it,"weights.bin").readBytes()})
            assertEquals(1,server.requestCount)
        }
    }
    @Test fun verifiedInstalledModelCanBeReacceptedOfflineWithoutDownloadSpace() = runTest {
        MockWebServer().use {server -> server.start();server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val m=model(true);val g=gate();g.record(m,true)
            downloads(server,g).download(m,false)
            g.record(m,false);g.record(m,true)
            // A fresh downloader verifies disk contents rather than trusting an in-memory stamp.
            val offline=downloads(server,g,wifi={false},free={0})
            offline.download(m,false)
            assertTrue(offline.installed(m));assertEquals(1,server.requestCount)
            File(temporary.root,"models/fixture/weights.bin").writeBytes(ByteArray(payload.size))
            failure("mobile.local.wifi_required") {downloads(server,g,wifi={false},free={0}).download(m,false)}
            assertEquals(1,server.requestCount)
        }
    }
    @Test fun fallbackAndMobileDataRequireAnExplicitDownload() = runTest {
        MockWebServer().use {primary ->MockWebServer().use {mirror ->
            primary.start();mirror.start();primary.enqueue(MockResponse().setResponseCode(503));mirror.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val d=downloads(primary,wifi={false},endpoints=listOf(primary.url("/").toString(),mirror.url("/").toString()))
            assertEquals(0,primary.requestCount);assertEquals(0,mirror.requestCount)
            d.download(model(),true);assertTrue(d.installed(model()));assertEquals(1,primary.requestCount);assertEquals(1,mirror.requestCount)
        }}
    }
    @Test fun cancellationRetainsResumeBytesWithoutActivating() = runTest {
        MockWebServer().use {server ->server.start()
            server.enqueue(MockResponse().setBody(Buffer().write(payload)).throttleBody(2,1,TimeUnit.SECONDS))
            val d=downloads(server)
            val job=launch(Dispatchers.IO) {d.download(model(),false)}
            withContext(Dispatchers.IO) {assertNotNull(server.takeRequest(5,TimeUnit.SECONDS))}
            job.cancelAndJoin();assertFalse(d.installed(model()));assertFalse(d.progress.value[model().id]?.running==true)
            d.delete(model());assertEquals(0L,d.usedBytes())
        }
    }
    @Test fun concurrentDownloadsReserveDiskAndCancellationReleasesIt() = runBlocking {
        MockWebServer().use {server -> server.start()
            server.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE))
            server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val d=downloads(server,free={16*1024*1024+payload.size.toLong()})
            val first=launch(Dispatchers.IO) {d.download(model(),false)}
            try {
                withContext(Dispatchers.IO) {assertNotNull(server.takeRequest(5,TimeUnit.SECONDS))}
                failure("mobile.local.storage_required") {withTimeout(5000) {d.download(model().copy(id="second"),false)}}
                assertEquals(1,server.requestCount)
            } finally {first.cancelAndJoin()}
            withTimeout(5000) {d.download(model().copy(id="second"),false)}
            assertTrue(d.installed(model().copy(id="second")))
            assertEquals(2,server.requestCount)
        }
    }
    @Test fun concurrentDownloadCountsOnlyBytesStillMissingFromOtherDownloads() = runBlocking {
        MockWebServer().use {server -> server.start()
            server.enqueue(MockResponse().setBody(Buffer().write(payload)).throttleBody(payload.size-1L,2,TimeUnit.SECONDS))
            server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val part=File(temporary.root,"models/fixture/weights.bin.part")
            val d=downloads(server,free={16*1024*1024+2L*payload.size-part.length()})
            val first=launch(Dispatchers.IO) {d.download(model(),false)}
            try {
                withTimeout(5000) {while(part.length()<payload.size-1) delay(10)}
                withTimeout(5000) {d.download(model().copy(id="second"),false)}
                assertTrue(d.installed(model().copy(id="second")))
                assertEquals(2,server.requestCount)
            } finally {first.cancelAndJoin()}
        }
    }
    @Test fun withdrawalBlocksPreviouslyVerifiedWeights() = runTest {
        MockWebServer().use {server ->server.start();server.enqueue(MockResponse().setBody(Buffer().write(payload)))
            val m=model(true);val g=gate();g.record(m,true);val d=downloads(server,g);d.download(m,false)
            g.record(m,false);failure("mobile.local.licence_required") {d.withModel(m) {fail()}}
        }
    }
    @Test fun nativeControlHonoursCancellationAndAudioHasSyntheticMetadata() {
        val job=Job();val values=mutableListOf<Float>();val control=NativeControl(job,{values+=it}) {true}
        control.progress(.5f);assertEquals(listOf(.5f),values);job.cancel();assertTrue(control.cancelled());control.progress(1f);assertEquals(1,values.size)
        val output=File(temporary.root,"result.wav");markSynthetic(LocalAudio(floatArrayOf(.1f,.2f,-.5f),24000),output)
        val bytes=output.readBytes();assertEquals("RIFF",bytes.copyOfRange(0,4).toString(Charsets.US_ASCII));assertTrue(bytes.toString(Charsets.US_ASCII).contains("VoiceStudio synthetic audio"))
    }

    @Test fun licenceReviewSummarisesTermsAndKeepsOnlyHttpsLinks() {
        fun licence(vararg terms: Pair<String, String>) = ModelLicence("x", "conditional", true, "c", "r", "f", JsonObject(terms.associate { it.first to JsonPrimitive(it.second) }))
        val review = licence("commercial_inference" to "restricted", "notes" to "  GPL data.  ", "evidence_url" to "https://huggingface.co/x/LICENSE", "source_url" to "http://insecure.example/m").review
        assertEquals(listOf("commercial", "outputs", "redistribution", "voice"), review.rights.map { it.first })
        assertEquals(listOf("restricted", "unknown", "unknown", "unknown"), review.rights.map { it.second })
        assertEquals("GPL data.", review.notes)
        assertEquals("https://huggingface.co/x/LICENSE", review.evidenceUrl)
        assertNull(review.sourceUrl)
        val blank = licence("commercial_inference" to "allowed-ish", "notes" to "  ", "evidence_url" to "https://u@evil.example/x").review
        assertTrue(blank.rights.all { it.second == "unknown" }); assertNull(blank.notes); assertNull(blank.evidenceUrl)
    }

    @Test fun termLinesCoverEveryDeclaredKeyReadably() {
        val terms = JsonObject(mapOf("evidence_revision" to JsonPrimitive("abc"), "variants" to kotlinx.serialization.json.JsonArray(emptyList()),
            "nested" to JsonObject(mapOf("a" to JsonPrimitive("x")))))
        val lines = ModelLicence("x", "conditional", true, "c", "r", "f", terms).termLines
        assertEquals(setOf("evidence_revision", "variants", "nested"), lines.map { it.key }.toSet())
        assertEquals("Evidence revision", lines.first { it.key == "evidence_revision" }.label)
        assertEquals("–", lines.first { it.key == "variants" }.value)
        assertEquals("a: x", lines.first { it.key == "nested" }.value)
    }
}
