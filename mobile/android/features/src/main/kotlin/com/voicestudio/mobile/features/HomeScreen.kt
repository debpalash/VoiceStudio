package com.voicestudio.mobile.features

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private data class HomeDestination(val route: String, val title: String, val detail: String,
    val icon: androidx.compose.ui.graphics.vector.ImageVector, val primary: Boolean = false)
private val destinations = listOf(
    HomeDestination("speak", "mobile.tabs.speak", "mobile.speak.placeholder", VSIcons.Wave, true),
    HomeDestination("voices", "nav.clone", "clone.reference_hint", Icons.Default.Person, true),
    HomeDestination("dub", "nav.dub", "homeUi.dub", VSIcons.Video, true),
    HomeDestination("dictate", "nav.dictation", "mobile.dictate.tap_to_start", VSIcons.Mic),
    HomeDestination("models", "mobile.local.models", "mobile.local.installed", VSIcons.Download),
    HomeDestination("connect", "mobile.local.my_computer", "mobile.local.pair_settings", VSIcons.Computer),
    HomeDestination("transcriptions", "transcriptions.title", "mobile.transcripts.hint", Icons.AutoMirrored.Filled.List),
    HomeDestination("handoff", "mobile.handoff.title", "mobile.handoff.hint", VSIcons.Folder),
    HomeDestination("design", "nav.design", "clone.design_placeholder", Icons.Default.Create),
    HomeDestination("stories", "nav.stories", "stories.subtitle", Icons.Default.Person),
    HomeDestination("audiobook", "audiobook.title", "audiobook.subtitle", VSIcons.Folder),
    HomeDestination("batch", "batch.title", "mobile.dub.pick_media", Icons.AutoMirrored.Filled.List),
    HomeDestination("gallery", "nav.gallery", "clone.preview_voice", Icons.Default.Person),
    HomeDestination("workflows", "workflows.title", "workflows.heading", Icons.Default.Share),
    HomeDestination("calls", "calls.title", "mobile.calls.hint", Icons.Default.Call),
    HomeDestination("local-api", "mobile.host.title", "mobile.host.hint", Icons.Default.Settings),
    HomeDestination("tools", "tools.title", "compare.title", Icons.Default.Settings),
)

