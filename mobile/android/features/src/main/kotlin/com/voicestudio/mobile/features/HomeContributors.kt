package com.voicestudio.mobile.features

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Star
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlin.math.ceil
import kotlin.math.min

/** Human contributors and portraits mirror Electron's bundled contributor snapshot. */
object HomeContributors {
    val logins = listOf("debpalash", "velixio", "kevin9327", "jaketame", "psiberfunk", "rudycelekli", "paoloantinori", "Chang-Jin-Lee", "swadhinbiswas", "BichengWang", "nirmalchatur", "Shivendra-Coherent", "marreiradigital", "nvtoan0201-swe")
    val coreMaintainers = setOf("debpalash", "velixio")
    const val REPOSITORY = "https://github.com/debpalash/VoiceStudio"
    fun rows(items: List<String>): List<List<String>> {
        val top = min(items.size, ceil((items.size + 3) / 3.0).toInt())
        val middle = min(items.size - top, ceil((items.size - top + 1) / 2.0).toInt())
        return listOf(items.subList(0, top), items.subList(top, top + middle), items.subList(top + middle, items.size)).filter { it.isNotEmpty() }
    }
}

@Serializable private data class MaintainerLink(val kind: String, val handle: String, val href: String)
@Serializable private data class MaintainerCard(val name: String, val role: String, val links: List<MaintainerLink>)

/** Both Home entrypoints use the same local cards and accessible contributor controls. */
@Composable fun ContributorCluster() = ContributorPanel()
@Composable fun ContributorStrip() = ContributorPanel()

@Composable private fun ContributorPanel() {
    val context = LocalContext.current
    val uri = LocalUriHandler.current
    val cards by produceState<Map<String,MaintainerCard>>(emptyMap(), context) {
        value = withContext(Dispatchers.IO) {
            context.assets.open("maintainer-cards.json").bufferedReader().use {
                Json.decodeFromString<Map<String,MaintainerCard>>(it.readText())
            }
        }
    }
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    Column(Modifier.width(112.dp).testTag("home.contributors"), verticalArrangement=Arrangement.spacedBy(4.dp),horizontalAlignment=Alignment.End) {
        Text(tr("mobile.home.brought_by"),style=MaterialTheme.typography.labelSmall,color=role("textMuted"))
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp), verticalAlignment=Alignment.CenterVertically) {
            HomeContributors.logins.filter {it in HomeContributors.coreMaintainers}.forEach {login ->
                Contributor(login, prominent=true, card=cards[login]) { selected=login }
            }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).testTag("home.other-contributors")) {
            (HomeContributors.logins.filterNot {it in HomeContributors.coreMaintainers}+"all").chunked(2).forEach {column ->
                Column {
                    column.forEach {login -> Contributor(login) {
                        uri.openUri(if(login=="all") "${HomeContributors.REPOSITORY}/graphs/contributors" else "https://github.com/$login")
                    } }
                }
            }
        }
    }
    selected?.let {login -> cards[login]?.let {card -> MaintainerSheet(login,card) {selected=null}} }
}

@Composable private fun Contributor(login: String, prominent: Boolean = false, card: MaintainerCard? = null, click: ()->Unit) {
    val all = login == "all"
    val label = when {
        all -> tr("homeUi.allContributors")
        card != null -> "${tr(card.name)} · @$login"
        else -> "GitHub · @$login"
    }
    val maintainerRole = if(prominent) tr(card?.role ?: "homeUi.coreMaintainer") else null
    Box(Modifier.heightIn(min=48.dp).width(48.dp)
        .clip(MaterialTheme.shapes.small)
        .clickable(role=Role.Button,onClickLabel=label,onClick=click)
        .testTag("home.contributor.$login")
        .semantics {contentDescription=label;if(maintainerRole!=null) stateDescription=maintainerRole},contentAlignment=Alignment.Center) {
        if(all) Box(Modifier.clip(CircleShape).background(role("surfaceRaised")).border(1.dp,role("border"),CircleShape)
            .padding(horizontal=8.dp,vertical=4.dp),contentAlignment=Alignment.Center) {
            Text(tr("homeUi.all"),style=MaterialTheme.typography.labelSmall,fontWeight=FontWeight.SemiBold,maxLines=1,overflow=TextOverflow.Ellipsis)
        } else Box(Modifier.size(if(prominent) 44.dp else 22.dp)) {
            ContributorPortrait(login,if(prominent) 44.dp else 22.dp)
            if(prominent) Box(Modifier.align(Alignment.BottomEnd).size(16.dp).clip(CircleShape)
                .background(role("messageAction")).border(1.5.dp,role("canvas"),CircleShape),contentAlignment=Alignment.Center) {
                Icon(Icons.Default.Star,null,Modifier.size(10.dp),tint=role("messageActionForeground"))
            }
        }
    }
}

