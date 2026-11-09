package com.voicestudio.mobile.local

import org.junit.*
import org.junit.Assert.*
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AdvancedDubTest {
    @Test fun styledAssEscapesControlSyntaxKeepsOriginalAndNeverInventsWordTimes() {
        val cue=DubSegmentState(SpeechSegment(1.125,2.5,"Source {text}"),translations=mapOf("fr" to "Hello {\\pos(0,0)}\nNext"),speaker="A, B")
        val ass=PhoneDubASS.export(listOf(cue),"fr",DubSubtitleStyle(fontSize=48,color="FFFF00",top=true,dual=true))
        assertTrue(ass.contains("Style: Default,Arial,48,&H0000FFFF"));assertTrue(ass.contains("Dialogue: 0,0:00:01.13,0:00:02.50,Default,A  B"))
        assertTrue(ass.contains("\\{\\\\pos(0,0)\\}"));assertTrue(ass.contains("\\NNext\\N{\\i1}Source \\{text\\}{\\i0}"));assertFalse(ass.contains("\\kf"))
        assertThrows(IllegalArgumentException::class.java) {PhoneDubASS.export(listOf(cue),"fr",DubSubtitleStyle(color="FFFFFF,Injected"))}
    }
    @Test fun castAssignmentPreservesExplicitAndLegacyLineOverridesAndMergeRejectsDifferentSpeakers() {
        val a=DubSegmentState(SpeechSegment(0.0,1.0,"First"),speaker="Alex")
        val b=DubSegmentState(SpeechSegment(1.0,2.0,"Second"),referenceFile="private.pcm",speaker="Alex")
        val cast=PhoneDubCast.assigning(listOf(a,b),"Alex","voice-1")
        assertEquals("voice-1",cast[0].voiceID);assertEquals("cast",cast[0].referenceOrigin);assertEquals("private.pcm",cast[1].referenceFile);assertNull(cast[1].voiceID)
        assertTrue(PhoneDubCast.assigning(cast,"Alex","voice-2",true).all {it.voiceID=="voice-2"})
        assertThrows(Exception::class.java) {DubEditing.merge(listOf(a,b.copy(speaker="Other")),0)}
    }
    private fun box(type: String,vararg payloads: ByteArray): ByteArray {
        val size=8+payloads.sumOf {it.size};return ByteBuffer.allocate(size).order(ByteOrder.BIG_ENDIAN).putInt(size).put(type.toByteArray(Charsets.US_ASCII)).apply {payloads.forEach(::put)}.array()
    }
    private fun audio(version: Int): ByteArray {
        val tkhd=ByteArray(if(version==1) 96 else 84).apply {this[0]=version.toByte();this[3]=7}
        val mdhd=ByteArray(if(version==1) 36 else 24).apply {this[0]=version.toByte()}
        val hdlr=ByteArray(24).apply {"soun".toByteArray().copyInto(this,8)}
        return box("trak",box("tkhd",tkhd),box("mdia",box("mdhd",mdhd),box("hdlr",hdlr)))
    }
    @Test fun muxMetadataMakesLanguageTracksAlternateAndMarksOnlyChosenFirstTrackDefaultForBothHeaderVersions() {
        val bytes=box("moov",audio(0),audio(1));MultiAudioMetadata.patch(bytes,listOf("fr","de"))
        fun value(offset: Int)=ByteBuffer.wrap(bytes,offset,2).short.toInt() and 65535
        val first=8+8+8;assertEquals(3,bytes[first+3].toInt());assertEquals(1,value(first+34))
        val second=8+audio(0).size+8+8;assertEquals(2,bytes[second+3].toInt());assertEquals(1,value(second+46))
        val firstLanguage=first+84+8+8+20
        val secondLanguage=second+96+8+8+32
        fun packed(code: String)=((code[0].code-96) shl 10) or ((code[1].code-96) shl 5) or (code[2].code-96)
        assertEquals(packed("fra"),value(firstLanguage));assertEquals(packed("deu"),value(secondLanguage))
        assertThrows(IllegalArgumentException::class.java) {MultiAudioMetadata.patch(bytes,listOf("fr"))}
        assertThrows(IllegalArgumentException::class.java) {MultiAudioMetadata.patch(box("moov",audio(0)).copyOf(13),listOf("fr"))}
    }
}
