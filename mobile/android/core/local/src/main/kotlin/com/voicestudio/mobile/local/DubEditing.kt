package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import java.util.UUID
import kotlin.math.roundToLong

/** Edits are pure values: callers publish one snapshot, including structural edits and undo. */
object DubEditing {
    fun split(segments: List<DubSegmentState>,index: Int,at: Double): List<DubSegmentState> {
        val cue=segments[index];val speech=cue.speech
        require(at.isFinite()&&at-speech.start>=.05&&speech.end-at>=.05)
        val fraction=(at-speech.start)/(speech.end-speech.start)
        val source=splitText(speech.text,fraction)
        val translated=cue.translations.mapValues {splitText(it.value,fraction)}
        val first=cue.copy(speech=speech.copy(end=at,text=source.first),translations=translated.mapValues {it.value.first},referenceText=if(cue.referenceFile==null) null else cue.referenceText,referenceNeedsTranscription=cue.referenceFile==null)
        val second=cue.copy(id=UUID.randomUUID().toString(),speech=speech.copy(start=at,text=source.second),translations=translated.mapValues {it.value.second},referenceText=if(cue.referenceFile==null) null else cue.referenceText,referenceNeedsTranscription=cue.referenceFile==null)
        return segments.toMutableList().also {it[index]=first;it.add(index+1,second)}
    }
    fun merge(segments: List<DubSegmentState>,index: Int): List<DubSegmentState> {
        require(index+1<segments.size)
        val a=segments[index];val b=segments[index+1]
        require(b.speech.start>=a.speech.start&&b.speech.end>a.speech.end&&b.speech.end-a.speech.start<=30)
        if(a.speaker!=b.speaker) throw ClientFailure("mobile.dub_advanced.merge_speakers")
        if(a.gain!=b.gain||a.muted!=b.muted) throw ClientFailure("mobile.processing.merge_levels")
        require(a.referenceFile==b.referenceFile&&(a.referenceFile==null||a.referenceText==b.referenceText))
        val translations=(a.translations.keys intersect b.translations.keys).associateWith {a.translations.getValue(it)+"\n"+b.translations.getValue(it)}
        val merged=a.copy(speech=SpeechSegment(a.speech.start,b.speech.end,a.speech.text+"\n"+b.speech.text),translations=translations,referenceText=if(a.referenceFile==null) null else a.referenceText,referenceNeedsTranscription=a.referenceFile==null)
        return segments.toMutableList().also {it[index]=merged;it.removeAt(index+1)}
    }
    fun importTranslation(segments: List<DubSegmentState>,cues: List<DubSubtitleCue>,target: String,duration: Double): List<DubSegmentState> {
        require(cues.isNotEmpty()&&cues.all {it.end<=duration})
        return cues.map {cue ->
            val overlap=segments.filter {it.speech.start<cue.end&&it.speech.end>cue.start}
            require(overlap.isNotEmpty())
            val exact=overlap.singleOrNull()?.takeIf {it.speech.start==cue.start&&it.speech.end==cue.end}
            if(exact!=null) exact.copy(translations=exact.translations+(target to cue.text)) else {
                // Preserve a custom voice only when all overlapping source cues use that same reference.
                if(overlap.any {it.speaker!=overlap.first().speaker}) throw ClientFailure("mobile.dub_advanced.merge_speakers")
                if(overlap.any {it.gain!=overlap.first().gain||it.muted!=overlap.first().muted}) throw ClientFailure("mobile.processing.merge_levels")
                require(overlap.map {it.referenceFile to (if(it.referenceFile!=null) it.referenceText else null)}.distinct().size==1)
                DubSegmentState(SpeechSegment(cue.start,cue.end,overlap.joinToString("\n") {it.speech.text}),referenceText=overlap.first().referenceText.takeIf {overlap.first().referenceFile!=null},translations=mapOf(target to cue.text),referenceFile=overlap.first().referenceFile,id=UUID.randomUUID().toString(),referenceNeedsTranscription=overlap.first().referenceFile==null,gain=overlap.first().gain,muted=overlap.first().muted,speaker=overlap.first().speaker,referenceOrigin=overlap.first().referenceOrigin,voiceID=overlap.first().voiceID)
            }
        }
    }
    private fun splitText(text: String,fraction: Double): Pair<String,String> {
        val points=text.codePoints().toArray();require(points.size>=2)
        val desired=(points.size*fraction).toInt().coerceIn(1,points.size-1)
        val boundaries=(1 until points.size).filter {Character.isWhitespace(points[it-1])||Character.isWhitespace(points[it])}
        val index=boundaries.minByOrNull {kotlin.math.abs(it-desired)} ?: desired
        val first=String(points,0,index).trim();val second=String(points,index,points.size-index).trim()
        require(first.isNotEmpty()&&second.isNotEmpty());return first to second
    }
}

