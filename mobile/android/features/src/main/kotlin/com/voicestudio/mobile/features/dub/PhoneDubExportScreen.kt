package com.voicestudio.mobile.features.dub

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import java.io.File
import java.util.UUID

@Composable fun PhoneDubExportScreen(vm: StudioViewModel,id: String,initialTarget: String,onClose: ()->Unit) {
    val phone by vm.phone.state.collectAsState();val record=phone.dubs.firstOrNull {it.request.id==id}
    val context=LocalContext.current;val scope=rememberCoroutineScope()
    var target by remember(id) {mutableStateOf(initialTarget.ifBlank {record?.request?.targets?.firstOrNull()?.code.orEmpty()})}
    var kind by remember {mutableStateOf("ass")};var style by remember {mutableStateOf(DubSubtitleStyle())}
    var job by remember {mutableStateOf<Job?>(null)};val busy=job?.isActive==true
    val workspace=remember {File(context.cacheDir,"dub-exports/${UUID.randomUUID()}")}
    DisposableEffect(Unit) {onDispose {val running=job;running?.cancel();if(running?.isCompleted!=false) workspace.deleteRecursively() else running.invokeOnCompletion {workspace.deleteRecursively()}}}
    Screen(bottomBar={Action("mobile.speak.share",!busy&&target.isNotEmpty()) {
        val selected=target;val mode=kind;val subtitleStyle=style
        job=scope.launch {
            try {
                val file=withContext(Dispatchers.IO) {
                    workspace.mkdirs()
                    when(mode) {
                        "ass" ->File(workspace,"subtitles-${UUID.randomUUID()}.ass").apply {writeText(PhoneDubASS.export(vm.phone.dubs.checkpoint(id).segments,selected,subtitleStyle))}
                        "video" ->vm.phone.dubs.exportMultiAudio(id,selected,workspace)
                        else ->vm.phone.dubs.exportPackage(id,selected,if(mode=="clips") DubPackage.CLIPS else DubPackage.STEMS,workspace)
                    }
                }
                ensureActive();shareLocalFile(context,file,when(mode) {"ass" -> "text/plain";"video" -> "video/mp4";else -> "application/zip"})
            } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {job=null}
        }
    }}) {
        Secondary("common.back",!busy,onClose);ScreenTitle("mobile.dub_advanced.exports")
        record?.let {Text(it.request.title)}
        if(!busy) {
            ChoicePicker("mobile.speak.language",target,record?.request?.targets.orEmpty().map {Choice(it.code,it.name)}) {target=it}
            ChoicePicker("mobile.dub_advanced.exports",kind,listOf(Choice("ass",tr("mobile.dub_advanced.ass")),Choice("clips",tr("mobile.dub_advanced.clips")),Choice("stems",tr("mobile.dub_advanced.stems")))+(if(record?.checkpoint?.outputs?.any {it.file.endsWith(".mp4")}==true) listOf(Choice("video",tr("mobile.dub_advanced.multi_audio"))) else emptyList())) {kind=it}
        }
        if(kind=="ass") {
            Text(tr("mobile.dub_advanced.font_size")+" "+style.fontSize)
            val label=tr("mobile.dub_advanced.font_size")
            Slider(style.fontSize.toFloat(),{style=style.copy(fontSize=it.toInt())},valueRange=18f..72f,steps=53,enabled=!busy,modifier=Modifier.semantics {contentDescription=label})
            if(!busy) ChoicePicker("mobile.dub_advanced.color",style.color,listOf(Choice("FFFFFF",tr("mobile.dub_advanced.white")),Choice("FFFF00",tr("mobile.dub_advanced.yellow")),Choice("00FFFF",tr("mobile.dub_advanced.cyan")))) {style=style.copy(color=it)}
            Toggle("mobile.dub_advanced.top",style.top) {if(!busy) style=style.copy(top=it)}
            Toggle("mobile.dub_advanced.dual",style.dual) {if(!busy) style=style.copy(dual=it)}
        } else if(kind=="stems") Text(tr("mobile.dub_advanced.stems_hint"))
        if(kind=="video") Text(tr("mobile.dub_advanced.multi_audio_hint")) else if(kind!="ass") Text(tr("mobile.dub_advanced.export_limit"))
        if(busy) {LinearProgressIndicator(Modifier.fillMaxWidth());Secondary("common.cancel") {job?.cancel()}}
    }
}
