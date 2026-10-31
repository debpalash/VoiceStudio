package com.voicestudio.mobile

import android.app.Application
import android.os.Bundle
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.drawable.Icon
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.SystemBarStyle
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.features.connect.ConnectScreen
import com.voicestudio.mobile.features.speak.SpeakScreen
import com.voicestudio.mobile.features.voices.VoicesScreen
import com.voicestudio.mobile.features.dub.DubScreen
import com.voicestudio.mobile.features.dictate.DictateScreen
import com.voicestudio.mobile.features.settings.SettingsScreen
import com.voicestudio.mobile.network.ContentReportClient
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Locale

class VoiceStudioApplication:Application()
class MainActivity:ComponentActivity() {
    private var incoming by mutableStateOf<Intent?>(null)
    override fun onNewIntent(intent: Intent) {super.onNewIntent(intent);incoming=intent}
    override fun onCreate(savedInstanceState:Bundle?) {
        setTheme(R.style.AppTheme)
        super.onCreate(savedInstanceState);enableEdgeToEdge()
        incoming=intent
        val l=L10n.load(this,Locale.getDefault().toLanguageTag())
        val shortcuts=listOf("speak","dictate").map {route ->
            ShortcutInfo.Builder(this,route).setShortLabel(l.t("mobile.integration.shortcut_$route"))
                .setIcon(Icon.createWithResource(this,android.R.drawable.ic_btn_speak_now))
                .setIntent(Intent(Intent.ACTION_VIEW,Uri.parse("voicestudio://workspace/$route"),this,MainActivity::class.java)).build()
        }
        // Publishing shortcuts writes to disk (@WorkerThread), so keep it off the main thread.
        val manager=getSystemService(ShortcutManager::class.java)
        lifecycleScope.launch(Dispatchers.IO) {manager.dynamicShortcuts=shortcuts}
        setContent {StudioApp(incomingIntent=incoming,onIncomingConsumed={incoming=null})}
    }
}

/** Reports go to VoiceStudio's app API: the dev server for debug builds, production for release. */
fun contentReportClient()=ContentReportClient(BuildConfig.CONTENT_REPORTS_URL,BuildConfig.VERSION_NAME)

