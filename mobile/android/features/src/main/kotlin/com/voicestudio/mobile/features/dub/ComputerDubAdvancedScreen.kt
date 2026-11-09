package com.voicestudio.mobile.features.dub

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.Job
import kotlinx.serialization.json.*
import java.io.File
import java.util.UUID

@Composable fun ComputerDubAdvancedScreen(vm: StudioViewModel,jobID: String,title: String,onClose: ()->Unit) {
    val state by vm.state.collectAsState();val context=LocalContext.current;val scope=rememberCoroutineScope()
    val api=vm.api ?: return;val host=state.hostUrl ?: return
    val receipts=remember {context.getSharedPreferences("advanced-dub-tasks",Context.MODE_PRIVATE)};val receipt="$host|$jobID"
    var tracks by remember(jobID,host) {mutableStateOf(emptyMap<String,ComputerDubTrack>())}
    var options by remember(jobID,host) {mutableStateOf(ComputerDubExport())}
    var analysis by remember(jobID,host) {mutableStateOf<ComputerDubAnalysis?>(null)}
    var cast by remember(jobID,host) {mutableStateOf(emptyMap<String,String>())}
    var speakers by remember {mutableStateOf("0")};var reviewed by remember {mutableStateOf(false)};var timing by remember {mutableStateOf("concise")}
    var pending by remember {mutableStateOf<TranslationEngine?>(null)}
    var task by remember(jobID,host) {mutableStateOf(receipts.getString(receipt,null))}
    var work by remember {mutableStateOf<Job?>(null)};val busy=work?.isActive==true
    val directory=remember {File(context.cacheDir,"computer-dub/${UUID.randomUUID()}")}
    suspend fun persistTask(value: String?) {
        withContext(Dispatchers.IO) {val editor=receipts.edit();if(value==null) editor.remove(receipt) else editor.putString(receipt,value);if(!editor.commit()) throw ClientFailure("mobile.simple.checkpoint_failed")};task=value
    }
    suspend fun monitor(id: String) {
        var sequence=0
        while(currentCoroutineContext().isActive) {
            val page=api.jobEvents(id,sequence);var failed=page.job.status in listOf("failed","cancelled")
            page.events.forEach {event ->sequence=maxOf(sequence,event.seq)
                val type=runCatching {api.json.parseToJsonElement(event.payload.removePrefix("data: ").trim()).jsonObject["type"]?.jsonPrimitive?.content}.getOrNull()
                if(type in listOf("error","failed","cancelled")) failed=true
            }
            if(failed) {persistTask(null);throw ClientFailure("mobile.errors.dub_failed")}
            if(page.job.status=="done") {persistTask(null);tracks=api.computerDubTracks(jobID).tracks;return}
            delay(2000)
        };currentCoroutineContext().ensureActive()
    }
    fun launch(action: suspend ()->Unit) {
        if(busy) return
        work=scope.launch {try {action()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {work=null}}
    }
    suspend fun startRender(provider: TranslationEngine,confirmed: Boolean) {
        val source=requireNotNull(analysis)
        val authorization=provider.authorize(confirmed) ?: throw ClientFailure("mobile.dub.translation_setup")
        val name=tracks[options.language]?.language ?: options.language
        val result=api.computerDubCast(jobID,source,options.language,name,cast,authorization,timing)
        persistTask(result.taskId);monitor(result.taskId)
    }
    LaunchedEffect(jobID,host) {try {tracks=api.computerDubTracks(jobID).tracks;val codes=tracks.keys.sorted();options=options.copy(language=codes.firstOrNull().orEmpty(),tracks=codes)} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    DisposableEffect(api) {onDispose {work?.cancel()}}
    DisposableEffect(Unit) {onDispose {val active=work;active?.cancel();if(active?.isCompleted!=false) directory.deleteRecursively() else active.invokeOnCompletion {directory.deleteRecursively()}}}
    Screen {
        Secondary("common.back",!busy,onClose);ScreenTitle("mobile.dub_advanced.exports");Text(title)
        Text(tr("mobile.dub_advanced.computer_hint"),style=MaterialTheme.typography.bodySmall)
        if(tracks.isNotEmpty()) Group {Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            if(!busy) {
                val kinds=listOf("media" to "mobile.dub_advanced.multi_audio","srt" to "mobile.editing.export_srt","vtt" to "mobile.editing.export_vtt","ass" to "mobile.dub_advanced.ass","clips" to "mobile.dub_advanced.clips","stems" to "mobile.dub_advanced.stems")
                ChoicePicker("mobile.dub_advanced.exports",options.kind,kinds.map {Choice(it.first,tr(it.second))}) {options=options.copy(kind=it)}
                ChoicePicker("exportModal.default_audio_track",options.language,tracks.keys.sorted().map {Choice(it,tracks[it]?.language ?: it)}) {options=options.copy(language=it,tracks=(options.tracks+it).distinct())}
            }
            if(options.kind=="media") {
                tracks.keys.sorted().forEach {code ->Row(Modifier.fillMaxWidth()) {
                    Text(tracks[code]?.language ?: code,Modifier.weight(1f));Switch(code in options.tracks,{include ->
                        val selected=options.tracks.filterNot {it==code}+(if(include) listOf(code) else emptyList())
                        options=options.copy(tracks=selected,language=options.language.takeIf {it in selected} ?: selected.sorted().firstOrNull().orEmpty())
                    },enabled=!busy)
                }}
                Toggle("exportModal.original",options.original) {if(!busy) options=options.copy(original=it)}
                Toggle("batch.preserve_bg",options.preserveBackground) {if(!busy) options=options.copy(preserveBackground=it)}
                Text(tr("mobile.dub_advanced.background_hint"),style=MaterialTheme.typography.bodySmall)
                Toggle("dub.burn_subs",options.burn) {if(!busy) options=options.copy(burn=it)}
                if(options.burn) {
                    Toggle("exportModal.dual_subs",options.dual) {if(!busy) options=options.copy(dual=it,karaoke=if(it) false else options.karaoke)}
                    Toggle("exportModal.subs_style_karaoke",options.karaoke) {if(!busy) options=options.copy(karaoke=it,dual=if(it) false else options.dual)}
                }
                if(!busy) ChoicePicker("audiobook.format",options.audioFormat,listOf(Choice("m4a","AAC / M4A"),Choice("wav","WAV"),Choice("mp3","MP3"),Choice("flac","FLAC"))) {options=options.copy(audioFormat=it)}
            } else if(options.kind in listOf("srt","vtt")) Toggle("exportModal.dual_subs",options.dual) {if(!busy) options=options.copy(dual=it)}
            Action("mobile.speak.share",!busy&&options.language.isNotEmpty()) {val snapshot=options;launch {
                val file=sharedDownload(context,api,snapshot.path(jobID),when(snapshot.kind) {"srt"->"srt";"vtt"->"vtt";"ass"->"ass";"clips","stems"->"zip";else->snapshot.audioFormat})
                try {if(vm.api!==api) throw CancellationException();shareFile(context,file,when(snapshot.kind) {"srt","vtt","ass"->"text/plain";"clips","stems"->"application/zip";else->"application/octet-stream"})} catch(e:Exception) {file.delete();throw e}
            }}
        }}
        Heading("dub.cast");Text(tr("mobile.dub_advanced.detect_hint"),style=MaterialTheme.typography.bodySmall)
        Field("dub.num_speakers_label",speakers,{if(!busy&&task==null) speakers=it})
        Secondary("mobile.dub_advanced.detect",!busy&&task==null) {launch {
            val count=speakers.toIntOrNull()?.takeIf {it in 0..20} ?: throw ClientFailure("mobile.dub_advanced.invalid_cast")
            analysis=api.computerDubAnalysis(jobID,count);reviewed=false;cast=emptyMap()
        }}
        analysis?.let {source ->
            source.warnings.forEach {Text(it,style=MaterialTheme.typography.bodySmall)}
            Text(tr("mobile.dub_advanced.review_cast"),style=MaterialTheme.typography.bodySmall)
            source.segments.mapNotNull {it.speakerId}.distinct().sorted().forEach {speaker ->
                Text(speaker,style=MaterialTheme.typography.titleSmall)
                if(!busy&&task==null) ChoicePicker("mobile.speak.voice",cast[speaker].orEmpty(),listOf(Choice("",tr("stories.defaultVoice")))+(if(source.sources[speaker]!=null) listOf(Choice(ComputerDubAnalysis.sourceVoiceID(speaker),tr("mobile.dub_advanced.source_voice"))) else emptyList())+state.profiles.map {Choice(it.id,it.name)}) {cast=cast+(speaker to it)}
            }
            Toggle("mobile.dub_advanced.cast_checked",reviewed) {if(!busy&&task==null) reviewed=it}
            if(!busy&&task==null) ChoicePicker("mobile.dub_advanced.timing",timing,listOf(Choice("concise",tr("dub.timing_concise")),Choice("smart_fit",tr("dub.timing_smart_fit")),Choice("stretch_video",tr("dub.timing_stretch_video")))) {timing=it}
            Action("dub.generate_dub",!busy&&reviewed&&options.language.isNotEmpty()&&task==null) {launch {
                val provider=api.translationEngine();if(provider.requiresConsent) pending=provider else startRender(provider,false)
            }}
        }
        if(task!=null&&!busy) Secondary("mobile.simple.resume") {task?.let {id ->launch {monitor(id)}}}
        task?.let {id ->Secondary("mobile.dub.abort") {work?.cancel();scope.launch {try {api.cancelComputerDubTask(id);persistTask(null)} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}}}
        if(busy) {LinearProgressIndicator(Modifier.fillMaxWidth());if(task==null) Secondary("common.cancel") {work?.cancel()}}
    }
    pending?.let {provider ->AlertDialog(onDismissRequest={pending=null},title={Text(tr("mobile.dub.translation_online_title"))},text={Text(tr("mobile.dub.translation_online_body",mapOf("service" to provider.consentName)))},confirmButton={TextButton(onClick={pending=null;launch {startRender(provider,true)}}) {Text(tr("mobile.dub.translation_allow"))}},dismissButton={TextButton(onClick={pending=null}) {Text(tr("mobile.dub.translation_decline"))}})}
}
