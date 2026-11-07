package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

@Composable internal fun CommunityGalleryContent(vm:StudioViewModel,query:String,navigate:(String)->Unit) {
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    val client=remember {CommunityGallery(File(context.cacheDir,"CommunityGallery"))}
    var catalogue by remember {mutableStateOf<CommunityCatalogue?>(null)}
    var pack by rememberSaveable {mutableStateOf("")}
    var language by rememberSaveable {mutableStateOf("")}
    var loading by remember {mutableStateOf(false)}
    var failed by remember {mutableStateOf(false)}
    var active by remember {mutableStateOf<String?>(null)}
    var work by remember {mutableStateOf<Job?>(null)}
    var playRequest by remember {mutableLongStateOf(0)}
    var audio by remember {mutableStateOf<Pair<String,File>?>(null)}
    var target by remember {mutableStateOf<GalleryEntry?>(null)}
    suspend fun refresh() {
        if(loading) return
        loading=true
        try {catalogue=client.refresh();failed=false} catch(e:CancellationException) {throw e} catch(_:Exception) {failed=true} finally {loading=false}
    }
    LaunchedEffect(Unit) {
        try {
            val seed=withContext(Dispatchers.IO) {context.assets.open("community-gallery.json").use {it.readBytes()}}
            catalogue=client.cached(seed);refresh()
        } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
    }
    DisposableEffect(Unit) {onDispose {work?.cancel()}}
    Text(tr("gallery.community_explainer"),color=role("textMuted"))
    catalogue?.let {index ->
        ChoicePicker("gallery.community_packs",pack,listOf(Choice("",tr("gallery.community_all_packs")))+index.packs.map {Choice(it.id,it.name)}) {pack=it}
        ChoicePicker("gallery.community_language",language,listOf(Choice("",tr("gallery.community_all_languages")))+index.items.map {it.language}.distinct().sorted().map {Choice(it,it)}) {language=it}
    }
    val ids=catalogue?.packs?.firstOrNull {it.id==pack}?.item_ids
    val items=catalogue?.items.orEmpty().filter {item ->
        (ids==null||item.id in ids)&&(language.isEmpty()||item.language==language)&&(query.isBlank()||listOf(item.name,item.language,item.author.orEmpty()).any {it.contains(query.trim(),true)})
    }
    if(loading) InlineLoading()
    if(failed) Secondary("common.retry") {scope.launch {refresh()}}
    Text(tr("gallery.all_voices",mapOf("count" to items.size)),style=MaterialTheme.typography.titleMedium)
    if(catalogue!=null&&items.isEmpty()) SearchEmpty(query)
    items.forEach {item ->
        Group {Column(Modifier.padding(VSTheme.inset)) {
            Text(item.name,style=MaterialTheme.typography.titleMedium)
            Text(listOfNotNull(item.author,item.license,item.language).joinToString(" · "),style=MaterialTheme.typography.bodySmall)
            if(item.type=="preset") {
                Text(tr("gallery.no_preview"),color=role("textMuted"),style=MaterialTheme.typography.bodySmall)
                TextButton(modifier=Modifier.testTag("gallery.community.use.${item.id}"),onClick={navigate("community-design/"+android.net.Uri.encode(Json.encodeToString(item)))}) {Text(tr("gallery.use_voice"))}
            } else {
                Secondary("common.play",active==null) {active=item.id;work=scope.launch {try {audio=item.id to client.recording(item);playRequest++} catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {active=null;work=null}}}
                Secondary("gallery.use_voice",active==null) {target=item}
            }
            audio?.takeIf {it.first==item.id}?.let {LocalAudioPlayer(it.second,autoPlay=true,playRequest=playRequest,failure=vm::fail)}
            if(active==item.id) {InlineLoading();Secondary("common.cancel") {work?.cancel()}}
        }}
    }
    Secondary("common.refresh",!loading) {scope.launch {refresh()}}
    target?.let {item ->AlertDialog(onDismissRequest={target=null},title={Text(tr("mobile.voices.consent_title"))},text={Text(tr("mobile.voices.consent_body")+"\n"+listOfNotNull(item.author,item.license).joinToString(" · "))},
        confirmButton={TextButton(onClick={
            target=null
            if(active==null) {active=item.id;work=scope.launch {try {
                val file=client.recording(item);ensureActive()
                val voice=vm.phone.voices.importCommunity(item,file,true);vm.phone.refresh();navigate("voice/"+android.net.Uri.encode(voice.id))
            } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)} finally {active=null;work=null}}}
        }) {Text(tr("mobile.voices.consent_confirm"))}},dismissButton={TextButton(onClick={target=null}) {Text(tr("common.cancel"))}})}
}
