package com.voicestudio.mobile.features.settings

import android.graphics.BitmapFactory
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable private data class SupportCatalog(val donations:List<Donation>,val projects:List<SupportProject>)
@Serializable private data class Donation(val id:String,val name:String,val url:String,val detailKey:String)
@Serializable private data class SupportProject(val id:String,val name:String,val url:String,val detailKey:String,val tierKey:String,val artwork:String)

@Composable private fun supportCatalog():SupportCatalog? {
    val context=LocalContext.current
    val catalog by produceState<SupportCatalog?>(null,context) {
        value=withContext(Dispatchers.IO) {
            context.assets.open("support.json").bufferedReader().use {Json.decodeFromString<SupportCatalog>(it.readText())}
        }
    }
    return catalog
}

@Composable fun SupportSettingsLinks(navigate:(String)->Unit) {
    Group {
        listOf("donate" to "mobile.support.donate","pro" to "proPage.title","other-apps" to "mobile.support.other_apps").forEachIndexed {index,(route,key) ->
            if(index>0) HorizontalDivider(color=role("border"))
            Surface(onClick={navigate(route)},color=role("surface"),shape=MaterialTheme.shapes.small,
                modifier=Modifier.fillMaxWidth().heightIn(min=48.dp).testTag("settings.$route")) {
                Row(Modifier.padding(VSTheme.inset),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    Icon(when(route) {"donate"->Icons.Default.Favorite;"pro"->Icons.Default.Star;else->VSIcons.Globe},null,Modifier.size(20.dp),tint=role("messageAction"))
                    Text(tr(key),Modifier.weight(1f))
                    Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(20.dp),tint=role("textMuted"))
                }
            }
        }
    }
}

@Composable private fun SupportPage(title:String,tag:String,content:@Composable ColumnScope.()->Unit) {
    Screen {
        Column(Modifier.widthIn(max=840.dp).fillMaxWidth().align(Alignment.CenterHorizontally).testTag(tag),
            verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            ScreenTitle(title)
            content()
        }
    }
}

