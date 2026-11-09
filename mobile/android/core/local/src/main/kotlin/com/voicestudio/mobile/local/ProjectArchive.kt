package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlinx.coroutines.*

data class ProjectArchiveFile(val path: String,val file: File)
data class ProjectArchiveEntry(val path: String,val size: Long,internal val offset: Long,internal val crc: Long)
/** Portable stored ZIP, streaming with strict central/local metadata and CRC checks. */
object ProjectArchive {
    const val MAX_BYTES=1_073_741_824L
    const val MAX_MEMBER=536_870_912L
    const val MAX_FILES=20000
    fun path(value: String): String {
        require(value.isNotEmpty()&&value.length<=240&&value.matches(Regex("[A-Za-z0-9_./-]+")))
        require(value.split('/').all {it.isNotEmpty()&&it!="."&&it!=".."&&!it.endsWith('.')&&it.substringBefore('.').uppercase() !in reserved})
        return value
    }
    private val reserved=setOf("CON","PRN","AUX","NUL")+(1..9).flatMap {listOf("COM$it","LPT$it")}
    suspend fun write(entries: List<ProjectArchiveFile>,output: File): File=withContext(Dispatchers.IO) {
        require(entries.size<=MAX_FILES&&!output.exists()&&entries.map {it.path.lowercase()}.distinct().size==entries.size)
        val pending=File(output.parentFile,".zip-${UUID.randomUUID()}")
        try {
            var total=0L
            ZipOutputStream(pending.outputStream()).use {zip->
                for(entry in entries) {
                    currentCoroutineContext().ensureActive();path(entry.path)
                    require(entry.file.isFile&&!Files.isSymbolicLink(entry.file.toPath()))
                    val size=entry.file.length();require(size<=MAX_MEMBER);total+=size;require(total<=MAX_BYTES)
                    val crc=CRC32();var count=0L
                    entry.file.inputStream().use {input->val bytes=ByteArray(262144);while(true) {currentCoroutineContext().ensureActive();val n=input.read(bytes);if(n<0) break;count+=n;require(count<=size);crc.update(bytes,0,n)}}
                    require(count==size)
                    zip.putNextEntry(ZipEntry(entry.path).apply {method=ZipEntry.STORED;this.size=size;compressedSize=size;this.crc=crc.value;time=0})
                    val actual=CRC32();count=0
                    entry.file.inputStream().use {input->val bytes=ByteArray(262144);while(true) {currentCoroutineContext().ensureActive();val n=input.read(bytes);if(n<0) break;count+=n;require(count<=size);actual.update(bytes,0,n);zip.write(bytes,0,n)}}
                    require(count==size&&actual.value==crc.value);zip.closeEntry()
                }
            }
            currentCoroutineContext().ensureActive()
            Files.move(pending.toPath(),output.toPath(),StandardCopyOption.ATOMIC_MOVE);output
        } finally {pending.delete()}
    }
    fun entries(source: File): List<ProjectArchiveEntry> {
        RandomAccessFile(source,"r").use {file->
            val size=file.length();require(size in 22..MAX_BYTES+8_388_608)
            val tailStart=maxOf(0,size-65557);file.seek(tailStart);val tail=ByteArray((size-tailStart).toInt());file.readFully(tail)
            val end=(tail.size-22 downTo 0).firstOrNull {u32(tail,it)==0x06054b50L} ?: error("Invalid ZIP")
            require(end+22+u16(tail,end+20)==tail.size&&u16(tail,end+4)==0&&u16(tail,end+6)==0&&u16(tail,end+8)==u16(tail,end+10))
            val count=u16(tail,end+10);val centralSize=u32(tail,end+12);val centralOffset=u32(tail,end+16)
            require(count<=MAX_FILES&&centralSize<=8_388_608&&centralOffset+centralSize==tailStart+end)
            file.seek(centralOffset);val central=ByteArray(centralSize.toInt());file.readFully(central)
            val entries=mutableListOf<ProjectArchiveEntry>();val names=hashSetOf<String>();var cursor=0;var total=0L
            repeat(count) {
                require(cursor+46<=central.size&&u32(central,cursor)==0x02014b50L)
                val nameSize=u16(central,cursor+28);val extra=u16(central,cursor+30);val comment=u16(central,cursor+32);val length=u32(central,cursor+24);val offset=u32(central,cursor+42);val mode=u32(central,cursor+38) ushr 16
                require(cursor+46+nameSize+extra+comment<=central.size&&length<=MAX_MEMBER&&u32(central,cursor+20)==length&&u16(central,cursor+10)==0&&u16(central,cursor+8) and 0x800.inv()==0)
                require(mode and 0xf000==0L||mode and 0xf000==0x8000L)
                val name=String(central,cursor+46,nameSize,Charsets.UTF_8);path(name);require(names.add(name.lowercase()))
                total+=length;require(total<=MAX_BYTES&&offset+30<=centralOffset)
                file.seek(offset);val local=ByteArray(30);file.readFully(local)
                require(u32(local,0)==0x04034b50L&&u16(local,6)==u16(central,cursor+8)&&u16(local,8)==0&&u32(local,14)==u32(central,cursor+16)&&u32(local,18)==length&&u32(local,22)==length&&u16(local,26)==nameSize)
                val localName=ByteArray(nameSize);file.readFully(localName);require(localName.contentEquals(name.toByteArray(Charsets.UTF_8)))
                val content=offset+30+nameSize+u16(local,28);require(content+length<=centralOffset)
                entries+=ProjectArchiveEntry(name,length,content,u32(central,cursor+16));cursor+=46+nameSize+extra+comment
            }
            require(cursor==central.size);return entries
        }
    }
    suspend fun extract(source: File,directory: File): Map<String,File> = withContext(Dispatchers.IO) {
        require(!directory.exists());val entries=entries(source);check(directory.mkdirs());var completed=false
        try {
            val result=mutableMapOf<String,File>()
            RandomAccessFile(source,"r").use {reader->
                for(entry in entries) {
                    currentCoroutineContext().ensureActive();val target=File(directory,entry.path);check(target.parentFile!!.mkdirs()||target.parentFile!!.isDirectory)
                    reader.seek(entry.offset);var remaining=entry.size;val crc=CRC32()
                    target.outputStream().use {writer->val bytes=ByteArray(262144);while(remaining>0) {currentCoroutineContext().ensureActive();val n=reader.read(bytes,0,minOf(bytes.size.toLong(),remaining).toInt());require(n>0);remaining-=n;crc.update(bytes,0,n);writer.write(bytes,0,n)}}
                    require(crc.value==entry.crc);result[entry.path]=target
                }
            };completed=true;result
        } finally {if(!completed) directory.deleteRecursively()}
    }
    private fun u16(data: ByteArray,offset: Int): Int {require(offset>=0&&offset+2<=data.size);return ByteBuffer.wrap(data,offset,2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xffff}
    private fun u32(data: ByteArray,offset: Int): Long {require(offset>=0&&offset+4<=data.size);return Integer.toUnsignedLong(ByteBuffer.wrap(data,offset,4).order(ByteOrder.LITTLE_ENDIAN).int)}
}
