package com.voicestudio.mobile.local

import java.io.File
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.math.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder

class BookTest {
    @get:Rule val temporary=TemporaryFolder()
    private fun entries()=listOf(
        "META-INF/container.xml" to "<container><rootfiles><rootfile full-path=\"OEBPS/book.opf\"/></rootfiles></container>",
        "OEBPS/book.opf" to "<package><manifest><item id=\"a\" href=\"one.xhtml\"/><item id=\"b\" href=\"two.xhtml\"/></manifest><spine><itemref idref=\"b\"/><itemref idref=\"a\"/></spine></package>",
        "OEBPS/one.xhtml" to "<html><head><title>Hidden title</title></head><body><h1>First</h1><p>First body</p></body></html>",
        "OEBPS/two.xhtml" to "<html><body><h1>Second</h1><p>Second body</p><script>do not speak</script></body></html>")
    private fun epub(entries: List<Pair<String,String>>): File {
        val file=temporary.newFile("${java.util.UUID.randomUUID()}.epub")
        ZipOutputStream(file.outputStream()).use {zip->for((name,text) in entries) {zip.putNextEntry(ZipEntry(name));zip.write(text.toByteArray(Charsets.UTF_8));zip.closeEntry()}}
        return file
    }
    @Test fun epubUsesSpineAndNeverExtractsOrSpeaksScripts() {
        val result=BookImport.epub(epub(entries()))
        assertTrue(result.startsWith("# Second"));assertTrue(result.contains("# First"));assertFalse(result.contains("Hidden title"));assertFalse(result.contains("do not speak"))
        assertEquals(listOf("Second","First"),ProductionPlan.parse(result).chapters)
        for(name in listOf("../escape","/absolute","OEBPS/../../escape","OEBPS\\escape","C:/escape")) {
            assertThrows(IllegalArgumentException::class.java) {BookImport.epub(epub(entries()+(name to "unsafe")))}
        }
        assertThrows(IllegalArgumentException::class.java) {BookImport.epub(epub(entries()+("OEBPS/./one.xhtml" to "duplicate")))}
        val entity=entries().toMutableList();entity[2]=entity[2].first to "<!DOCTYPE html [<!ENTITY x SYSTEM \"file:///etc/passwd\">]><html><body>&x;</body></html>"
        assertThrows(IllegalArgumentException::class.java) {BookImport.epub(epub(entity))}
        val truncated=epub(entries());truncated.writeBytes(truncated.readBytes().dropLast(10).toByteArray())
        assertThrows(java.util.zip.ZipException::class.java) {BookImport.epub(truncated)}
    }
    @Test fun pronunciationPausesAndLineSpeedAreLiteralAndBounded() {
        val plan=ProductionPlan.parse("[voice:Alice] [speed:1.5] Dr. Wu [pause 500ms] meets Dr. Watson.\n[pause:1]\nNext",mapOf("Dr." to "Doctor","Wu" to "Woo","Doctor" to "Wrong"),0.75f)
        assertEquals("Doctor Woo",plan.segments[0].text);assertEquals(1.5f,plan.segments[0].speed)
        assertEquals(0.5,plan.segments[1].pause,0.0);assertEquals("meets Doctor Watson.",plan.segments[2].text)
        assertEquals(0.75f,plan.segments.last().speed);assertEquals("Alice",plan.segments.last().role)
        assertThrows(IllegalArgumentException::class.java) {ProductionPlan.parse("Hello [pause:31]")}
        assertThrows(IllegalArgumentException::class.java) {ProductionPlan.parse("[speed:NaN] Hello")}
        assertThrows(IllegalArgumentException::class.java) {ProductionPlan.parse("Hello",mapOf("" to "bad"))}
        val sample=FloatArray(24000) {(sin(it*2*PI*220/24000)*0.5).toFloat()}
        for(rate in listOf(0.5f,0.75f,1.25f,2f)) {
            val stretched=NarrationAudio.speed(sample,rate)
            assertEquals((24000.0/rate).roundToInt(),stretched.size);assertTrue(stretched.all {it.isFinite()})
            val crossings=(0 until stretched.lastIndex).count {stretched[it]<=0&&stretched[it+1]>0}
            assertTrue(abs(crossings*24000.0/stretched.size-220)<25)
        }
    }
    @Test fun editedLineAndPronunciationReuseUnchangedNarration()=runTest {
        val store=ProductionStore(File(temporary.root,"production"));var calls=0
        var project=ProductionProject(kind="audiobook",title="Book",script="First\nSecond\n# Third\nThird")
        val render: suspend (ProductionSegment,String)->LocalAudio={_,_->calls++;LocalAudio(FloatArray(2400) {.2f},24000)}
        store.render(project,"model1",synthesize=render);assertEquals(3,calls)
        project=project.copy(script="First\nChanged\n# Third\nThird")
        store.render(project,"model1",synthesize=render);assertEquals(4,calls)
        project=project.copy(pronunciations=mapOf("Third" to "Thurd"))
        store.render(project,"model1",synthesize=render);assertEquals(5,calls)
        assertEquals(listOf(0.0,0.7),store.chapterCues(project,"model1").map {it.start})
        project=project.copy(script="First [pause 1s]\n# Next\nThird")
        store.render(project,"model1",synthesize=render);assertEquals(5,calls)
        assertEquals(listOf(0.0,1.1),store.chapterCues(project,"model1").map {it.start})
    }
    @Test fun chapterMetadataPreservesMediaOffsetsAndProvenance() {
        fun atom(type: String,body: ByteArray)=ByteArrayOutputStream().also {bytes->DataOutputStream(bytes).use {it.writeInt(body.size+8);it.write(type.toByteArray(Charsets.ISO_8859_1));it.write(body)}}.toByteArray()
        val movieHeader=ByteArray(100);ByteBuffer.wrap(movieHeader).apply {position(12);putInt(1000);putInt(2000);position(96);putInt(1)}
        val media=atom("mdat",byteArrayOf(1,2,3,4));val movie=atom("moov",atom("mvhd",movieHeader))
        val prefix=atom("ftyp",ByteArray(8));val original=prefix+movie+media;val mediaOffset=prefix.size+movie.size
        val file=temporary.newFile("book.m4b").apply {writeBytes(original)}
        BookExport.addMetadata(file,listOf(BookChapter("First",0.0),BookChapter("Second",1.0)))
        val bytes=file.readBytes();assertArrayEquals(media,bytes.copyOfRange(mediaOffset,mediaOffset+media.size))
        assertEquals("free",bytes.copyOfRange(20,24).toString(Charsets.US_ASCII))
        val metadata=bytes.toString(Charsets.ISO_8859_1)
        assertTrue(metadata.contains("VoiceStudio synthetic audio"));assertTrue(metadata.contains("chpl"));assertTrue(metadata.contains("First"));assertTrue(metadata.contains("Second"))
    }
}
