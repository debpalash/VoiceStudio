package com.voicestudio.mobile

import android.app.Application
import android.content.Context
import android.content.ContextWrapper
import android.os.Bundle
import android.os.Parcel
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.text.input.ImeAction
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
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
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

    private fun awaitCheckpoint(directory: File,project: ProductionProject) {
        compose.waitUntil(5000) {
            // Robolectric pauses Main between test actions. Disk publication
            // completes on IO, then the serial collector resumes on Main.
            compose.waitForIdle()
            runCatching {ProductionDraftRepository(directory).restore(project.id)==project}.getOrDefault(false)
        }
    }

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
        awaitCheckpoint(temporary.root,project)
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
            compose.onNode(
                hasSetTextAction() and SemanticsMatcher.expectValue(SemanticsProperties.ImeAction,ImeAction.Default),
                useUnmergedTree=true,
            ).performScrollTo()
            compose.waitUntil(15000) {
                compose.waitForIdle()
                compose.onAllNodes(hasSetTextAction(),useUnmergedTree=true).fetchSemanticsNodes()
                    .any {it.config.getOrNull(SemanticsProperties.EditableText)?.text==project.script}
            }
            val checkpoints=File(app.noBackupFilesDir,"production-drafts")
            awaitCheckpoint(checkpoints,project)
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
        awaitCheckpoint(temporary.root,project)
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {assertTrue("Every project field must restore",project==state.value)}
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

    @Test fun failedReplacementKeepsTheLastDurableHandleThroughRecreation() {
        val restored=StateRestorationTester(compose)
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        val failures=mutableListOf<Throwable>()
        val rejectWrites=AtomicBoolean(false)
        val directory=object:File(temporary.root,"durable-drafts") {
            override fun isDirectory()=if(rejectWrites.get()) false else super.isDirectory()
            override fun mkdirs()=if(rejectWrites.get()) false else super.mkdirs()
        }
        restored.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject("stories","Untitled",directory,{failures+=it})
        }
        // A blank title keeps this draft out of the named project library.
        val project=fixture("stories").copy(title="")
        compose.runOnIdle {
            state.value=project
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        awaitCheckpoint(directory,project)
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {assertTrue("Every project field must restore",project==state.value)}
        rejectWrites.set(true)
        compose.runOnIdle {
            state.value=project.copy(script="A newer edit that cannot be checkpointed.")
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        compose.waitForIdle()
        restored.emulateSavedInstanceStateRestore()
        compose.waitUntil(5000) {compose.waitForIdle();failures.any {it is java.io.IOException}}
        compose.runOnIdle {assertTrue("The last durable blank-title draft must remain reachable",project==state.value)}
        assertTrue(failures.any {it is java.io.IOException})
        assertEquals(project,ProductionDraftRepository(directory).restore(project.id))
    }

    @Test fun checkpointWritesDoNotRunOnTheActivityThread() {
        val wroteOnMain=AtomicBoolean(false)
        val directory=object:File(temporary.root,"observed-drafts") {
            override fun isDirectory():Boolean {
                if(Looper.myLooper()==Looper.getMainLooper()) wroteOnMain.set(true)
                return super.isDirectory()
            }
        }
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        compose.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})
        }
        val project=fixture("stories")
        compose.runOnIdle {
            state.value=project
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        awaitCheckpoint(directory,project)
        assertFalse("JSON serialization, fsync and checkpoint publication must stay off the Activity thread",wroteOnMain.get())
        assertEquals(project,ProductionDraftRepository(directory).restore(project.id))
    }

    @Test fun firstFailedSaveDoesNotPublishAnUnwrittenHandle() {
        val invalidRoot=temporary.newFile("unwritable-drafts")
        val restored=StateRestorationTester(compose)
        val failures=mutableListOf<Throwable>()
        lateinit var state:MutableState<ProductionProject>
        restored.setContent {state=rememberProductionProject("stories","Untitled",invalidRoot,{failures+=it})}
        val project=fixture("stories")
        compose.runOnIdle {state.value=project}
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {
            assertNotEquals(project.id,state.value.id)
            assertEquals("",state.value.script)
        }
        compose.waitUntil(5000) {compose.waitForIdle();failures.isNotEmpty()}
        assertTrue(failures.isNotEmpty())
        assertTrue(failures.all {it is java.io.IOException})
        assertTrue(invalidRoot.isFile)
    }

    @Test fun pendingCheckpointDoesNotBlockActivitySaveOrOverwriteNewerEdits() {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val blockNextWrite=AtomicBoolean(false)
        val directory=object:File(temporary.root,"ordered-drafts") {
            override fun isDirectory():Boolean {
                if(blockNextWrite.compareAndSet(true,false)) {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled write was not released")
                }
                return super.isDirectory()
            }
        }
        val restored=StateRestorationTester(compose)
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        val failures=mutableListOf<Throwable>()
        restored.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject("stories","Untitled",directory,{failures+=it})
        }
        val project=fixture("stories")
        compose.runOnIdle {state.value=project}
        awaitCheckpoint(directory,project)
        val latest=project.copy(script=project.script+"Latest ordered edit.")
        try {
            blockNextWrite.set(true)
            compose.runOnIdle {state.value=project.copy(script="Earlier in-flight edit.")}
            compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L}
            compose.runOnIdle {
                state.value=latest
                val start=System.nanoTime()
                ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                    .apply {isAccessible=true}.invoke(activity,Bundle())
                assertTrue("An in-flight checkpoint must not block Activity state saving",System.nanoTime()-start<500_000_000L)
            }
        } finally {release.countDown()}
        awaitCheckpoint(directory,latest)
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {assertTrue("The newest durable edit and all metadata must restore",state.value==latest)}
        assertTrue(failures.isEmpty())
    }

    @Test fun staleQueuedSnapshotCannotRecreateADeletedCheckpoint() {
        val repository=ProductionDraftRepository(temporary.root)
        val project=fixture("stories")
        repository.save(project)
        repository.delete(project.id)
        assertNull(repository.saveIfCurrent(project) {false})
        assertThrows(java.io.FileNotFoundException::class.java) {repository.restore(project.id)}
        assertEquals(project.id,repository.saveIfCurrent(project) {true})
        assertEquals(project,repository.restore(project.id))
    }

    @Test fun failedNewProjectCannotBorrowThePreviousProjectsHandle() {
        val rejectWrites=AtomicBoolean(false)
        val directory=object:File(temporary.root,"separate-drafts") {
            override fun isDirectory()=if(rejectWrites.get()) false else super.isDirectory()
            override fun mkdirs()=if(rejectWrites.get()) false else super.mkdirs()
        }
        val restored=StateRestorationTester(compose)
        lateinit var state:MutableState<ProductionProject>
        restored.setContent {state=rememberProductionProject("stories","Untitled",directory,{})}
        val first=fixture("stories")
        compose.runOnIdle {state.value=first}
        awaitCheckpoint(directory,first)
        restored.emulateSavedInstanceStateRestore()
        val second=fixture("stories").copy(script="A separate uncheckpointed project.")
        rejectWrites.set(true)
        compose.runOnIdle {state.value=second}
        compose.waitForIdle()
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {
            assertNotEquals(first.id,state.value.id)
            assertNotEquals(second.id,state.value.id)
            assertEquals("",state.value.script)
        }
        assertEquals(first,ProductionDraftRepository(directory).restore(first.id))
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
        // Failed background checkpoint attempts still surface on the UI.
        compose.waitUntil(5000) {compose.waitForIdle();failures.isNotEmpty()}
        assertTrue(failures.isNotEmpty())
        assertTrue(failures.all {it is java.io.IOException})
        assertEquals(project,state.value)
        assertTrue(invalidRoot.isFile)
    }
}
