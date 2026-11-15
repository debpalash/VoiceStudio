package com.voicestudio.mobile

import android.app.Application
import android.view.SurfaceView
import androidx.compose.runtime.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
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
}
