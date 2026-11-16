package com.voicestudio.mobile

import android.app.Application
import android.view.SurfaceView
import androidx.compose.runtime.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.semantics.SemanticsActions
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.LocalAudioPlayer
import com.voicestudio.mobile.features.LocalAudioPlayerFactory
import com.voicestudio.mobile.features.HostPlayer
import com.voicestudio.mobile.features.LocalHostPlayerFactory
import com.voicestudio.mobile.network.PairingAddress
import com.voicestudio.mobile.network.VoiceStudioApi
import com.voicestudio.mobile.features.LocalL10n
import com.voicestudio.mobile.l10n.L10n
import org.junit.Assert.assertSame
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import androidx.lifecycle.viewModelScope
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.network.*
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.features.dub.ComputerDubAdvancedScreen
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.*
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import android.os.Looper
import org.robolectric.Shadows.shadowOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.ArgumentCaptor
import org.mockito.Mockito.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class LocalVideoPlaybackTest {
    @get:Rule val compose=createComposeRule()

    private fun playbackOwner(local:Boolean) {
        val player=mock(Player::class.java)
        val app=ApplicationProvider.getApplicationContext<Application>()
        val api=VoiceStudioApi(PairingAddress.parse("http://127.0.0.1:8765"))
        var shown by mutableStateOf(true)
        var grant=0
        val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val original=grant;{observed+=original}}
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalHostPlayerFactory provides {_,_->player},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {if(shown) {
                    if(local)LocalAudioPlayer(File("fixture.wav"),failure) else HostPlayer(api,"fixture.wav",failure)
                }}
            }
        }
        compose.waitForIdle()
        val callbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val old=callbacks.allValues.last()
        val error=PlaybackException("private playback fault",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED)
        compose.runOnIdle {grant=1;old.onPlayerError(error)}
        assertEquals("The original callback must retain its observed owner",listOf(0),observed)
        compose.onNodeWithContentDescription("Play").performClick()
        compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val fresh=callbacks.allValues.last()
        compose.runOnIdle {old.onPlayerError(error);fresh.onPlayerError(error)}
        assertEquals("A new play owns a fresh callback; old queued callbacks stay retired",listOf(0,1),observed)
        compose.runOnIdle {shown=false}
        compose.waitForIdle()
        compose.runOnIdle {fresh.onPlayerError(error)}
        assertEquals("Disposed playback cannot emit a late failure",listOf(0,1),observed)
    }
    @Test fun localPlaybackBindsEachStartAndRetiresOldCallbacks()=playbackOwner(true)
    @Test fun hostPlaybackBindsEachStartAndRetiresOldCallbacks()=playbackOwner(false)

    private fun seekingKeepsTheLoadOwner(local:Boolean) {
        val player=mock(Player::class.java)
        `when`(player.duration).thenReturn(10000L)
        val app=ApplicationProvider.getApplicationContext<Application>()
        val api=VoiceStudioApi(PairingAddress.parse("http://127.0.0.1:8765"))
        var grant=0
        val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val original=grant;{observed+=original}}
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalHostPlayerFactory provides {_,_->player},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {
                    if(local) LocalAudioPlayer(File("fixture.wav"),failure) else HostPlayer(api,"fixture.wav",failure)
                }
            }
        }
        compose.waitForIdle()
        val callbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val original=callbacks.allValues.last()
        compose.runOnIdle {grant=1}
        compose.onNodeWithContentDescription("Seek").performSemanticsAction(SemanticsActions.SetProgress) {it(0.5f)}
        compose.waitForIdle()
        verify(player).seekTo(5000L)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val afterSeek=callbacks.allValues.last()
        val error=PlaybackException("private pending load fault",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED)
        compose.runOnIdle {afterSeek.onPlayerError(error)}
        assertEquals("Seeking must not replace an unfinished load's authority",listOf(0),observed)
        assertSame(original,afterSeek)
        compose.onNodeWithContentDescription("Play").performClick()
        compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val fresh=callbacks.allValues.last()
        compose.runOnIdle {original.onPlayerError(error);fresh.onPlayerError(error)}
        assertEquals("A genuinely new play still uses its own authority",listOf(0,1),observed)
    }
    @Test fun localSeekCannotReplacePendingLoadConsent()=seekingKeepsTheLoadOwner(true)
    @Test fun hostSeekCannotReplacePendingLoadConsent()=seekingKeepsTheLoadOwner(false)

    private fun playCannotReplacePendingPreparation(local:Boolean, initial:Int) {
        val player=mock(Player::class.java)
        `when`(player.playbackState).thenReturn(Player.STATE_BUFFERING)
        val app=ApplicationProvider.getApplicationContext<Application>()
        val api=VoiceStudioApi(PairingAddress.parse("http://127.0.0.1:8765"))
        var grant=initial
        val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val original=grant;{observed+=original}}
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalHostPlayerFactory provides {_,_->player},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {if(local)LocalAudioPlayer(File("fixture.wav"),failure) else HostPlayer(api,"fixture.wav",failure)}
            }
        }
        compose.waitForIdle()
        val callbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val preparationOwner=callbacks.allValues.last()
        compose.runOnIdle {grant=initial+1}
        compose.onNodeWithContentDescription("Play").performClick();compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val actualDelivery=callbacks.allValues.last()
        val fault=PlaybackException("private preparation failure",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED)
        compose.runOnIdle {actualDelivery.onPlayerError(fault)}
        assertEquals("Pending prepare must retain OFF/old-grant owner through Play",listOf(initial),observed)
        assertSame(preparationOwner,actualDelivery)
        // Once that operation has failed, the next explicit retry is a fresh
        // semantic operation and may use the current permission.
        compose.onNodeWithContentDescription("Play").performClick();compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        compose.runOnIdle {callbacks.allValues.last().onPlayerError(fault)}
        assertEquals(listOf(initial,initial+1),observed)
    }
    @Test fun hostPendingPrepareCannotBorrowOffToOn()=playCannotReplacePendingPreparation(false,0)
    @Test fun localPendingPrepareCannotBorrowOffToOn()=playCannotReplacePendingPreparation(true,0)
    @Test fun hostPendingPrepareCannotBorrowRegrant()=playCannotReplacePendingPreparation(false,1)
    @Test fun localPendingPrepareCannotBorrowRegrant()=playCannotReplacePendingPreparation(true,1)

    @Test fun repeatedPreviewPlayRequestsRestartTheExistingPlayer() {
        val player=mock(Player::class.java)
        var request by mutableLongStateOf(1)
        val app=ApplicationProvider.getApplicationContext<Application>()
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {LocalAudioPlayer(File("preview.wav"),autoPlay=true,playRequest=request) {{error ->throw AssertionError(error)}}}
            }
        }
        compose.waitForIdle();verify(player).play();verify(player).seekTo(0)
        compose.runOnIdle {request++}
        compose.waitForIdle();verify(player,times(2)).play();verify(player,times(2)).seekTo(0)
        verify(player,never()).release()
    }

    @Test fun switchingVideosAttachesTheRetainedSurfaceToTheNewPlayer() {
        val first=mock(Player::class.java)
        val second=mock(Player::class.java)
        val players=ArrayDeque(listOf(first,second))
        var file by mutableStateOf(File("first.mp4"))
        val app=ApplicationProvider.getApplicationContext<Application>()
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {players.removeFirst()},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {LocalAudioPlayer(file) {{error ->throw AssertionError(error)}}}
            }
        }
        compose.waitForIdle()
        val initial=ArgumentCaptor.forClass(SurfaceView::class.java)
        verify(first,atLeastOnce()).setVideoSurfaceView(initial.capture())
        compose.runOnIdle {file=File("second.mp4")}
        compose.waitForIdle()
        val replacement=ArgumentCaptor.forClass(SurfaceView::class.java)
        verify(second,atLeastOnce()).setVideoSurfaceView(replacement.capture())
        assertSame(initial.value,replacement.value)
        verify(first).release()
        verify(second,never()).release()
    }

    private fun advancedDubKeepsLaunchOwner(initiallyEnabled:Boolean,replace:Boolean) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        runBlocking {store.update {Preferences()}}
        val reports=CopyOnWriteArrayList<String>()
        val directory=Files.createTempDirectory("advanced-dub-owner").toFile()
        val policy=app.assets.open("diagnostics.json").bufferedReader(Charsets.UTF_8).use {DiagnosticPolicy(it.readText())}
        val reporter=DiagnosticReports(directory,policy,DiagnosticDestination("fixture","https://collector.invalid","https://app.invalid"),"1.0.0",transport=object:DiagnosticTransport {
            override suspend fun send(destination:DiagnosticDestination,body:ByteArray,allowed:()->Boolean):Int {
                check(allowed());reports.add(body.toString(Charsets.UTF_8));return 201
            }
            override fun cancelAll()=Unit
        })
        val field=AutomaticDiagnostics::class.java.getDeclaredField("reporter").apply {isAccessible=true}
        val previous=field.get(AutomaticDiagnostics)
        runBlocking {reporter.load();if(initiallyEnabled)reporter.setConsent(true)}
        field.set(AutomaticDiagnostics,reporter)
        val entered=CountDownLatch(1);val release=CountDownLatch(1)
        val providerCalls=AtomicInteger();val terminalCalls=AtomicInteger();val generatedTasks=AtomicInteger()
        val paths=CopyOnWriteArrayList<String>()
        val server=MockWebServer()
        server.dispatcher=object:okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request:RecordedRequest):MockResponse {
                val path=request.path.orEmpty();paths.add(path)
                return when {
                    path=="/health" -> MockResponse().setHeader("x-omnivoice-backend","test").setBody("""{"status":"ok","version":"test"}""")
                    path in listOf("/profiles","/history","/dub/history") -> MockResponse().setBody("[]")
                    path=="/engines" -> MockResponse().setBody("""{"tts":{"active":"test","backends":[]}}""")
                    path=="/dub/tracks/owner-job" -> MockResponse().setBody("""{"tracks":{"en":{"language":"English"}}}""")
                    path=="/engines/diarisation" -> MockResponse().setBody("""{"installed":true}""")
                    path.startsWith("/dub/transcribe-stream/") -> MockResponse().setBody("event: final\ndata: {\"segments\":[{\"id\":\"1\",\"start\":0,\"end\":1,\"text\":\"Neutral fixture\"}]}\n\nevent: done\ndata: {}\n\n")
                    path=="/engines/translation" -> {
                        if(providerCalls.incrementAndGet()==1) {entered.countDown();check(release.await(15,TimeUnit.SECONDS))}
                        MockResponse().setBody("""{"active":"argos","engines":[{"id":"argos","category":"offline","installed":true,"ready":true}]}""")
                    }
                    path=="/dub/translate" -> MockResponse().setBody("""{"translated":[{"id":"1","text":"Neutral translated"}]}""")
                    path=="/dub/generate/owner-job" -> MockResponse().setBody("""{"task_id":"owner-task-${generatedTasks.incrementAndGet()}"}""")
                    path.startsWith("/jobs/owner-task-") -> {
                        terminalCalls.incrementAndGet()
                        val id=path.substringAfter("/jobs/").substringBefore('/')
                        MockResponse().setBody("""{"job":{"id":"$id","status":"failed","created_at":100,"updated_at":101},"events":[{"seq":1,"created_at":101,"payload":"data: {\"type\":\"error\"}"}],"event_watermark":{"seq":1,"created_at":101}}""")
                    }
                    else -> MockResponse().setResponseCode(500)
                }
            }
        };server.start()
        val vm=StudioViewModel(app,store)
        val l10n=L10n.load(app,"en")
        fun idle()=compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.state.value.busy}
        try {
            idle();vm.connect(server.url("/").toString(),"","");idle()
            app.getSharedPreferences("advanced-dub-tasks",android.content.Context.MODE_PRIVATE).edit().clear().commit()
            compose.setContent {CompositionLocalProvider(LocalL10n provides l10n) {VoiceStudioTheme {ComputerDubAdvancedScreen(vm,"owner-job","Fixture",{})}}}
            compose.waitForIdle()
            compose.onNodeWithText(l10n.t("mobile.dub_advanced.detect")).performScrollTo().performClick()
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();compose.onAllNodes(androidx.compose.ui.test.hasContentDescription(l10n.t("mobile.dub_advanced.cast_checked"))).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithContentDescription(l10n.t("mobile.dub_advanced.cast_checked")).performScrollTo().performClick()
            compose.onNodeWithText(l10n.t("dub.generate_dub")).performScrollTo().performClick()
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();entered.count==0L}
            assertTrue("Generate did not reach its held provider read",entered.count==0L)
            if(replace)runBlocking {reporter.setConsent(false);reporter.setConsent(true)}
            release.countDown()
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();terminalCalls.get()==1&&vm.state.value.error?.key=="mobile.errors.dub_failed"}
            runBlocking {reporter.flush()}
            assertTrue(paths.contains("/dub/generate/owner-job"))
            assertEquals("Provider await lent the submission a later diagnostic grant",if(initiallyEnabled&&!replace)1 else 0,reports.size)
            vm.clearError()
            compose.onNodeWithText(l10n.t("dub.generate_dub")).performScrollTo().performClick()
            compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();terminalCalls.get()==2&&vm.state.value.error?.key=="mobile.errors.dub_failed"}
            runBlocking {reporter.flush()}
            assertEquals("A separate later Generate click must remain reportable",if(initiallyEnabled&&!replace)2 else 1,reports.size)
        } finally {
            release.countDown();vm.viewModelScope.cancel();server.shutdown();field.set(AutomaticDiagnostics,previous)
            runBlocking {reporter.close()};directory.deleteRecursively()
        }
    }
    @Test fun advancedDubProviderAwaitCannotBorrowLaterOptIn()=advancedDubKeepsLaunchOwner(false,true)
    @Test fun advancedDubProviderAwaitCannotBorrowReplacementGrant()=advancedDubKeepsLaunchOwner(true,true)
    @Test fun advancedDubProviderAwaitSameGrantAndFreshClickReport()=advancedDubKeepsLaunchOwner(true,false)


    private fun readyPlayBindsFreshOwner(local:Boolean) {
        val player=mock(Player::class.java)
        val app=ApplicationProvider.getApplicationContext<Application>()
        val api=VoiceStudioApi(PairingAddress.parse("http://127.0.0.1:8765"))
        var grant=0;val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val original=grant;{observed+=original}}
        compose.setContent {CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalHostPlayerFactory provides {_,_->player},LocalL10n provides L10n.load(app,"en")) {
            VoiceStudioTheme {if(local)LocalAudioPlayer(File("ready.wav"),failure) else HostPlayer(api,"ready.wav",failure)}
        }}
        compose.waitForIdle()
        val callbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val initial=callbacks.allValues.last()
        compose.runOnIdle {initial.onPlaybackStateChanged(Player.STATE_READY);grant=1}
        compose.onNodeWithContentDescription("Play").performClick();compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        compose.runOnIdle {callbacks.allValues.last().onPlayerError(PlaybackException("synthetic ready failure",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED))}
        assertEquals(listOf(1),observed)
    }
    @Test fun readyLocalPlayCanUseFreshConsent()=readyPlayBindsFreshOwner(true)
    @Test fun readyHostPlayCanUseFreshConsent()=readyPlayBindsFreshOwner(false)

    private fun previewRequestKeepsPreparationOwner(initial:Int) {
        val player=mock(Player::class.java)
        `when`(player.playbackState).thenReturn(Player.STATE_BUFFERING)
        val app=ApplicationProvider.getApplicationContext<Application>()
        var request by mutableLongStateOf(1)
        var grant=initial;val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val original=grant;{observed+=original}}
        compose.setContent {CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalL10n provides L10n.load(app,"en")) {
            VoiceStudioTheme {LocalAudioPlayer(File("preview.wav"),autoPlay=true,playRequest=request,failure=failure)}
        }}
        compose.waitForIdle()
        val callbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val initialListener=callbacks.allValues.last()
        compose.runOnIdle {grant=initial+1;request++};compose.waitForIdle()
        verify(player,atLeastOnce()).addListener(callbacks.capture())
        val delivered=callbacks.allValues.last()
        compose.runOnIdle {delivered.onPlayerError(PlaybackException("synthetic pending preview",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED))}
        assertSame(initialListener,delivered)
        assertEquals(listOf(initial),observed)
        verify(player,times(2)).play();verify(player,times(2)).seekTo(0)
    }
    @Test fun autoplayRequestCannotBorrowLaterOptIn()=previewRequestKeepsPreparationOwner(0)
    @Test fun autoplayRequestCannotBorrowReplacementGrant()=previewRequestKeepsPreparationOwner(1)


    private fun hostPathSwitchRetiresPreviousPreparation(initial:Int) {
        val first=mock(Player::class.java)
        val second=mock(Player::class.java)
        `when`(first.playbackState).thenReturn(Player.STATE_BUFFERING)
        val players=ArrayDeque(listOf(first,second))
        val app=ApplicationProvider.getApplicationContext<Application>()
        val api=VoiceStudioApi(PairingAddress.parse("http://127.0.0.1:8765"))
        var path by mutableStateOf("first.wav")
        var grant=initial;val observed=mutableListOf<Int>()
        val failure:()->(Throwable)->Unit={val owner=grant;{observed+=owner}}
        compose.setContent {CompositionLocalProvider(LocalHostPlayerFactory provides {_,_->players.removeFirst()},LocalL10n provides L10n.load(app,"en")) {
            VoiceStudioTheme {HostPlayer(api,path,failure)}
        }}
        compose.waitForIdle()
        compose.runOnIdle {grant=initial+1;path="second.wav"};compose.waitForIdle()
        val firstCallbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(first,atLeastOnce()).addListener(firstCallbacks.capture())
        val queuedOldPlayerCallback=firstCallbacks.allValues.last()
        val fault=PlaybackException("synthetic previous source failure",null,PlaybackException.ERROR_CODE_IO_UNSPECIFIED)
        compose.runOnIdle {queuedOldPlayerCallback.onPlayerError(fault)}
        assertEquals("An old source cannot report through the new source's listener",emptyList<Int>(),observed)
        verify(first).release();verify(second).prepare()
        val secondCallbacks=ArgumentCaptor.forClass(Player.Listener::class.java)
        verify(second,atLeastOnce()).addListener(secondCallbacks.capture())
        compose.runOnIdle {secondCallbacks.allValues.last().onPlayerError(fault)}
        assertEquals("The separately requested source owns the current grant",listOf(initial+1),observed)
    }
    @Test fun hostPathSwitchCannotBorrowLaterOptIn()=hostPathSwitchRetiresPreviousPreparation(0)
    @Test fun hostPathSwitchCannotBorrowReplacementGrant()=hostPathSwitchRetiresPreviousPreparation(1)

}
