package com.voicestudio.mobile.features.dictate

import androidx.compose.material.icons.filled.*
import android.Manifest
import kotlinx.coroutines.launch
import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.*
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.CancellationException

@Composable private fun ComputerDictateScreen(vm:StudioViewModel) {
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val api=vm.api ?: return
    val controller=remember(api) {DictationController(api,scope,vm::fail,completed={vm.phone.saveTranscript(it)})}
    val transcript by controller.text.collectAsState()
    val active by controller.active.collectAsState()
    val lifecycle=LocalLifecycleOwner.current
    var readiness by remember {mutableStateOf<Readiness?>(null)}
    var checking by remember {mutableStateOf(true)}
    var readinessError by remember {mutableStateOf<ClientFailure?>(null)}
    suspend fun check() {
        checking=true;readinessError=null
        try {readiness=api.readiness()}
        catch(e:CancellationException) {throw e}
        catch(e:Exception) {readiness=null;readinessError=e as? ClientFailure ?: ClientFailure("mobile.connect.error_unreachable",mapOf("host" to api.address.baseUrl.host))}
        finally {checking=false}
    }
    LaunchedEffect(api) {check()}
    val ready=readiness?.ready==true && !checking
    DisposableEffect(lifecycle,controller) {
        val observer=LifecycleEventObserver {_,event -> if(event==Lifecycle.Event.ON_STOP) controller.close()}
        lifecycle.lifecycle.addObserver(observer)
        onDispose {lifecycle.lifecycle.removeObserver(observer);controller.close()}
    }
    var holding by remember {mutableStateOf(false)}
    val holdStart=permissionAction(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",vm::fail) {if(holding) controller.start()}
    val start=permissionAction(Manifest.permission.RECORD_AUDIO,"mobile.permissions.microphone",vm::fail,controller::start)
    DictationLayout(content={
        ScreenTitle("mobile.tabs.dictate")
        if(checking) {Text(tr("common.loading"));LinearProgressIndicator()}
        readinessError?.let {error ->
            ErrorMessage(error.key,error.params)
        }
        if(!checking && readinessError==null && !ready) {
            val (key,params)=(readiness ?: Readiness(false)).guidance()
            Row(verticalAlignment=androidx.compose.ui.Alignment.CenterVertically) {
                Text(tr("mobile.simple.models_error"),Modifier.weight(1f),maxLines=1)
                Info("mobile.simple.info",key,params=params)
            }
        }
        if(!ready&&!checking) Secondary("mobile.simple.retry") {scope.launch {check()}}
        DictationTranscript(transcript)
    }, controls={
        DictationControls(transcript,active,ready,
            copy={context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText(null,transcript))},
            share={shareText(context,transcript)},
            tap={holding=false;if(active) controller.finish() else start()},hold={holding=true;holdStart()},
            release={if(holding) {holding=false;controller.finish()}})
    })
}

@Composable fun DictateScreen(vm: StudioViewModel,onHistory: ()->Unit = {}) {
    Column {
    Secondary("transcriptions.title",onClick=onHistory)
    com.voicestudio.mobile.features.models.TaskLocation(vm,"dictate",setOf("asr-stream")) {location,model ->
        if(location==com.voicestudio.mobile.local.RunLocation.COMPUTER) ComputerDictateScreen(vm)
        else {
            val phone by vm.phone.state.collectAsState()
            PhoneDictateScreen(vm,model,location==com.voicestudio.mobile.local.RunLocation.PHONE&&model?.let {phone.canRun(it)}==true)
        }
    }
}
}
