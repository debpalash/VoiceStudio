package com.voicestudio.mobile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*

private val tabs=listOf("home","voices","dub","dictate","settings")
internal fun isWorkspace(route:String)=route in tabs

@Composable internal fun StudioTopBar(route:String,onBack:()->Unit,onProjects:()->Unit) {
    val title=when {
        route=="home" -> "nav.home"
        route=="transcriptions" -> "transcriptions.title"
        route=="handoff" -> "mobile.handoff.title"
        route=="design"||route.startsWith("community-design/") -> "nav.design"
        route=="stories" -> "nav.stories"
        route=="audiobook" -> "audiobook.title"
        route=="batch" -> "batch.title"
        route=="gallery" -> "nav.gallery"
        route=="workflows" -> "workflows.title"
        route=="calls" -> "calls.title"
        route=="local-api" -> "mobile.host.title"
        route=="tools" -> "tools.title"
        route=="incoming" -> "mobile.integration.received"
        route.startsWith("preset/") -> "mobile.tabs.speak"
        route=="models" -> "mobile.local.models"
        route=="projects"||route.startsWith("project/") -> "projects.title"
        route=="host-dubs" -> "mobile.dub.history"
        route=="speak"||route.startsWith("voice/")||route.startsWith("host-voice/") -> "mobile.tabs.speak"
        route=="clone" -> "mobile.voices.new_clone"
        else -> "mobile.tabs.$route"
    }
    Row(Modifier.fillMaxWidth().statusBarsPadding().heightIn(min=52.dp).padding(horizontal=VSTheme.inset),
        verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
        if(!isWorkspace(route)) IconButton(onClick=onBack) {Icon(Icons.AutoMirrored.Filled.ArrowBack,tr("common.back"))}
        Text(tr(title),Modifier.weight(1f).semantics {heading()},style=MaterialTheme.typography.titleLarge,maxLines=1,overflow=TextOverflow.Ellipsis)
        if(route=="home") TextButton(onClick=onProjects,shape=CircleShape) {
            Icon(VSIcons.Folder,null,Modifier.size(20.dp));Spacer(Modifier.width(6.dp));Text(tr("projects.title"))
        }
    }
}

/** Visible names keep all five destinations discoverable before learning their icons. */
@Composable internal fun StudioDock(workspace:String,navigate:(String)->Unit) {
    Box(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal=VSTheme.inset,vertical=8.dp),contentAlignment=Alignment.Center) {
        Surface(shape=CircleShape,color=role("surfaceOverlay"),border=BorderStroke(VSTheme.borderWidth,role("border")),
            modifier=Modifier.widthIn(max=440.dp).fillMaxWidth()) {
            Row(Modifier.selectableGroup().padding(4.dp),verticalAlignment=Alignment.CenterVertically) {
                tabs.forEach {tab ->
                    val label=tr(if(tab=="home") "nav.home" else "mobile.tabs.$tab")
                    val selected=workspace==tab
                    Column(Modifier.weight(1f).heightIn(min=60.dp).selectable(selected=selected,role=Role.Tab,onClick={navigate(tab)})
                        .semantics {contentDescription=label},horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.Center) {
                        Box(Modifier.size(52.dp,40.dp).background(if(selected) role("messageSurface") else androidx.compose.ui.graphics.Color.Transparent,CircleShape),contentAlignment=Alignment.Center) {
                            Icon(when(tab) {"home" -> Icons.Default.Home;"voices" -> Icons.Default.Person;"dub" -> VSIcons.Video;"dictate" -> VSIcons.Mic;else -> Icons.Default.Settings},null,
                                Modifier.size(24.dp),tint=role(if(selected) "messageAction" else "textMuted"))
                        }
                        Text(label,style=MaterialTheme.typography.labelSmall,maxLines=1,overflow=TextOverflow.Ellipsis,
                            color=role(if(selected) "messageAction" else "textMuted"))
                    }
                }
            }
        }
    }
}