@Composable fun HomeScreen(vm: StudioViewModel, navigate: (String)->Unit) {
    val phone by vm.phone.state.collectAsState()
    val host by vm.state.collectAsState()
    val context = LocalContext.current
    val artwork by produceState<ImageBitmap?>(null, context) {
        value = withContext(Dispatchers.IO) { context.assets.open("signal-field.webp").use { BitmapFactory.decodeStream(it)?.asImageBitmap() } }
    }
    var audioSource by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        try { vm.phone.refresh() } catch(e: kotlinx.coroutines.CancellationException) {throw e} catch(e: Exception) {vm.fail(e)}
    }
    Screen {
        Column(Modifier.widthIn(max=900.dp).fillMaxWidth().align(Alignment.CenterHorizontally), verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            if(!LocalScreenTitleProvided.current) Row(Modifier.fillMaxWidth(), verticalAlignment=Alignment.CenterVertically) {
                Text(tr("nav.home"), Modifier.weight(1f).semantics { heading() }, style=MaterialTheme.typography.titleMedium)
                TextButton(onClick={navigate("projects")}) { Icon(VSIcons.Folder,null); Text(tr("projects.title")) }
            }
            Surface(shape=MaterialTheme.shapes.large, color=role("canvas")) {
                Box {
                    artwork?.let { Image(it,null,Modifier.matchParentSize(),contentScale=ContentScale.Crop,alpha=.25f) }
                    HomeHero(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=8.dp))
                }
            }
            BoxWithConstraints {
                val columns = if(maxWidth >= 326.dp * LocalDensity.current.fontScale) 2 else 1
                Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    destinations.chunked(columns).forEach { group ->
                        Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                            group.forEach { item -> HomeCard(item,Modifier.weight(1f)) {navigate(if(item.route=="voices") "clone" else item.route)} }
                        }
                    }
                }
            }
            HomeSection("projects.generated_audio") {
                if(phone.recent.isEmpty()&&host.history.isEmpty()) HomeEmpty("mobile.speak.empty_recent")
                phone.recent.take(4).forEach { item ->
                    HomeRow(item.text,tr("mobile.local.this_phone"),Icons.Default.PlayArrow,Modifier.testTag("home.audio.${item.id}")) {
                        audioSource="phone";vm.phone.play(item)
                    }
                }
                host.history.take(4).forEach {item ->
                    HomeRow(item.text,tr("mobile.local.my_computer"),Icons.Default.PlayArrow) {
                        vm.api?.let {audioSource="computer";vm.play(it.audioPath(item.audioPath))}
                    }
                }
                if(audioSource=="phone") phone.audio?.let {LocalAudioPlayer(it,vm::fail)}
                if(audioSource=="computer") vm.api?.let {api -> host.audio?.let {HostPlayer(api,it,vm::fail)} }
            }
            HomeSection("projects.title") {
                if(phone.dubs.isEmpty()&&host.dubHistory.isEmpty()) HomeEmpty("projects.empty")
                phone.dubs.take(4).forEach { record ->
                    HomeRow(record.request.title,tr(if(record.checkpoint.done) "mobile.local.this_phone" else record.checkpoint.stage),VSIcons.Video,Modifier.testTag("home.project.${record.request.id}")) {
                        navigate("project/${record.request.id}")
                    }
                }
                if(host.dubHistory.isNotEmpty()) HomeRow(tr("mobile.dub.history"),tr("mobile.local.my_computer"),VSIcons.Computer) {navigate("host-dubs")}
            }
            HomeSection("clone.saved_profiles") {
                if(phone.voices.isEmpty()&&host.profiles.isEmpty()) HomeEmpty("mobile.simple.empty_voices")
                phone.voices.take(4).forEach {voice ->
                    HomeRow(voice.name,tr("mobile.local.this_phone"),Icons.Default.Person) {
                        vm.preference {it.copy(runLocations=it.runLocations+("speak" to "phone"))}
                        navigate("voice/${voice.id}")
                    }
                }
                host.profiles.take(4).forEach {voice -> HomeRow(voice.name,tr("mobile.local.my_computer"),Icons.Default.Person) {
                    vm.preference {it.copy(runLocations=it.runLocations+("speak" to "computer"))}
                    navigate("host-voice/"+android.net.Uri.encode(voice.id))
                } }
            }
        }
    }
}
@Composable private fun HomeCard(item: HomeDestination, modifier: Modifier, click: ()->Unit) {
    Surface(onClick=click,modifier=modifier.testTag("home.${item.route}"),shape=MaterialTheme.shapes.large,
        color=role(if(item.primary) "surfaceRaised" else "surface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
        Column(Modifier.heightIn(min=if(item.primary) 120.dp else 112.dp).padding(horizontal=12.dp,vertical=8.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically) {
                Surface(shape=androidx.compose.foundation.shape.CircleShape,color=role("messageSurface")) {
                    Icon(item.icon,null,Modifier.size(32.dp).padding(6.dp),tint=role("messageAction"))
                }
                Icon(Icons.AutoMirrored.Filled.ArrowForward,null,tint=role("textMuted"),modifier=Modifier.size(18.dp))
            }
            Column(verticalArrangement=Arrangement.spacedBy(6.dp)) {
                Text(tr(item.title),style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.SemiBold)
                Text(tr(item.detail),style=MaterialTheme.typography.bodySmall,color=role("textMuted"),
                    maxLines=if(androidx.compose.ui.platform.LocalDensity.current.fontScale>1.3f) Int.MAX_VALUE else 2,overflow=TextOverflow.Ellipsis)
            }
        }
    }
}
@Composable private fun HomeSection(title: String, content: @Composable ColumnScope.()->Unit) {
    Column(verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
        Heading(title)
        Surface(Modifier.fillMaxWidth(),shape=MaterialTheme.shapes.large,color=role("surface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
            Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap),content=content)
        }
    }
}
@Composable private fun HomeEmpty(key: String) {Text(tr(key),Modifier.padding(vertical=VSTheme.gap),color=role("textMuted"))}
@Composable private fun HomeRow(title: String, subtitle: String, icon: androidx.compose.ui.graphics.vector.ImageVector, modifier: Modifier=Modifier, click: ()->Unit) {
    Surface(onClick=click,modifier=modifier.fillMaxWidth(),color=role("surface"),shape=MaterialTheme.shapes.small) {
        Row(Modifier.heightIn(min=44.dp),horizontalArrangement=Arrangement.spacedBy(VSTheme.gap),verticalAlignment=Alignment.CenterVertically) {
            Icon(icon,null,Modifier.size(32.dp),tint=role("messageAction"))
            Column(Modifier.weight(1f)) {
                Text(title,maxLines=2,overflow=TextOverflow.Ellipsis)
                Text(subtitle,style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
            }
            Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(18.dp),tint=role("textMuted"))
        }
    }
}


/** Keep the wordmark and credits parallel; accessibility text can use the full width. */
@Composable fun HomeHero(modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier.testTag("home.hero")) {
        val stacked = LocalDensity.current.fontScale >= 1.6f || maxWidth < 260.dp
        if(stacked) Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
            HomeHeroText(Modifier.fillMaxWidth())
            Box(Modifier.fillMaxWidth(),contentAlignment=Alignment.CenterEnd) { ContributorStrip() }
        } else Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(12.dp),verticalAlignment=Alignment.CenterVertically) {
            HomeHeroText(Modifier.weight(1f))
            ContributorStrip()
        }
    }
}

@Composable private fun HomeHeroText(modifier: Modifier = Modifier) {
    Column(modifier.testTag("home.hero-text"),verticalArrangement=Arrangement.spacedBy(6.dp)) {
        Text(tr("app.name"),style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.SemiBold,
            modifier=Modifier.semantics {heading()})
        Text(tr("mobile.home.subtitle"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
    }
}
