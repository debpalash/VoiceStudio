package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import java.io.File
import java.util.Locale
import java.util.UUID
import kotlin.math.roundToLong

 data class DubSubtitleStyle(val fontSize: Int=36,val color: String="FFFFFF",val top: Boolean=false,val dual: Boolean=false) {val valid get()=fontSize in 18..72&&color in listOf("FFFFFF","FFFF00","00FFFF")}
object PhoneDubASS {
    fun export(segments: List<DubSegmentState>,target: String,style: DubSubtitleStyle=DubSubtitleStyle()): String {
        require(style.valid&&segments.isNotEmpty()&&segments.size<=10000)
        val bgr=style.color.takeLast(2)+style.color.substring(2,4)+style.color.take(2)
        val header="""
            [Script Info]
            ScriptType: v4.00+
            PlayResX: 1920
            PlayResY: 1080
            WrapStyle: 0
            ScaledBorderAndShadow: yes

            [V4+ Styles]
            Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
            Style: Default,Arial,${style.fontSize},&H00$bgr,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,${if(style.top) 8 else 2},64,64,64,1

            [Events]
            Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        """.trimIndent()
        return header+"\n"+segments.joinToString("\n") {cue ->
            require(cue.speech.end>cue.speech.start)
            val text=cue.translations[target]?.takeIf(String::isNotBlank) ?: throw ClientFailure("mobile.editing.subtitle_missing")
            val speaker=cue.speaker.orEmpty().replace(',',' ').replace('\r',' ').replace('\n',' ')
            "Dialogue: 0,${stamp(cue.speech.start)},${stamp(cue.speech.end)},Default,$speaker,0,0,0,,${escape(text)}${if(style.dual) "\\N{\\i1}"+escape(cue.speech.text)+"{\\i0}" else ""}"
        }+"\n"
    }
    private fun escape(text: String)=text.replace("\\","\\\\").replace("{","\\{").replace("}","\\}").replace("\r\n","\\N").replace("\r","\\N").replace("\n","\\N")
    private fun stamp(value: Double): String {require(value.isFinite()&&value in 0.0..14400.0);val cs=(value*100).roundToLong();return "%d:%02d:%02d.%02d".format(Locale.ROOT,cs/360000,cs/6000%60,cs/100%60,cs%100)}
}
enum class DubPackage { CLIPS, STEMS }
@Serializable private data class DubExportManifest(val format: String="voicestudio-dub-export",val version: Int=1,val kind: String,val sourceHash: String?,val language: String,val modelPins: List<DubModelPin>,val segments: List<DubSegmentState>,val originalIsIsolatedBackground: Boolean=false)
suspend fun PhoneDubStore.exportPackage(id: String,target: String,kind: DubPackage,workspace: File): File = exclusive(id) {withContext(Dispatchers.IO) {
    val request=request(id);val cp=checkpoint(id)
    if(!cp.done||request.targets.none {it.code==target}||cp.segments.isEmpty()) throw ClientFailure("mobile.dub_advanced.generate_first")
    val rendered=cp.renders.filter {it.target==target}.associateBy {it.index}
    val pieces=cp.segments.mapIndexed {index,cue ->
        val render=rendered[index] ?: throw ClientFailure("mobile.dub_advanced.generate_first")
        if(render.segmentID!=cue.id||render.scope!=dubSegmentScope(cue,target)||render.file==null) throw ClientFailure("mobile.dub_advanced.generate_first")
        val pcm=PcmFile(file(id,render.file))
        if(pcm.frames!=render.frames||sha256(pcm.file)!=render.sha256) throw ClientFailure("mobile.simple.media_failed")
        (cue.speech.start*24000).toLong() to pcm
    }
    val count=if(kind==DubPackage.STEMS) cp.frames*2 else rendered.values.sumOf {it.frames}
    if(count>500_000_000||kind==DubPackage.STEMS&&cp.frames*2>536_870_000) throw ClientFailure("mobile.dub_advanced.export_limit")
    if(directory(id).usableSpace<count*4+16_000_000) throw ClientFailure("mobile.local.storage_required")
    val root=File(workspace,"dub-package-${UUID.randomUUID()}").apply {mkdirs()}
    try {
        val entries=mutableListOf<ProjectArchiveFile>()
        if(kind==DubPackage.CLIPS) pieces.forEachIndexed {index,piece ->
            ensureActive();val cue=cp.segments[index];val dry=piece.second.read(0,piece.second.frames.toInt());val gain=if(cue.muted) 0.0 else cue.gain
            val output=File(root,"%05d.wav".format(Locale.ROOT,index+1));markSynthetic(LocalAudio(FloatArray(dry.samples.size) {(dry.samples[it]*gain).toFloat().coerceIn(-1f,1f)},24000),output)
            entries+=ProjectArchiveFile("segments/${output.name}",output)
        } else {
            val mixed=mixTimeline(File(root,"mix.pcm"),cp.frames,pieces,cp.segments.map {if(it.muted) 0.0 else it.gain})
            val dubbed=File(root,"dubbed.wav");markSynthetic(mixed,dubbed)
            val original=File(root,"original.wav");val pcm=PcmFile(file(id,"original.pcm"))
            if(pcm.frames!=cp.frames||sha256(pcm.file)!=cp.originalHash) throw ClientFailure("mobile.simple.media_failed")
            if(PhoneAudioProcessing.containsSyntheticMarker(file(id,request.sourceFile))) markSynthetic(pcm,original) else PhoneAudioProcessing.writeNaturalWave(pcm,original)
            entries+=ProjectArchiveFile("dubbed.wav",dubbed);entries+=ProjectArchiveFile("original.wav",original);mixed.file.delete()
        }
        for(ext in listOf("srt","vtt","ass")) {
            val text=if(ext=="ass") PhoneDubASS.export(cp.segments,target) else DubSubtitles.export(cp.segments,target,ext=="vtt")
            val output=File(root,"subtitles.$ext").apply {writeText(text)};entries+=ProjectArchiveFile(output.name,output)
        }
        // Packages are shareable outputs, not voice-conditioning backups.
        val publicSegments=cp.segments.map {it.copy(voiceID=null,referenceFile=null,referenceText=null,referenceOrigin=null,referenceNeedsTranscription=false)}
        val manifest=File(root,"manifest.json").apply {writeText(LocalJson.encodeToString(DubExportManifest.serializer(),DubExportManifest(kind=kind.name.lowercase(Locale.ROOT),sourceHash=request.sourceHash,language=target,modelPins=request.models,segments=publicSegments)))}
        entries+=ProjectArchiveFile("manifest.json",manifest)
        val output=File(workspace,"dub-${kind.name.lowercase(Locale.ROOT)}-${UUID.randomUUID()}.zip")
        ProjectArchive.write(entries,output)
    } finally {root.deleteRecursively()}
}}
