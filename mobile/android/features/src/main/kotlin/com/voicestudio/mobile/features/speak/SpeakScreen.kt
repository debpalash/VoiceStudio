package com.voicestudio.mobile.features.speak

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.voicestudio.mobile.designsystem.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.local.BookExport
import com.voicestudio.mobile.local.PhoneModel
import com.voicestudio.mobile.features.*
import java.io.File
import kotlinx.coroutines.*

@Composable private fun ComputerSpeakScreen(vm:StudioViewModel, text: String, onTextChange: (String)->Unit, initialVoiceID: String? = null) {
    val state by vm.state.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var voice by rememberSaveable(initialVoiceID) {mutableStateOf(initialVoiceID.orEmpty())}
    var engine by rememberSaveable {mutableStateOf("")}
    var language by rememberSaveable {mutableStateOf("Auto")}
    val catalog=rememberLanguageCatalog(vm::fail)
    val l=LocalL10n.current
    val profile=state.profiles.firstOrNull {it.id==voice}
    val engines=engineChoices(state.engines,profile) {l.t(it)}
    var savePath by rememberSaveable {mutableStateOf<String?>(null)}
    val save=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("audio/wav")) {uri ->
        val path=savePath;savePath=null
        if(uri!=null && path!=null) scope.launch {
            try {val file=sharedDownload(context,vm.api!!,path,"wav");withContext(Dispatchers.IO) {context.contentResolver.openOutputStream(uri)!!.use {out -> file.inputStream().use {it.copyTo(out)}}};file.delete()}
            catch(e:Exception) {vm.fail(e)}
        }
    }
    LaunchedEffect(state.profiles) {if(voice.isNotBlank() && profile==null) voice=""}
    LaunchedEffect(voice,profile?.language,catalog) {language=catalog?.preferred(profile) ?: "Auto"}
    LaunchedEffect(engines,state.engines.active) {
        if(engines.none {it.id==engine && it.enabled}) engine=initialEngine(state.engines,engines)
    }
    Screen(bottomBar={
        Action(if(state.busy) "mobile.speak.generating" else "mobile.speak.generate",text.isNotBlank()&&engines.any {it.id==engine&&it.enabled}&&!state.busy) {
            vm.generate(text,voice,engine,language)
        }
    }) {
        ScreenTitle("mobile.tabs.speak")
        Field("mobile.speak.placeholder",text,onTextChange,multiline=true)
        if(catalog==null) InlineLoading()
        Group {
            ChoicePicker("mobile.speak.voice",voice,listOf(Choice("",tr("mobile.simple.default_voice")))+state.profiles.map {Choice(it.id,it.name)}) {voice=it}
            HorizontalDivider(color=role("border"))
            ChoicePicker("mobile.speak.engine",engine,engines) {engine=it}
            HorizontalDivider(color=role("border"))
            ChoicePicker("mobile.speak.language",language,catalog?.names.orEmpty().map {Choice(it,it)}) {language=it}
        }
        val audio=state.audio;val api=vm.api
        if(audio!=null && api!=null) {
            HostPlayer(api,audio,vm::fail)
            Secondary("mobile.speak.share",!state.busy) {vm.run {shareFile(context,sharedDownload(context,api,audio,"wav"),"audio/wav")}}
            Secondary("common.save",!state.busy) {savePath=audio;save.launch("${java.util.UUID.randomUUID()}.wav")}
            ReportButton(vm) {vm.computerSpeakSubject(audio)}
        }
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {Heading("mobile.speak.recent")}
            IconAction("common.refresh",Icons.Default.Refresh,!state.busy,onClick=vm::refresh)
        }
        if(state.catalogLoading) InlineLoading()
        else if(state.history.isEmpty()) EmptyState("mobile.simple.empty_recent")
        if(state.history.isNotEmpty()) Group {
            state.history.forEach {item ->
                Row(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalAlignment=Alignment.CenterVertically) {
                    Text(item.text,Modifier.weight(1f),style=MaterialTheme.typography.bodyMedium,maxLines=1,overflow=androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                    IconAction("player.play",Icons.Default.PlayArrow) {vm.play(vm.api!!.audioPath(item.audioPath))}
                    IconAction(if(item.isStarred) "mobile.speak.unstar" else "mobile.speak.star",Icons.Default.Star,!state.busy) {vm.star(item)}
                    IconAction("common.delete",Icons.Default.Delete,!state.busy) {vm.deleteHistory(item)}
                    ReportAction(vm) {vm.computerSpeakSubject(vm.api?.audioPath(item.audioPath) ?: item.audioPath,item.text)}
                }
                HorizontalDivider()
            }
        }
    }
}

