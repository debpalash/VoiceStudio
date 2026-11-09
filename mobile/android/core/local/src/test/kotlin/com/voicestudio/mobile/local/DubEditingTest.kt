package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import org.junit.Test
import org.junit.Assert.*

class DubEditingTest {
    private val segments=listOf(
        DubSegmentState(SpeechSegment(0.0,2.0,"First source sentence"),"Exact reference",mapOf("fr" to "First translated sentence"),"custom.pcm","first"),
        DubSegmentState(SpeechSegment(2.0,4.0,"Second source sentence"),"Exact reference",mapOf("fr" to "Second translated sentence"),"custom.pcm","second"))
    @Test fun srtAndVttRoundTripUnicodeTimingAndMultilineText() {
        val input="\uFEFF1\r\n00:00:01,125 --> 00:00:02,900\r\n<b>Hello</b> &amp; goodbye\r\nSecond line\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nNext line\r\n"
        val cues=DubSubtitles.parse(input)
        assertEquals(1.125,cues[0].start,0.0);assertEquals("Hello & goodbye\nSecond line",cues[0].text)
        val editable=cues.mapIndexed {index,cue ->DubSegmentState(SpeechSegment(cue.start,cue.end,"Source"),translations=mapOf("fr" to cue.text),id="$index")}
        assertEquals(cues,DubSubtitles.parse(DubSubtitles.export(editable,"fr",false)))
        assertEquals(cues,DubSubtitles.parse(DubSubtitles.export(editable,"fr",true)))
        val vtt="WEBVTT\n\nNOTE comment\nignore me\n\ncue-id\n00:01.125 --> 00:02.900 align:start\nHello &amp; goodbye\nSecond line\n"
        assertEquals(cues.take(1),DubSubtitles.parse(vtt))
    }
    @Test fun malformedAndUnboundedSubtitlesFailBeforeTimelineChanges() {
        listOf("1\n00:00:02,000 --> 00:00:01,000\nBad", "1\n00:00:00,000 --> 00:00:31,000\nToo long", "1\n00:99:00,000 --> 01:00:00,000\nBad", "WEBVTT\n\nnot a cue").forEach {text ->
            try {DubSubtitles.parse(text);fail(text)} catch(e:ClientFailure) {assertEquals("mobile.editing.subtitle_invalid",e.key)}
        }
    }
    @Test fun splitAndMergePreserveCustomReferencesAndRejectConflictingVoices() {
        val split=DubEditing.split(segments,0,1.0)
        assertEquals(3,split.size);assertEquals("first",split[0].id);assertNotEquals(split[0].id,split[1].id)
        assertEquals("custom.pcm",split[1].referenceFile);assertEquals("Exact reference",split[1].referenceText)
        val merged=DubEditing.merge(split,0)
        assertEquals(2,merged.size);assertEquals(segments[0].speech.start,merged[0].speech.start,0.0);assertEquals(segments[0].speech.end,merged[0].speech.end,0.0)
        try {DubEditing.merge(segments.mapIndexed {index,cue ->if(index==1) cue.copy(referenceFile="other.pcm") else cue},0);fail()} catch(_:IllegalArgumentException) {}
    }
    @Test fun importRetainsExactCueIdentityAndDoesNotInventSourcesAcrossGaps() {
        val exact=DubEditing.importTranslation(segments,listOf(DubSubtitleCue(0.0,2.0,"New translation")),"fr",4.0)
        assertEquals("first",exact.single().id);assertEquals("custom.pcm",exact.single().referenceFile)
        try {DubEditing.importTranslation(segments,listOf(DubSubtitleCue(5.0,6.0,"No source")),"fr",8.0);fail()} catch(_:IllegalArgumentException) {}
        val conflicting=segments.mapIndexed {index,cue ->if(index==1) cue.copy(referenceFile="other.pcm") else cue}
        try {DubEditing.importTranslation(conflicting,listOf(DubSubtitleCue(1.0,3.0,"New translation")),"fr",4.0);fail()} catch(_:IllegalArgumentException) {}
    }
    @Test fun subtitleExportPreservesLiteralMarkupAndCachePruningProtectsPublishedFiles() {
        val cue=DubSegmentState(SpeechSegment(0.0,1.0,"Source"),translations=mapOf("fr" to "Say <hello> & goodbye and &lt;literal&gt; &amp;"),id="cue")
        for(vtt in listOf(false,true)) assertEquals("Say <hello> & goodbye and &lt;literal&gt; &amp;",DubSubtitles.parse(DubSubtitles.export(listOf(cue),"fr",vtt)).single().text)
        val root=java.nio.file.Files.createTempDirectory("dub-cache-").toFile()
        try {
            val source=java.io.File(root,"original.pcm").apply {writeBytes(ByteArray(40))}
            val keep=java.io.File(root,"cache-${"a".repeat(64)}.pcm").apply {writeBytes(ByteArray(40))}
            val old=java.io.File(root,"cache-${"b".repeat(64)}.pcm").apply {writeBytes(ByteArray(8));setLastModified(1000)}
            val latest=java.io.File(root,"cache-${"c".repeat(64)}.pcm").apply {writeBytes(ByteArray(8));setLastModified(2000)}
            val oldMetadata=java.io.File(root,old.name.removeSuffix(".pcm")+".json").apply {writeText("metadata")}
            pruneDubCaches(root,setOf(keep.name),8,1)
            assertTrue(source.exists());assertTrue(keep.exists());assertTrue(latest.exists());assertFalse(old.exists());assertFalse(oldMetadata.exists())
        } finally {root.deleteRecursively()}
    }

}
