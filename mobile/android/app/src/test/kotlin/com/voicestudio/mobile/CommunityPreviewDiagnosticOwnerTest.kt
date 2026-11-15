package com.voicestudio.mobile

import android.app.Application
import android.os.Looper
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.local.CommunityGallery
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.mockito.ArgumentCaptor
import org.mockito.Mockito.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],application=Application::class)
class CommunityPreviewDiagnosticOwnerTest {
    @get:Rule val compose=createComposeRule()
    private fun preview(startEnabled:Boolean,changeGrant:Boolean,expected:Int)=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val directory=Files.createTempDirectory("gallery-preview-owner").toFile()
        val bodies=CopyOnWriteArrayList<String>()
        val transport=object:DiagnosticTransport {
            override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int {
                check(allowed());bodies.add(body.toString(Charsets.UTF_8));return 204
            }
            override fun cancelAll()=Unit
        }
        val policy=app.assets.open("diagnostics.json").bufferedReader(Charsets.UTF_8).use {DiagnosticPolicy(it.readText())}
        val reporter=DiagnosticReports(File(directory,"reports"),policy,DiagnosticDestination("fixture","https://collector.invalid","https://app.invalid"),"1.0.0",transport=transport)
        val field=AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply {isAccessible=true}
        val previous=field.get(AutomaticDiagnostics)
        reporter.load();field.set(AutomaticDiagnostics,reporter)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        val entered=CountDownLatch(1);val release=CountDownLatch(1)
        val audio="controlled preview bytes".toByteArray(Charsets.UTF_8)
        val hash=MessageDigest.getInstance("SHA-256").digest(audio).joinToString("") {"%02x".format(it)}
        val manifest="""{"schema_version":1,"packs":[],"items":[{"id":"fixture","type":"voice","name":"Fixture preview","language":"English","audio":{"url":"https://cdn.jsdelivr.net/fixture.wav","sha256":"$hash","ref_text":"A fixture reference"}}]}"""
        val client=OkHttpClient.Builder().addInterceptor {chain ->
            val bytes=if(chain.request().url.encodedPath.endsWith("fixture.wav")) {
                entered.countDown();check(release.await(15,TimeUnit.SECONDS));audio
            } else manifest.toByteArray(Charsets.UTF_8)
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK").body(bytes.toResponseBody()).build()
        }.build()
        val galleryRoot=File(directory,"gallery").apply {mkdirs()}
        File(galleryRoot,"manifest.json").writeText(manifest,Charsets.UTF_8)
        val gallery=CommunityGallery(galleryRoot,client)
        val player=mock(Player::class.java)
        try {
            if(startEnabled)reporter.setConsent(true)
            compose.setContent {
                CompositionLocalProvider(LocalL10n provides L10n.load(app,"en"),LocalCommunityGalleryFactory provides {gallery},LocalAudioPlayerFactory provides {player}) {
                    VoiceStudioTheme {GalleryScreen(vm) {}}
                }
            }
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();compose.onAllNodesWithText("Fixture preview").fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText("Play").performScrollTo().performClick()
            assertTrue("The actual gallery recording fetch must be held",entered.await(15,TimeUnit.SECONDS))
            if(changeGrant) {if(startEnabled)reporter.setConsent(false);reporter.setConsent(true)}
            release.countDown()
            // Settle the automatic initial operation before injecting its failure.
            compose.waitUntil(15000) {
                shadowOf(Looper.getMainLooper()).idle()
                val calls=mockingDetails(player).invocations
                calls.any {it.method.name=="prepare"} && calls.any {it.method.name=="play"}
            }
            compose.waitForIdle()
            val listeners=ArgumentCaptor.forClass(Player.Listener::class.java)
            verify(player,atLeastOnce()).addListener(listeners.capture())
            val failure=PlaybackException("private preview error",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED)
            compose.runOnIdle {listeners.allValues.last().onPlayerError(failure)}
            reporter.flush()
            assertEquals("Preview preparation must retain the original click's authority",expected,bodies.size)
            if(changeGrant) {
                // A new explicit inner-player retry must observe the new grant,
                // even though its original automatic preparation was older.
                vm.clearError()
                val retryCount=mockingDetails(player).invocations.count {it.method.name=="prepare"}
                compose.onNodeWithContentDescription("Play").performScrollTo().assertIsDisplayed().performClick()
                compose.waitUntil(15000) {mockingDetails(player).invocations.count {it.method.name=="prepare"}>retryCount}
                verify(player,atLeastOnce()).addListener(listeners.capture())
                compose.runOnIdle {listeners.allValues.last().onPlayerError(failure)}
                reporter.flush()
                assertEquals("Fresh inner Play must remain reportable",1,bodies.size)
                val count=mockingDetails(player).invocations.count {it.method.name=="prepare"}
                compose.onNodeWithText("Play").performScrollTo().performClick()
                compose.waitUntil(15000) {mockingDetails(player).invocations.count {it.method.name=="prepare"}>count}
                verify(player,atLeastOnce()).addListener(listeners.capture())
                compose.runOnIdle {listeners.allValues.last().onPlayerError(failure)}
                reporter.flush()
                assertEquals("A new outer preview click must remain reportable",2,bodies.size)
            }
            assertTrue(bodies.none {it.contains("private preview error")||it.contains("A fixture reference")})
        } finally {
            release.countDown();vm.viewModelScope.cancel();field.set(AutomaticDiagnostics,previous);reporter.close();client.dispatcher.executorService.shutdown();directory.deleteRecursively()
        }
    }
    @Test fun previewStartedOffCannotBorrowLaterOptIn()=preview(false,true,0)
    @Test fun previewCannotBorrowReplacementGrant()=preview(true,true,0)
    @Test fun sameGrantPreviewStillReports()=preview(true,false,1)
}