/** Preset speech and saved-voice cloning use different models, so each keeps its own remembered choice. */
internal fun speakModelKey(capability: String)=if(capability=="clone") "speak-clone" else "speak"
internal fun chooseSpeakModel(models: List<PhoneModel>,installed: Set<String>,choices: Map<String,String>,capability: String, ramBytes: Long = Long.MAX_VALUE): PhoneModel? {
    val matching=models.filter {it.capability==capability}
    return models.firstOrNull {it.id==choices[speakModelKey(capability)]&&(if(capability=="tts") it.supportsSpeech else it.capability==capability)} ?: matching.firstOrNull {it.id in installed&&it.fits(ramBytes)} ?: matching.firstOrNull {it.id in installed} ?: matching.firstOrNull {it.fits(ramBytes)} ?: matching.firstOrNull()
}

@Composable fun SpeakScreen(vm: StudioViewModel, initialPhoneVoiceID: String? = null, initialComputerVoiceID: String? = null, initialPhonePreset: Int? = null, initialPhoneModelID: String? = null, initialText: String? = null, openGallery: (() -> Unit)? = null) {
    var text by rememberSaveable(initialText) {mutableStateOf(initialText.orEmpty())}
    com.voicestudio.mobile.features.models.TaskLocation(vm,"speak",setOf("tts","clone"),initialLocation=when {initialComputerVoiceID!=null->"computer";initialPhoneVoiceID!=null||initialPhonePreset!=null->"phone";else->null}) {location,model ->
        if(location==com.voicestudio.mobile.local.RunLocation.COMPUTER) ComputerSpeakScreen(vm,text,{text=it},initialComputerVoiceID)
        else {
            val phone by vm.phone.state.collectAsState()
            val context=LocalContext.current
            val scope=rememberCoroutineScope()
            var speaker by rememberSaveable(initialPhoneVoiceID) {mutableStateOf(initialPhoneVoiceID?.let {"phone:"+it} ?: (initialPhonePreset ?: 0).toString())}
            val voice=phone.voices.firstOrNull {"phone:"+it.id==speaker}
            val capability=if(voice==null) "tts" else "clone"
            val prefs by vm.preferences.collectAsState()
            var requestedModel by remember(initialPhoneModelID) {mutableStateOf(initialPhoneModelID)}
            LaunchedEffect(prefs.phoneModels[speakModelKey(capability)]) {if(prefs.phoneModels[speakModelKey(capability)]==requestedModel) requestedModel=null}
            val selected=phone.models.firstOrNull {it.id==requestedModel&&it.supportsSpeech&&(voice==null||it.supportsCloning)} ?: chooseSpeakModel(phone.models,phone.installed,prefs.phoneModels,capability,vm.phone.ramBytes)
            LaunchedEffect(phone.voices) {if(speaker.startsWith("phone:")&&voice==null) speaker="0"}
            var exportFormat by rememberSaveable {mutableStateOf("wav")}
            var exporting by remember {mutableStateOf(false)}
            var exportJob by remember {mutableStateOf<Job?>(null)}
            var saveSource by remember {mutableStateOf<File?>(null)}
            val save=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("audio/*")) {uri ->
                val source=saveSource;saveSource=null
                if(uri!=null&&source!=null) scope.launch {
                    try {withContext(Dispatchers.IO) {context.contentResolver.openOutputStream(uri)!!.use {out -> source.inputStream().use {it.copyTo(out)}}}}
                    catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
                }
            }
            fun export(source: File,saveFile: Boolean) {
                val format=exportFormat
                exporting=true
                exportJob=scope.launch {
                    try {
                        val file=BookExport.encode(source,format)
                        if(saveFile) {saveSource=file;save.launch(file.name)}
                        else shareLocalFile(context,file,when(format) {"mp3"->"audio/mpeg";"m4a"->"audio/mp4";else->"audio/wav"})
                    } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {exporting=false;exportJob=null}
                }
            }
            DisposableEffect(Unit) {onDispose {vm.phone.cancelInference()}}
            val speakers=if(selected?.requiresReference==true) 0 else selected?.option("num_speakers",selected.option("speaker_count","1"))?.toIntOrNull()?.coerceIn(1,128) ?: 1
            val validSpeaker=voice!=null||speaker.toIntOrNull()?.let {it in 0 until speakers}==true
            LaunchedEffect(selected?.id,speakers) {if(voice==null&&!validSpeaker) speaker=if(selected?.requiresReference==true) "" else "0"}
            var language by rememberSaveable {mutableStateOf("en")}
            LaunchedEffect(selected?.id,voice?.id) {language=if(voice!=null||selected?.capability=="clone") "auto" else language.takeIf {it in selected?.languages.orEmpty()} ?: selected?.languages?.firstOrNull().orEmpty()}
            val languageChoices=if(voice!=null||selected?.capability=="clone") listOf(Choice("auto",tr("languagePicker.auto"))) else selected?.languages.orEmpty().map {Choice(it,languageName(it,LocalL10n.current.locale))}
            Screen(bottomBar={
                if(selected!=null&&!phone.canRun(selected)) {
                    com.voicestudio.mobile.features.models.MissingModelsCard(vm,"mobile.tabs.speak",listOf(selected),prominent=true)
                } else Action("mobile.speak.generate",text.isNotBlank()&&selected!=null&&phone.canRun(selected)&&validSpeaker&&phone.progress==null&&!exporting) {
                    vm.phone.speak(selected!!,text,speaker.toIntOrNull() ?: 0,voice,language.takeUnless {it=="auto"})
                }
            }) {
                ScreenTitle("mobile.tabs.speak")
                Field("mobile.speak.placeholder",text,{text=it},multiline=true)
                Group {
                    ChoicePicker("mobile.speak.voice",speaker,(if(selected?.requiresReference==true) listOf(Choice("",tr("mobile.dub.choose_voice"),enabled=false)) else emptyList())+(0 until speakers).map {Choice(it.toString(),if(selected?.supportsDesign==true) tr("mobile.simple.default_voice") else tr("mobile.local.preset_voice",mapOf("number" to it+1)))}+phone.voices.map {Choice("phone:"+it.id,it.name)}) {speaker=it}
                    HorizontalDivider(color=role("border"))
                    ChoicePicker("mobile.local.model",selected?.id.orEmpty(),phone.models.filter {it.supportsSpeech}.map {
                        phoneModelChoice(it,vm.phone.ramBytes,tr("mobile.simple.${it.capability}"))
                    }) {id ->
                        val chosen=phone.models.first {it.id==id}
                        vm.phone.cancelInference();requestedModel=id
                        if(voice!=null&&!chosen.supportsCloning) speaker="0"
                        vm.preference {prefs -> prefs.copy(phoneModels=prefs.phoneModels+(speakModelKey(if(voice!=null&&chosen.supportsCloning) "clone" else "tts") to id)+(if(chosen.supportsCloning) mapOf("speak-clone" to id) else emptyMap()))}
                    }
                    HorizontalDivider(color=role("border"))
                    ChoicePicker("mobile.speak.language",language,languageChoices) {language=it}
                }
                com.voicestudio.mobile.features.models.ModelRamWarning(vm,listOfNotNull(selected))
                if(selected?.requiresReference==true&&voice==null) {
                    Text(tr("mobile.speak.reference_required"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
                    openGallery?.let {Secondary("nav.gallery",phone.progress==null,it)}
                }
                phone.progress?.let {p ->
                    LinearProgressIndicator(progress={p},modifier=Modifier.fillMaxWidth())
                    Secondary("common.cancel") {vm.phone.cancelInference()}
                }
                phone.audio?.let {file ->
                    LocalAudioPlayer(file,vm::fail)
                    ChoicePicker("audiobook.format",exportFormat,listOf(Choice("wav","WAV"),Choice("m4a","AAC / M4A"),Choice("mp3","MP3"))) {exportFormat=it}
                    Secondary("mobile.speak.share",!exporting) {export(file,false)}
                    Secondary("common.save",!exporting) {export(file,true)}
                    if(exporting) {CircularProgressIndicator();Secondary("common.cancel") {exportJob?.cancel()}}
                    phone.recent.firstOrNull {vm.phone.generationFile(it)==file}?.let {take -> ReportButton(vm) {ReportSubjects.phoneSpeak(take)}}
                }
                Heading("mobile.speak.recent")
                if(phone.recent.isEmpty()) EmptyState("mobile.simple.empty_recent")
                phone.recent.forEach {item ->
                    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                        Text(item.text,Modifier.weight(1f),maxLines=1,overflow=androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                        IconAction("player.play",Icons.Default.PlayArrow) {vm.phone.play(item)}
                        IconAction("common.delete",Icons.Default.Delete) {vm.phone.deleteGeneration(item)}
                        ReportAction(vm) {ReportSubjects.phoneSpeak(item)}
                    }
                }

            }
        }
    }
}
