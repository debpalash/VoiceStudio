package com.voicestudio.mobile

import android.app.Application
import android.content.Context
import android.content.ContextWrapper
import android.os.Bundle
import android.os.Parcel
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
import androidx.compose.runtime.saveable.SaveableStateRegistry
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
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
import com.voicestudio.mobile.features.ProductionDraftSession
import com.voicestudio.mobile.features.ProductionProjectState
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

    private fun savedStrings(value: Any?): List<String> = when(value) {
        is String->listOf(value)
        is androidx.compose.runtime.State<*>->savedStrings(value.value)
        is Map<*,*>->value.values.flatMap(::savedStrings)
        is Iterable<*>->value.flatMap(::savedStrings)
        is Array<*>->value.flatMap(::savedStrings)
        else->emptyList()
    }

    private fun awaitEditor(state: MutableState<ProductionProject>) {
        compose.waitUntil(5000) {compose.waitForIdle();!(state as ProductionProjectState).loading}
    }

    private fun editProject(state: MutableState<ProductionProject>,project: ProductionProject) {
        awaitEditor(state)
        compose.runOnIdle {state.value=project}
    }

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
        editProject(state,project)
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

    @Test fun realProductionEditorAlsoKeepsTheActivityTransactionSmall() {
        val app=ApplicationProvider.getApplicationContext<Application>()
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        val store=SessionStore(app,object:CredentialVault {
            override fun read(host:String):Secret?=null
            override fun write(host:String,secret:Secret?)=Unit
        })
        runBlocking {store.update {Preferences(consent=false)}}
        val vm=StudioViewModel(app,store)
        try {
            val project=fixture("stories")
            runBlocking {vm.phone.production.save(project)}
            assertTrue("The named library must retain the complete source project",runBlocking {vm.phone.production.list().first {it.id==project.id}}==project)
            lateinit var activity:ComponentActivity
            compose.setContent {
                activity=LocalContext.current.activity()
                CompositionLocalProvider(LocalL10n provides com.voicestudio.mobile.l10n.L10n.load(app,"en")) {
                    VoiceStudioTheme {ProductionScreen(vm,"stories")}
                }
            }
            compose.waitUntil(15000) {compose.waitForIdle();compose.onAllNodesWithText(project.title).fetchSemanticsNodes().isNotEmpty()}
            compose.onNodeWithText(project.title).performScrollTo().performSemanticsAction(SemanticsActions.OnClick) {action->
                assertTrue("The registered project button must accept its click",action())
            }
            compose.onNode(
                hasSetTextAction() and SemanticsMatcher.expectValue(SemanticsProperties.ImeAction,ImeAction.Default),
                useUnmergedTree=true,
            ).performScrollTo()
            try {
                compose.waitUntil(15000) {
                    compose.waitForIdle()
                    compose.onAllNodes(hasSetTextAction(),useUnmergedTree=true).fetchSemanticsNodes()
                        .any {it.config.getOrNull(SemanticsProperties.EditableText)?.text==project.script}
                }
            } catch(error:Throwable) {
                val lengths=compose.onAllNodes(hasSetTextAction(),useUnmergedTree=true).fetchSemanticsNodes()
                    .mapNotNull {it.config.getOrNull(SemanticsProperties.EditableText)?.text?.length}
                throw AssertionError("Manuscript not loaded: expected ${project.script.length} characters, editable lengths=$lengths, failure key=${vm.state.value.error?.key}",error)
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
        editProject(state,project)
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
        assertThrows(IllegalArgumentException::class.java) {repository.acquireOwner("../stories") {null}}
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
        lateinit var registry:SaveableStateRegistry
        val failures=mutableListOf<Throwable>()
        val rejectWrites=AtomicBoolean(false)
        val directory=object:File(temporary.root,"durable-drafts") {
            override fun isDirectory()=if(rejectWrites.get()) false else super.isDirectory()
            override fun mkdirs()=if(rejectWrites.get()) false else super.mkdirs()
        }
        restored.setContent {
            activity=LocalContext.current.activity()
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",directory,{failures+=it})
        }
        // A blank title keeps this draft out of the named project library.
        val project=fixture("stories").copy(title="")
        awaitEditor(state)
        compose.runOnIdle {
            state.value=project
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        awaitCheckpoint(directory,project)
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {assertTrue("Every project field must restore",project==state.value)}
        rejectWrites.set(true)
        val unsaved=project.copy(script="A newer edit that cannot be checkpointed.")
        compose.runOnIdle {
            state.value=unsaved
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        compose.waitForIdle()
        restored.emulateSavedInstanceStateRestore()
        compose.waitUntil(5000) {compose.waitForIdle();failures.any {it is java.io.IOException}}
        compose.runOnIdle {
            assertTrue("Live recreation must retain the pending edit",unsaved==state.value)
            val saved=savedStrings(registry.performSave())
            assertTrue("Process recovery must retain this project's reference",project.id in saved)
            assertFalse(unsaved.script in saved)
        }
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
        awaitEditor(state)
        compose.runOnIdle {
            state.value=project
            ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                .apply {isAccessible=true}.invoke(activity,Bundle())
        }
        awaitCheckpoint(directory,project)
        assertFalse("JSON serialization, fsync and checkpoint publication must stay off the Activity thread",wroteOnMain.get())
        assertEquals(project,ProductionDraftRepository(directory).restore(project.id))
    }

    @Test fun failedFirstCheckpointKeepsItsStableReferenceAndLiveEdits() {
        val invalidRoot=temporary.newFile("unwritable-drafts")
        val restored=StateRestorationTester(compose)
        val failures=mutableListOf<Throwable>()
        lateinit var state:MutableState<ProductionProject>
        lateinit var registry:SaveableStateRegistry
        restored.setContent {
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",invalidRoot,{failures+=it})
        }
        compose.waitUntil(5000) {compose.waitForIdle();(state as ProductionProjectState).loadFailed}
        compose.runOnIdle {(state as ProductionProjectState).startNew()}
        val project=fixture("stories")
        editProject(state,project)
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {
            assertTrue("Live recreation retains unsaved edits in its owner",state.value==project)
            val saved=savedStrings(registry.performSave())
            assertTrue("A pending known project needs a stable recovery reference",project.id in saved)
            assertFalse(project.script in saved)
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
        editProject(state,project)
        awaitCheckpoint(directory,project)
        val latest=project.copy(script=project.script+"Latest ordered edit.")
        try {
            blockNextWrite.set(true)
            editProject(state,project.copy(script="Earlier in-flight edit."))
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
        lateinit var registry:SaveableStateRegistry
        restored.setContent {
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",directory,{})
        }
        val first=fixture("stories")
        editProject(state,first)
        awaitCheckpoint(directory,first)
        restored.emulateSavedInstanceStateRestore()
        val second=fixture("stories").copy(script="A separate uncheckpointed project.")
        rejectWrites.set(true)
        editProject(state,second)
        compose.waitForIdle()
        restored.emulateSavedInstanceStateRestore()
        compose.runOnIdle {
            assertTrue("Live pending state must remain intact",state.value==second)
            val saved=savedStrings(registry.performSave())
            assertFalse("The new project cannot borrow an older handle",first.id in saved)
            assertTrue("The selected project retains its own pending reference",second.id in saved)
            assertFalse(second.script in saved)
        }
        assertEquals(first,ProductionDraftRepository(directory).restore(first.id))
    }

    private fun recreateWithPendingEdit(checkResponsiveness: Boolean) {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val finished=CountDownLatch(1)
        val blockNextWrite=AtomicBoolean(false)
        val directory=object:File(temporary.root,"rotation-drafts") {
            override fun isDirectory():Boolean {
                if(blockNextWrite.compareAndSet(true,false)) {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled writer was not released")
                }
                return super.isDirectory()
            }
        }
        val restored=StateRestorationTester(compose)
        lateinit var state:MutableState<ProductionProject>
        restored.setContent {state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})}
        val original=fixture("stories").copy(title="")
        editProject(state,original)
        awaitCheckpoint(directory,original)
        blockNextWrite.set(true)
        editProject(state,original.copy(script="The older checkpoint in flight."))
        compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L}
        val latest=original.copy(script=original.script+"The pending edit at rotation.")
        editProject(state,latest)
        // Release a broken synchronous restore without hanging the test JVM.
        val watchdog=Thread {
            if(!finished.await(1,TimeUnit.SECONDS)) release.countDown()
        }.apply {isDaemon=true;start()}
        var elapsed=0L
        try {
            val start=System.nanoTime()
            restored.emulateSavedInstanceStateRestore()
            elapsed=System.nanoTime()-start
        } finally {
            finished.countDown();release.countDown();watchdog.join(1500)
        }
        if(checkResponsiveness) {
            assertTrue("Restoring a lightweight reference must not wait for the held disk writer",elapsed<500_000_000L)
        } else {
            compose.waitUntil(5000) {compose.waitForIdle();state.value==latest}
            awaitCheckpoint(directory,latest)
            compose.runOnIdle {assertTrue("Rotation must preserve the full pending blank-title draft",state.value==latest)}
        }
    }

    @Test fun recreationDoesNotWaitForAHeldDiskWriter()=recreateWithPendingEdit(true)

    @Test fun rotationRetainsTheLatestPendingBlankTitleEdit()=recreateWithPendingEdit(false)

    @Test fun explicitReplacementRetiresOnlyUnreferencedBlankTitleCheckpoints() {
        lateinit var state:MutableState<ProductionProject>
        lateinit var activity:ComponentActivity
        val directory=temporary.newFolder("replacement-drafts")
        val named=fixture("stories")
        ProductionDraftRepository(directory).save(named)
        val unknown=File(directory,"unrelated.json").apply {writeText("An unrelated app-private fixture")}
        compose.setContent {
            activity=LocalContext.current.activity()
            state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})
        }
        var latest=fixture("stories").copy(title="")
        repeat(6) {
            latest=fixture("stories").copy(title="",script="An explicitly replaced draft $it.")
            editProject(state,latest)
            awaitCheckpoint(directory,latest)
            compose.runOnIdle {
                ComponentActivity::class.java.getDeclaredMethod("onSaveInstanceState",Bundle::class.java)
                    .apply {isAccessible=true}.invoke(activity,Bundle())
            }
        }
        // Current draft + previous Activity recovery reference, plus the two
        // files this editor never owned. No age/count-based sweep is allowed.
        compose.waitUntil(5000) {compose.waitForIdle();directory.listFiles()!!.count {it.name.endsWith(".json")}<=4}
        assertEquals(named,ProductionDraftRepository(directory).restore(named.id))
        assertEquals(latest,ProductionDraftRepository(directory).restore(latest.id))
        assertEquals("An unrelated app-private fixture",unknown.readText())
    }

    @Test fun coldReferenceLoadsOffThreadAndRejectsEditsUntilReady() {
        val first=temporary.newFolder("first-owner")
        val second=temporary.newFolder("cold-owner")
        var directory:File=first
        var swapOnDispose=false
        lateinit var state:ProductionProjectState
        lateinit var registry:SaveableStateRegistry
        val restored=StateRestorationTester(compose)
        restored.setContent {
            DisposableEffect(Unit) {onDispose {if(swapOnDispose) directory=second}}
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})
        }
        val project=fixture("stories").copy(title="")
        editProject(state,project)
        awaitCheckpoint(first,project)
        ProductionDraftRepository(second).save(project)
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val finished=CountDownLatch(1)
        val holdingRoot=object:File(temporary.root,"held-disk") {
            override fun isDirectory():Boolean {
                entered.countDown()
                if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled IO was not released")
                return super.isDirectory()
            }
        }
        val disk=Thread {ProductionDraftRepository(holdingRoot).save(fixture("audiobook"))}.apply {isDaemon=true;start()}
        assertTrue(entered.await(5,TimeUnit.SECONDS))
        val watchdog=Thread {if(!finished.await(1,TimeUnit.SECONDS)) release.countDown()}.apply {isDaemon=true;start()}
        try {
            swapOnDispose=true
            val start=System.nanoTime()
            restored.emulateSavedInstanceStateRestore()
            assertTrue("Cold restoration must return before the held IO",System.nanoTime()-start<500_000_000L)
            compose.runOnIdle {
                assertTrue("The cold editor must expose its loading gate",state.loading)
                val before=state.value
                state.value=fixture("stories")
                assertTrue("Edits must not replace a pending restored manuscript",state.value==before)
                assertTrue("A pending load retains its recovery reference",project.id in savedStrings(registry.performSave()))
            }
        } finally {finished.countDown();release.countDown();watchdog.join(1500);disk.join(5000)}
        compose.waitUntil(5000) {compose.waitForIdle();!state.loading&&state.value==project}
        assertEquals(listOf("${project.id}.json"),second.listFiles()!!.filter {it.name.endsWith(".json")}.map {it.name})
    }

    @Test fun supersededDelayedOwnerCannotOverwriteTheNewerCheckpoint() {
        val repository=ProductionDraftRepository(temporary.root)
        val first=fixture("stories")
        val old=repository.acquireOwner("stories") {ProductionDraftRepository.EditorSnapshot(first,first.id,first.id,emptySet())}
        repository.saveOwned(first,old) {true}
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val finished=CountDownLatch(1)
        val attempted=java.util.concurrent.atomic.AtomicReference<String?>("not attempted")
        val delayed=Thread {
            entered.countDown()
            if(release.await(5,TimeUnit.SECONDS)) attempted.set(ProductionDraftRepository(temporary.root).saveOwned(first,old) {true})
            finished.countDown()
        }.apply {isDaemon=true;start()}
        try {
            assertTrue(entered.await(5,TimeUnit.SECONDS))
            val next=ProductionDraftRepository(temporary.root)
            val newer=first.copy(script=first.script+"The newer owner's edit.")
            val owner=next.acquireOwner("stories") {ProductionDraftRepository.EditorSnapshot(newer,null,null,emptySet())}
            assertEquals(first,owner.previous?.project)
            assertEquals(newer.id,next.saveOwned(newer,owner) {true})
            release.countDown()
            assertTrue(finished.await(5,TimeUnit.SECONDS))
            assertNull("The delayed old generation must refuse publication",attempted.get())
            assertTrue("The actual newer file must remain intact",next.restore(newer.id)==newer)
            next.release(owner)
        } finally {release.countDown();delayed.join(5000)}
    }

    @Test fun legacyJsonLoadsAsynchronouslyAndMigratesToADurableReference() {
        val repository=ProductionDraftRepository(temporary.root)
        val project=fixture("stories").copy(title="")
        val failures=mutableListOf<Throwable>()
        lateinit var session:ProductionDraftSession
        compose.setContent {}
        val store=androidx.lifecycle.ViewModelStore()
        // Hold the shared repository IO lock so the legacy parse cannot finish
        // before the loading gate is checked (it raced on fast runners).
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val holding=object:File(temporary.root,"held-legacy-json-io") {
            override fun isDirectory():Boolean {
                entered.countDown()
                if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled legacy IO was not released")
                return super.isDirectory()
            }
        }
        val disk=Thread {ProductionDraftRepository(holding).save(fixture("audiobook"))}.apply {isDaemon=true;start()}
        try {
            try {
                assertTrue(entered.await(5,TimeUnit.SECONDS))
                compose.runOnIdle {
                    session=ProductionDraftSession(repository,"stories","Untitled")
                    store.put("legacy",session)
                    session.attach {failures+=it}
                    session.initialize(Json.encodeToString(ProductionProject.serializer(),project))
                    assertTrue(session.state.loading)
                    assertEquals("Existing inline recovery data stays until migration is durable",Json.encodeToString(ProductionProject.serializer(),project),session.savedHandle())
                }
            } finally {release.countDown();disk.join(5000)}
            compose.waitUntil(5000) {compose.waitForIdle();!session.state.loading&&session.state.value==project}
            awaitCheckpoint(temporary.root,project)
            compose.waitUntil(5000) {compose.waitForIdle();session.savedHandle()==project.id}
            assertTrue(failures.isEmpty())
            assertTrue(repository.restore(project.id)==project)
        } finally {compose.runOnIdle {store.clear()}}
    }

    @Test fun clearedSessionHandsPendingEditsToANewerOwner() {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val blockNextWrite=AtomicBoolean(false)
        val directory=object:File(temporary.root,"cleared-session") {
            override fun isDirectory():Boolean {
                if(blockNextWrite.compareAndSet(true,false)) {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled writer was not released")
                }
                return super.isDirectory()
            }
        }
        compose.setContent {}
        val oldStore=androidx.lifecycle.ViewModelStore()
        val newStore=androidx.lifecycle.ViewModelStore()
        lateinit var old:ProductionDraftSession
        lateinit var next:ProductionDraftSession
        val original=fixture("stories").copy(title="")
        val latest=original.copy(script=original.script+"An edit pending at owner replacement.")
        try {
            compose.runOnIdle {
                old=ProductionDraftSession(ProductionDraftRepository(directory),"stories","Untitled")
                oldStore.put("draft",old);old.initialize(null)
            }
            awaitEditor(old.state)
            compose.runOnIdle {old.state.value=original}
            awaitCheckpoint(directory,original)
            blockNextWrite.set(true)
            compose.runOnIdle {old.state.value=original.copy(script="An older in-flight snapshot.")}
            compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L}
            compose.runOnIdle {
                old.state.value=latest
                oldStore.clear()
                next=ProductionDraftSession(ProductionDraftRepository(directory),"stories","Untitled")
                newStore.put("draft",next);next.initialize(original.id)
                assertFalse(next.state.loading)
                assertTrue("The new owner inherits the latest complete pending value",next.state.value==latest)
            }
            release.countDown()
            awaitCheckpoint(directory,latest)
            assertTrue(ProductionDraftRepository(directory).restore(latest.id)==latest)
        } finally {release.countDown();compose.runOnIdle {oldStore.clear();newStore.clear()}}
    }

    private fun failedRestoreCanStartANewDraft(corrupt: Boolean) {
        val directory=temporary.newFolder("failed-restore")
        val repository=ProductionDraftRepository(directory)
        val missing=fixture("stories").id
        val original=File(directory,"$missing.json")
        val corruptBytes="Unreadable checkpoint fixture".toByteArray()
        if(corrupt) original.writeBytes(corruptBytes)
        val named=fixture("stories")
        repository.save(named)
        val failures=mutableListOf<Throwable>()
        val store=androidx.lifecycle.ViewModelStore()
        lateinit var session:ProductionDraftSession
        compose.setContent {}
        try {
            compose.runOnIdle {
                session=ProductionDraftSession(repository,"stories","Untitled")
                store.put("recovery",session);session.attach {failures+=it};session.initialize(missing)
            }
            compose.waitUntil(5000) {compose.waitForIdle();session.state.loadFailed}
            compose.runOnIdle {
                val before=session.state.value
                session.state.value=fixture("stories")
                assertTrue("Failed restore must keep ordinary editing protected",session.state.value==before)
                session.state.startNew()
                assertFalse(session.state.loading)
                assertFalse(session.state.loadFailed)
                assertNotEquals(missing,session.state.value.id)
                val edited=fixture("stories").copy(id=session.state.value.id,title="")
                session.state.value=edited
                assertTrue("Explicit New must provide a usable editor",session.state.value==edited)
            }
            awaitCheckpoint(directory,session.state.value)
            assertTrue(failures.isNotEmpty())
            if(corrupt) assertArrayEquals(corruptBytes,original.readBytes()) else assertFalse(original.exists())
            assertTrue("Named projects must remain untouched",repository.restore(named.id)==named)
        } finally {compose.runOnIdle {store.clear()}}
    }

    @Test fun missingRestoreHasExplicitNewRecoveryWithoutDeletingUserFiles()=failedRestoreCanStartANewDraft(false)

    @Test fun corruptRestoreHasExplicitNewRecoveryWithoutDeletingUserFiles()=failedRestoreCanStartANewDraft(true)

    @Test fun activitySaveRetriesAFailedReplacementAfterStorageRecovers() {
        val rejectWrites=AtomicBoolean(false)
        val directory=object:File(temporary.root,"retry-checkpoint") {
            override fun isDirectory()=if(rejectWrites.get()) false else super.isDirectory()
            override fun mkdirs()=if(rejectWrites.get()) false else super.mkdirs()
        }
        lateinit var state:MutableState<ProductionProject>
        lateinit var registry:SaveableStateRegistry
        val failures=mutableListOf<Throwable>()
        compose.setContent {
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",directory,{failures+=it})
        }
        val first=fixture("stories")
        editProject(state,first)
        awaitCheckpoint(directory,first)
        compose.runOnIdle {assertTrue(first.id in savedStrings(registry.performSave()))}
        rejectWrites.set(true)
        val latest=first.copy(script=first.script+"An edit retried after storage recovery.")
        editProject(state,latest)
        compose.waitUntil(5000) {compose.waitForIdle();failures.isNotEmpty()}
        assertTrue(ProductionDraftRepository(directory).restore(first.id)==first)
        rejectWrites.set(false)
        compose.runOnIdle {assertTrue(first.id in savedStrings(registry.performSave()))}
        awaitCheckpoint(directory,latest)
        assertTrue("A later real save must retry the pending replacement",ProductionDraftRepository(directory).restore(first.id)==latest)
    }

    @Test fun firstPendingSaveRestoresAfterItsWriteFinishesAndOwnershipIsLost() {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val directory=object:File(temporary.root,"first-pending-reference") {
            override fun isDirectory():Boolean {
                entered.countDown()
                if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled first write was not released")
                return super.isDirectory()
            }
        }
        lateinit var state:MutableState<ProductionProject>
        lateinit var registry:SaveableStateRegistry
        compose.setContent {
            registry=LocalSaveableStateRegistry.current!!
            state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})
        }
        val project=fixture("stories").copy(title="")
        var captured:String?=null
        try {
            compose.runOnIdle {assertFalse("An untouched initial editor has no pending reference",state.value.id in savedStrings(registry.performSave()))}
            editProject(state,project)
            compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L}
            assertFalse(File(directory,"${project.id}.json").exists())
            compose.runOnIdle {
                val values=savedStrings(registry.performSave())
                captured=values.firstOrNull {it==project.id}
                assertFalse("The registered saver must never store the manuscript",project.script in values)
            }
        } finally {release.countDown()}
        awaitCheckpoint(directory,project)
        val repository=ProductionDraftRepository(directory)
        // Discard all live-owner recovery authority, as happens at process death.
        repository.release(repository.acquireOwner("stories") {null})
        lateinit var cold:ProductionDraftSession
        val coldStore=androidx.lifecycle.ViewModelStore()
        try {
            compose.runOnIdle {
                cold=ProductionDraftSession(repository,"stories","Untitled")
                coldStore.put("cold",cold);cold.initialize(captured)
            }
            compose.waitUntil(5000) {compose.waitForIdle();!cold.state.loading}
            compose.runOnIdle {
                assertEquals("A pending known write needs a stable lightweight reference",project.id,captured)
                assertTrue("Cold restore must recover the complete checkpoint after that write finishes",cold.state.value==project)
            }
        } finally {compose.runOnIdle {coldStore.clear()}}
    }

    @Test fun legacyLoadKeepsItsActivityRecoverySourceUntilMigrationCompletes() {
        val directory=temporary.newFolder("legacy-null-reference")
        val repository=ProductionDraftRepository(directory)
        val project=fixture("stories").copy(title="")
        val legacy=Json.encodeToString(ProductionProject.serializer(),project)
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val holding=object:File(temporary.root,"held-legacy-io") {
            override fun isDirectory():Boolean {
                entered.countDown()
                if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled legacy IO was not released")
                return super.isDirectory()
            }
        }
        val disk=Thread {ProductionDraftRepository(holding).save(fixture("audiobook"))}.apply {isDaemon=true;start()}
        assertTrue(entered.await(5,TimeUnit.SECONDS))
        val oldStore=androidx.lifecycle.ViewModelStore()
        val owner=object:ViewModelStoreOwner {override val viewModelStore=oldStore}
        lateinit var registry:SaveableStateRegistry
        lateinit var state:ProductionProjectState
        var captured:String?=null
        try {
            compose.setContent {
                CompositionLocalProvider(LocalViewModelStoreOwner provides owner) {
                    remember {
                        ProductionDraftSession(repository,"stories","Untitled").also {
                            oldStore.put("production-draft:stories:${directory.absolutePath}",it)
                            it.initialize(legacy)
                        }
                    }
                    registry=LocalSaveableStateRegistry.current!!
                    state=rememberProductionProject("stories","Untitled",directory,{throw AssertionError(it)})
                }
            }
            compose.runOnIdle {
                assertTrue("The legacy parse must still be waiting on IO",state.loading)
                val values=savedStrings(registry.performSave())
                captured=values.firstOrNull {it==legacy||it==project.id}
                assertEquals("Pre-decode state must retain the existing legacy recovery source",legacy,captured)
                assertFalse(project.script in values)
            }
        } finally {release.countDown();disk.join(5000)}
        awaitCheckpoint(directory,project)
        compose.runOnIdle {oldStore.clear()}
        repository.release(repository.acquireOwner("stories") {null})
        val coldStore=androidx.lifecycle.ViewModelStore()
        lateinit var cold:ProductionDraftSession
        try {
            compose.runOnIdle {
                cold=ProductionDraftSession(repository,"stories","Untitled")
                coldStore.put("cold-legacy",cold);cold.initialize(captured)
            }
            compose.waitUntil(5000) {compose.waitForIdle();!cold.state.loading}
            assertTrue("A completed migration must survive loss of every RAM owner",cold.state.value==project)
        } finally {compose.runOnIdle {oldStore.clear();coldStore.clear()}}
    }

    private fun invalidActiveReferenceCanRecover(useRetry: Boolean) {
        val directory=temporary.newFolder("invalid-active")
        val repository=ProductionDraftRepository(directory)
        val named=fixture("stories")
        repository.save(named)
        val pointer=File(directory,".active-stories")
        val invalid="99\n../unknown-checkpoint\n".toByteArray()
        pointer.writeBytes(invalid)
        compose.setContent {}
        val store=androidx.lifecycle.ViewModelStore()
        lateinit var session:ProductionDraftSession
        try {
            compose.runOnIdle {
                session=ProductionDraftSession(repository,"stories","Untitled")
                store.put("invalid-active",session);session.attach {};session.initialize(null)
            }
            compose.waitUntil(5000) {compose.waitForIdle();session.state.loadFailed}
            assertArrayEquals("A failed read must preserve unknown metadata",invalid,pointer.readBytes())
            if(useRetry) {
                pointer.writeText("1\n${named.id}\n")
                compose.runOnIdle {session.state.retryRestore()}
                compose.waitUntil(5000) {compose.waitForIdle();!session.state.loading}
                assertTrue(session.state.value==named)
            } else {
                compose.runOnIdle {session.state.startNew()}
                awaitEditor(session.state)
                assertNotEquals(named.id,session.state.value.id)
                awaitCheckpoint(directory,session.state.value)
            }
            assertTrue("Neither recovery may remove a named project",repository.restore(named.id)==named)
        } finally {compose.runOnIdle {store.clear()}}
    }

    @Test fun invalidActiveReferenceSupportsExplicitRetry()=invalidActiveReferenceCanRecover(true)

    @Test fun invalidActiveReferenceSupportsExplicitNewWithoutRemovingUserFiles()=invalidActiveReferenceCanRecover(false)

    @Test fun nonnullReferenceOverridesAnotherProjectsActivePointer() {
        val directory=temporary.newFolder("explicit-reference")
        val repository=ProductionDraftRepository(directory)
        val active=fixture("stories");val requested=fixture("stories").copy(title="")
        repository.save(active);repository.save(requested)
        File(directory,".active-stories").writeText("1\n${active.id}\n")
        compose.setContent {}
        val store=androidx.lifecycle.ViewModelStore()
        lateinit var session:ProductionDraftSession
        try {
            compose.runOnIdle {
                session=ProductionDraftSession(repository,"stories","Untitled")
                store.put("explicit-reference",session);session.initialize(requested.id)
            }
            compose.waitUntil(5000) {compose.waitForIdle();!session.state.loading}
            assertTrue("The fallback must never replace a supplied Activity reference",session.state.value==requested)
            assertTrue(repository.restore(active.id)==active)
        } finally {compose.runOnIdle {store.clear()}}
    }

    @Test fun failedActiveReferencePublicationPreservesThePreviousSelection() {
        val directory=temporary.newFolder("pointer-publication")
        val repository=ProductionDraftRepository(directory)
        val first=fixture("stories");val next=fixture("stories")
        val owner=repository.acquireOwner("stories") {null}
        try {
            assertEquals(first.id,repository.saveOwned(first,owner) {true})
            val pointer=File(directory,".active-stories")
            val previous=pointer.readBytes()
            val pending=File(directory,".active-stories.new").apply {mkdirs()}
            val blocker=File(pending,"preserve").apply {writeText("Controlled replacement failure")}
            assertThrows(java.io.IOException::class.java) {repository.saveOwned(next,owner) {true}}
            assertArrayEquals(previous,pointer.readBytes())
            assertTrue(repository.restore(first.id)==first)
            assertTrue(blocker.delete());assertTrue(pending.delete())
            assertEquals(next.id,repository.saveOwned(next,owner) {true})
            assertEquals("1\n${next.id}\n",pointer.readText())
        } finally {repository.release(owner)}
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
        compose.waitUntil(5000) {compose.waitForIdle();(state as ProductionProjectState).loadFailed}
        compose.runOnIdle {(state as ProductionProjectState).startNew()}
        val project=fixture("stories")
        editProject(state,project)
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

    @Test fun editBackToEarlierValueRewritesPartlyCommittedSameIdCheckpoint() {
        val directory=temporary.newFolder("same-id-edit-back")
        val repository=ProductionDraftRepository(directory)
        val store=androidx.lifecycle.ViewModelStore()
        val failures=mutableListOf<Throwable>()
        lateinit var session:ProductionDraftSession
        compose.setContent {}
        val first=fixture("stories").copy(title="")
        try {
            compose.runOnIdle {
                session=ProductionDraftSession(repository,"stories","Untitled")
                store.put("edit-back",session);session.attach {failures+=it};session.initialize(null)
            }
            awaitEditor(session.state)
            compose.runOnIdle {session.state.value=first}
            awaitCheckpoint(directory,first)
            // Checkpoint publication succeeds, then the independent active
            // pointer fails. The last acknowledged cache is still first.
            val pending=File(directory,".active-stories.new").apply {mkdirs()}
            val blocker=File(pending,"preserve").apply {writeText("Controlled publication failure")}
            val replacement=first.copy(script="A replacement committed under the same UUID.")
            compose.runOnIdle {session.state.value=replacement}
            compose.waitUntil(5000) {compose.waitForIdle();failures.isNotEmpty()}
            assertTrue("The failed save really replaced the same-ID checkpoint",repository.restore(first.id)==replacement)
            assertTrue(blocker.delete());assertTrue(pending.delete())
            compose.runOnIdle {session.state.value=first}
            // This is a genuine cold-disk assertion, not a live state echo.
            awaitCheckpoint(directory,first)
            assertTrue("Cold recovery must match the visible edit-back",ProductionDraftRepository(directory).restore(first.id)==first)
        } finally {compose.runOnIdle {store.clear()}}
    }

    private fun legacyProcessLossBeforeDurability(afterDecode:Boolean,switchProject:Boolean=false) {
        val directory=temporary.newFolder("legacy-interrupted-$afterDecode-$switchProject")
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val block=AtomicBoolean(afterDecode)
        val draftRoot=object:File(directory.path) {
            override fun isDirectory():Boolean {
                if(block.compareAndSet(true,false)) {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled migration write was not released")
                }
                return super.isDirectory()
            }
        }
        val repository=ProductionDraftRepository(draftRoot)
        val project=fixture("stories").copy(title="")
        val legacy=Json.encodeToString(ProductionProject.serializer(),project)
        val oldStore=androidx.lifecycle.ViewModelStore()
        val oldOwner=object:ViewModelStoreOwner {override val viewModelStore=oldStore}
        var captured:String?=null
        lateinit var registry:SaveableStateRegistry
        lateinit var state:ProductionProjectState
        val failures=mutableListOf<Throwable>()
        val disk=if(afterDecode) null else Thread {
            val heldRoot=object:File(temporary.root,"hold-legacy-parse") {
                override fun isDirectory():Boolean {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled parse wait was not released")
                    return super.isDirectory()
                }
            }
            ProductionDraftRepository(heldRoot).save(fixture("audiobook"))
        }.apply {isDaemon=true;start()}
        if(!afterDecode) assertTrue(entered.await(5,TimeUnit.SECONDS))
        val coldStore=androidx.lifecycle.ViewModelStore()
        lateinit var cold:ProductionDraftSession
        try {
            compose.setContent {
                CompositionLocalProvider(LocalViewModelStoreOwner provides oldOwner) {
                    remember {
                        ProductionDraftSession(repository,"stories","Untitled").also {
                            oldStore.put("production-draft:stories:${draftRoot.absolutePath}",it)
                            it.initialize(legacy)
                        }
                    }
                    registry=LocalSaveableStateRegistry.current!!
                    state=rememberProductionProject("stories","Untitled",draftRoot,{failures+=it})
                }
            }
            if(afterDecode) compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L && !state.loading}
            val replacement=fixture("stories").copy(title="",script="A genuinely new explicit draft.")
            compose.runOnIdle {
                assertEquals(!afterDecode,state.loading)
                if(switchProject) state.value=replacement
                val saved=savedStrings(registry.performSave())
                captured=saved.firstOrNull {it==legacy||it==project.id||it==replacement.id}
                if(switchProject) {
                    assertEquals("New must never carry the old legacy manuscript",replacement.id,captured)
                    assertFalse(legacy in saved)
                }
                oldStore.clear()
                // Simulate losing every RAM owner/handoff while IO is still
                // held. Superseded writers cannot finish recovery for us.
                repository.release(repository.acquireOwner("stories") {null})
            }
            release.countDown();disk?.join(5000)
            if(switchProject) return
            compose.runOnIdle {
                cold=ProductionDraftSession(ProductionDraftRepository(directory),"stories","Untitled")
                coldStore.put("cold-interrupted-legacy",cold);cold.attach {failures+=it};cold.initialize(captured)
            }
            compose.waitUntil(5000) {compose.waitForIdle();!cold.state.loading||cold.state.loadFailed}
            assertFalse("Legacy recovery cannot depend on a cancelled migration finishing",cold.state.loadFailed)
            assertTrue("The pre-existing manuscript must survive process loss before migration is durable",cold.state.value==project)
            awaitCheckpoint(directory,project)
            compose.waitUntil(5000) {compose.waitForIdle();cold.savedHandle()==project.id}
        } finally {
            release.countDown();disk?.join(5000)
            compose.runOnIdle {oldStore.clear();coldStore.clear()}
        }
    }

    @Test fun legacyActivitySaveSurvivesProcessLossWhileParseWaits()=legacyProcessLossBeforeDurability(false)
    @Test fun legacyActivitySaveSurvivesProcessLossWhileFirstWriteWaits()=legacyProcessLossBeforeDurability(true)
    @Test fun explicitNewDoesNotSavePreviousLegacyPayload()=legacyProcessLossBeforeDurability(true,true)


    @Test fun pendingLegacyMigrationKeepsRecoverySourceThroughLiveOwnerHandoff() {
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val block=AtomicBoolean(true)
        val directory=object:File(temporary.root,"legacy-live-handoff") {
            override fun isDirectory():Boolean {
                if(block.compareAndSet(true,false)) {
                    entered.countDown()
                    if(!release.await(10,TimeUnit.SECONDS)) throw java.io.IOException("Controlled legacy handoff was not released")
                }
                return super.isDirectory()
            }
        }
        val repository=ProductionDraftRepository(directory)
        val project=fixture("stories").copy(title="")
        val legacy=Json.encodeToString(ProductionProject.serializer(),project)
        val oldStore=androidx.lifecycle.ViewModelStore()
        val nextStore=androidx.lifecycle.ViewModelStore()
        lateinit var old:ProductionDraftSession
        lateinit var next:ProductionDraftSession
        compose.setContent {}
        try {
            compose.runOnIdle {
                old=ProductionDraftSession(repository,"stories","Untitled")
                oldStore.put("legacy-old",old);old.initialize(legacy)
            }
            compose.waitUntil(5000) {compose.waitForIdle();entered.count==0L && !old.state.loading}
            compose.runOnIdle {
                oldStore.clear()
                next=ProductionDraftSession(repository,"stories","Untitled")
                nextStore.put("legacy-new",next);next.initialize(null)
                assertTrue("A live owner keeps the complete decoded draft",next.state.value==project)
                assertEquals("A handoff cannot retire the only recovery source before IO commits",legacy,next.savedHandle())
            }
            release.countDown()
            awaitCheckpoint(directory,project)
            compose.waitUntil(5000) {compose.waitForIdle();next.savedHandle()==project.id}
        } finally {release.countDown();compose.runOnIdle {oldStore.clear();nextStore.clear()}}
    }

}