@Composable private fun SupportHero(title:String,body:String,icon:ImageVector,note:String?=null) {
    Surface(shape=MaterialTheme.shapes.large,color=role("messageSurface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
        Column(Modifier.fillMaxWidth().padding(24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Icon(icon,null,Modifier.size(32.dp),tint=role("messageAction"))
            Text(tr(title),style=MaterialTheme.typography.headlineSmall,modifier=Modifier.semantics {heading()})
            Text(tr(body),style=MaterialTheme.typography.bodyLarge)
            note?.let {Text(tr(it),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))}
        }
    }
}

@Composable fun DonateScreen() {
    val catalog=supportCatalog()
    val uri=LocalUriHandler.current
    SupportPage("mobile.support.donate","support.donate") {
        SupportHero("donate.hero_title","donate.hero_desc",Icons.Default.Favorite,"mobile.support.gift_note")
        Heading("donate.platforms")
        if(catalog==null) Text(tr("common.loading"))
        catalog?.donations?.forEach {donation ->
            Surface(onClick={uri.openUri(donation.url)},shape=MaterialTheme.shapes.large,color=role("surface"),border=BorderStroke(VSTheme.borderWidth,role("border")),
                modifier=Modifier.fillMaxWidth().heightIn(min=48.dp).testTag("support.donate.${donation.id}")) {
                Row(Modifier.padding(20.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                        Text(donation.name,style=MaterialTheme.typography.titleMedium)
                        Text(tr(donation.detailKey),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
                    }
                    Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(24.dp),tint=role("messageAction"))
                }
            }
        }
        Text(tr("donate.footer"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
        Heading("support.other_ways")
        Group {
            listOf(Triple("star","support.star_github","https://github.com/debpalash/VoiceStudio"),
                Triple("discord","support.join_discord","https://discord.gg/bzQavDfVV9")).forEachIndexed {index,(id,key,url) ->
                if(index>0) HorizontalDivider(color=role("border"))
                TextButton(onClick={uri.openUri(url)},shape=MaterialTheme.shapes.small,
                    modifier=Modifier.fillMaxWidth().heightIn(min=48.dp).testTag("support.donate.$id")) {
                    Icon(if(id=="star") Icons.Default.Star else VSIcons.Globe,null,Modifier.size(20.dp))
                    Spacer(Modifier.width(VSTheme.gap))
                    Text(tr(key),Modifier.weight(1f))
                    Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(20.dp))
                }
            }
        }
    }
}

@Composable fun ProScreen() {
    SupportPage("proPage.title","support.pro") {
        SupportHero("proPage.title","mobile.support.pro_scope",Icons.Default.Star)
        Surface(shape=MaterialTheme.shapes.large,color=role("surface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
            Column(Modifier.fillMaxWidth().padding(24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Heading("proPage.free_forever")
                Text(tr("proPage.free_body"),style=MaterialTheme.typography.bodyLarge)
                Text(tr("proPage.free_note"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
            }
        }
        Heading("proPage.features_title")
        AdaptiveSupportCards(listOf("automation","history","delivery","preflight")) {feature,modifier ->
            Surface(modifier.testTag("support.pro.$feature"),shape=MaterialTheme.shapes.large,color=role("surface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
                Column(Modifier.fillMaxWidth().padding(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                    Icon(when(feature) {"automation"->Icons.Default.Refresh;"history"->Icons.Default.DateRange;"delivery"->VSIcons.Folder;else->Icons.Default.CheckCircle},null,Modifier.size(24.dp),tint=role("messageAction"))
                    Heading("proPage.${feature}_title")
                    Text(tr("proPage.${feature}_body"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
                }
            }
        }
        Text(tr("proPage.commercial_body"),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
    }
}

@Composable fun OtherAppsScreen() {
    val catalog=supportCatalog()
    val uri=LocalUriHandler.current
    SupportPage("mobile.support.other_apps","support.other-apps") {
        Text(tr("mobile.support.other_apps_intro"),style=MaterialTheme.typography.bodyLarge)
        if(catalog==null) Text(tr("common.loading"))
        catalog?.projects?.let {projects -> AdaptiveSupportCards(projects) {project,modifier ->
            Surface(onClick={uri.openUri(project.url)},modifier=modifier.heightIn(min=48.dp).testTag("support.project.${project.id}"),
                shape=MaterialTheme.shapes.large,color=role("surface"),border=BorderStroke(VSTheme.borderWidth,role("border"))) {
                Column {
                    SupportArtwork(project)
                    Column(Modifier.padding(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                        Text(tr(project.tierKey),style=MaterialTheme.typography.labelMedium,color=role("messageAction"))
                        Text(project.name,style=MaterialTheme.typography.titleLarge,modifier=Modifier.semantics {heading()})
                        Text(tr(project.detailKey),style=MaterialTheme.typography.bodyMedium,color=role("textMuted"))
                        Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            Text(tr("common.open"),style=MaterialTheme.typography.labelLarge,color=role("messageAction"))
                            Icon(Icons.AutoMirrored.Filled.ArrowForward,null,Modifier.size(20.dp),tint=role("messageAction"))
                        }
                    }
                }
            }
        }}
    }
}

@Composable private fun <T> AdaptiveSupportCards(items:List<T>,card:@Composable (T,Modifier)->Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val columns=if(maxWidth>=600.dp) 2 else 1
        Column(verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            items.chunked(columns).forEach {row ->
                Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                    row.forEach {card(it,Modifier.weight(1f))}
                    if(row.size<columns) Spacer(Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable private fun SupportArtwork(project:SupportProject) {
    val context=LocalContext.current
    val artwork by produceState<ImageBitmap?>(null,context,project.artwork) {
        value=withContext(Dispatchers.IO) {
            context.assets.open("support-artwork/${project.artwork}").use {BitmapFactory.decodeStream(it)?.asImageBitmap()}
        }
    }
    artwork?.let {
        Box(Modifier.fillMaxWidth().padding(start=20.dp,top=20.dp)) {
            Image(it,null,Modifier.size(72.dp).testTag("support.artwork.${project.id}"),contentScale=ContentScale.Fit)
        }
    }
}
