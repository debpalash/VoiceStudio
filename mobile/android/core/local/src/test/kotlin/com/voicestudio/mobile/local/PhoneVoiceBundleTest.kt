package com.voicestudio.mobile.local

import java.io.File
import java.util.UUID
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.*
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import com.voicestudio.mobile.network.ClientFailure

class PhoneVoiceBundleTest {
    @get:Rule val temporary=TemporaryFolder()
    private val decoder=object: AudioDecoder {
        override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {
            assertEquals(30,maxSeconds);writePcm(LocalAudio(FloatArray(4800) {.2f},24000),output);return PcmFile(output)
        }
    }
    private suspend fun archive(text: String="Reference words",extra: Boolean=false,designed: Boolean=false): File {
        val metadata=File(temporary.root,"metadata.json").apply {writeText(buildJsonObject {put("bundle_version",1);put("profile_name","Gallery voice");put("ref_text",text);put("kind",if(designed) "design" else "clone")}.toString())}
        val reference=File(temporary.root,"ref_audio.wav").apply {if(designed) writeText("Unmarked decoded fixture") else markSynthetic(LocalAudio(FloatArray(4800) {.2f},24000),this)}
        val files=mutableListOf(ProjectArchiveFile("metadata.json",metadata),ProjectArchiveFile("ref_audio.wav",reference))
        if(extra) files+=ProjectArchiveFile("unexpected.wav",reference)
        return ProjectArchive.write(files,File(temporary.root,"${UUID.randomUUID()}.omnivoice"))
    }
    @Test fun copyInstallsFreshVoiceAndRetainsLicenseWithoutInference()=runTest {
        val source=archive();val store=PhoneVoiceStore(File(temporary.root,"voices"));val credit=mapOf("source" to "community","id" to "foreign-id","author" to "Original creator","license" to "CC BY 4.0")
        val first=PhoneVoiceBundle.install(source,store,credit,true,decoder);val second=PhoneVoiceBundle.install(source,store,credit,true,decoder)
        assertNotEquals(first.id,second.id);assertEquals("Reference words",first.transcript);assertEquals(credit,first.sourceAttribution)
        assertEquals(2,store.list().size);assertEquals("CC BY 4.0",store.list().first().sourceAttribution?.get("license"));assertTrue(PhoneAudioProcessing.containsSyntheticMarker(store.preview(first)))
    }
    @Test fun missingPermissionTranscriptAndUnexpectedAssetsPublishNothing()=runTest {
        val store=PhoneVoiceStore(File(temporary.root,"voices"));val source=archive()
        try {PhoneVoiceBundle.install(source,store,emptyMap(),false,decoder);fail()} catch(_:ClientFailure) {}
        for(bad in listOf(archive(" "),archive(extra=true))) {try {PhoneVoiceBundle.install(bad,store,emptyMap(),true,decoder);fail()} catch(_:ClientFailure) {}}
        assertTrue(store.list().isEmpty())
    }
    @Test fun designedReferenceGetsSyntheticMarkerEvenWhenHostAudioHasNone()=runTest {
        val source=archive(designed=true);val store=PhoneVoiceStore(File(temporary.root,"voices"))
        val voice=PhoneVoiceBundle.install(source,store,emptyMap(),true,decoder)
        assertTrue(PhoneAudioProcessing.containsSyntheticMarker(store.preview(voice)))
    }

}
