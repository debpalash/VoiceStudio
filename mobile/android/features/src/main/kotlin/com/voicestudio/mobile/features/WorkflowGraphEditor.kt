package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.local.*

fun workflowGraphStepKey(kind: String)=when(kind) {"start"->"workflows.step_start";"audio"->"workflows.step_audio";"speak"->"workflows.step_speak";"transcribe"->"workflows.step_transcribe";"translate"->"workflows.step_translate";"convert"->"workflows.step_convert";"normalize"->"workflows.step_normalize";"condition"->"workflows.step_condition";else->"workflows.step_end"}
@Composable fun WorkflowGraphEditor(graph: WorkflowGraph,voices: List<Choice>,languages: List<Choice>,change: (WorkflowGraph)->Unit) {
    var kind by remember {mutableStateOf("condition")}
    fun edit(id: String,body: (WorkflowGraphStep)->WorkflowGraphStep)=change(graph.copy(steps=graph.steps.map {if(it.id==id) body(it) else it}))
    Text(tr("mobile.workflowParity.graph_hint"))
    graph.steps.forEachIndexed {index,step ->
        key(step.id) {Group {Column(Modifier.padding(VSTheme.inset)) {
            Row {Text("${index+1} · "+tr(workflowGraphStepKey(step.kind)),Modifier.weight(1f));if(step.kind !in setOf("start","audio")) IconAction("workflows.remove_step",Icons.Default.Delete) {change(graph.copy(steps=graph.steps.filterNot {it.id==step.id},connections=graph.connections.filterNot {it.source==step.id||it.target==step.id}))}}
            Field("workflows.step_name",step.title.orEmpty(),{value->edit(step.id) {it.copy(title=value)}})
            if(step.kind in setOf("start","audio")) ChoicePicker("mobile.workflowParity.input_type",step.kind,listOf(Choice("start",tr("clone.script")),Choice("audio",tr("mobile.flow.import_media")))) {value->edit(step.id) {it.copy(kind=value)}}
            val destinations=listOf(Choice("",tr("mobile.workflowParity.choose_next")))+graph.steps.withIndex().filter {it.value.id!=step.id}.map {Choice(it.value.id,"${it.index+1} · "+it.value.title?.takeIf {t->t.isNotBlank()}.orEmpty().ifEmpty {tr(workflowGraphStepKey(it.value.kind))})}
            if(step.kind=="condition") {
                Field("workflows.condition_phrase",step.text.orEmpty(),{value->edit(step.id) {it.copy(text=value)}})
                ChoicePicker("workflows.condition_match",step.match ?: "contains",listOf(Choice("contains",tr("workflows.match_contains")),Choice("equals",tr("workflows.match_equals")),Choice("starts",tr("workflows.match_starts")),Choice("ends",tr("workflows.match_ends")))) {value->edit(step.id) {it.copy(match=value)}}
                Text(tr("workflows.condition_hint"))
                ChoicePicker("mobile.workflowParity.yes_route",graph.destination(step.id,"yes").orEmpty(),destinations) {change(graph.connect(step.id,it,"yes"))}
                ChoicePicker("mobile.workflowParity.no_route",graph.destination(step.id,"no").orEmpty(),destinations) {change(graph.connect(step.id,it,"no"))}
            } else if(step.kind!="end") ChoicePicker("mobile.workflowParity.next_route",graph.destination(step.id).orEmpty(),destinations) {change(graph.connect(step.id,it))}
            if(step.kind in setOf("speak","convert")) {
                ChoicePicker("mobile.speak.voice",step.voiceId.orEmpty(),listOf(Choice("",tr("mobile.workflowParity.bind_voices"),enabled=false))+voices.filter {step.kind!="convert"||it.id.startsWith("phone:")}) {value->edit(step.id) {it.copy(voiceId=value)}}
                if(step.kind=="speak") ChoicePicker("stories.global_speed",(step.speed ?: 1.0).toString(),listOf(.5,.75,1.0,1.25,1.5,2.0).map {Choice(it.toString(),"$it×")}) {value->edit(step.id) {it.copy(speed=value.toDouble())}}
            }
            if(step.kind=="translate") {
                ChoicePicker("mobile.workflowParity.source_language",step.sourceLanguage ?: "auto",listOf(Choice("auto",tr("languagePicker.auto")))+languages) {value->edit(step.id) {it.copy(sourceLanguage=value)}}
                ChoicePicker("mobile.speak.language",step.language ?: "en",languages) {value->edit(step.id) {it.copy(language=value)}}
            }
            if(step.kind=="speak") ChoicePicker("mobile.speak.language",step.language ?: "Auto",listOf(Choice("Auto",tr("languagePicker.auto")))+languages.map {it.copy(enabled=!step.voiceId.orEmpty().startsWith("phone:"))}) {value->edit(step.id) {it.copy(language=value)}}
            if(step.kind=="normalize") ChoicePicker("workflowRun.peak",(step.targetDb ?: -2.0).toString(),listOf(-24.0,-18.0,-12.0,-6.0,-2.0,-1.0).map {Choice(it.toString(),"$it dBFS")}) {value->edit(step.id) {it.copy(targetDb=value.toDouble())}}
            if(step.kind !in setOf("start","audio","condition","end")) ChoicePicker("mobile.flow.retries_label",(step.retries ?: 0).toString(),(0..3).map {Choice(it.toString(),it.toString())}) {value->edit(step.id) {it.copy(retries=value.toInt())}}
        }}}
    }
    ChoicePicker("workflows.add_step",kind,listOf("speak","condition","translate","transcribe","convert","normalize","end").map {Choice(it,tr(workflowGraphStepKey(it)))}) {kind=it}
    Secondary("workflows.add_step",graph.steps.size<40) {change(graph.copy(steps=graph.steps+WorkflowGraphStep(kind=kind,text=if(kind=="condition") "?" else null,voiceId=if(kind=="speak") "preset:0" else null,language=if(kind=="translate") "en" else null,sourceLanguage=if(kind=="translate") "auto" else null)))}
    val error=runCatching {graph.validate()}.exceptionOrNull() as? com.voicestudio.mobile.network.ClientFailure
    error?.let {Text(tr(it.key,it.params))}
}
