package com.voicestudio.mobile

import android.app.Application
import android.view.SurfaceView
import androidx.compose.runtime.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.media3.common.Player
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.LocalAudioPlayer
import com.voicestudio.mobile.features.LocalAudioPlayerFactory
import com.voicestudio.mobile.features.LocalL10n
import com.voicestudio.mobile.l10n.L10n
import org.junit.Assert.assertSame
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

    @Test fun repeatedPreviewPlayRequestsRestartTheExistingPlayer() {
        val player=mock(Player::class.java)
        var request by mutableLongStateOf(1)
        val app=ApplicationProvider.getApplicationContext<Application>()
        compose.setContent {
            CompositionLocalProvider(LocalAudioPlayerFactory provides {player},LocalL10n provides L10n.load(app,"en")) {
                VoiceStudioTheme {LocalAudioPlayer(File("preview.wav"),autoPlay=true,playRequest=request) {throw AssertionError(it)}}
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
                VoiceStudioTheme {LocalAudioPlayer(file) {throw AssertionError(it)}}
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