data class DubSubtitleCue(val start: Double,val end: Double,val text: String)
object DubSubtitles {
    fun parse(text: String): List<DubSubtitleCue> = try {
        require(text.toByteArray(Charsets.UTF_8).size<=4_000_000)
        val normalized=text.removePrefix("\uFEFF").replace("\r\n","\n").replace('\r','\n')
        val blocks=normalized.split(Regex("\n[ \\t]*\n"));val cues=mutableListOf<DubSubtitleCue>()
        blocks.forEach {block ->
            val lines=block.trim().lines()
            if(lines.firstOrNull().isNullOrBlank()||lines.first().startsWith("WEBVTT")||lines.first().startsWith("NOTE")||lines.first() in setOf("STYLE","REGION")) return@forEach
            val timing=if(lines.first().contains("-->")) 0 else 1
            require(lines.size>timing+1)
            val match=Regex("^([^ ]+)\\s+-->\\s+([^ ]+)(?:\\s+.*)?$").matchEntire(lines[timing].trim()) ?: error("timing")
            val start=time(match.groupValues[1]);val end=time(match.groupValues[2]);require(start>=0&&end>start&&end<=14400&&end-start<=30)
            val words=decodeEntities(lines.drop(timing+1).joinToString("\n").replace(Regex("<[^>]*>"),"")).trim()
            require(words.isNotEmpty()&&words.length<=4000);cues+=DubSubtitleCue(start,end,words)
        }
        require(cues.isNotEmpty()&&cues.size<=10000&&cues.zipWithNext().all {it.first.start<=it.second.start});cues
    } catch(e:Exception) {throw ClientFailure("mobile.editing.subtitle_invalid")}
    fun export(segments: List<DubSegmentState>,target: String,vtt: Boolean): String {
        require(segments.isNotEmpty())
        return (if(vtt) "WEBVTT\n\n" else "")+segments.mapIndexed {index,segment ->
            val text=segment.translations[target]?.takeIf {it.isNotBlank()} ?: throw ClientFailure("mobile.editing.subtitle_missing")
            val escaped=text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\r\n","\n").replace('\r','\n').replace(Regex("\n[ \\t]*\n"),"\n")
            "${index+1}\n${stamp(segment.speech.start,vtt)} --> ${stamp(segment.speech.end,vtt)}\n$escaped"
        }.joinToString("\n\n")+"\n"
    }
    private fun decodeEntities(value: String): String = Regex("&(?:amp|lt|gt|quot|apos|#39|nbsp);").replace(value) {match ->
        when(match.value) {"&amp;" -> "&";"&lt;" -> "<";"&gt;" -> ">";"&quot;" -> "\"";"&apos;","&#39;" -> "'";"&nbsp;" -> " ";else -> match.value}
    }
    private fun time(value: String): Double {
        val components=value.replace(',','.').split(':');require(components.size in 2..3)
        val seconds=components.last().toDouble();val minutes=components[components.size-2].toInt();val hours=if(components.size==3) components[0].toInt() else 0
        require(seconds.isFinite()&&seconds>=0&&seconds<60&&minutes in 0..59&&hours>=0);return hours*3600+minutes*60+seconds
    }
    private fun stamp(value: Double,vtt: Boolean): String {
        require(value.isFinite()&&value>=0);val ms=(value*1000).roundToLong()
        return "%02d:%02d:%02d%s%03d".format(java.util.Locale.ROOT,ms/3600000,ms/60000%60,ms/1000%60,if(vtt) "." else ",",ms%1000)
    }
}
