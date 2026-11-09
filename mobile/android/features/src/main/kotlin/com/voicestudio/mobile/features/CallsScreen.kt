package com.voicestudio.mobile.features

import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*

@Composable fun CallsScreen(vm:StudioViewModel) {
    val host by vm.state.collectAsState()
    val api=vm.api
    val scope=rememberCoroutineScope()
    val lifecycle=LocalLifecycleOwner.current
    var active by remember {mutableStateOf(true)}
    var voices by remember(host.connectionId) {mutableStateOf(emptyList<CallVoice>())}
    var readiness by remember(host.connectionId) {mutableStateOf(emptyList<CallReadiness>())}
    var disclosure by remember(host.connectionId) {mutableStateOf("")}
    var loaded by remember(host.connectionId) {mutableStateOf(false)}
    var history by remember(host.connectionId) {mutableStateOf(emptyList<CallRecord>())}
    var current by remember(host.connectionId) {mutableStateOf<CallRecord?>(null)}
    var number by remember {mutableStateOf("")}
    var brief by remember {mutableStateOf("")}
    var voice by remember(host.connectionId) {mutableStateOf("")}
    var minutes by remember {mutableStateOf("10")}
    var say by remember(host.connectionId) {mutableStateOf("")}
    var confirm by remember(host.connectionId) {mutableStateOf(false)}
    var busy by remember {mutableStateOf(false)}
    val authenticated=api?.sessionToken!=null
    suspend fun load() {
        if(api==null||!authenticated) return
        try {
            val calls=CallsApi(api)
            val ready=calls.readiness();val options=calls.options();val records=calls.recent();val profiles=calls.voices().filter {it.eligible}
            if(vm.api===api) {readiness=ready;disclosure=options.disclosure;history=records;voices=profiles;loaded=true;if(profiles.none {it.id==voice}) voice=profiles.firstOrNull()?.id.orEmpty()}
        } catch(e:CancellationException) {throw e} catch(e:Exception) {if(vm.api===api) vm.fail(e)}
    }
    fun control(action:String,text:String="",enabled:Boolean=false) {
        val selected=current ?: return
        if(api==null||busy) return
        busy=true
        scope.launch {try {
            val calls=CallsApi(api);calls.control(selected.id,action,text,enabled);val updated=calls.call(selected.id)
            if(vm.api===api) {current=updated;if(action=="say") say=""}
        } catch(e:CancellationException) {throw e} catch(e:Exception) {if(vm.api===api) vm.fail(e)} finally {busy=false}}
    }
    DisposableEffect(lifecycle) {
        val observer=LifecycleEventObserver {_,event -> if(event==Lifecycle.Event.ON_STOP) active=false else if(event==Lifecycle.Event.ON_START) active=true}
        lifecycle.lifecycle.addObserver(observer);onDispose {lifecycle.lifecycle.removeObserver(observer)}
    }
    LaunchedEffect(host.connectionId) {load()}
    LaunchedEffect(current?.id,active,host.connectionId) {
        val id=current?.id
        if(api==null||id==null||!active) return@LaunchedEffect
        try {while(isActive) {
            val record=CallsApi(api).call(id)
            if(vm.api!==api) break
            current=record
            if(!record.live) {load();break}
            delay(2000)
        }} catch(e:CancellationException) {throw e} catch(e:Exception) {if(vm.api===api) vm.fail(e)}
    }
    val valid=authenticated&&loaded&&!busy&&readiness.isNotEmpty()&&readiness.all {it.ok}&&number.matches(Regex("\\+[1-9][0-9]{6,14}"))&&brief.isNotBlank()&&brief.length<=4000&&voices.any {it.id==voice}
    Screen {
        ScreenTitle("calls.title")
        Text(tr("mobile.calls.hint"))
        if(!authenticated) Text(tr("mobile.calls.authentication")) else {
            Secondary("calls.recheck",!busy) {scope.launch {load()}}
            readiness.filterNot {it.ok}.forEach {Text(tr("calls.ready_"+it.id)+": "+it.detail)}
            Field("calls.number_label",number,{number=it})
            Field("calls.brief_label",brief,{brief=it},multiline=true)
            ChoicePicker("calls.voice_label",voice,voices.map {Choice(it.id,it.name)}) {voice=it}
            Text(tr("calls.voice_rule"))
            ChoicePicker("calls.max_duration",minutes,listOf(2,5,10,15,30).map {Choice(it.toString(),tr("calls.minutes",mapOf("minutes" to it)))}) {minutes=it}
            Heading("calls.first_line");Text(disclosure)
            Action("calls.call",valid) {confirm=true}
            current?.let {record ->
                Text(record.to_masked+" · "+record.status,style=MaterialTheme.typography.titleMedium)
                if(record.live) {
                    Toggle("calls.take_over",record.takeover) {if(!busy) control("takeover",enabled=it)}
                    Field("calls.say_label",say,{say=it},multiline=true)
                    Secondary("calls.say",!busy&&say.isNotBlank()&&say.length<=4000) {control("say",text=say)}
                    Action("calls.hang_up",!busy) {control("hangup")}
                }
                record.transcript.forEach {line -> Text(tr(if(line.speaker=="agent") "calls.speaker_agent" else "calls.speaker_caller")+": "+line.text)}
                record.summary?.let {Text(it)}
            }
            Heading("calls.history")
            history.forEach {record -> TextButton(onClick={current=record}) {Text(record.to_masked+" · "+record.status)}}
        }
    }
    if(confirm) AlertDialog(onDismissRequest={confirm=false},title={Text(tr("calls.confirm_title",mapOf("number" to number)))},text={Text(tr("calls.confirm_body",mapOf("voice" to voices.firstOrNull {it.id==voice}?.name.orEmpty(),"minutes" to minutes))+"\n"+disclosure+"\n"+tr("mobile.calls.billing"))},dismissButton={TextButton(onClick={confirm=false}) {Text(tr("common.cancel"))}},confirmButton={TextButton(enabled=valid,onClick={
        confirm=false
        if(api!=null&&valid) {val to=number;val task=brief;val selected=voice;val opening=disclosure;val duration=minutes.toInt();busy=true
            scope.launch {try {val record=CallsApi(api).start(to,task,selected,opening,duration);if(vm.api===api) current=record} catch(e:CancellationException) {throw e} catch(e:Exception) {if(vm.api===api) vm.fail(e)} finally {busy=false}}
        }
    }) {Text(tr("calls.confirm_call"))}})
}
