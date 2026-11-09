package com.voicestudio.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.features.PhoneIncoming
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PhoneIncomingTest {
    private val context get()=ApplicationProvider.getApplicationContext<Context>()
    @Test fun sharingTextIsBoundedAndOpensReviewWithoutStartingATask()=runBlocking {
        val intent=Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT,"x".repeat(25000))
        val payload=requireNotNull(PhoneIncoming.read(context,intent))
        assertEquals(20000,payload.text?.length);assertNull(payload.file);assertNull(payload.route)
    }
    @Test fun workspaceLinksOnlyAllowKnownEditorsAndKeepInputAsText()=runBlocking {
        val payload=requireNotNull(PhoneIncoming.read(context,Intent(Intent.ACTION_VIEW,Uri.parse("voicestudio://workspace/speak?text=Hello%20phone"))))
        assertEquals("speak",payload.route);assertEquals("Hello phone",payload.text)
        assertNull(PhoneIncoming.read(context,Intent(Intent.ACTION_VIEW,Uri.parse("voicestudio://workspace/start-recording"))))
        assertNull(PhoneIncoming.read(context,Intent(Intent.ACTION_VIEW,Uri.parse("https://workspace/dictate"))))
    }
    @Test fun untrustedFileSchemesAndUnsupportedTypesAreRejected()=runBlocking {
        val file=Intent(Intent.ACTION_SEND).setType("audio/wav").putExtra(Intent.EXTRA_STREAM,Uri.parse("file:///private/audio.wav"))
        assertNull(PhoneIncoming.read(context,file))
        assertNull(PhoneIncoming.read(context,Intent(Intent.ACTION_SEND).setType("application/octet-stream")))
    }
}
