package com.voicestudio.mobile

import android.app.Application
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
import androidx.compose.runtime.saveable.SaveableStateRegistry
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.CredentialVault
import com.voicestudio.mobile.data.Preferences
import com.voicestudio.mobile.data.Secret
import com.voicestudio.mobile.data.SessionStore
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.LocalL10n
import com.voicestudio.mobile.features.ProductionScreen
import com.voicestudio.mobile.features.StudioViewModel
import com.voicestudio.mobile.l10n.L10n
import java.io.File
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The same harness/callsite captures the old screen and restores the current one.
 * Fixtures are produced by compiling the actual old ProductionScreen unchanged,
 * not by predicting its compiler-generated key or constructing a Saver by hand.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],application=Application::class)
class ProductionUpgradeSavedStateTest {
    @get:Rule val compose=createComposeRule()

    // Preserve the public registry's complete typed shape, including the old
    // MutableState<String> saver envelope. No key is selected or guessed here.
    private fun encode(value:Any?):JsonElement=when(value) {
        null -> JsonNull
        is MutableState<*> -> buildJsonObject {put("state",encode(value.value))}
        is String -> JsonPrimitive(value)
        is Boolean -> JsonPrimitive(value)
        is Int -> buildJsonObject {put("int",value)}
        is Long -> buildJsonObject {put("long",value)}
        is Float -> buildJsonObject {put("float",value)}
        is Double -> buildJsonObject {put("double",value)}
        is List<*> -> JsonArray(value.map(::encode))
        else -> error("Unsupported public registry value ${value.javaClass.name}")
    }
    private fun decode(value:JsonElement):Any?=when(value) {
        JsonNull -> null
        is JsonArray -> value.map(::decode)
        is JsonPrimitive -> if(value.isString) value.content else value.boolean
        is JsonObject -> when {
            "state" in value -> mutableStateOf(decode(value.getValue("state")))
            "int" in value -> value.getValue("int").jsonPrimitive.int
            "long" in value -> value.getValue("long").jsonPrimitive.long
            "float" in value -> value.getValue("float").jsonPrimitive.float
            "double" in value -> value.getValue("double").jsonPrimitive.double
            else -> error("Unsupported registry fixture type")
        }
    }
    private fun manuscript(kind:String)="Synthetic $kind upgrade manuscript.\nThe complete untitled draft must survive an app update.\n".repeat(5)

    private fun restoreActualScreen(kind:String) {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        runBlocking {store.update {Preferences(consent=false)}}
        val vm=StudioViewModel(app,store)
        val strings=L10n.load(app,"en")
        val captureDirectory=System.getenv("VOICESTUDIO_CAPTURE_PRODUCTION_UPGRADE")?.takeIf {it.isNotBlank()}
        val restored:Map<String,List<Any?>>?=if(captureDirectory!=null) null else {
            val resource="production-upgrade/$kind.json"
            val saved=checkNotNull(javaClass.classLoader!!.getResourceAsStream(resource)) {"Missing actual old-screen fixture $resource"}
                .bufferedReader().use {Json.parseToJsonElement(it.readText()).jsonObject}
            saved.mapValues {(_,value)->value.jsonArray.map(::decode)}
        }
        val registry=SaveableStateRegistry(restored,canBeSaved={true})
        try {
            // Keep this composition callsite unchanged between old capture and
            // current restore; parent groups are part of Compose's real key.
            compose.setContent {
                CompositionLocalProvider(LocalSaveableStateRegistry provides registry,LocalL10n provides strings) {
                    VoiceStudioTheme {ProductionScreen(vm,kind)}
                }
            }
            val editor=hasSetTextAction() and SemanticsMatcher.expectValue(SemanticsProperties.ImeAction,ImeAction.Default)
            compose.waitUntil(15000) {compose.onAllNodes(editor,useUnmergedTree=true).fetchSemanticsNodes().isNotEmpty()}
            if(captureDirectory==null && control!="restore") {
                assertEquals("Without the old project entry the editor must start empty","",compose.onNode(editor,useUnmergedTree=true).fetchSemanticsNode().config.getOrNull(SemanticsProperties.EditableText)?.text)
                assertTrue("Missing history must not come from an old VM or disk checkpoint",consumedProjects.isEmpty())
                assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
                return
            }
            if(captureDirectory!=null) {
                val title=hasSetTextAction() and hasText(strings.t(if(kind=="stories") "stories.untitled" else "audiobook.untitled"))
                compose.onNode(title,useUnmergedTree=true).performScrollTo().performSemanticsAction(SemanticsActions.SetText) {assertTrue(it(AnnotatedString("")))}
                compose.onNode(editor,useUnmergedTree=true).performScrollTo().performSemanticsAction(SemanticsActions.SetText) {assertTrue(it(AnnotatedString(manuscript(kind))))}
                compose.waitForIdle()
                assertTrue("Blank-title source must not already exist in the old named-project library",runBlocking {vm.phone.production.list()}.none {it.script==manuscript(kind)})
                val saved=registry.performSave()
                val json=JsonObject(saved.mapValues {(_,values)->JsonArray(values.map(::encode))})
                File(captureDirectory).mkdirs()
                File(captureDirectory,"$kind.json").writeText(json.toString())
                println("Captured actual old $kind screen: ${saved.size} public registry entries")
            }
            compose.onNode(editor,useUnmergedTree=true).performScrollTo()
            compose.waitUntil(15000) {
                compose.waitForIdle()
                compose.onAllNodes(editor,useUnmergedTree=true).fetchSemanticsNodes().singleOrNull()
                    ?.config?.getOrNull(SemanticsProperties.EditableText)?.text==manuscript(kind)
            }
            val titleField=compose.onNode(hasSetTextAction() and hasText(strings.t("stories.projectName")))
            titleField.performScrollTo()
            assertEquals("Restored screen keeps the blank title","",titleField.fetchSemanticsNode().config.getOrNull(SemanticsProperties.EditableText)?.text)
            assertFalse(vm.phone.downloads.progress.value.values.any {it.running})
            if(captureDirectory==null) {
                assertEquals("The old project value must actually be consumed",1,consumedProjects.size)
                val old=consumedProjects.single()
                assertEquals(kind,old.kind)
                // Editor visibility precedes the IO checkpoint acknowledgement.
                compose.waitUntil(5000) {
                    compose.waitForIdle()
                    registry.performSave().values.flatten().any {(it as? MutableState<*>)?.value==old.id}
                }
                val current=registry.performSave()
                assertFalse("Legacy manuscript must not remain as an unconsumed registry entry",current.values.flatten().any {fixtureProject(it)!=null})
                val referenceKeys=current.filterValues {values->values.any {(it as? MutableState<*>)?.value==old.id}}.keys
                assertTrue("The restored project's UUID must replace its legacy JSON",referenceKeys.isNotEmpty())
                println("Actual $kind legacy project consumed at $consumedKeys; current UUID saved at $referenceKeys")
            }
        } finally {vm.viewModelScope.cancel()}
    }