@Composable private fun ContributorPortrait(login: String, size: Dp) {
    val context = LocalContext.current
    val avatar by produceState<ImageBitmap?>(null, context, login) {
        value = withContext(Dispatchers.IO) {
            val name = context.assets.list("contributors")?.firstOrNull {it.substringBeforeLast('.') == login} ?: return@withContext null
            context.assets.open("contributors/$name").use {BitmapFactory.decodeStream(it)?.asImageBitmap()}
        }
    }
    Box(Modifier.size(size).testTag("home.contributor-avatar.$login").clip(CircleShape).background(role("surfaceRaised"))
        .border(1.dp,role("border"),CircleShape),contentAlignment=Alignment.Center) {
        avatar?.let {Image(it,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)}
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun MaintainerSheet(login: String, card: MaintainerCard, dismiss: ()->Unit) {
    val uri = LocalUriHandler.current
    ModalBottomSheet(onDismissRequest=dismiss,sheetState=rememberModalBottomSheetState(skipPartiallyExpanded=true),
        containerColor=role("surfaceOverlay"),modifier=Modifier.testTag("home.maintainer-card")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal=24.dp).padding(bottom=24.dp),
            verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Surface(shape=MaterialTheme.shapes.large,color=role("messageSurface")) {
                Row(Modifier.fillMaxWidth().padding(16.dp),horizontalArrangement=Arrangement.spacedBy(12.dp),verticalAlignment=Alignment.CenterVertically) {
                    ContributorPortrait(login,64.dp)
                    Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)) {
                        Text(tr(card.name),style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold,modifier=Modifier.semantics {heading()})
                        Text("@$login",style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
                        Row(horizontalArrangement=Arrangement.spacedBy(4.dp),verticalAlignment=Alignment.CenterVertically) {
                            Icon(Icons.Default.Star,null,Modifier.size(16.dp),tint=role("messageAction"))
                            Text(tr(card.role),Modifier.weight(1f),style=MaterialTheme.typography.labelMedium,color=role("textMuted"))
                        }
                    }
                }
            }
            card.links.forEach {link ->
                val label = when(link.kind) {"website"->tr("homeUi.website");"github"->"GitHub";else->"X"}
                Surface(onClick={uri.openUri(link.href)},modifier=Modifier.fillMaxWidth().heightIn(min=56.dp)
                    .testTag("home.maintainer-link.${link.kind}"),shape=MaterialTheme.shapes.medium,color=role("surfaceRaised")) {
                    Row(Modifier.padding(12.dp),horizontalArrangement=Arrangement.spacedBy(12.dp),verticalAlignment=Alignment.CenterVertically) {
                        MaintainerLinkIcon(link.kind)
                        Column(Modifier.weight(1f)) {
                            Text(label,style=MaterialTheme.typography.titleSmall)
                            Text(link.handle,style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
                        }
                        if(link.kind=="website") Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(20.dp))
                        else Text(tr("homeUi.follow"),style=MaterialTheme.typography.labelMedium,color=role("messageAction"))
                    }
                }
            }
            TextButton(onClick=dismiss,modifier=Modifier.fillMaxWidth().heightIn(min=48.dp)) {Text(tr("common.close"))}
        }
    }
}

@Composable private fun MaintainerLinkIcon(kind: String) {
    val context = LocalContext.current
    if(kind=="website") Icon(VSIcons.Globe,null,Modifier.size(24.dp))
    else {
        // The app bundles canonical brand vectors; the feature library does not depend on app R.
        val resource = remember(context,kind) {context.resources.getIdentifier("contributor_$kind","drawable",context.packageName)}
        if(resource!=0) Icon(painterResource(resource),null,Modifier.size(24.dp))
        else Icon(VSIcons.Link,null,Modifier.size(24.dp))
    }
}
