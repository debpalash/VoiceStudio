package com.voicestudio.mobile.local

import java.io.File
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder

class WorkflowTest {
    @get:Rule val temporary=TemporaryFolder()
    @Test fun workflowPersistsAndResumesCompletedTransformations()=runTest {
        val root=File(temporary.root,"workflows");val store=PhoneWorkflowStore(root)
        val workflow=PhoneWorkflow(name="Translate and speak",steps=listOf(PhoneWorkflowStep(kind="normalize"),PhoneWorkflowStep(kind="translate",language="fr"),PhoneWorkflowStep(kind="speak")))
        var translated=0;var spoken=0
        val execute: suspend (PhoneWorkflowStep,String)->WorkflowValue = {step,text ->
            if(step.kind=="translate") {translated++;assertEquals("Hello world",text);WorkflowValue("Bonjour")}
            else {spoken++;if(spoken==1) throw CancellationException();assertEquals("Bonjour",text);WorkflowValue(text,LocalAudio(FloatArray(2400) {.2f},24000))}
        }
        try {store.run(workflow,"  Hello   world  ","v1",execute=execute);fail()} catch(_:CancellationException) {}
        val restarted=PhoneWorkflowStore(root)
        val result=restarted.run(workflow,"  Hello   world  ","v1",execute=execute)
        assertEquals("Bonjour",result.text);assertNotNull(result.audio)
        assertEquals(1,translated);assertEquals(2,spoken);assertEquals(workflow.id,restarted.list().single().id);assertEquals("  Hello   world  ",restarted.list().single().inputText)
        restarted.run(workflow,"  Hello   world  ","v2",execute=execute)
        assertEquals(2,translated);assertEquals(3,spoken)
        assertFalse(PhoneWorkflow(name="Invalid",steps=listOf(PhoneWorkflowStep(kind="speak"),PhoneWorkflowStep(kind="translate"))).valid)
    }
    @Test fun branchesAndRetriesResumeTheirExactNextStep()=runTest {
        val root=File(temporary.root,"branches");val store=PhoneWorkflowStore(root)
        val workflow=PhoneWorkflow(name="Branch",steps=listOf(PhoneWorkflowStep(kind="condition",containsText="missing",skipSteps=1),PhoneWorkflowStep(kind="translate"),PhoneWorkflowStep(kind="translate",retries=1)))
        var calls=0
        val execute: suspend (PhoneWorkflowStep,String)->WorkflowValue={_,text->calls++;if(calls==1) error("transient");WorkflowValue("$text translated")}
        val first=store.run(workflow,"hello","v1",execute=execute)
        assertEquals("hello translated",first.text);assertNull(first.audio);assertEquals(2,calls)
        val resumed=PhoneWorkflowStore(root).run(workflow,"hello","v1",execute=execute)
        assertEquals(first.text,resumed.text);assertEquals(2,calls)
        val skipSpeech=PhoneWorkflow(name="Quiet",steps=listOf(PhoneWorkflowStep(kind="condition",containsText="say",skipSteps=1),PhoneWorkflowStep(kind="speak")))
        assertNull(store.run(skipSpeech,"quiet","v1") {_,_->error("Skipped step executed")}.audio)
    }
    @Test fun importedMediaSurvivesOriginalRemovalAndRejectsTampering()=runTest {
        val source=File(temporary.root,"voice.wav").apply {writeText("fixture")}
        val store=PhoneWorkflowStore(File(temporary.root,"media"))
        val workflow=store.importAudio(source,PhoneWorkflow(name="Transcribe",steps=listOf(PhoneWorkflowStep(kind="transcribe"))))
        assertTrue(source.delete());val imported=store.source(workflow)
        assertEquals("recognized",store.run(workflow,"","asr-v1") {_,_->WorkflowValue("recognized")}.text)
        imported.writeText("changed")
        try {store.run(workflow,"","asr-v1") {_,_->WorkflowValue("wrong")};fail("Tampered source accepted")} catch(_:IllegalArgumentException) {}
        assertFalse(PhoneWorkflow(name="Bad",steps=listOf(PhoneWorkflowStep(kind="normalize"),PhoneWorkflowStep(kind="transcribe"))).valid)
    }
    @Test fun designedVoiceRetainsSeedRecipeAndMarkedReferenceWithoutASR()=runTest {
        val store=PhoneVoiceStore(File(temporary.root,"voices"));val model=fixtureModel("clone")
        val voice=store.saveDesign("Designed","A designed reference",LocalAudio(FloatArray(4800) {.2f},24000),model,"female, young adult",77)
        val restored=store.list().single()
        assertEquals(77L,restored.designSeed);assertEquals("female, young adult",restored.designInstruction)
        assertEquals(model.repo,restored.modelRepo);assertEquals("A designed reference",restored.transcript)
        assertEquals(4800,store.reference(voice).samples.size)
        assertTrue(store.preview(voice).readText(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
    }
}
