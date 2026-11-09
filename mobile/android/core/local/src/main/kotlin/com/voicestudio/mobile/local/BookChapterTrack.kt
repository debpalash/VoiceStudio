package com.voicestudio.mobile.local

import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer

/** QuickTime text chapters for Apple Books and AVFoundation, plus the portable Nero chapter atom. */
internal object BookChapterTrack {
    data class Result(val payload: ByteArray,val media: ByteArray)
    fun add(movie: ByteArray,offset: Long,chapters: List<BookChapter>): Result {
        val boxes=children(movie.copyOfRange(8,movie.size));val header=requireNotNull(boxes.firstOrNull {it.first=="mvhd"}?.second)
        require(header.size>=100&&chapters.firstOrNull()?.start==0.0)
        val version=header[8].toInt();val scale: Long;val duration: Long
        if(version==0) {scale=u32(header,20);duration=u32(header,24)} else {require(version==1&&header.size>=120);scale=u32(header,28);duration=u64(header,32)}
        require(scale>0&&duration in 1..0xffffffffL)
        val total=(duration.toDouble()/scale*1000).let {kotlin.math.round(it).toLong()};require(total in 1..0xffffffffL)
        var maximum=0L
        for((type,track) in boxes) if(type=="trak") children(track.copyOfRange(8,track.size)).firstOrNull {it.first=="tkhd"}?.second?.let {maximum=maxOf(maximum,u32(it,if(it[8].toInt()==1) 28 else 20))}
        val identifier=maxOf(maximum+1,u32(header,header.size-4));require(identifier<0xffffffffL)
        val samples=ByteArrayOutputStream();val sizes=mutableListOf<Int>();val ticks=mutableListOf<Int>()
        chapters.forEachIndexed {index,chapter->
            val start=kotlin.math.round(chapter.start*1000).toLong();val end=if(index+1<chapters.size) kotlin.math.round(chapters[index+1].start*1000).toLong() else total
            require(start>=0&&end>start&&end<=total)
            val title=ByteArrayOutputStream()
            for(point in chapter.title.codePoints().toArray()) {val value=String(Character.toChars(point)).toByteArray(Charsets.UTF_8);if(title.size()+value.size>255) break;title.write(value)}
            val sample=bytes {writeShort(title.size());write(title.toByteArray());write(atom("encd",byteArrayOf(0,0,1,0)))}
            samples.write(sample);sizes+=sample.size;ticks+=(end-start).toInt()
        }
        val payload=ByteArrayOutputStream()
        for((type,data) in boxes) when(type) {
            "mvhd"->{val updated=data.clone();val next=bytes {writeInt((identifier+1).toInt())};next.copyInto(updated,updated.size-4);payload.write(updated)}
            "trak"->{val members=children(data.copyOfRange(8,data.size));require(members.none {it.first=="tref"});payload.write(atom("trak",data.copyOfRange(8,data.size)+atom("tref",atom("chap",bytes {writeInt(identifier.toInt())}))))}
            else->payload.write(data)
        }
        val tkhd=bytes {writeInt(2);writeInt(0);writeInt(0);writeInt(identifier.toInt());writeInt(0);writeInt(duration.toInt());write(ByteArray(16));write(matrix);write(ByteArray(8))}
        val mdhd=bytes {write(ByteArray(12));writeInt(1000);writeInt(total.toInt());writeInt(0)}
        val hdlr=bytes {write(ByteArray(8));write("text".toByteArray(Charsets.US_ASCII));write(ByteArray(12));write("SubtitleHandler\u0000".toByteArray(Charsets.US_ASCII))}
        val generic=bytes {writeInt(0);writeShort(64);repeat(3) {writeShort(128)};writeInt(0)}
        val gmhd=atom("gmhd",atom("gmin",generic)+atom("text",matrix))
        val dref=bytes {writeInt(0);writeInt(1);write(atom("url ",bytes {writeInt(1)}))}
        val textEntry="0000003b7465787400000000000000010000000100000000000000000000000000000000000000010000000000000000000d667461620001000100".chunked(2).map {it.toInt(16).toByte()}.toByteArray()
        val stsd=bytes {writeInt(0);writeInt(1);write(textEntry)}
        val stts=bytes {writeInt(0);writeInt(ticks.size);ticks.forEach {writeInt(1);writeInt(it)}}
        val stsc=bytes {writeInt(0);writeInt(1);writeInt(1);writeInt(sizes.size);writeInt(1)}
        val stsz=bytes {writeInt(0);writeInt(0);writeInt(sizes.size);sizes.forEach {writeInt(it)}}
        val co64=bytes {writeInt(0);writeInt(1);writeLong(offset+8)}
        val stbl=atom("stbl",atom("stsd",stsd)+atom("stts",stts)+atom("stsc",stsc)+atom("stsz",stsz)+atom("co64",co64))
        val minf=atom("minf",gmhd+atom("dinf",atom("dref",dref))+stbl)
        val mdia=atom("mdia",atom("mdhd",mdhd)+atom("hdlr",hdlr)+minf)
        payload.write(atom("trak",atom("tkhd",tkhd)+mdia))
        return Result(payload.toByteArray(),atom("mdat",samples.toByteArray()))
    }
    private val matrix get()=bytes {listOf(0x10000,0,0,0,0x10000,0,0,0,0x40000000).forEach {writeInt(it)}}
    private fun bytes(block: DataOutputStream.()->Unit): ByteArray=ByteArrayOutputStream().also {out->DataOutputStream(out).use {it.block()}}.toByteArray()
    private fun atom(type: String,payload: ByteArray): ByteArray=bytes {writeInt(payload.size+8);write(type.toByteArray(Charsets.US_ASCII));write(payload)}
    private fun u32(data: ByteArray,offset: Int): Long {require(offset>=0&&offset+4<=data.size);return Integer.toUnsignedLong(ByteBuffer.wrap(data,offset,4).int)}
    private fun u64(data: ByteArray,offset: Int): Long {require(offset>=0&&offset+8<=data.size);return ByteBuffer.wrap(data,offset,8).long}
    private fun children(data: ByteArray): List<Pair<String,ByteArray>> {
        val result=mutableListOf<Pair<String,ByteArray>>();var cursor=0
        while(cursor<data.size) {
            require(cursor+8<=data.size&&result.size<10000);val size=u32(data,cursor);require(size in 8..(data.size-cursor).toLong())
            result+=String(data,cursor+4,4,Charsets.US_ASCII) to data.copyOfRange(cursor,cursor+size.toInt());cursor+=size.toInt()
        };return result
    }
}
