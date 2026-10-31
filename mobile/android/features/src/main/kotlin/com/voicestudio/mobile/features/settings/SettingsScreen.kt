package com.voicestudio.mobile.features.settings

import android.Manifest
import androidx.compose.ui.Modifier
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.l10n.L10n

@Composable fun SettingsScreen(vm:StudioViewModel,version:String,onConnect:()->Unit = {},onModels:()->Unit = {},onSupport:(String)->Unit = {}) {
    val prefs by vm.preferences.collectAsState()
    val state by vm.state.collectAsState()
    val context=LocalContext.current
    val l=LocalL10n.current
    var editUrl by remember {mutableStateOf<String?>(null)}
    var editName by remember {mutableStateOf("")}
    var licenses by remember {mutableStateOf(false)}
    val notification=permissionAction(Manifest.permission.POST_NOTIFICATIONS,"mobile.settings.notifications_help",vm::fail) {vm.preference {it.copy(notifications=true)}}
    Screen {
        ScreenTitle("mobile.tabs.settings")
        Group {
            SettingsLink("mobile.tabs.connect",onClick=onConnect)
            HorizontalDivider(color=role("border"))
            SettingsLink("mobile.local.models",onClick=onModels)
        }
        if(state.connected) {
            Heading("mobile.settings.connection")
            SettingsLink("mobile.settings.connection",prefs.activeUrl.orEmpty(),onConnect)
            SettingsLink("mobile.connect.disconnect",onClick=vm::disconnect)
            prefs.hosts.forEach {host ->
                SettingsLink("mobile.android.host_label",host.label) {editUrl=host.url;editName=host.label}
                if(host.url!=prefs.activeUrl) {
                    Secondary("mobile.connect.connect",!state.busy) {vm.reconnect(host)}
                    Secondary("mobile.connect.forget",!state.busy) {vm.forget(host.url)}
                }
            }
        }
            Heading("mobile.settings.appearance")
            val palettes=paletteChoices(l)
            Group {
            Picker("mobile.settings.mode",prefs.mode,listOf("dark","light","system").map {it to l.t("mobile.settings.mode_$it")}) {value -> vm.preference {it.copy(mode=value)}}
            ChoicePicker("mobile.settings.light_palette",prefs.lightPalette,palettes.map {Choice(it.first,it.second,swatch=VSTheme.roles(it.first,false).getValue("messageAction"))}) {value -> vm.preference {it.copy(lightPalette=value)}}
            ChoicePicker("mobile.settings.dark_palette",prefs.darkPalette,palettes.map {Choice(it.first,it.second,swatch=VSTheme.roles(it.first,true).getValue("messageAction"))}) {value -> vm.preference {it.copy(darkPalette=value)}}
            }

            Group {
            Picker("mobile.settings.language",prefs.locale,listOf("system" to l.t("mobile.settings.mode_system"))+L10n.languages.map {it to languageName(it,it)}) {value -> vm.preference {it.copy(locale=value)}}
            HorizontalDivider(color=role("border"))
            Toggle("mobile.settings.notifications",prefs.notifications) {enabled -> if(enabled&&Build.VERSION.SDK_INT>=33) notification() else vm.preference {it.copy(notifications=enabled)}}
            }
        Heading("mobile.settings.privacy")
        Group {
            Info("mobile.simple.privacy","mobile.local.privacy",row=true)
            HorizontalDivider(color=role("border"))
            Toggle("mobile.simple.analytics",prefs.consent==true) {vm.consent(it)}
        }
        Heading("mobile.settings.about")
        SupportSettingsLinks(onSupport)
        Group {
            SettingsLink("mobile.android.app_name",version) {licenses=true}
            HorizontalDivider(color=role("border"))
            SettingsLink("mobile.settings.licenses") {licenses=true}
        }
        if(state.connected) SettingsLink("mobile.android.model_licenses") {context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(vm.api!!.address.baseUrl.toString())))}
    }
    if(editUrl!=null) AlertDialog(onDismissRequest={editUrl=null},text={Field("mobile.android.host_label",editName,{editName=it})},confirmButton={TextButton(onClick={val url=editUrl;vm.preference {it.copy(hosts=it.hosts.map {h -> if(h.url==url) h.copy(label=editName) else h})};editUrl=null}) {Text(tr("common.save"))}},dismissButton={TextButton(onClick={editUrl=null}) {Text(tr("common.cancel"))}})
    if(licenses) AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest={licenses=false},title={Text(tr("mobile.settings.licenses"))},text={
        val content=remember {context.assets.list("licenses").orEmpty().filter {it.endsWith(".txt")}.sorted().joinToString("\n\n") {name -> context.assets.open("licenses/$name").bufferedReader().use {it.readText()}}}
        Text(content,Modifier.verticalScroll(rememberScrollState()))
    },confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={licenses=false}) {Text(tr("common.close"))}})
}
