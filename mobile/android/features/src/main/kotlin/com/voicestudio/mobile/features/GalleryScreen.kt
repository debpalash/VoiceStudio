package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.network.CommunityItem
import com.voicestudio.mobile.network.downloadPhoneVoiceBundle
import com.voicestudio.mobile.local.PhoneVoice
import com.voicestudio.mobile.local.PhoneVoiceBundle
import java.io.File
import kotlinx.coroutines.*

@Composable fun GalleryScreen(vm: StudioViewModel,navigate: (String)->Unit) {
    val phone by vm.phone.state.collectAsState()
    val host by vm.state.collectAsState()
    val scope=rememberCoroutineScope()
    val context=LocalContext.current
    var location by rememberSaveable {mutableStateOf("community")}
    var query by rememberSaveable {mutableStateOf("")}
    var items by remember(host.connectionId) {mutableStateOf(emptyList<CommunityItem>())}
    var total by remember(host.connectionId) {mutableStateOf(0)}
    var loading by remember {mutableStateOf(false)}
    var imported by rememberSaveable(host.connectionId) {mutableStateOf(mapOf<String,String>())}
    var copyTarget by remember(host.connectionId) {mutableStateOf<CommunityItem?>(null)}
    var copied by remember(host.connectionId) {mutableStateOf<PhoneVoice?>(null)}
    var copying by remember {mutableStateOf(false)}
    var copyJob by remember {mutableStateOf<Job?>(null)}
    var audio by remember(host.connectionId) {mutableStateOf<String?>(null)}
    suspend fun load() {
        val api=vm.api ?: return
        if(loading) return
        loading=true
        try {
            val page=api.community(items.size)
            if(vm.api===api) {items=items+page.items;total=page.total}
        } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {loading=false}
    }
    LaunchedEffect(location,host.connectionId) {if(location=="computer"&&items.isEmpty()) load() else vm.phone.refresh()}
    Screen {
        ScreenTitle("nav.gallery")
        ChoicePicker("nav.gallery",location,listOf(Choice("community",tr("gallery.zone_community")),Choice("phone",tr("mobile.local.this_phone")),Choice("computer",tr("mobile.local.my_computer")))) {location=it}
        Field("mobile.voices.search",query,{query=it})
        if(location=="community") {
            CommunityGalleryContent(vm,query,navigate)
        } else if(location=="phone") {
            val presets=phone.models.filter {it.capability=="tts"}.map {model ->
                val speakers=model.option("num_speakers","1").toIntOrNull()?.coerceIn(1,128) ?: 1
                val matches=(0 until speakers).filter {speaker ->
                    query.isBlank()||model.name.contains(query.trim(),true)||tr("mobile.local.preset_voice",mapOf("number" to speaker+1)).contains(query.trim(),true)
                }
                model to matches
            }.filter {it.second.isNotEmpty()}
            Text(tr("gallery.all_voices",mapOf("count" to presets.sumOf {it.second.size})),style=MaterialTheme.typography.titleMedium)
            if(presets.isEmpty()) SearchEmpty(query)
            presets.forEach {(model,speakers) ->
                Text(model.name,style=MaterialTheme.typography.titleMedium)
                if(phone.canRun(model)) Text(tr("mobile.ios.status_ready"),color=role("textMuted"))
                else com.voicestudio.mobile.features.models.MissingModelsCard(vm,"nav.gallery",listOf(model))
                speakers.forEach {speaker ->
                    TextButton(modifier=Modifier.testTag("gallery.preset.${model.id}.$speaker"),onClick={vm.run {
                        vm.store.update {it.copy(phoneModels=it.phoneModels+("speak" to model.id),runLocations=it.runLocations+("speak" to "phone"))}
                        navigate("preset/"+android.net.Uri.encode(model.id)+"/$speaker")
                    }}) {Text(tr("mobile.local.preset_voice",mapOf("number" to speaker+1)))}
                }
            }
            Secondary("mobile.local.models") {navigate("models")}
            Secondary("mobile.tabs.voices") {navigate("voices")}
        } else if(vm.api==null) {
            Text(tr("gallery.community_explainer"),color=role("textMuted"))
            Text(tr("mobile.local.pair_settings"),color=role("textMuted"))
            Secondary("mobile.simple.reconnect") {navigate("connect")}
        }
        else {
            Text(tr("gallery.community_explainer"),color=role("textMuted"))
            items.filter {query.isBlank()||it.name.contains(query,true)}.forEach {item ->
                Group {Column(Modifier.padding(VSTheme.inset)) {
                    Text(item.name,style=MaterialTheme.typography.titleMedium)
                    Text(listOfNotNull(item.author,item.license,item.language).joinToString(" · "),style=MaterialTheme.typography.bodySmall)
                    Secondary("common.play") {audio="/community/items/${com.voicestudio.mobile.network.VoiceStudioApi.segment(item.id)}/preview"}
                    Secondary("gallery.use_voice",!host.busy) {vm.run {
                        val api=vm.api ?: return@run
                        val saved=api.useCommunity(item.id)
                        if(vm.api===api) {imported=imported+(item.id to saved.profile_id);vm.refreshVoiceLibrary();navigate("host-voice/"+android.net.Uri.encode(saved.profile_id))}
                    }}
                    if(item.id in imported) Secondary("mobile.galleryCopy.action",!copying) {copyTarget=item}
                }}
            }
            Text(tr("mobile.galleryCopy.requires_host"))
            if(copying) {InlineLoading();Secondary("common.cancel") {copyJob?.cancel()}}
            copied?.let {voice->Secondary("mobile.galleryCopy.ready") {navigate("voice/"+android.net.Uri.encode(voice.id))}}
            if(items.isEmpty()&&!loading) EmptyState("gallery.community_empty")
            if(items.size<total) Secondary("common.more_info",!loading) {scope.launch {load()}}
            if(loading) InlineLoading()
            audio?.let {path ->vm.api?.let {HostPlayer(it,path,vm::fail)}}
        }
    }
    DisposableEffect(host.connectionId) {onDispose {copyJob?.cancel()}}
    copyTarget?.let {item ->AlertDialog(onDismissRequest={copyTarget=null},title={Text(tr("mobile.galleryCopy.action"))},text={Text(tr("mobile.galleryCopy.confirm")+"\n"+listOfNotNull(item.author,item.license).joinToString(" · "))},
        confirmButton={TextButton(onClick={
            copyTarget=null;val api=vm.api;val profile=imported[item.id]
            if(api!=null&&profile!=null&&!copying) {
                copying=true;copyJob=scope.launch {try {
                    val file=File.createTempFile("voice-copy-",".omnivoice",context.cacheDir)
                    try {
                        api.downloadPhoneVoiceBundle(profile,file)
                        if(vm.api!==api) throw CancellationException()
                        val attribution=mapOf("source" to "community","id" to item.id,"name" to item.name)+listOfNotNull(item.author?.let {"author" to it},item.license?.let {"license" to it},item.language?.let {"language" to it}).toMap()
                        copied=PhoneVoiceBundle.install(file,vm.phone.voices,attribution,true);vm.phone.refresh()
                    } finally {file.delete()}
                } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {copying=false;copyJob=null}}
            }
        }) {Text(tr("mobile.galleryCopy.action"))}},dismissButton={TextButton(onClick={copyTarget=null}) {Text(tr("common.cancel"))}})}

}
