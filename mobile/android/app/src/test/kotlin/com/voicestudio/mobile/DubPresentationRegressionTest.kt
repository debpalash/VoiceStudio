package com.voicestudio.mobile

import android.app.Application
import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Looper
import android.provider.OpenableColumns
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.features.dub.*
import com.voicestudio.mobile.features.speak.SpeakScreen
import com.voicestudio.mobile.features.voices.VoicesScreen
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.network.DubHistory
import kotlinx.coroutines.*
import okhttp3.mockwebserver.*
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ContentProviderController
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class DubPresentationRegressionTest {
    @get:Rule val compose=createComposeRule()
    private val app get()=ApplicationProvider.getApplicationContext<Application>()
    private fun content(block: @androidx.compose.runtime.Composable ()->Unit) {
        compose.setContent {CompositionLocalProvider(LocalL10n provides L10n.load(app,"en")) {VoiceStudioTheme {block()}}}
    }
    @Test fun sourceUsesProviderDisplayNameAndRemoveNamesTheFile() {
        val provider=ContentProviderController.of(DisplayNameProvider()).create("com.voicestudio.test.media").get()
        val uri=Uri.parse("content://com.voicestudio.test.media/internal-uuid")
        assertEquals("vs-dub-test.wav",mediaDisplayName(app,uri))
        var removed=false
        content {SelectedDubSource(mediaDisplayName(app,uri)!!) {removed=true}}
        compose.onNodeWithText("vs-dub-test.wav").assertExists()
        compose.onNodeWithText("Remove").assertExists()
        compose.onNodeWithContentDescription("Remove vs-dub-test.wav").performClick()
        assertTrue(removed)
        provider.shutdown()
    }
    @Test fun groupedHistoryHasMetadataAndCompactNamedDownloadAndShareActions() {
        var downloads=0;var shares=0
        content {Group {DubHistoryRow(DubHistory("job",filename="vs-dub-test.wav",language="French",duration=65.0),true,{downloads++},{shares++})}}
        compose.onNodeWithText("vs-dub-test.wav").assertExists()
        compose.onNodeWithText(L10n.load(ApplicationProvider.getApplicationContext(),"en").t("mobile.dub.history_meta",mapOf("language" to "French","duration" to "1:05"))).assertExists()
        val download=compose.onNodeWithContentDescription("Download vs-dub-test.wav")
        val share=compose.onNodeWithContentDescription("Share vs-dub-test.wav")
        assertTrue(download.fetchSemanticsNode().boundsInRoot.width < compose.onRoot().fetchSemanticsNode().boundsInRoot.width/2)
        download.performClick();share.performClick()
        assertEquals(1,downloads);assertEquals(1,shares)
    }
    @Test fun multiPickerSearchWithoutMatchesShowsQuery() {
        content {MultiPicker("mobile.dub.target_languages",emptyList(),listOf("fr" to "French")) {}}
        compose.onNodeWithText("Target languages").performClick()
        compose.onNodeWithText("Search").performTextInput("missing")
        compose.onNodeWithText("No matches for “missing”.").assertExists()
    }
    @Test fun voicesSearchWithoutMatchesShowsQuery() {
        withHost {vm ->
            content {VoicesScreen(vm)}
            compose.onNodeWithText("Search voices").performScrollTo().performTextInput("missing")
            compose.onNodeWithText("No matches for “missing”.").assertExists()
        }
    }
    @Test fun speakRendersControlsWhileLanguageCatalogIsSuspended() {
        val release=CompletableDeferred<Unit>()
        withHost {vm ->
            compose.setContent {
                CompositionLocalProvider(LocalL10n provides L10n.load(app,"en"),LocalLanguageCatalogLoader provides {
                    release.await();LanguageCatalog(listOf("Auto","French"))
                }) {VoiceStudioTheme {Column {SpeakScreen(vm)}}}
            }
            compose.onNodeWithText("Type what you want to hear…").assertExists()
            compose.onNodeWithText("Loading…").assertExists()
            // This test keeps the loader suspended: no disk/locale mapping must
            // finish before the first screen and picker controls are composed.
        }
        release.complete(Unit)
    }
    @Test fun dubRendersBeforeLanguageDataArrives() {
        withHost {vm ->
            compose.setContent {
                CompositionLocalProvider(LocalL10n provides L10n.load(app,"en"),LocalLanguageCatalogLoader provides {
                    awaitCancellation()
                }) {VoiceStudioTheme {DubScreen(vm)}}
            }
            compose.onNode(hasText("Dub") and SemanticsMatcher.keyIsDefined(androidx.compose.ui.semantics.SemanticsProperties.Heading)).assertExists()
            compose.onNodeWithText("Loading…").assertExists()
            compose.onNodeWithText("Paste a link").assertExists()
        }
    }
    @Test fun everyTabRendersItsTitleWhileLanguageLoadingIsSuspended() {
        withHost {vm ->
            compose.setContent {
                CompositionLocalProvider(LocalLanguageCatalogLoader provides {awaitCancellation()}) {StudioApp(vm)}
            }
            for(tab in listOf("Speech","Voices","Dub","Dictate","Settings","Speech")) {
                if(tab=="Speech") {
                    compose.onNode(hasText("Home") and hasClickAction()).performClick()
                    compose.onNodeWithTag("home.speak").performScrollTo().performClick()
                } else compose.onNode(hasText(tab) and hasClickAction()).performClick()
                compose.onNode(hasText(tab) and SemanticsMatcher.keyIsDefined(androidx.compose.ui.semantics.SemanticsProperties.Heading)).assertExists()
            }
        }
    }
    private fun withHost(block: (StudioViewModel)->Unit) {
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String)=Secret()
            override fun write(host:String,secret:Secret?) {}
        })
        runBlocking {store.update {Preferences(consent=false)}}
        val server=MockWebServer().apply {
            dispatcher=object:Dispatcher() {
                override fun dispatch(request:RecordedRequest)=when(request.path) {
                    "/health" -> MockResponse().setHeader("x-omnivoice-backend","test").setBody("""{"status":"ok","version":"test"}""")
                    "/profiles" -> MockResponse().setBody("""[{"id":"voice","name":"Sample voice"}]""")
                    "/history","/dub/history" -> MockResponse().setBody("[]")
                    "/engines" -> MockResponse().setBody("""{"tts":{"active":"test","backends":[]}}""")
                    else -> MockResponse().setResponseCode(404)
                }
            };start()
        }
        val vm=StudioViewModel(app,store)
        fun idle()=compose.waitUntil(15000) {shadowOf(Looper.getMainLooper()).idle();!vm.state.value.loading&&!vm.state.value.busy}
        try {idle();vm.connect(server.url("/").toString(),"","");idle();block(vm)}
        finally {vm.viewModelScope.cancel();server.shutdown()}
    }
}

class DisplayNameProvider:ContentProvider() {
    override fun onCreate()=true
    override fun getType(uri:Uri)="audio/wav"
    override fun query(uri:Uri,projection:Array<out String>?,selection:String?,selectionArgs:Array<out String>?,sortOrder:String?):Cursor =
        MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME)).apply {addRow(arrayOf("vs-dub-test.wav"))}
    override fun insert(uri:Uri,values:ContentValues?):Uri?=null
    override fun delete(uri:Uri,selection:String?,selectionArgs:Array<out String>?)=0
    override fun update(uri:Uri,values:ContentValues?,selection:String?,selectionArgs:Array<out String>?)=0
}
