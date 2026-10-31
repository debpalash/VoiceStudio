package com.voicestudio.mobile.features.voices

import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.features.models.*
import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.network.Profile
import kotlinx.coroutines.*
import java.io.File

@Composable fun VoicesScreen(vm:StudioViewModel,initialCreating:Boolean=false,onCancel:()->Unit={},onCreated:()->Unit={}) {
    TaskLocation(vm,"clone",setOf("clone"),dependencies=setOf("asr")) {location,model -> VoicesContent(vm,location,model,initialCreating,onCancel,onCreated)}
}
@Composable private fun VoicesContent(vm:StudioViewModel,location: RunLocation,model: PhoneModel?,initialCreating:Boolean,onCancel:()->Unit,onCreated:()->Unit) {
    val state by vm.state.collectAsState()
    val phone by vm.phone.state.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val asr=phone.models.filter {it.capability=="asr"}.let {items -> items.firstOrNull {it.id==preferences.phoneModels["clone-asr"]} ?: items.firstOrNull {phone.canRun(it,vm.phone.ramBytes)} ?: items.firstOrNull()}
    val cloneReady=model?.let {it.capability=="clone"&&phone.canRun(it,vm.phone.ramBytes)}==true&&asr?.let {phone.canRun(it,vm.phone.ramBytes)}==true
    val profiles=phone.voices.map {Profile("phone:"+it.id,it.name,audioUrl=it.id)}+state.profiles
    fun local(profile: Profile)=phone.voices.firstOrNull {"phone:"+it.id==profile.id}
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val lifecycle=LocalLifecycleOwner.current
    var cloning by rememberSaveable {mutableStateOf(initialCreating)}
    var more by rememberSaveable {mutableStateOf(false)}
    var query by rememberSaveable {mutableStateOf("")}
    var name by rememberSaveable {mutableStateOf("")}
    var sample by rememberSaveable {mutableStateOf<String?>(null)}
    var permission by rememberSaveable {mutableStateOf(false)}
    var recording by remember {mutableStateOf(false)}
    var seconds by remember {mutableIntStateOf(0)}
    var edit by remember {mutableStateOf<Profile?>(null)}
    var editName by remember {mutableStateOf("")}
    var delete by remember {mutableStateOf<Profile?>(null)}
    var preview by remember {mutableStateOf<String?>(null)}
    val recorder=remember {SampleRecorder(context)}
    LaunchedEffect(Unit) {sample?.let {if(!File(it).exists()) sample=null}}
    DisposableEffect(lifecycle) {
        val observer=LifecycleEventObserver {_,event -> if(event==Lifecycle.Event.ON_STOP) {vm.phone.cancelInference();if(cloning) vm.cancelClone();if(recording) {recorder.discard();recording=false;sample=null}}}
        lifecycle.lifecycle.addObserver(observer)
        onDispose {lifecycle.lifecycle.removeObserver(observer);if(cloning) vm.cancelClone();cloning=false;if(recording) recorder.discard();sample?.let {File(it).delete()};sample=null;vm.phone.cancelInference()}
    }
    LaunchedEffect(recording) {seconds=0;while(recording) {
        delay(1000);seconds++
        if(seconds>=29) {try {sample=recorder.stop().absolutePath} catch(e:Exception) {sample=null;vm.fail(e)} finally {recording=false}}
    }}
    val record=permissionAction(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",vm::fail) {
        try {sample?.let {File(it).delete()};sample=recorder.start().absolutePath;recording=true} catch(e:Exception) {vm.fail(e)}
    }
    val pick=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri ->
        if(uri!=null) scope.launch {try {sample?.let {File(it).delete()};sample=copyMedia(context,uri,"audio").absolutePath} catch(e:Exception) {vm.fail(e)}}
    }
    Screen(bottomBar=if(!cloning&&profiles.isEmpty()) {{
        Action("mobile.voices.new_clone",!state.busy&&phone.progress==null) {cloning=true}
    }} else null,centerContent=!cloning&&profiles.isEmpty()&&!state.catalogLoading) {
        ScreenTitle("mobile.tabs.voices")
        if(!cloning&&profiles.isNotEmpty()) Action("mobile.voices.new_clone",!state.busy&&phone.progress==null) {cloning=true}
        if(cloning) {
        if(location!=RunLocation.COMPUTER) {
            Text(tr("mobile.calls.clone_hint"),color=role("textMuted"))
            if(!cloneReady) MissingModelsCard(vm,"mobile.tabs.voices",listOfNotNull(model,asr))
            Secondary("mobile.simple.more") {more=!more}
            if(more) {
                Heading("mobile.simple.clone")
                TaskModelPicker(vm,"clone",setOf("clone"),model)
                Heading("mobile.simple.asr")
                TaskModelPicker(vm,"clone-asr",setOf("asr"),asr)
            }
        }
        Field("mobile.voices.name_label",name,{name=it.take(200)})
        Info("mobile.voices.consent_title","mobile.voices.consent_body")
        ConsentCheck("mobile.simple.voice_consent",permission,{permission=it;if(!it) {vm.phone.cancelInference();vm.cancelClone()}})
        Secondary("mobile.voices.pick_file",!recording) {pick.launch(arrayOf("audio/*"))}
        if(recording) {
            Text(tr("mobile.voices.recording",mapOf("seconds" to seconds)))
            Secondary("common.stop") {try {sample=recorder.stop().absolutePath;recording=false} catch(e:Exception) {recording=false;sample=null;vm.fail(e)}}
        } else Secondary("mobile.voices.record",onClick=record)
        if(sample!=null&&!recording) Text(tr("mobile.android.sample_ready"))
        Action("mobile.voices.new_clone",permission&&sample!=null&&name.isNotBlank()&&!state.busy&&!recording&&phone.progress==null&&(location==RunLocation.COMPUTER||cloneReady)) {
            if(location==RunLocation.COMPUTER) vm.clone(name,File(sample!!),hasConsent={permission&&cloning}) {if(cloning) {sample=null;name="";permission=false;cloning=false;onCreated()}}
            else vm.phone.clone(name,File(sample!!),asr!!,model!!,permission) {sample=null;name="";permission=false;cloning=false;onCreated()}
        }
        Secondary("common.cancel",!recording) {vm.phone.cancelInference();vm.cancelClone();cloning=false;sample?.let {File(it).delete()};sample=null;onCancel()}
        }
        if(!cloning) {
        if(profiles.isEmpty()&&!state.catalogLoading) {
            Column(Modifier.fillMaxWidth().heightIn(min=320.dp),horizontalAlignment=Alignment.CenterHorizontally,
                verticalArrangement=Arrangement.spacedBy(8.dp,Alignment.CenterVertically)) {
                Icon(Icons.Default.Person,null,Modifier.size(40.dp),tint=role("textMuted"))
                Text(tr("mobile.simple.empty_voices"),style=MaterialTheme.typography.titleMedium)
                Text(tr("clone.reference_hint"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"),
                    textAlign=androidx.compose.ui.text.style.TextAlign.Center)
            }
        } else {
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {Field("mobile.voices.search",query,{query=it})}
            IconAction("common.refresh",Icons.Default.Refresh,!state.busy,onClick=vm::refresh)
        }
        phone.progress?.let {p -> LinearProgressIndicator(progress={p},modifier=Modifier.fillMaxWidth());Secondary("common.cancel") {vm.phone.cancelInference()}}
        val matches=profiles.filter {it.name.contains(query,true)}
        if(query.isNotBlank() && matches.isEmpty()) SearchEmpty(query)
        else if(state.catalogLoading) InlineLoading()
        Group {
            matches.forEach {profile ->
                var menu by remember {mutableStateOf(false)}
                Row(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    Surface(shape=androidx.compose.foundation.shape.CircleShape,color=role("messageSurface")) {Text(profile.name.take(1).uppercase(),Modifier.padding(VSTheme.inset))}
                    Column(Modifier.weight(1f)) {
                        Text(profile.name,style=MaterialTheme.typography.titleMedium)
                        local(profile)?.sourceAttribution?.let {credit ->Text(listOfNotNull(credit["author"],credit["license"],credit["name"]).joinToString(" · "),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))}
                        LocationBadge(local(profile)!=null)
                    }
                    IconAction("player.play",Icons.Default.PlayArrow,profile.audioUrl!=null) {preview=profile.audioUrl}
                    Box {
                        IconAction("mobile.android.more_actions",Icons.Default.MoreVert,!state.busy,mapOf("name" to profile.name)) {menu=true}
                        DropdownMenu(expanded=menu,onDismissRequest={menu=false},shape=MaterialTheme.shapes.large,containerColor=role("surfaceOverlay")) {
                            DropdownMenuItem(text={Text(tr("mobile.voices.rename"))},onClick={menu=false;edit=profile;editName=profile.name})
                            DropdownMenuItem(text={Text(tr("common.delete"))},onClick={menu=false;delete=profile})
                            if(LocalContentReportClient.current!=null) DropdownMenuItem(text={Text(tr("mobile.report.action"))},
                                leadingIcon={Icon(Icons.Default.Warning,contentDescription=null)},modifier=Modifier.heightIn(min=VSTheme.touchTarget),
                                onClick={menu=false;val voice=local(profile);vm.reports.open(if(voice!=null) ReportSubjects.phoneClone(voice) else ReportSubjects.computerClone(profile))})
                        }
                    }
                }
                HorizontalDivider()
            }
        }
        }
        }
        preview?.let {id ->
            val voice=phone.voices.firstOrNull {it.id==id}
            if(voice!=null) LocalAudioPlayer(vm.phone.voices.preview(voice),vm::fail)
            else vm.api?.let {HostPlayer(it,id,vm::fail)}
        }

    }
    if(edit!=null) AlertDialog(modifier=Modifier.dismissKeyboardOnTap(),shape=MaterialTheme.shapes.large,onDismissRequest={edit=null},title={Text(tr("mobile.voices.rename"))},text={Field("mobile.voices.name_label",editName,{editName=it})},
        confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={val voice=local(edit!!);if(voice!=null) vm.phone.renameVoice(voice,editName) else vm.rename(edit!!.id,editName);edit=null},enabled=editName.isNotBlank()) {Text(tr("common.save"))}},dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={edit=null}) {Text(tr("common.cancel"))}})
    if(delete!=null) AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest={delete=null},text={Text(tr("mobile.voices.delete_confirm",mapOf("name" to delete!!.name)))},
        confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={val voice=local(delete!!);if(voice!=null) vm.phone.deleteVoice(voice) else vm.deleteProfile(delete!!.id);delete=null}) {Text(tr("common.delete"))}},dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={delete=null}) {Text(tr("common.cancel"))}})
}