@Composable fun StudioApp(vm:StudioViewModel = viewModel(), reportClient:ContentReportClient? = remember {contentReportClient()},incomingIntent: Intent?=null,onIncomingConsumed: ()->Unit={}) {
    val prefs by vm.preferences.collectAsStateWithLifecycle()
    val state by vm.state.collectAsStateWithLifecycle()
    val context=LocalContext.current
    val configuration=LocalConfiguration.current
    val locale=if(prefs.locale=="system") configuration.locales[0].toLanguageTag() else prefs.locale
    val l=remember(locale) {L10n.load(context,locale)}
    val dark=when(prefs.mode) {"light" -> false;"system" -> (configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK)==android.content.res.Configuration.UI_MODE_NIGHT_YES;else -> true}
    val chrome=Color(VSTheme.roles(if(dark) prefs.darkPalette else prefs.lightPalette,dark).getValue("chrome")).toArgb()
    SideEffect {
        (context as? ComponentActivity)?.let {activity ->
            val style=if(dark) SystemBarStyle.dark(chrome) else SystemBarStyle.light(chrome,chrome)
            activity.enableEdgeToEdge(statusBarStyle=style,navigationBarStyle=style)
            activity.window.isNavigationBarContrastEnforced=false
        }
    }
    val direction=if(Locale.forLanguageTag(locale).language=="ar") LayoutDirection.Rtl else LayoutDirection.Ltr
    val snackbars=remember {SnackbarHostState()}
    CompositionLocalProvider(LocalL10n provides l,LocalLayoutDirection provides direction,LocalContentReportClient provides reportClient) {
        VoiceStudioTheme(ThemePreferences(prefs.mode,prefs.lightPalette,prefs.darkPalette)) {
            CompositionLocalProvider(LocalScreenTitleProvided provides true) {
            key(state.connectionId) {
            val initialRoute=remember {if(state.error?.key=="mobile.connect.error_session_expired") "connect" else "home"}
            val nav=rememberNavController()
            var received by remember {mutableStateOf<PhoneIncoming?>(null)}
            LaunchedEffect(incomingIntent,state.loading) {
                if(!state.loading&&incomingIntent!=null) {
                    try {PhoneIncoming.read(context,incomingIntent)?.let {payload ->
                        received=payload
                        nav.navigate(if(payload.text==null&&payload.file==null) payload.route ?: "incoming" else "incoming") {launchSingleTop=true}
                    }} catch(e:Exception) {vm.fail(e)}
                    onIncomingConsumed()
                }
            }
            val back by nav.currentBackStackEntryAsState()
            val route=back?.destination?.route ?: "home"
            var activeWorkspace by rememberSaveable {mutableStateOf("home")}
            LaunchedEffect(route) {if(isWorkspace(route)) activeWorkspace=route}
            val dismissKeyboard=rememberKeyboardDismiss()
            Scaffold(containerColor=role("canvas"),snackbarHost={SnackbarHost(snackbars)},
                topBar={StudioTopBar(route,onBack={dismissKeyboard();nav.navigateUp()},onProjects={dismissKeyboard();nav.navigate("projects")})},
                bottomBar={StudioDock(if(isWorkspace(route)) route else activeWorkspace) {tab -> dismissKeyboard();activeWorkspace=tab;nav.navigate(tab) {popUpTo(nav.graph.startDestinationId) {saveState=true};launchSingleTop=true;restoreState=tab!="home"}}}) {padding ->
                Surface(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding).dismissKeyboardOnTap(),color=role("canvas")) {
                    Column {
                    state.error?.let {error ->
                        Surface(color=role("errorSurface"),shape=MaterialTheme.shapes.large,modifier=Modifier.fillMaxWidth().padding(VSTheme.inset)) {
                            Column(Modifier.padding(VSTheme.inset)) {
                                ErrorMessage(error.key,error.params,color=role("errorForeground"),modifier=Modifier.fillMaxWidth())
                                val models=error.key.split('.').take(2)==listOf("mobile","local")||error.key.contains("model")||error.key.contains("licence")
                                val connection=error.key.split('.').take(2)==listOf("mobile","connect")||error.key=="mobile.errors.offline"
                                val action=when {error.key=="mobile.permissions.denied" -> "mobile.permissions.open_settings";models -> "mobile.simple.download_models";connection -> "mobile.simple.reconnect";state.canRetry -> "mobile.simple.retry";else -> "common.close"}
                                TextButton(shape=MaterialTheme.shapes.small,onClick={
                                    when {error.key=="mobile.permissions.denied" -> {vm.clearError();openSystemSettings(context)}
                                        models -> {
                                            vm.clearError()
                                            if(error.key.split('.').take(2)==listOf("mobile","local")||vm.api==null) nav.navigate("models")
                                            else context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,android.net.Uri.parse(vm.api!!.address.baseUrl.toString())))
                                        }
                                        connection -> {vm.clearError();nav.navigate("connect")}
                                        state.canRetry -> vm.retryError()
                                        else -> vm.clearError()}
                                }) {Text(tr(action))}
                            }
                        }
                    }
                    if(state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                    Box(Modifier.weight(1f)) {
                    if(state.loading) Screen {Text(tr("common.loading"));LinearProgressIndicator()}
                    else NavHost(navController=nav,startDestination=initialRoute,
                        enterTransition={androidx.compose.animation.EnterTransition.None}, exitTransition={androidx.compose.animation.ExitTransition.None},
                        popEnterTransition={androidx.compose.animation.EnterTransition.None}, popExitTransition={androidx.compose.animation.ExitTransition.None}) {
                        composable("connect") {ConnectScreen(vm)}
                        composable("settings") {SettingsScreen(vm,BuildConfig.VERSION_NAME,onConnect={nav.navigate("connect")},onModels={nav.navigate("models")},onSupport={nav.navigate(it)})}
                        composable("donate") {com.voicestudio.mobile.features.settings.DonateScreen()}
                        composable("pro") {com.voicestudio.mobile.features.settings.ProScreen()}
                        composable("other-apps") {com.voicestudio.mobile.features.settings.OtherAppsScreen()}
                        composable("models") {com.voicestudio.mobile.features.models.ModelsScreen(vm)}
                        composable("home") {HomeScreen(vm) {nav.navigate(it)}}
                        composable("host-dubs") {com.voicestudio.mobile.features.dub.ComputerDubScreen(vm)}
                        composable("projects") {com.voicestudio.mobile.features.dub.PhoneProjectScreen(vm)}
                        composable("project/{id}") {entry -> com.voicestudio.mobile.features.dub.PhoneProjectScreen(vm,entry.arguments?.getString("id"))}
                        composable("host-voice/{id}") {entry -> SpeakScreen(vm,initialComputerVoiceID=entry.arguments?.getString("id"),openGallery={nav.navigate("gallery")})}
                        composable("voice/{id}") {entry -> SpeakScreen(vm,entry.arguments?.getString("id"),openGallery={nav.navigate("gallery")})}
                        composable("speak") {SpeakScreen(vm,openGallery={nav.navigate("gallery")})}
                        composable("design") {VoiceDesignScreen(vm)}
                        composable("community-design/{recipe}") {entry ->
                            val recipe=remember(entry) {runCatching {kotlinx.serialization.json.Json.decodeFromString<com.voicestudio.mobile.local.GalleryEntry>(entry.arguments?.getString("recipe").orEmpty())}.getOrNull()}
                            VoiceDesignScreen(vm,recipe)
                        }
                        composable("stories") {ProductionScreen(vm,"stories")}
                        composable("audiobook") {ProductionScreen(vm,"audiobook")}
                        composable("batch") {com.voicestudio.mobile.features.dub.BatchDubScreen(vm)}
                        composable("workflows") {WorkflowsScreen(vm)}
                        composable("incoming") {received?.let {PhoneIncomingScreen(vm,it)}}
                        composable("calls") {CallsScreen(vm)}
                        composable("local-api") {LocalAPIScreen(vm,onVoices={nav.navigate("voices")})}
                        composable("gallery") {GalleryScreen(vm) {nav.navigate(it)}}
                        composable("preset/{model}/{speaker}") {entry -> SpeakScreen(vm,initialPhonePreset=entry.arguments?.getString("speaker")?.toIntOrNull(),initialPhoneModelID=entry.arguments?.getString("model"),openGallery={nav.navigate("gallery")})}
                        composable("tools") {VoiceToolsScreen(vm)}
                        composable("voices") {VoicesScreen(vm)}
                        composable("clone") {VoicesScreen(vm,initialCreating=true,onCancel={nav.popBackStack()},onCreated={nav.navigate("voices") {popUpTo("clone") {inclusive=true}}})}
                        composable("dub") {DubScreen(vm)}
                        composable("dictate") {DictateScreen(vm,onHistory={nav.navigate("transcriptions")})}
                        composable("transcriptions") {TranscriptsScreen(vm,onDictate={nav.navigate("dictate")})}
                        composable("handoff") {HandoffScreen(vm)}
                    }
                    }
                    }
                }
            }
            }
            }
            if(!state.loading&&prefs.consent==null) AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest={vm.consent(false)},title={Text(tr("consent.title"))},
                text={Column(Modifier.verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {Text(tr("consent.body"));Text(tr("mobile.consent.phone_note"))}},
                confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={vm.consent(true)}) {Text(tr("consent.yes"))}},
                dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={vm.consent(false)}) {Text(tr("consent.no"))}})
            ContentReportHost(vm,snackbars)
        }
    }
}
