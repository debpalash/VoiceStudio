package com.voicestudio.mobile

import android.app.Application
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.features.TranscriptImportSession
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.*
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.test.*
import okhttp3.*
import okhttp3.mockwebserver.*
import okio.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],application=Application::class)
class StudioDiagnosticOwnerTest {
    private val app get()=ApplicationProvider.getApplicationContext<Application>()
    @Before fun prepare() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
    }
    @After fun reset() {Dispatchers.resetMain()}

    private class Collector:DiagnosticTransport {
        val bodies=CopyOnWriteArrayList<String>()
        override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int {
            check(allowed());bodies.add(body.toString(Charsets.UTF_8));return 201
        }
        override fun cancelAll()=Unit
    }
    private suspend fun reports(block:suspend(DiagnosticReports,Collector)->Unit) {
        val directory=Files.createTempDirectory("studio-owner-diagnostics").toFile()
        val collector=Collector()
        val policy=app.assets.open("diagnostics.json").bufferedReader(Charsets.UTF_8).use {DiagnosticPolicy(it.readText())}
        val reporter=DiagnosticReports(directory,policy,DiagnosticDestination("fixture","https://collector.invalid","https://app.invalid"),"1.0.0",transport=collector)
        val field=AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply {isAccessible=true}
        val previous=field.get(AutomaticDiagnostics)
        reporter.load();field.set(AutomaticDiagnostics,reporter)
        try {block(reporter,collector)}
        finally {field.set(AutomaticDiagnostics,previous);reporter.close();directory.deleteRecursively()}
    }
    private suspend fun viewModel(engine:LocalEngine?=null):StudioViewModel {
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences()}
        return StudioViewModel(app,store,engine).also {vm->
            withTimeout(15000) {vm.state.first {!it.loading};vm.phone.state.first {!it.loading}}
            vm.clearError()
        }
    }
    private class BodyGate {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        fun body(original:ResponseBody):ResponseBody=object:ResponseBody() {
            private val held=object:ForwardingSource(original.source()) {
                override fun read(sink:Buffer,byteCount:Long):Long {
                    entered.countDown();check(release.await(15,TimeUnit.SECONDS))
                    return super.read(sink,byteCount)
                }
            }.buffer()
            override fun contentType()=original.contentType()
            override fun contentLength()=original.contentLength()
            override fun source()=held
        }
    }
    private suspend fun hostFailure(startEnabled:Boolean,replaceGrant:Boolean,expected:Int,cancel:Boolean=false) {
        reports {reporter,collector->
            val vm=viewModel()
            val server=MockWebServer()
            val gate=AtomicReference(BodyGate())
            server.start()
            try {
                val api=VoiceStudioApi(PairingAddress.parse(server.url("/").toString(),""))
                val heldClient=api.client.newBuilder().addNetworkInterceptor {chain->
                    val response=chain.proceed(chain.request())
                    response.newBuilder().body(gate.get().body(checkNotNull(response.body))).build()
                }.build()
                VoiceStudioApi::class.java.getDeclaredField("client").apply {isAccessible=true}.set(api,heldClient)
                StudioViewModel::class.java.getDeclaredField("api").apply {isAccessible=true}.set(vm,api)
                if(startEnabled)reporter.setConsent(true)
                server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"private rejected generation"}"""))
                vm.generate("private script","","fixture","en")
                assertTrue("VoiceStudioApi never entered the failed response body",withContext(Dispatchers.IO) {gate.get().entered.await(15,TimeUnit.SECONDS)})
                if(replaceGrant) {reporter.setConsent(false);reporter.setConsent(true)}
                if(cancel)vm.cancel()
                gate.get().release.countDown()
                withTimeout(15000) {vm.state.first {!it.busy}}
                reporter.flush()
                assertEquals("Delayed HTTP failure used a different grant",expected,collector.bodies.size)
                if(replaceGrant) {
                    gate.set(BodyGate().also {it.release.countDown()})
                    server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"private later rejection"}"""))
                    vm.retryError()
                    withTimeout(15000) {vm.state.first {!it.busy&&it.error!=null}}
                    reporter.flush()
                    assertEquals("A genuinely later retry must report",1,collector.bodies.size)
                }
                assertTrue(collector.bodies.none {it.contains("private")})
            }finally {gate.get().release.countDown();vm.viewModelScope.cancel();server.shutdown()}
        }
    }
    @Test fun httpBodyCannotBorrowLaterOptIn()=runBlocking {hostFailure(false,true,0)}
    @Test fun httpBodyCannotBorrowReplacementGrant()=runBlocking {hostFailure(true,true,0)}
    @Test fun httpBodySameGrantReports()=runBlocking {hostFailure(true,false,1)}
    @Test fun httpBodyCancellationDoesNotReport()=runBlocking {hostFailure(true,false,0,cancel=true)}

    private class EngineGate {
        val entered=CompletableDeferred<Unit>()
        val release=CompletableDeferred<Unit>()
    }
    private class FailingEngine:LocalEngine {
        var gate=EngineGate()
        var linkage=false
        override suspend fun synthesize(model:PhoneModel,text:String,speaker:Int,speed:Float,language:String?,progress:(Float)->Unit):LocalAudio {
            val owned=gate
            try {if(linkage)throw UnsatisfiedLinkError("private native failure") else throw IllegalStateException("private native failure")}
            finally {withContext(NonCancellable) {owned.entered.complete(Unit);owned.release.await()}}
        }
        override suspend fun clone(model:PhoneModel,text:String,reference:LocalAudio,referenceText:String,progress:(Float)->Unit):LocalAudio=error("unused")
        override suspend fun transcribe(model:PhoneModel,audio:LocalAudio,progress:(Float)->Unit):String=error("unused")
        override fun streamTranscribe(model:PhoneModel,audio:Flow<LocalAudio>):Flow<Transcript> = emptyFlow()
        override suspend fun translate(model:PhoneModel,text:String,targetLanguage:String,progress:(Float)->Unit):String=error("unused")
    }
    private suspend fun phoneFailure(startEnabled:Boolean,replaceGrant:Boolean,expected:Int,cancel:Boolean=false,linkage:Boolean=false) {
        reports {reporter,collector->
            val engine=FailingEngine().also {it.linkage=linkage}
            val vm=viewModel(engine)
            val model=vm.phone.state.value.models.first {it.capability=="tts"}
            try {
                if(startEnabled)reporter.setConsent(true)
                vm.phone.speak(model,"private script",0)
                withTimeout(15000) {engine.gate.entered.await()}
                if(replaceGrant) {reporter.setConsent(false);reporter.setConsent(true)}
                if(cancel)vm.phone.cancelInference()
                engine.gate.release.complete(Unit)
                withTimeout(15000) {vm.phone.state.first {it.progress==null}}
                reporter.flush()
                assertEquals("PhoneStudio callback borrowed later consent",expected,collector.bodies.size)
                if(replaceGrant) {
                    engine.gate=EngineGate().also {it.release.complete(Unit)}
                    vm.retryError()
                    withTimeout(15000) {vm.phone.state.first {it.progress==null};vm.state.first {it.error!=null}}
                    reporter.flush()
                    assertEquals("A later phone retry must report",1,collector.bodies.size)
                }
                assertTrue(collector.bodies.none {it.contains("private")})
            }finally {engine.gate.release.complete(Unit);vm.viewModelScope.cancel()}
        }
    }
    @Test fun nativeCleanupCannotBorrowLaterOptIn()=runBlocking {phoneFailure(false,true,0)}
    @Test fun nativeCleanupCannotBorrowReplacementGrant()=runBlocking {phoneFailure(true,true,0)}
    @Test fun nativeLinkageCallbackCannotBorrowLaterOptIn()=runBlocking {phoneFailure(false,true,0,linkage=true)}
    @Test fun nativeCleanupSameGrantReports()=runBlocking {phoneFailure(true,false,1)}
    @Test fun nativeCleanupCancellationDoesNotReport()=runBlocking {phoneFailure(true,false,0,cancel=true)}

    private suspend fun retainedImport(startEnabled:Boolean,replaceGrant:Boolean,cancel:Boolean=false) {
        reports {reporter,collector->
            val vm=viewModel()
            val importer=TranscriptImportSession(app)
            val entered=CompletableDeferred<Unit>()
            val release=CompletableDeferred<Unit>()
            try {
                if(startEnabled)reporter.setConsent(true)
                importer.start(vm::failureHandler) {
                    try {throw IllegalStateException("private document import failure")}
                    finally {withContext(NonCancellable) {entered.complete(Unit);release.await()}}
                }
                val owned=checkNotNull(importer.work)
                withTimeout(15000) {entered.await()}
                if(replaceGrant) {reporter.setConsent(false);reporter.setConsent(true)}
                if(cancel)owned.cancel()
                release.complete(Unit);owned.join()
                reporter.flush()
                assertEquals(if(startEnabled&&!replaceGrant&&!cancel)1 else 0,collector.bodies.size)
                if(replaceGrant) {
                    importer.start(vm::failureHandler) {throw IllegalStateException("private new document import")}
                    importer.work?.join()
                    reporter.flush()
                    assertEquals("A later retained-controller operation must report",1,collector.bodies.size)
                }
            }finally {release.complete(Unit);importer.viewModelScope.cancel();vm.viewModelScope.cancel()}
        }
    }
    @Test fun retainedImportCannotBorrowLaterOptIn()=runBlocking {retainedImport(false,true)}
    @Test fun retainedImportCannotBorrowReplacementGrant()=runBlocking {retainedImport(true,true)}
    @Test fun retainedImportSameGrantReports()=runBlocking {retainedImport(true,false)}
    @Test fun retainedImportCancellationDoesNotReport()=runBlocking {retainedImport(true,false,cancel=true)}
}
