package com.voicestudio.mobile.local

import android.content.Context
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader
import com.tom_roush.pdfbox.io.MemoryUsageSetting
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.File
import java.io.StringReader
import java.util.zip.CRC32
import java.util.zip.ZipFile

/** Entirely local document import. ZIP members are bounded and never extracted to disk. */
object BookImport {
    const val TEXT_LIMIT=1_000_000
    suspend fun read(source: File,context: Context): String=withContext(Dispatchers.IO) {
        require(source.isFile&&source.length()<=67_108_864L)
        val text=when(source.extension.lowercase()) {
            "epub"->epub(source)
            "pdf"->{
                PDFBoxResourceLoader.init(context.applicationContext)
                PDDocument.load(source,MemoryUsageSetting.setupTempFileOnly()).use {pdf->
                    require(!pdf.isEncrypted&&pdf.numberOfPages<=5000)
                    val result=StringBuilder();val stripper=PDFTextStripper()
                    for(page in 1..pdf.numberOfPages) {
                        currentCoroutineContext().ensureActive();stripper.startPage=page;stripper.endPage=page
                        val part=stripper.getText(pdf);require(result.length+part.length+2<=TEXT_LIMIT);result.append(part).append("\n\n")
                    }
                    result.toString()
                }
            }
            else->source.inputStream().reader(Charsets.UTF_8).use {reader->
                val result=StringBuilder();val buffer=CharArray(8192)
                while(true) {currentCoroutineContext().ensureActive();val count=reader.read(buffer);if(count<0) break;require(result.length+count<=TEXT_LIMIT);result.append(buffer,0,count)}
                result.toString()
            }
        }
        if(text.isBlank()) throw ClientFailure("mobile.books.no_text")
        require(text.length<=TEXT_LIMIT);text
    }
    fun epub(source: File): String {
        require(source.length()<=67_108_864L)
        ZipFile(source).use {zip->
            val entries=zip.entries().toList();require(entries.size<=4000)
            val names=hashSetOf<String>();var total=0L
            for(entry in entries) {
                require(entry.size in 0..16_777_216L&&entry.compressedSize in 0..67_108_864L&&entry.method in listOf(0,8))
                total+=entry.size;require(total<=134_217_728L)
                val normalized=path(entry.name.trimEnd('/'));require(names.add(normalized))
            }
            fun member(name: String): ByteArray {
                val safe=path(name)
                val entry=entries.firstOrNull {!it.isDirectory&&path(it.name)==safe} ?: throw ClientFailure("mobile.books.import_failed")
                val bytes=zip.getInputStream(entry).use {input->
                    val result=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
                    while(true) {val count=input.read(buffer);if(count<0) break;require(result.size()+count<=entry.size);result.write(buffer,0,count)}
                    result.toByteArray()
                }
                require(bytes.size.toLong()==entry.size&&CRC32().apply {update(bytes)}.value==entry.crc)
                return bytes
            }
            val container=parse(member("META-INF/container.xml"))
            val packagePath=path(requireNotNull(container.rootfile));val opf=parse(member(packagePath))
            require(opf.spine.isNotEmpty()&&opf.spine.size<=5000)
            val base=packagePath.substringBeforeLast('/',"")
            val result=StringBuilder()
            opf.spine.forEachIndexed {index,id->
                val href=requireNotNull(opf.manifest[id])
                require(!href.contains(':')&&!href.contains('?'))
                val decoded=java.net.URLDecoder.decode(href.replace("+","%2B"),"UTF-8").substringBefore('#')
                val document=parse(member(path(if(base.isEmpty()) decoded else "$base/$decoded")))
                val body=document.text.trim()
                if(body.isNotEmpty()) {
                    val chapter="# "+(document.heading?.trim()?.takeIf {it.isNotEmpty()} ?: (index+1).toString())+"\n"+body+"\n\n"
                    require(result.length+chapter.length<=TEXT_LIMIT);result.append(chapter)
                }
            }
            if(result.isBlank()) throw ClientFailure("mobile.books.no_text")
            return result.toString()
        }
    }
    internal fun path(value: String): String {
        require(value.isNotEmpty()&&!value.startsWith('/')&&!value.contains('\\')&&!value.contains(':')&&!value.contains('\u0000'))
        val parts=mutableListOf<String>()
        for(part in value.split('/')) when(part) {"."->Unit;".."->{require(parts.isNotEmpty());parts.removeAt(parts.lastIndex)};else->{require(part.isNotEmpty());parts+=part}}
        require(parts.isNotEmpty());return parts.joinToString("/")
    }
    private data class Document(var rootfile: String?=null,val manifest: MutableMap<String,String> = mutableMapOf(),val spine: MutableList<String> = mutableListOf(),var text: String="",var heading: String?=null)
    private fun parse(bytes: ByteArray): Document {
        require(bytes.size<=16_777_216)
        val input=bytes.toString(Charsets.UTF_8);require(!input.uppercase().contains("<!ENTITY"))
        val parser=XmlPullParserFactory.newInstance().apply {isNamespaceAware=true}.newPullParser()
        parser.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES,true)
        parser.setInput(StringReader(input))
        val result=Document();val text=StringBuilder();var ignored=0;var headingDepth=0;var heading=StringBuilder()
        while(parser.eventType!=XmlPullParser.END_DOCUMENT) {
            when(parser.eventType) {
                XmlPullParser.START_TAG->{
                    val name=parser.name.lowercase()
                    when(name) {
                        "rootfile"->result.rootfile=parser.getAttributeValue(null,"full-path")
                        "item"->{val id=parser.getAttributeValue(null,"id");val href=parser.getAttributeValue(null,"href");if(id!=null&&href!=null) result.manifest[id]=href}
                        "itemref"->{val id=parser.getAttributeValue(null,"idref");if(id!=null&&parser.getAttributeValue(null,"linear")!="no") result.spine+=id}
                    }
                    if(ignored>0||name in listOf("head","style","script")) ignored++
                    if(ignored==0) {
                        if(name in listOf("p","div","br","li","h1","h2","h3","section")) text.append('\n')
                        if(result.heading==null&&headingDepth==0&&name in listOf("h1","h2")) {headingDepth=parser.depth;heading=StringBuilder()}
                    }
                }
                XmlPullParser.END_TAG->{
                    if(ignored>0) ignored-- else {
                        if(headingDepth==parser.depth) {result.heading=heading.toString();headingDepth=0}
                        if(parser.name.lowercase() in listOf("p","div","li","h1","h2","h3","section")) text.append('\n')
                    }
                }
                XmlPullParser.TEXT,XmlPullParser.CDSECT,XmlPullParser.ENTITY_REF->if(ignored==0) {
                    val value=parser.text.orEmpty();text.append(value);if(headingDepth>0) heading.append(value);require(text.length<=TEXT_LIMIT)
                }
                XmlPullParser.DOCDECL->require(!parser.text.orEmpty().uppercase().contains("ENTITY"))
            }
            parser.next()
        }
        result.text=text.toString();return result
    }
}
