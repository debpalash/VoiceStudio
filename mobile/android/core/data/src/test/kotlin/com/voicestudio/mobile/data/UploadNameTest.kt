package com.voicestudio.mobile.data

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Test

class UploadNameTest {
    private val cached = File("cache/1f2e.wav")

    @Test fun extensionlessDisplayNameGetsTheCachedExtension() {
        assertEquals("Interview.wav", uploadName("Interview", cached))
    }

    @Test fun matchingExtensionIsKeptAsIs() {
        assertEquals("vs-dub-test.wav", uploadName("vs-dub-test.wav", cached))
        assertEquals("Clip.WAV", uploadName("Clip.WAV", cached))
    }

    @Test fun mismatchedExtensionIsSuffixed() {
        assertEquals("Interview.final.wav", uploadName("Interview.final", cached))
    }

    @Test fun missingDisplayNameFallsBackToTheCachedName() {
        assertEquals("1f2e.wav", uploadName(null, cached))
        assertEquals("1f2e.wav", uploadName("  ", cached))
    }
}
