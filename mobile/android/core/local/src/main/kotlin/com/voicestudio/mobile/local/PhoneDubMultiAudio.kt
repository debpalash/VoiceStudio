package com.voicestudio.mobile.local

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Locale
import java.util.UUID

data class DubMuxTrack(val file: File,val language: String,val name: String)
object PhoneDubMultiAudio {
    suspend fun export(source: File,tracks: List<DubMuxTrack>,output: File): File = withContext(Dispatchers.IO) {
        require(tracks.isNotEmpty()&&tracks.size<=20&&tracks.map {it.language}.distinct().size==tracks.size&&!output.exists())
        val pending=File(output.parentFile,"${UUID.randomUUID()}.mp4")
        val readers=mutableListOf<MediaExtractor>();var writer: MediaMuxer?=null;var started=false;var completed=false
        try {
            val picture=MediaExtractor().also {readers+=it;it.setDataSource(source.path)}
            val video=(0 until picture.trackCount).firstOrNull {picture.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/")==true} ?: throw ClientFailure("mobile.dub_advanced.video_required")
            picture.selectTrack(video)
            val muxer=MediaMuxer(pending.path,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);writer=muxer
            val retriever=MediaMetadataRetriever()
            try {retriever.setDataSource(source.path);retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull()?.takeIf {it in listOf(0,90,180,270)}?.let(muxer::setOrientationHint)} finally {retriever.release()}
            val streams=mutableListOf(picture to muxer.addTrack(picture.getTrackFormat(video)))
            for(track in tracks) {
                val reader=MediaExtractor().also {readers+=it;it.setDataSource(track.file.path)}
                val audio=(0 until reader.trackCount).firstOrNull {reader.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true} ?: throw ClientFailure("mobile.simple.media_failed")
                reader.selectTrack(audio);val format=reader.getTrackFormat(audio)
                format.setString(MediaFormat.KEY_LANGUAGE,track.language.replace('_','-'));streams+=reader to muxer.addTrack(format)
            }
            val provenance=muxer.addTrack(MediaFormat().apply {setString(MediaFormat.KEY_MIME,"application/voicestudio.synthetic-audio")})
            muxer.start();started=true
            val marker="VoiceStudio synthetic audio".toByteArray(Charsets.US_ASCII)
            muxer.writeSampleData(provenance,ByteBuffer.wrap(marker),MediaCodec.BufferInfo().apply {set(0,marker.size,0,0)})
            var buffer=ByteBuffer.allocate(1_048_576);val info=MediaCodec.BufferInfo()
            while(true) {
                ensureActive();val stream=streams.filter {it.first.sampleTrackIndex>=0}.minByOrNull {it.first.sampleTime} ?: break
                val reader=stream.first;val size=reader.sampleSize
                if(size !in 1..67_108_864||reader.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED!=0) throw ClientFailure("mobile.simple.media_failed")
                if(size>buffer.capacity()) buffer=ByteBuffer.allocate(size.toInt())
                buffer.clear();val count=reader.readSampleData(buffer,0)
                if(count<0) {reader.advance();continue}
                info.set(0,count,reader.sampleTime.coerceAtLeast(0),if(reader.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC!=0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                buffer.position(0);buffer.limit(count);muxer.writeSampleData(stream.second,buffer,info);reader.advance()
            }
            muxer.stop();started=false;muxer.release();writer=null
            // MediaMuxer has no public alternate-group/default-track API. Patch only fixed-size
            // track-header/language fields in the newly created file, never source media offsets.
            MultiAudioMetadata.apply(pending,tracks.map {it.language})
            ensureActive();Files.move(pending.toPath(),output.toPath(),StandardCopyOption.ATOMIC_MOVE);completed=true;output
        } finally {
            if(started) runCatching {writer?.stop()};runCatching {writer?.release()};readers.forEach {runCatching {it.release()}}
            pending.delete()
        }
    }
}
internal object MultiAudioMetadata {
    internal fun apply(source: File,languages: List<String>) {
        RandomAccessFile(source,"rw").use {file ->
            var offset=0L;var found=false
            while(offset+8<=file.length()) {
                file.seek(offset);val small=Integer.toUnsignedLong(file.readInt());val kind=ByteArray(4).also(file::readFully).toString(Charsets.US_ASCII)
                val size=if(small==1L) file.readLong() else if(small==0L) file.length()-offset else small
                require(size >= (if(small==1L) 16L else 8L) && size<=file.length()-offset)
                if(kind=="moov") {
                    require(!found&&small!=1L&&size<=33_554_432);found=true
                    file.seek(offset);val data=ByteArray(size.toInt());file.readFully(data);patch(data,languages)
                    file.seek(offset);file.write(data);file.fd.sync()
                };offset+=size
            };require(found&&offset==file.length())
        }
    }
    private data class Box(val type: String,val payload: Int,val end: Int)
    private fun boxes(data: ByteArray,start: Int,end: Int): List<Box> {
        var offset=start;val result=mutableListOf<Box>()
        while(offset+8<=end) {
            val size=java.nio.ByteBuffer.wrap(data,offset,4).int;require(size>=8&&size<=end-offset)
            result+=Box(String(data,offset+4,4,Charsets.US_ASCII),offset+8,offset+size);offset+=size
        };require(offset==end);return result
    }
    internal fun patch(data: ByteArray,languages: List<String>) {
        var index=0
        for(track in boxes(data,8,data.size).filter {it.type=="trak"}) {
            val fields=boxes(data,track.payload,track.end);val media=fields.firstOrNull {it.type=="mdia"} ?: continue
            val mediaFields=boxes(data,media.payload,media.end);val handler=mediaFields.firstOrNull {it.type=="hdlr"} ?: continue
            require(handler.end-handler.payload>=12)
            if(String(data,handler.payload+8,4,Charsets.US_ASCII)!="soun") continue
            require(index<languages.size)
            val header=fields.single {it.type=="tkhd"};val version=data[header.payload].toInt();require(version in 0..1)
            val alternate=header.payload+if(version==1) 46 else 34;require(alternate+2<=header.end)
            data[header.payload+1]=0;data[header.payload+2]=0;data[header.payload+3]=if(index==0) 3 else 2
            data[alternate]=0;data[alternate+1]=1
            val mdhd=mediaFields.single {it.type=="mdhd"};val mediaVersion=data[mdhd.payload].toInt();require(mediaVersion in 0..1);val languageOffset=mdhd.payload+(if(mediaVersion==1) 32 else 20);require(languageOffset+2<=mdhd.end)
            val code=runCatching {Locale.forLanguageTag(languages[index].replace('_','-')).getISO3Language()}.getOrNull()?.takeIf {it.matches(Regex("[a-z]{3}"))} ?: "und"
            val packed=((code[0].code-96) shl 10) or ((code[1].code-96) shl 5) or (code[2].code-96)
            data[languageOffset]=(packed ushr 8).toByte();data[languageOffset+1]=packed.toByte();index++
        };require(index==languages.size)
    }
}
suspend fun PhoneDubStore.exportMultiAudio(id: String,defaultLanguage: String,workspace: File): File = exclusive(id) {withContext(Dispatchers.IO) {
    val request=request(id);val cp=checkpoint(id)
    if(!cp.done||cp.outputs.none {it.language==defaultLanguage}||cp.outputs.any {!it.file.endsWith(".mp4")}) throw ClientFailure("mobile.dub_advanced.video_required")
    val tracks=cp.outputs.sortedBy {if(it.language==defaultLanguage) 0 else 1}.map {
        val source=file(id,it.file);if(sha256(source)!=it.sha256) throw ClientFailure("mobile.simple.media_failed")
        DubMuxTrack(source,it.language,it.name)
    }
    val source=file(id,request.sourceFile)
    if(request.sourceHash!=null&&sha256(source)!=request.sourceHash) throw ClientFailure("mobile.simple.media_failed")
    workspace.mkdirs();PhoneDubMultiAudio.export(source,tracks,File(workspace,"dub-multitrack-${UUID.randomUUID()}.mp4"))
}}
