package com.voicestudio.mobile.network

import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLEncoder

@Serializable data class ComputerDubTrack(val language: String?=null,val duration: Double?=null)
@Serializable data class ComputerDubTracks(val tracks: Map<String,ComputerDubTrack>)
@Serializable data class ComputerDiarization(val installed: Boolean,val label: String?=null)
@Serializable data class ComputerCastSource(val kind: String,val duration: Double?=null)
data class ComputerDubAnalysis(val segments: List<Segment>,val warnings: List<String>,val sources: Map<String,ComputerCastSource>) {
    companion object {
        fun parse(data: ByteArray): ComputerDubAnalysis {
            require(data.size<=16_777_216)
            val decoder=Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            val text=decoder.decode(java.nio.ByteBuffer.wrap(data)).toString().replace("\r\n","\n")
            val json=Json {ignoreUnknownKeys=true};var final:List<Segment>?=null;var sources=emptyMap<String,ComputerCastSource>();val warnings=mutableListOf<String>();var terminal=false
            for(block in text.split("\n\n")) {
                val lines=block.lines();val event=lines.firstOrNull {it.startsWith("event:")}?.removePrefix("event:")?.trim().orEmpty()
                val payload=lines.filter {it.startsWith("data:")}.joinToString("\n") {it.removePrefix("data:").trim()}
                if(event in listOf("error","aborted","cancelled")) throw ClientFailure("mobile.errors.dub_failed")
                if(event=="warning") json.parseToJsonElement(payload).jsonObject["detail"]?.jsonPrimitive?.contentOrNull?.let {if(warnings.size<100) warnings+=it.take(2000)}
                if(event=="final") {
                    val value=json.parseToJsonElement(payload).jsonObject
                    final=json.decodeFromJsonElement<List<Segment>>(value.getValue("segments"))
                    sources=value["cast_sources"]?.let {json.decodeFromJsonElement<Map<String,ComputerCastSource>>(it)} ?: emptyMap()
                }
                if(event=="done") terminal=true
            }
            val segments=final ?: throw ClientFailure("mobile.dub_advanced.invalid_cast")
            if(!terminal||segments.isEmpty()||segments.size>10000||segments.map {it.id?.jsonPrimitive?.content}.distinct().size!=segments.size||segments.any {!it.start.isFinite()||!it.end.isFinite()||it.start<0||it.end<=it.start||it.end>14400||it.text.isEmpty()||it.text.length>4000||it.speakerId.orEmpty().length>80}) throw ClientFailure("mobile.dub_advanced.invalid_cast")
            val restored=segments.map {it.copy(text=it.textOriginal ?: it.text)}
            if(restored.any {it.text.isBlank()||it.text.length>4000}) throw ClientFailure("mobile.dub_advanced.invalid_cast")
            return ComputerDubAnalysis(restored,warnings,sources)
        }
        fun sourceVoiceID(speaker: String)="auto:"+speaker.lowercase(java.util.Locale.ROOT).mapNotNull {when {it.isLetterOrDigit()->it;it==' '||it=='-'->'_';else->null}}.joinToString("").ifEmpty {"speaker"}
    }
}
data class ComputerDubExport(val kind: String="media",val language: String="",val tracks: List<String> = emptyList(),val original: Boolean=false,val preserveBackground: Boolean=false,val burn: Boolean=false,val dual: Boolean=false,val karaoke: Boolean=false,val audioFormat: String="m4a") {
    fun path(job: String): String {
        fun safe(value: String)=value.matches(Regex("[A-Za-z0-9_-]{1,80}"))
        require(safe(job)&&safe(language)&&tracks.all(::safe)&&audioFormat in listOf("wav","m4a","mp3","flac")&&!(dual&&karaoke))
        val endpoint=mapOf("media" to "download","srt" to "srt","vtt" to "vtt","ass" to "ass","clips" to "export-segments","stems" to "export-stems")[kind] ?: throw IllegalArgumentException()
        val query=linkedMapOf("lang" to language)
        if(kind=="media") {
            require(tracks.isNotEmpty()&&language in tracks)
            query.putAll(linkedMapOf("default_track" to language,"include_tracks" to (tracks+(if(original) listOf("original") else emptyList())).joinToString(","),"preserve_bg" to if(preserveBackground) "1" else "0","burn_subs" to if(burn) "1" else "0","dual" to if(dual) "1" else "0","karaoke" to if(karaoke) "1" else "0","out_format" to audioFormat))
        } else if(kind in listOf("srt","vtt")) query["dual"]=if(dual) "1" else "0"
        return "/dub/$endpoint/${VoiceStudioApi.segment(job)}?"+query.entries.joinToString("&") {URLEncoder.encode(it.key,"UTF-8")+"="+URLEncoder.encode(it.value,"UTF-8")}
    }
}
suspend fun VoiceStudioApi.computerDubTracks(job: String):ComputerDubTracks=withContext(Dispatchers.IO) {raw("/dub/tracks/${VoiceStudioApi.segment(job)}").use {json.decodeFromString(it.body!!.string())}}
suspend fun VoiceStudioApi.computerDiarization():ComputerDiarization=withContext(Dispatchers.IO) {raw("/engines/diarisation").use {json.decodeFromString(it.body!!.string())}}
suspend fun VoiceStudioApi.computerDubAnalysis(job: String,speakers: Int=0):ComputerDubAnalysis=withContext(Dispatchers.IO) {
    require(speakers in 0..20)
    if(!computerDiarization().installed) throw ClientFailure("mobile.dub_advanced.install_on_computer")
    raw("/dub/transcribe-stream/${VoiceStudioApi.segment(job)}?reanalyze_speakers=true"+(if(speakers==0) "" else "&num_speakers=$speakers")).use {readComputerDubAnalysis(it)}
}
/** Keep cancellation attached after HTTP headers arrive, including a stalled SSE body read. */
internal suspend fun readComputerDubAnalysis(response: okhttp3.Response): ComputerDubAnalysis = coroutineScope {
    val cleanup=launch(start=CoroutineStart.UNDISPATCHED) {try {awaitCancellation()} finally {response.close()}}
    try {
        val input=response.body!!.byteStream();val output=java.io.ByteArrayOutputStream();val bytes=ByteArray(16384)
        while(true) {ensureActive();val count=input.read(bytes);if(count<0) break;require(output.size()+count<=16_777_216);output.write(bytes,0,count)}
        ensureActive();ComputerDubAnalysis.parse(output.toByteArray())
    } catch(error: Exception) {ensureActive();throw error} finally {cleanup.cancel()}
}
suspend fun VoiceStudioApi.computerDubTexts(job: String,language: String):Map<String,String> = withContext(Dispatchers.IO) {
    raw("/dub/segments-text/${VoiceStudioApi.segment(job)}?lang="+URLEncoder.encode(language,"UTF-8")).use {json.parseToJsonElement(it.body!!.string()).jsonObject.getValue("texts").jsonObject.mapValues {it.value.jsonPrimitive.content}}
}
suspend fun VoiceStudioApi.computerDubCast(job: String,analysis: ComputerDubAnalysis,language: String,name: String,voices: Map<String,String>,authorization: TranslationAuthorization,timing: String="concise"):TaskResult = withContext(Dispatchers.IO) {
    require(timing in listOf("concise","smart_fit","stretch_video"))
    val current=translationEngine()
    if(current.id!=authorization.provider||current.category!=authorization.category||current.routingIdentity!=authorization.routingIdentity||current.requiresConsent&&!authorization.onlineConfirmed) throw ClientFailure("mobile.dub.translation_changed")
    val translated=translate(job,language,null,analysis.segments,authorization)
    if(translated.translated.size!=analysis.segments.size||translated.translated.any {it.error!=null}) throw ClientFailure("mobile.errors.translation")
    val texts=translated.translated.associate {it.id.jsonPrimitive.content to it.text}
    val body=buildJsonObject {
        put("language_code",language);put("language",name);put("timing_strategy",timing)
        putJsonArray("segment_ids") {analysis.segments.forEach {add(it.id?.jsonPrimitive?.content ?: throw ClientFailure("mobile.dub_advanced.invalid_cast"))}}
        putJsonArray("segments") {analysis.segments.forEach {cue ->
            val id=cue.id?.jsonPrimitive?.content ?: throw ClientFailure("mobile.dub_advanced.invalid_cast")
            val text=texts[id]?.takeIf(String::isNotEmpty) ?: throw ClientFailure("mobile.dub_advanced.missing_translation")
            add(buildJsonObject {put("start",cue.start);put("end",cue.end);put("text",text);put("profile_id",voices[cue.speakerId.orEmpty()].orEmpty());put("target_lang",language);putRenderSettings(cue)})
        }}
    }
    raw("/dub/generate/${VoiceStudioApi.segment(job)}","POST",body.toString().toRequestBody("application/json".toMediaType())).use {json.decodeFromString(it.body!!.string())}
}
suspend fun VoiceStudioApi.cancelComputerDubTask(task: String) {raw("/tasks/cancel/${VoiceStudioApi.segment(task)}","POST",ByteArray(0).toRequestBody()).close()}