    @Test fun oldStoriesSnapshotRestoresIntoCurrentScreen()=restoreActualScreen("stories")
    @Test fun oldAudiobookSnapshotRestoresIntoCurrentScreen()=restoreActualScreen("audiobook")

    private var control="restore"
    private val consumedProjects=mutableListOf<com.voicestudio.mobile.local.ProductionProject>()
    private val consumedKeys=mutableListOf<String>()

    private fun fixtureProject(value:Any?):com.voicestudio.mobile.local.ProductionProject? {
        val saved=(value as? MutableState<*>)?.value as? String ?: return null
        val project=runCatching {Json.decodeFromString(com.voicestudio.mobile.local.ProductionProject.serializer(),saved)}.getOrNull() ?: return null
        return project.takeIf {it.title.isBlank() && it.script==manuscript(it.kind)}
    }

    // This non-composable factory leaves the original setContent body, caller
    // signature and byte positions untouched. It wraps only the public registry
    // API; no private Compose state or generated-key guessing is involved.
    private fun SaveableStateRegistry(restoredValues:Map<String,List<Any?>>?,canBeSaved:(Any)->Boolean):SaveableStateRegistry {
        val values=when(control) {
            "empty" -> emptyMap()
            "omit-project" -> restoredValues?.mapValues {(_,entries)->entries.map {if(fixtureProject(it)!=null) null else it}}
            else -> restoredValues
        }
        val delegate=androidx.compose.runtime.saveable.SaveableStateRegistry(values,canBeSaved)
        return object:SaveableStateRegistry by delegate {
            override fun consumeRestored(key:String):Any? {
                val value=delegate.consumeRestored(key)
                fixtureProject(value)?.let {consumedProjects+=it;consumedKeys+=key}
                return value
            }
        }
    }

    @Test fun emptyRegistryCannotRecoverManuscriptFromEarlierProcessState() {
        org.junit.Assume.assumeTrue(System.getenv("VOICESTUDIO_CAPTURE_PRODUCTION_UPGRADE").isNullOrBlank())
        control="empty";restoreActualScreen("stories")
    }
    @Test fun omittingOnlyActualOldProjectValuePreventsRestoration() {
        org.junit.Assume.assumeTrue(System.getenv("VOICESTUDIO_CAPTURE_PRODUCTION_UPGRADE").isNullOrBlank())
        control="omit-project";restoreActualScreen("stories")
    }

}
