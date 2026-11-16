package com.voicestudio.mobile

import android.app.Application
import android.content.Context
import android.content.ContextWrapper
import android.os.Bundle
import android.os.Parcel
import androidx.activity.ComponentActivity
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
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
import com.voicestudio.mobile.features.rememberProductionProject
import com.voicestudio.mobile.features.ProductionDraftRepository
import com.voicestudio.mobile.local.ProductionProject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.Json
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],application=Application::class)
class ProductionSavedStateTest {
    @get:Rule val compose=createComposeRule()
    @get:Rule val temporary=TemporaryFolder()

    private fun fixture(kind: String)=ProductionProject(
        kind=kind,title="Saved fixture",script="A complete fixture sentence.\n".repeat(12000),
        defaultVoice="phone:reference-a",cast=mapOf("Narrator" to "phone:reference-b"),modifiedAt=123456789L,
        pronunciations=mapOf("fixture" to "test"),speed=1.25f,
        handoff=buildJsonObject {put("engine","fixture-engine");put("revision","fixture-v1");put("audio","generated/output.wav")},
    )

    private fun Context.activity(): ComponentActivity =
        generateSequence(this) {(it as? ContextWrapper)?.baseContext}
            .filterIsInstance<ComponentActivity>().first()

    private fun savedActivityBytes(kind: String) {
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        val failures=mutableListOf<Throwable>()
        compose.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject(kind,"Untitled",temporary.root,{failures+=it})
        }
        val project=fixture(kind)
        compose.runOnIdle {state.value=project}
        compose.waitForIdle()
        val bundle=Bundle()
        compose.runOnIdle {
            // Real ComponentActivity -> SavedStateRegistry -> Compose saver,
            // without measuring a reimplemented JSON-only approximation.
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,bundle)
        }
        val parcel=Parcel.obtain()
        val bytes=try {parcel.writeBundle(bundle);parcel.dataSize()} finally {parcel.recycle()}
        println("Production $kind Activity Bundle: $bytes bytes for ${project.script.length} script characters")
        assertTrue("Large manuscripts must remain on disk, not in the activity transaction ($bytes bytes)",bytes<4096)
        assertTrue(failures.isEmpty())
    }

    @Test fun storiesManuscriptDoesNotEnterTheActivityTransaction()=savedActivityBytes("stories")
    @Test fun audiobookManuscriptDoesNotEnterTheActivityTransaction()=savedActivityBytes("audiobook")

    @Test fun realProductionEditorAlsoKeepsTheActivityTransactionSmall()=runBlocking {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        store.update {Preferences(consent=false)}
        val vm=StudioViewModel(app,store)
        try {
            val project=fixture("stories")
            vm.phone.production.save(project)
            lateinit var activity:ComponentActivity
            compose.setContent {
                activity=LocalContext.current.activity()
                CompositionLocalProvider(LocalL10n provides com.voicestudio.mobile.l10n.L10n.load(app,"en")) {
                    VoiceStudioTheme {ProductionScreen(vm,"stories")}
                }
            }
            compose.waitUntil(15000) {compose.onAllNodesWithText(project.title).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText(project.title).performScrollTo().performClick()
            compose.waitForIdle()
            val bundle=Bundle()
            compose.runOnIdle {
                ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                    .apply {isAccessible=true}.invoke(activity,bundle)
            }
            val parcel=Parcel.obtain()
            val bytes=try {parcel.writeBundle(bundle);parcel.dataSize()} finally {parcel.recycle()}
            println("Full Production editor Activity Bundle: $bytes bytes")
            assertTrue("The actual text field must not duplicate the manuscript in saved state ($bytes bytes)",bytes<16384)
            assertTrue(vm.phone.downloads.progress.value.values.none {it.running})
        } finally {vm.viewModelScope.cancel()}
    }

    @Test fun recreationRestoresTheWholeProjectIncludingVoiceAudioAndEngineMetadata() {
        val restored=StateRestorationTester(compose)
        lateinit var state:MutableState<ProductionProject>
        val failures=mutableListOf<Throwable>()
        restored.setContent {state=rememberProductionProject("stories","Untitled",temporary.root,{failures+=it})}
        val project=fixture("stories")
        compose.runOnIdle {state.value=project}
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {assertEquals(project,state.value)}
        assertTrue(failures.isEmpty())
    }

    @Test fun checkpointSurvivesNewRepositoryAndOverwritesOnlyTheSameDraft() {
        val first=fixture("stories")
        val second=fixture("audiobook")
        val repository=ProductionDraftRepository(temporary.root)
        assertEquals(first.id,repository.save(first))
        assertEquals(second.id,repository.save(second))
        val edited=first.copy(script=first.script+"Final edit before activity save.",title="Updated fixture")
        assertEquals(first.id,repository.save(edited))
        val restarted=ProductionDraftRepository(temporary.root)
        assertEquals(edited,restarted.restore(first.id))
        assertEquals(second,restarted.restore(second.id))
        assertEquals(2,temporary.root.listFiles()!!.size)
    }

    @Test fun legacySavedJsonRemainsIntactAndMigratesToASmallHandle() {
        val project=fixture("stories")
        val legacy=Json.encodeToString(ProductionProject.serializer(),project)
        val repository=ProductionDraftRepository(temporary.root)
        val restored=repository.restore(legacy)
        assertEquals(project,restored)
        val handle=repository.save(restored)
        assertEquals(project.id,handle)
        assertTrue(handle.length<100)
        assertEquals(project,ProductionDraftRepository(temporary.root).restore(handle))
    }

    @Test fun invalidHandlesCannotReadOrWriteOutsideTheDraftDirectory() {
        val repository=ProductionDraftRepository(temporary.root)
        val project=fixture("stories")
        for(id in listOf("../outside","/outside","1-1-1-1-1")) {
            assertThrows(IllegalArgumentException::class.java) {repository.restore(id)}
            assertThrows(IllegalArgumentException::class.java) {repository.save(project.copy(id=id))}
        }
        assertTrue(temporary.root.listFiles()!!.isEmpty())
    }

    @Test fun failedReplacementPreservesThePreviousCheckpoint() {
        val project=fixture("stories")
        val repository=ProductionDraftRepository(temporary.root)
        repository.save(project)
        // A real filesystem failure at AtomicFile's replacement output.
        val pending=java.io.File(temporary.root,"${project.id}.json.new")
        assertTrue(pending.mkdir())
        assertThrows(java.io.IOException::class.java) {repository.save(project.copy(script="New unsaved text"))}
        assertTrue(pending.delete())
        assertEquals(project,ProductionDraftRepository(temporary.root).restore(project.id))
    }

    @Test fun explicitDeleteRemovesOnlyThatProjectsCheckpoint() {
        val repository=ProductionDraftRepository(temporary.root)
        val deleted=fixture("stories")
        val retained=fixture("audiobook")
        repository.save(deleted)
        repository.save(retained)
        repository.delete(deleted.id)
        assertThrows(java.io.FileNotFoundException::class.java) {repository.restore(deleted.id)}
        assertEquals(retained,ProductionDraftRepository(temporary.root).restore(retained.id))
        assertEquals(listOf("${retained.id}.json"),temporary.root.listFiles()!!.map {it.name})
    }

    @Test fun saveFailureShowsAnErrorWithoutPuttingTheManuscriptBackInTheBundle() {
        val invalidRoot=temporary.newFile("not-a-directory")
        val failures=mutableListOf<Throwable>()
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        compose.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject("stories","Untitled",invalidRoot,{failures+=it})
        }
        val project=fixture("stories")
        compose.runOnIdle {state.value=project}
        compose.waitForIdle()
        val bundle=Bundle()
        compose.runOnIdle {
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,bundle)
        }
        val parcel=Parcel.obtain()
        try {parcel.writeBundle(bundle);assertTrue(parcel.dataSize()<4096)} finally {parcel.recycle()}
        // Compose checks the saver's value at registration as well as when
        // the activity saves. Every failed attempt must surface locally.
        assertTrue(failures.isNotEmpty())
        assertTrue(failures.all {it is java.io.IOException})
        assertEquals(project,state.value)
        assertTrue(invalidRoot.isFile)
    }
}
