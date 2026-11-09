package com.voicestudio.mobile.local

import java.io.File
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder

class PhoneTranscriptTest {
    @get:Rule val temporary=TemporaryFolder()
    private val model=fixtureModel("asr")
    private val decoder=object: AudioDecoder {
        override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {
            writePcm(LocalAudio(FloatArray(504000) {.2f},24000),output);return PcmFile(output)
        }
    }
    @Test fun historyPersistsSearchEditsAndIdempotentSessions()=runTest {
        val root=File(temporary.root,"history");val store=PhoneTranscriptStore(root);val id=UUID.randomUUID().toString()
        store.saveDictation(id,"Meeting","  Original words  ")
        store.saveDictation(id,"Duplicate","Different words")
        val restored=PhoneTranscriptStore(root)
        assertEquals(1,restored.list("ORIGINAL").size)
        restored.edit(id,"Corrected words")
        assertEquals("Original words",restored.record(id).originalText)
        assertEquals("Corrected words",restored.list("corrected").first().text)
        restored.edit(id,null);assertEquals("Original words",restored.record(id).text)
        restored.delete(id);assertTrue(restored.list().isEmpty())
    }
    @Test fun fileResumesCompletedChunksAndPreservesAbsoluteTimes()=runTest {
        val root=File(temporary.root,"history");val store=PhoneTranscriptStore(root)
        val record=store.prepare(temporary.newFile(),"Interview",model,decoder)
        var calls=0
        val recognize: suspend (LocalAudio)->List<SpeechSegment> ={calls++;if(calls==2) throw CancellationException();listOf(SpeechSegment(0.0,.5,if(calls==1) "First" else "Second"))}
        try {store.run(record.id,model,recognize=recognize);fail()} catch(_:CancellationException) {}
        assertEquals(480000L,store.record(record.id).nextFrame)
        val restored=PhoneTranscriptStore(root);val result=restored.run(record.id,model,recognize=recognize)
        assertTrue(result.complete);assertEquals("First\nSecond",result.originalText);assertEquals(20.0,result.segments[1].start,0.0);assertEquals(3,calls)
        assertFalse(File(root,"${record.id}/source.pcm").exists())
        assertTrue(PhoneTranscriptExport.subtitles(result,true).contains("00:00:20.000 --> 00:00:20.500"))
        restored.edit(record.id,"Edited")
        try {PhoneTranscriptExport.subtitles(restored.record(record.id),true);fail()} catch(_:IllegalArgumentException) {}
    }
    @Test fun corruptSourceNeverReachesRecognizerAndMarkupEscaped()=runTest {
        val root=File(temporary.root,"history");val store=PhoneTranscriptStore(root)
        val record=store.prepare(temporary.newFile(),"Interview",model,decoder)
        File(root,"${record.id}/source.pcm").writeText("changed")
        try {store.run(record.id,model) {fail("Corrupt input reached recognizer");emptyList()};fail()} catch(_:IllegalArgumentException) {}
        val cue=PhoneTranscript(title="Captions",segments=listOf(SpeechSegment(0.0,1.0,"<b>Hello</b> & goodbye\n\nnext")))
        assertTrue(PhoneTranscriptExport.subtitles(cue,false).contains("&lt;b&gt;Hello&lt;/b&gt; &amp; goodbye\nnext"))
    }
    @Test fun emojiTitlesFitUtf16LimitWithoutBreakingSurrogates()=runTest {
        val store=PhoneTranscriptStore(File(temporary.root,"titles"));val title="👨‍👩‍👧‍👦".repeat(120)
        val saved=store.saveDictation(java.util.UUID.randomUUID().toString(),title,"Spoken words")
        assertTrue(saved.valid);assertTrue(saved.title.length<=300);assertFalse(saved.title.last().isHighSurrogate());assertEquals("Spoken words",saved.originalText)
        val fallback=store.saveDictation(java.util.UUID.randomUUID().toString()," \n ","\nFirst words")
        assertEquals("First words",fallback.title)
        val prepared=store.prepare(temporary.newFile(),title,fixtureModel(),decoder)
        assertTrue(prepared.valid);assertTrue(prepared.title.length<=300)
    }

    @Test fun oversizedSourceRejectedBeforeDecoder()=runTest {
        val source=temporary.newFile()
        java.io.RandomAccessFile(source,"rw").use {it.setLength(PhoneTranscriptStore.MAX_SOURCE_BYTES+1)}
        val store=PhoneTranscriptStore(File(temporary.root,"history"))
        try {store.prepare(source,"Too large",model,decoder);fail("Oversized source accepted")} catch(_:com.voicestudio.mobile.network.ClientFailure) {}
        assertTrue(store.list().isEmpty())
    }

}
