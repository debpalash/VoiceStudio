package com.voicestudio.mobile.features.models

import android.text.format.Formatter
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.local.*

@Composable fun ModelsScreen(vm: StudioViewModel) {
    val state by vm.phone.state.collectAsState()
    val preferences by vm.preferences.collectAsState()
    Screen {
        ScreenTitle("mobile.local.models")
        Text(tr("mobile.local.storage_used",mapOf("size" to Formatter.formatFileSize(LocalContext.current,state.used))))
        Toggle("mobile.simple.mobile_data",preferences.allowMobileModels) {allowed -> vm.preference {it.copy(allowMobileModels=allowed)}}
        if(state.loading) InlineLoading()
        state.models.forEach {model -> ModelCard(vm,model)}
    }
}
@OptIn(ExperimentalMaterial3Api::class)
@Composable fun ModelCard(vm: StudioViewModel, model: PhoneModel) {
    val state by vm.phone.state.collectAsState()
    val progress by vm.phone.downloads.progress.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val context=LocalContext.current
    var terms by remember(model.id,model.licence.termsFingerprint) {mutableStateOf(false)}
    var downloadAfterAccept by remember(model.id) {mutableStateOf(false)}
    val installed=model.id in state.installed
    val fits=model.fits(vm.phone.ramBytes)
    val downloading=progress[model.id]?.running==true
    Group {
        Column(Modifier.fillMaxWidth().padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Text(model.name,style=MaterialTheme.typography.titleMedium)
            Text(Formatter.formatFileSize(context,model.totalBytes))
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Text(tr("mobile.simple.${model.capability}"),Modifier.weight(1f),style=MaterialTheme.typography.labelSmall)
                IconAction("mobile.local.review_licence",Icons.Default.Info) {downloadAfterAccept=false;terms=true}
            }
            if(!fits) Text(tr("mobile.simple.ram",mapOf("ram" to model.minRamGb)),color=role("textMuted"))
            progress[model.id]?.let {p ->
                if(p.running) {
                    LinearProgressIndicator(progress={if(p.total>0) (p.received.toFloat()/p.total).coerceIn(0f,1f) else 0f},modifier=Modifier.fillMaxWidth())
                    Text(tr("mobile.local.transfer",mapOf("received" to Formatter.formatFileSize(context,p.received),"total" to Formatter.formatFileSize(context,p.total))))
                }
                p.error?.let {ErrorMessage(it,mapOf("ram" to model.minRamGb))}
            }
            if(downloading) Secondary("common.cancel") {vm.phone.cancelDownload(model)}
            else if(!installed) Secondary("mobile.local.download",fits) {
                try {if(!vm.phone.licence.accepted(model)) {downloadAfterAccept=true;terms=true} else vm.phone.download(model,preferences.allowMobileModels)} catch(e:Exception) {vm.fail(e)}
            } else Secondary("common.delete") {vm.phone.delete(model)}
        }
    }
    if(terms) ModelLicenceSheet(vm,model,onDismiss={terms=false}) {
        terms=false
        if(downloadAfterAccept) vm.phone.download(model,preferences.allowMobileModels)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun ModelLicenceSheet(vm: StudioViewModel, model: PhoneModel, onDismiss: ()->Unit, accepted: ()->Unit) {
    val state by vm.phone.state.collectAsState()
    val progress by vm.phone.downloads.progress.collectAsState()
    val context=LocalContext.current
    val installed=model.id in state.installed
    val downloading=progress[model.id]?.running==true
    var checked by remember(model.id,model.licence.termsFingerprint) {mutableStateOf(false)}
    var saving by remember(model.id) {mutableStateOf(false)}
    ModalBottomSheet(containerColor=role("surfaceOverlay"),onDismissRequest={if(!saving) onDismiss()}) {
        Column(Modifier.fillMaxWidth().heightIn(max=(LocalConfiguration.current.screenHeightDp*.65f).dp).padding(VSTheme.inset).verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            val review=model.licence.review
            val uri=androidx.compose.ui.platform.LocalUriHandler.current
            var showTerms by remember(model.id) {mutableStateOf(false)}
            Text(model.name,style=MaterialTheme.typography.titleLarge)
            Text(tr("mobile.local.category.${model.licence.category}"),style=MaterialTheme.typography.titleSmall,color=role(if(model.licence.category=="commercial-ok") "text" else "textMuted"))
            review.rights.forEach {(key,status) ->
                Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween) {
                    Text(tr("mobile.local.right_$key"))
                    Text(tr("mobile.local.right_$status"),color=role(if(status=="restricted") "error" else "textMuted"))
                }
            }
            review.notes?.let {Text(it,style=MaterialTheme.typography.bodyMedium)}
            if(model.licence.credit.isNotEmpty()) Text(model.licence.credit,style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
            review.evidenceUrl?.let {url -> TextButton(onClick={uri.openUri(url)}) {Text(tr("mobile.local.licence_view"))}}
            review.sourceUrl?.let {url -> TextButton(onClick={uri.openUri(url)}) {Text(tr("mobile.local.licence_source"))}}
            Secondary("mobile.local.exact_terms") {showTerms=!showTerms}
            if(showTerms) {
                Text(model.languages.joinToString(", "))
                Text(tr("mobile.local.storage_used",mapOf("size" to Formatter.formatFileSize(context,state.storage[model.id] ?: 0))))
                Text(tr("mobile.local.licence_label",mapOf("licence" to model.licence.id,"category" to model.licence.category)))
                if(model.licence.rightsHolder!=model.licence.credit) Text(model.licence.rightsHolder)
                androidx.compose.foundation.text.selection.SelectionContainer {
                    Column(verticalArrangement=Arrangement.spacedBy(VSTheme.gap / 2)) {
                        model.licence.termLines.forEach {line ->
                            Text(line.label,style=MaterialTheme.typography.labelSmall,color=role("textMuted"))
                            Text(line.value,style=MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                Text(tr("mobile.local.terms_fingerprint",mapOf("fingerprint" to model.licence.termsFingerprint)),style=MaterialTheme.typography.bodySmall)
                state.receipts[model.id].orEmpty().asReversed().forEach {receipt ->
                    Text(tr("mobile.local.receipt",mapOf("action" to tr(if(receipt.accepted) "mobile.local.accepted" else "mobile.local.not_accepted"),"time" to java.text.DateFormat.getDateTimeInstance().format(java.util.Date(receipt.time)),"version" to receipt.appVersion,"fingerprint" to receipt.fingerprint)),style=MaterialTheme.typography.bodySmall)
                }
            }
            Text(tr("mobile.local.licensor_notice"))
            if(!installed&&!downloading&&(state.storage[model.id] ?: 0)>0) Secondary("common.delete") {vm.phone.delete(model);onDismiss()}
            ConsentCheck("mobile.local.licence_confirm",checked) {checked=it}
            Secondary("mobile.local.withdraw",!saving) {vm.phone.accept(model,false,model.licence.termsFingerprint);onDismiss()}
        }
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceEvenly) {
            TextButton(enabled=!saving,onClick=onDismiss) {Text(tr("mobile.simple.decline"))}
            TextButton(enabled=checked&&!saving,onClick={saving=true;vm.phone.accept(model,true,model.licence.termsFingerprint) {saved -> saving=false;if(saved) accepted()}}) {Text(tr("mobile.simple.accept"))}
        }
    }
}

@Composable fun TaskLocation(vm: StudioViewModel, task: String, capabilities: Set<String>, dependencies: Set<String> = emptySet(), initialLocation: String? = null, content: @Composable (RunLocation,PhoneModel?)->Unit) {
    val host by vm.state.collectAsState()
    val phone by vm.phone.state.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val choices=phone.models.filter {it.capability in capabilities}
    val selected=choices.firstOrNull {it.id==preferences.phoneModels[task]} ?: choices.firstOrNull {it.id in phone.installed&&it.fits(vm.phone.ramBytes)} ?: choices.firstOrNull()
    val ready=selected?.let {it.id in phone.installed&&it.fits(vm.phone.ramBytes)}==true&&dependencies.all {cap -> phone.models.any {it.capability==cap&&it.id in phone.installed&&it.fits(vm.phone.ramBytes)}}
    var entryLocation by rememberSaveable(initialLocation) {mutableStateOf(initialLocation)}
    val location=routeTask(entryLocation ?: preferences.runLocations[task],ready,host.connected)
    Column(Modifier.fillMaxSize()) {
        if(host.connected) Row(Modifier.fillMaxWidth().padding(horizontal=VSTheme.inset),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                if(LocalDensity.current.fontScale<=1.5f) Text(tr("mobile.local.run_on"),style=MaterialTheme.typography.labelSmall)
                SingleChoiceSegmentedButtonRow(Modifier.weight(1f)) {
                    listOf("phone" to "mobile.local.this_phone","computer" to "mobile.simple.computer").forEachIndexed {index,(id,key) ->
                        SegmentedButton(selected=(location==RunLocation.COMPUTER)==(id=="computer"),onClick={entryLocation=id;vm.preference {it.copy(runLocations=it.runLocations+(task to id))};vm.phone.cancelInference();if(task=="clone"&&(location==RunLocation.COMPUTER)!=(id=="computer")) vm.cancelClone()},shape=SegmentedButtonDefaults.itemShape(index,2)) {Text(tr(key),maxLines=1,overflow=TextOverflow.Ellipsis)}
                    }
                }
        }
        Box(Modifier.weight(1f)) {content(location,selected)}
    }
}

@Composable fun TaskModelPicker(vm: StudioViewModel, task: String, capabilities: Set<String>, selected: PhoneModel?, compact: Boolean = false, key: String = task) {
    val phone by vm.phone.state.collectAsState()
    ChoicePicker("mobile.local.model",selected?.id.orEmpty(),phone.models.filter {it.capability in capabilities}.map {
        Choice(it.id,it.name,enabled=it.fits(vm.phone.ramBytes),supporting=if(it.fits(vm.phone.ramBytes)) tr("mobile.simple.${it.capability}") else tr("mobile.local.ram_required",mapOf("ram" to it.minRamGb)))
    },compact=compact) {id -> vm.phone.cancelInference();vm.preference {it.copy(phoneModels=it.phoneModels+(key to id))}}
}

/** One explicit Get action starts the dependency list; each gated model keeps its own sheet. */
@Composable fun MissingModelsCard(vm: StudioViewModel, feature: String, models: List<PhoneModel>, prominent: Boolean = false) {
    val state by vm.phone.state.collectAsState()
    val progress by vm.phone.downloads.progress.collectAsState()
    val preferences by vm.preferences.collectAsState()
    val context=LocalContext.current
    val needed=models.distinctBy {it.id}.filterNot {state.canRun(it,vm.phone.ramBytes)}
    var queue by rememberSaveable {mutableStateOf(emptyList<String>())}
    var showModels by rememberSaveable {mutableStateOf(false)}
    if(needed.isEmpty()) return
    val pending=state.models.firstOrNull {it.id==queue.firstOrNull()}
    LaunchedEffect(queue,state.receipts) {
        if(pending!=null && state.accepted(pending)) {
            vm.phone.download(pending,preferences.allowMobileModels)
            queue=queue.drop(1)
        }
    }
    val size=Formatter.formatFileSize(context,needed.sumOf {it.totalBytes})
    val label=if(needed.size==1) tr("mobile.simple.download_one",mapOf("name" to needed.single().name,"size" to size))
        else tr("mobile.simple.downloads",mapOf("feature" to tr(feature),"count" to needed.size,"size" to size))
    val running=needed.any {progress[it.id]?.running==true}
    Group {
        Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                IconAction("mobile.simple.download_models",VSIcons.Download) {showModels=true}
                Column(Modifier.weight(1f)) {
                    Text(label,style=MaterialTheme.typography.labelLarge)
                    if(needed.size==1) Text(tr("mobile.simple.${needed.single().capability}"),style=MaterialTheme.typography.labelSmall,color=role("textMuted"))
                }
                if(!prominent) {
                    if(running) IconAction("common.cancel",Icons.Default.Close) {queue=emptyList();needed.forEach(vm.phone::cancelDownload)}
                    else TextButton(enabled=queue.isEmpty()&&needed.all {it.fits(vm.phone.ramBytes)},onClick={queue=needed.map {it.id}}) {Text(tr("mobile.local.download"))}
                }
            }
            needed.firstOrNull {!it.fits(vm.phone.ramBytes)}?.let {Text(tr("mobile.simple.ram",mapOf("ram" to it.minRamGb)),color=role("textMuted"))}
            if(running) {
                val total=needed.sumOf {progress[it.id]?.total ?: it.totalBytes}
                val received=needed.sumOf {progress[it.id]?.received ?: 0L}
                LinearProgressIndicator(progress={(received.toFloat()/total.coerceAtLeast(1)).coerceIn(0f,1f)},modifier=Modifier.fillMaxWidth())
            }
            if(prominent) {
                Button(modifier=Modifier.fillMaxWidth(),enabled=running||(queue.isEmpty()&&needed.all {it.fits(vm.phone.ramBytes)}),onClick={
                    if(running) {queue=emptyList();needed.forEach(vm.phone::cancelDownload)} else queue=needed.map {it.id}
                }) {Text(tr(if(running) "common.cancel" else "mobile.local.download"))}
            }
        }
    }
    if(pending!=null&&!state.accepted(pending)) ModelLicenceSheet(vm,pending,onDismiss={queue=emptyList()}) {
        // Receipt refresh advances the queue only after the exact terms have been saved.
    }
    if(showModels) ModelsDialog(vm) {showModels=false}
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun ModelsDialog(vm: StudioViewModel, dismiss: ()->Unit) {
    ModalBottomSheet(onDismissRequest=dismiss,containerColor=role("surfaceOverlay")) {
        Box(Modifier.fillMaxWidth().fillMaxHeight(.9f)) {
            CompositionLocalProvider(LocalScreenTitleProvided provides false) {ModelsScreen(vm)}
        }
    }
}
