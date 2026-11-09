package com.voicestudio.mobile.features

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.Job
import java.io.File
import java.util.UUID

private data class HandoffImport(val file: File,val preview: HandoffPreview)
@Composable fun HandoffScreen(vm: StudioViewModel) {
    val context=LocalContext.current;val scope=rememberCoroutineScope()
    val phone by vm.phone.state.collectAsState();val preferences by vm.preferences.collectAsState();val host by vm.state.collectAsState()
    var projects by remember {mutableStateOf(emptyList<ProductionProject>())}
    var remote by remember {mutableStateOf(emptyList<HandoffRemote>())}
    var pending by remember {mutableStateOf<HandoffImport?>(null)}
    var permission by rememberSaveable {mutableStateOf(false)}
    var busy by remember {mutableStateOf(false)};var work by remember {mutableStateOf<Job?>(null)}
    var notice by remember {mutableStateOf<String?>(null)}
    val configuration=listOf("tts","clone").mapNotNull {capability->
        val options=phone.models.filter {it.capability==capability};val key=if(capability=="tts") "production-tts" else "production-clone"
        phone.preferredModel(options,vm.phone.ramBytes,preferences.phoneModels[key])
    }.joinToString("|") {modelPin(it).identity}
    suspend fun refresh() {vm.phone.refresh();projects=vm.phone.production.list()}
    fun run(action: suspend ()->Unit) {
        if(busy) return;busy=true
        work=scope.launch {try {action()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(if(e is ClientFailure) e else ClientFailure("mobile.handoff.failed"))} finally {busy=false;work=null}}
    }
    fun export(project: ProductionProject?=null,dub: String?=null,send: Boolean) {
        val allow=permission;val config=configuration
        run {
            val file=File(context.cacheDir,"handoff-${UUID.randomUUID()}.vsproject")
            if(project!=null) PhoneProjectHandoff.exportProduction(project,vm.phone.production,vm.phone.voices,config,file)
            else if(dub!=null) PhoneProjectHandoff.exportDub(dub,vm.phone.dubs,phone.models,vm.phone.voices,file)
            if(send) {
                try {if(PhoneProjectHandoff.preview(file).voices>0&&!allow) throw ClientFailure("mobile.handoff.voices_required");requireNotNull(vm.api).sendHandoff(file,allow);remote=requireNotNull(vm.api).handoffInbox()} finally {file.delete()}
            } else shareLocalFile(context,file,"application/zip")
        }
    }
    val importer=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri->if(uri!=null) run {
        val file=File(context.cacheDir,"handoff-${UUID.randomUUID()}.vsproject")
        try {
            withContext(Dispatchers.IO) {requireNotNull(context.contentResolver.openInputStream(uri)).use {input->file.outputStream().use {out->val bytes=ByteArray(262144);var total=0L;while(true) {currentCoroutineContext().ensureActive();val n=input.read(bytes);if(n<0) break;total+=n;require(total<=ProjectArchive.MAX_BYTES+8_388_608);out.write(bytes,0,n)}}}}
            permission=false;pending=HandoffImport(file,withContext(Dispatchers.IO) {PhoneProjectHandoff.preview(file)})
        } catch(e:Exception) {file.delete();throw e}
    }}
    LaunchedEffect(Unit) {try {refresh();if(vm.api!=null) remote=vm.api!!.handoffInbox()} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}}
    Screen {
        ScreenTitle("mobile.handoff.title");Text(tr("mobile.handoff.hint"));Text(tr("mobile.handoff.compatibility"),style=MaterialTheme.typography.bodySmall)
        Secondary("mobile.handoff.import",!busy) {importer.launch(arrayOf("application/zip","application/octet-stream","*/*"))}
        Row(Modifier.fillMaxWidth()) {Checkbox(permission,{if(!busy) permission=it});Text(tr("mobile.handoff.permission"),Modifier.weight(1f))}
        if(busy) {CircularProgressIndicator();Secondary("common.cancel") {work?.cancel()}}
        for(project in projects) {
            HeadingText(project.title)
            Secondary("mobile.handoff.export",!busy) {export(project=project,send=false)}
            if(vm.api!=null) Secondary("mobile.handoff.send",!busy) {export(project=project,send=true)}
        }
        for(record in phone.dubs) {
            HeadingText(record.request.title)
            Secondary("mobile.handoff.export",!busy) {export(dub=record.request.id,send=false)}
            if(vm.api!=null) Secondary("mobile.handoff.send",!busy&&permission) {export(dub=record.request.id,send=true)}
        }
        if(vm.api!=null) {
            Heading("mobile.handoff.inbox");Secondary("common.refresh",!busy) {run {remote=requireNotNull(vm.api).handoffInbox()}}
            for(item in remote) {Text(item.title);Secondary("mobile.handoff.receive",!busy) {run {
                val file=File(context.cacheDir,"handoff-${UUID.randomUUID()}.vsproject")
                try {requireNotNull(vm.api).receiveHandoff(item.id,file);permission=false;pending=HandoffImport(file,withContext(Dispatchers.IO) {PhoneProjectHandoff.preview(file)})} catch(e:Exception) {file.delete();throw e}
            }}}
        }
    }
    pending?.let {item->AlertDialog(onDismissRequest={pending=null;item.file.delete()},title={Text(item.preview.title)},text={Column {
        Text(tr("mobile.handoff.compatibility"))
        if(item.preview.voices>0) Row {Checkbox(permission,{permission=it});Text(tr("mobile.handoff.permission"))}
    }},confirmButton={TextButton(enabled=item.preview.voices==0||permission,onClick={
        pending=null;val allow=permission;val config=configuration
        run {try {val result=PhoneProjectHandoff.restore(item.file,vm.phone.production,vm.phone.dubs,vm.phone.voices,phone.models,config,allow);notice=result.title;refresh()} finally {item.file.delete()}}
    }) {Text(tr("mobile.handoff.import"))}},dismissButton={TextButton(onClick={pending=null;item.file.delete()}) {Text(tr("common.cancel"))}})}
    notice?.let {title->AlertDialog(onDismissRequest={notice=null},title={Text(tr("mobile.handoff.imported"))},text={Text(title)},confirmButton={TextButton(onClick={notice=null}) {Text(tr("common.confirm"))}})}
}
@Composable private fun HeadingText(value: String) {Text(value,style=MaterialTheme.typography.titleMedium)}
