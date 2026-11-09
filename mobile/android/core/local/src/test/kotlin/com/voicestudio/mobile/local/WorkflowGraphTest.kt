package com.voicestudio.mobile.local

import java.io.File
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest

class WorkflowGraphTest {
    @get:Rule val temporary=TemporaryFolder()
    private fun graph(): WorkflowGraph {
        val g=WorkflowGraph.create("Branch",listOf("start","condition","translate","translate","translate","end"))
        val n=g.steps.mapIndexed {i,s->if(i==1) s.copy(text="URGENT") else if(i in 2..4) s.copy(language="fr",sourceLanguage="auto") else s}
        return g.copy(steps=n,connections=listOf(WorkflowGraphConnection(source=n[0].id,target=n[1].id),WorkflowGraphConnection(source=n[1].id,target=n[2].id,sourceHandle="yes"),WorkflowGraphConnection(source=n[1].id,target=n[3].id,sourceHandle="no"),WorkflowGraphConnection(source=n[2].id,target=n[4].id),WorkflowGraphConnection(source=n[3].id,target=n[4].id),WorkflowGraphConnection(source=n[4].id,target=n[5].id)))
    }
    @Test fun bothBranchesRejoinAndRestartReusesCompletedNode()=runTest {
        val g=graph();val workflow=PhoneWorkflow(name="Branch",graph=g);val root=File(temporary.root,"runs");val store=PhoneWorkflowStore(root)
        val visits=mutableListOf<String>();var interrupt=true
        val execute: suspend (WorkflowGraphStep,WorkflowGraphValue,File)->WorkflowGraphOutput={s,value,_->visits+=s.id;if(s.id==g.steps[4].id&&interrupt) {interrupt=false;throw CancellationException()};WorkflowGraphOutput(text=if(s.id==g.steps[4].id) value.text+" rejoined" else if(s.id==g.steps[2].id) "YES" else "NO")}
        try {store.runGraph(workflow,"an urgent task","v1",execute=execute);fail()} catch(_:CancellationException) {}
        val restored=PhoneWorkflowStore(root)
        assertEquals("YES rejoined",restored.runGraph(workflow,"an urgent task","v1",execute=execute).text)
        assertEquals(1,visits.count {it==g.steps[2].id});assertEquals(0,visits.count {it==g.steps[3].id});assertEquals(2,visits.count {it==g.steps[4].id})
        assertEquals("NO rejoined",restored.runGraph(workflow,"ordinary","v1",execute=execute).text);assertEquals(2,restored.graphRuns(workflow).size)
    }
    @Test fun desktopRoundTripAndGraphContractRejectUnsupportedNodes() {
        val g=graph();g.validate();val json=WorkflowGraphInterchange.encode(g,"Hello")
        val restored=WorkflowGraphInterchange.decode(json);assertEquals(g.connections,restored.connections);assertTrue(restored.steps.all {it.position!=null})
        for(bad in listOf(g.copy(connections=g.connections.filterNot {it.sourceHandle=="no"}),g.copy(steps=g.steps.mapIndexed {i,s->if(i==2) s.copy(kind="call") else s}),g.copy(steps=g.steps.mapIndexed {i,s->if(i==2) s.copy(kind="speak",voiceId="preset:0") else s}))) {
            try {bad.validate();fail("Invalid graph accepted")} catch(_:com.voicestudio.mobile.network.ClientFailure) {}
        }
        val before=java.util.Locale.getDefault();try {java.util.Locale.setDefault(java.util.Locale.forLanguageTag("tr"));assertTrue(WorkflowGraphStep(kind="condition",text="I",match="equals").matches(" i "))} finally {java.util.Locale.setDefault(before)}
    }
    @Test fun markedAudioCacheRejectsCorruption()=runTest {
        val workflow=PhoneWorkflow(name="Speak",graph=WorkflowGraph.create("Speak"));val store=PhoneWorkflowStore(File(temporary.root,"audio"))
        val execute: suspend (WorkflowGraphStep,WorkflowGraphValue,File)->WorkflowGraphOutput={_,_,_->WorkflowGraphOutput(audio=LocalAudio(floatArrayOf(.2f,-.2f),24000))}
        val file=requireNotNull(store.runGraph(workflow,"Hello","v1",execute=execute).audio);assertTrue(file.readText(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"));file.writeText("broken")
        try {store.runGraph(workflow,"Hello","v1",execute=execute);fail()} catch(_:com.voicestudio.mobile.network.ClientFailure) {}
        assertFalse(store.graphRuns(workflow).first().done)
    }
}
