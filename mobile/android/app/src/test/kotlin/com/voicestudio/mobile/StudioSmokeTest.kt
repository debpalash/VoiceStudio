package com.voicestudio.mobile

import android.app.Application
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.media3.common.Player
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.features.*
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.mockito.Mockito.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class StudioSmokeTest {
    @get:Rule val compose=createComposeRule()
    private fun awaitCondition(condition:()->Boolean) {
        compose.waitUntil(15000) {
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            condition()
        }
    }
    @Test fun launchConsentConnectGenerateAndPlay() {
        val app=ApplicationProvider.getApplicationContext<Application>()
        val store=SessionStore(app,object:CredentialVault {
            private val secrets=mutableMapOf<String,Secret>()
            override fun read(host:String)=secrets[host]
            override fun write(host:String,secret:Secret?) {if(secret==null) secrets.remove(host) else secrets[host]=secret}
        })
        runBlocking {store.update {Preferences()}}
        val engineRead=java.util.concurrent.CountDownLatch(1)
        val releaseEngines=java.util.concurrent.CountDownLatch(1)
        val server=MockWebServer()
        server.dispatcher=object:Dispatcher() {
            override fun dispatch(request:RecordedRequest):MockResponse = when(request.path) {
                "/health" -> MockResponse().setHeader("x-omnivoice-backend","test").setBody("""{"status":"ok","version":"0.0.0","device":"cpu"}""")
                "/profiles" -> MockResponse().setBody("""[{"id":"v1","name":"Sample voice","language":"French","ref_audio_path":"sample.wav"}]""")
                "/engines" -> {
                    engineRead.countDown();releaseEngines.await(15,java.util.concurrent.TimeUnit.SECONDS)
                    MockResponse().setBody("""{"tts":{"active":"test","backends":[{"id":"test","display_name":"Local engine","supports_cloning":true,"available":true,"supported_language_names":["English"]}]}}""")
                }
                "/history","/dub/history" -> MockResponse().setBody("[]")
                "/generate" -> MockResponse().setHeader("X-Audio-Id","out").setHeader("X-Audio-Path","out.wav").setHeader("X-Audio-Duration","1").setHeader("Content-Type","audio/wav").setBody("RIFF")
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        val player=mock(Player::class.java)
        `when`(player.duration).thenReturn(1000)
        `when`(player.playbackState).thenReturn(Player.STATE_READY)
        val vm=StudioViewModel(app,store)
        try {
            compose.setContent {CompositionLocalProvider(LocalHostPlayerFactory provides {_,_->player}) {StudioApp(vm)}}
            awaitCondition {!vm.state.value.loading}
            compose.onNodeWithText("Saved on this phone. Nothing is sent.").assertExists()
            compose.onNodeWithText("No thanks").assertExists().performClick()
            awaitCondition {vm.preferences.value.consent==false}
            compose.onNodeWithText("Settings").performClick()
            compose.onNodeWithText("Connect").performScrollTo().performClick()
            compose.onNodeWithText("Enter address").performClick()
            compose.onNodeWithText("Server address").performTextInput(server.url("/").toString())
            compose.onAllNodesWithText("Connect").filter(hasClickAction()).onFirst().performScrollTo().performClick()
            awaitCondition {vm.state.value.connected&&engineRead.count==0L}
            compose.onAllNodes(isDialog()).assertCountEquals(0)
            compose.onNodeWithTag("home.speak").performScrollTo().performClick()
            compose.onNodeWithText("Type what you want to hear…").performTextInput("A local generation")
            releaseEngines.countDown()
            awaitCondition {!vm.state.value.busy}
            compose.onNodeWithText("Default voice").assertExists()
            compose.onNodeWithText("Local engine").assertExists()
            compose.onNodeWithText("Auto").assertExists()
            compose.onNodeWithText("Default voice").performScrollTo().performClick()
            compose.onNodeWithText("Sample voice").performClick()
            compose.onNodeWithText("French").assertExists()
            compose.onNodeWithText("Sample voice").performClick()
            compose.onNodeWithText("Default voice").performClick()
            compose.onNodeWithText("Auto").assertExists()
            compose.onNodeWithText("Generate").assertIsDisplayed().performClick()
            awaitCondition {vm.state.value.audio!=null&&!vm.state.value.busy}
            compose.onNodeWithContentDescription("Play").performScrollTo().performClick()
            verify(player).play()
            assertEquals("/audio/out.wav",vm.state.value.audio)
            assertFalse(vm.preferences.value.consent!!)
            compose.onNodeWithText("Voices").performClick()
            compose.onNodeWithText("Clone a voice").assertExists()
            compose.onNodeWithContentDescription("More actions for Sample voice").performScrollTo().performClick()
            compose.onNodeWithText("Rename").performClick()
            compose.onNodeWithText("Cancel").performClick()
            compose.onNodeWithText("Clone a voice").performScrollTo().performClick()
            compose.onNodeWithText("Voice name").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Cancel").assertIsDisplayed().performClick()
        } finally {releaseEngines.countDown();server.shutdown()}
    }
}
