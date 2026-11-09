package com.voicestudio.mobile.local

import android.media.*
import kotlinx.coroutines.*
import java.io.File
import java.io.RandomAccessFile
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

 data class BookChapter(val title: String,val start: Double)

/** Native AAC-LC; streaming encode and bounded MP4 metadata, no external process. */
object BookExport {
    suspend fun cues(files: List<File>,titles: List<String>): List<BookChapter> = withContext(Dispatchers.IO) {
        var start=0.0
        files.mapIndexed {index,file->
            val cue=BookChapter(titles.getOrElse(index) {(index+1).toString()},start)
            RandomAccessFile(file,"r").use {input->
                val id=ByteArray(4);input.readFully(id);require(String(id,Charsets.US_ASCII)=="RIFF");input.skipBytes(8)
                var byteRate=0L;var size=0L
                while(input.filePointer+8<=input.length()) {
                    input.readFully(id);val count=Integer.toUnsignedLong(Integer.reverseBytes(input.readInt()));val end=input.filePointer+count+(count and 1L)
                    require(end<=input.length())
                    when(String(id,Charsets.US_ASCII)) {"fmt "->{require(count>=16);input.skipBytes(8);byteRate=Integer.toUnsignedLong(Integer.reverseBytes(input.readInt()))};"data"->size=count}
                    input.seek(end)
                }
                require(byteRate>0&&size>0);start+=size.toDouble()/byteRate+0.25
            };cue
        }
    }
    suspend fun encode(wave: File,format: String,chapters: List<BookChapter> = emptyList(),synthetic: Boolean=true): File = withContext(Dispatchers.IO) {
        if(format=="wav") return@withContext wave
        if(format=="mp3") return@withContext PhoneAudioExport.mp3(wave,File(wave.parentFile,wave.nameWithoutExtension+".mp3"),synthetic)
        require(format in listOf("m4a","m4b")&&(format!="m4b"||chapters.size<=255))
        val pending=File(wave.parentFile,UUID.randomUUID().toString()+".m4a");val pcm=File(wave.parentFile,UUID.randomUUID().toString()+".pcm")
        try {
            val audio=AndroidAudioDecoder().decode(wave,pcm,14400) {}
            encodeAac(audio,pending) {}
            currentCoroutineContext().ensureActive();addMetadata(pending,if(format=="m4b") chapters else emptyList(),synthetic)
            currentCoroutineContext().ensureActive()
            val output=File(wave.parentFile,wave.nameWithoutExtension+"."+format)
            Files.move(pending.toPath(),output.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);output
        } finally {pending.delete();pcm.delete()}
    }
    private fun atom(type: String,payload: ByteArray): ByteArray=ByteArrayOutputStream().also {bytes->DataOutputStream(bytes).use {out->out.writeInt(payload.size+8);out.write(type.toByteArray(Charsets.ISO_8859_1));out.write(payload)}}.toByteArray()
    internal fun addMetadata(source: File,chapters: List<BookChapter>,synthetic: Boolean=true) {
        require(chapters.size<=255&&chapters.all {it.start.isFinite()&&it.start>=0}&&chapters.zipWithNext().all {it.first.start<=it.second.start})
        RandomAccessFile(source,"rw").use {file->
            val length=file.length();var position=0L;var moovPosition=-1L;var original=ByteArray(0)
            while(position+8<=length) {
                file.seek(position);val small=Integer.toUnsignedLong(file.readInt());val type=ByteArray(4);file.readFully(type)
                val size=if(small==1L) file.readLong() else if(small==0L) length-position else small
                require(size>=if(small==1L) 16L else 8L);require(size<=length-position)
                if(String(type,Charsets.ISO_8859_1)=="moov") {require(small!=1L&&moovPosition<0&&size<=33_554_432);moovPosition=position;file.seek(position);original=ByteArray(size.toInt());file.readFully(original)}
                position+=size
            }
            require(position==length&&moovPosition>=0)
            val marker=(if(synthetic) "VoiceStudio synthetic audio" else "VoiceStudio audio").toByteArray(Charsets.UTF_8)
            val comment=ByteArrayOutputStream().also {bytes->DataOutputStream(bytes).use {out->out.writeShort(marker.size);out.writeShort(0);out.write(marker)}}.toByteArray()
            val metadata=ByteArrayOutputStream();metadata.write(atom("©cmt",comment))
            if(chapters.isNotEmpty()) {
                val cues=ByteArrayOutputStream();DataOutputStream(cues).use {out->
                    out.writeInt(0x01000000);out.writeInt(0);out.writeByte(chapters.size)
                    for(chapter in chapters) {
                        require(chapter.start<=Long.MAX_VALUE/10_000_000.0);out.writeLong((chapter.start*10_000_000).toLong())
                        val title=StringBuilder();var size=0
                        val chars=chapter.title.codePoints().toArray()
                        for(point in chars) {val value=String(Character.toChars(point));val bytes=value.toByteArray(Charsets.UTF_8);if(size+bytes.size>255) break;size+=bytes.size;title.append(value)}
                        out.writeByte(size);out.write(title.toString().toByteArray(Charsets.UTF_8))
                    }
                };metadata.write(atom("chpl",cues.toByteArray()))
            }
            val track=if(chapters.isEmpty()) null else BookChapterTrack.add(original,length,chapters)
            val payload=ByteArrayOutputStream();if(track!=null) payload.write(track.payload) else payload.write(original,8,original.size-8);payload.write(atom("udta",metadata.toByteArray()))
            file.seek(length);if(track!=null) file.write(track.media);file.write(atom("moov",payload.toByteArray()));file.fd.sync()
            file.seek(moovPosition+4);file.write("free".toByteArray(Charsets.US_ASCII));file.fd.sync()
        }
    }
    private suspend fun encodeAac(audio: PcmFile,output: File,progress: (Float)->Unit) {
        val codec=MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
        var muxer: MediaMuxer?=null;var codecStarted=false;var muxStarted=false;var completed=false
        try {
            val format=MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC,audio.sampleRate,1)
            format.setInteger(MediaFormat.KEY_AAC_PROFILE,MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            format.setInteger(MediaFormat.KEY_BIT_RATE,64000)
            codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();codecStarted=true
            val writer=MediaMuxer(output.path,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);muxer=writer
            var track=-1;var inputDone=false;var outputDone=false;var sent=0L
            val info=MediaCodec.BufferInfo()
            while(!outputDone) {
                currentCoroutineContext().ensureActive()
                if(!inputDone) {
                    val index=codec.dequeueInputBuffer(10000)
                    if(index>=0) {
                        val buffer=requireNotNull(codec.getInputBuffer(index)).order(ByteOrder.LITTLE_ENDIAN);buffer.clear()
                        val count=minOf(buffer.remaining()/2L,audio.frames-sent).toInt()
                        val pts=sent*1_000_000/audio.sampleRate
                        if(count<=0) {codec.queueInputBuffer(index,0,0,pts,MediaCodec.BUFFER_FLAG_END_OF_STREAM);inputDone=true}
                        else {
                            audio.read(sent,count).samples.forEach {buffer.putShort((it.coerceIn(-1f,1f)*32767).toInt().toShort())}
                            codec.queueInputBuffer(index,0,count*2,pts,0);sent+=count;progress(sent.toFloat()/audio.frames)
                        }
                    }
                }
                when(val index=codec.dequeueOutputBuffer(info,10000)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        check(!muxStarted);track=writer.addTrack(codec.outputFormat);writer.start();muxStarted=true
                    }
                    else -> if(index>=0) {
                        try {
                            if(info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0&&info.size>0) {
                                check(muxStarted)
                                val buffer=requireNotNull(codec.getOutputBuffer(index));buffer.position(info.offset);buffer.limit(info.offset+info.size)
                                writer.writeSampleData(track,buffer,info)
                            }
                            outputDone=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                        } finally {codec.releaseOutputBuffer(index,false)}
                    }
                }
            }
            completed=true
        } finally {
            runCatching {if(codecStarted) codec.stop()};runCatching {codec.release()}
            try {if(muxStarted) {if(completed) muxer?.stop() else runCatching {muxer?.stop()}}} finally {runCatching {muxer?.release()}}
        }
    }
}
