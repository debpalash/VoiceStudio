package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.network.ContentReport
import com.voicestudio.mobile.network.ReportSubject

/** Reason ids in contract order with their full catalogue keys. */
internal val reportReasons = listOf(
    "sexual" to "mobile.report.reasons.sexual",
    "hateful" to "mobile.report.reasons.hateful",
    "impersonation" to "mobile.report.reasons.impersonation",
    "misleading" to "mobile.report.reasons.misleading",
    "other" to "mobile.report.reasons.other",
)

/** The Report action on one generated result; hidden when the app supplies no report client. */
@Composable fun ReportAction(vm: StudioViewModel, subject: () -> ReportSubject) {
    if(LocalContentReportClient.current==null) return
    IconAction("mobile.report.action",Icons.Default.Warning) {vm.reports.open(subject())}
}

/** Full-width Report button under a just-made result. */
@Composable fun ReportButton(vm: StudioViewModel, enabled: Boolean = true, subject: () -> ReportSubject) {
    if(LocalContentReportClient.current==null) return
    Secondary("mobile.report.action",enabled) {vm.reports.open(subject())}
}

/** Hosts the report sheet once for the whole app and announces a stored report. */
@Composable fun ContentReportHost(vm: StudioViewModel, snackbars: SnackbarHostState) {
    val l=LocalL10n.current
    val strings by rememberUpdatedState(l)
    LaunchedEffect(vm.reports) {vm.reports.sent.collect {snackbars.showSnackbar(strings.t("mobile.report.sent"))}}
    val sheet by vm.reports.sheet.collectAsState()
    val client=LocalContentReportClient.current
    val current=sheet
    if(current!=null&&client!=null) ContentReportSheet(current,
        edit=vm.reports::edit, send={vm.reports.send(client)}, dismiss=vm.reports::close)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable fun ContentReportSheet(sheet: ReportSheet, edit: ((com.voicestudio.mobile.network.ReportDraft)->com.voicestudio.mobile.network.ReportDraft)->Unit, send: ()->Unit, dismiss: ()->Unit) {
    val draft=sheet.draft
    val sending=sheet.status==ReportStatus.SENDING
    ModalBottomSheet(onDismissRequest=dismiss,containerColor=role("surfaceOverlay")) {
        Column(Modifier.fillMaxWidth().heightIn(max=(LocalConfiguration.current.screenHeightDp*.7f).dp).dismissKeyboardOnTap()
            .verticalScroll(rememberScrollState()).padding(horizontal=VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Text(tr("mobile.report.title"),style=MaterialTheme.typography.titleLarge,modifier=Modifier.semantics {heading()})
            Text(tr("mobile.report.reason"),style=MaterialTheme.typography.titleSmall,modifier=Modifier.semantics {heading()})
            Column(Modifier.fillMaxWidth().selectableGroup()) {
                reportReasons.forEach {(id,key) ->
                    val selected=draft.reason==id
                    Row(Modifier.fillMaxWidth().heightIn(min=VSTheme.touchTarget).testTag("report.reason.$id")
                        .selectable(selected=selected,enabled=!sending,role=Role.RadioButton,onClick={edit {it.copy(reason=id)}}),
                        verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
                        RadioButton(selected=selected,onClick=null,enabled=!sending)
                        Text(tr(key),Modifier.weight(1f))
                    }
                }
            }
            OutlinedTextField(value=draft.note,onValueChange={value -> edit {it.copy(note=value.take(ContentReport.NOTE_LIMIT))}},
                enabled=!sending,label={Text(tr("mobile.report.note"))},minLines=3,maxLines=6,
                supportingText={Text("${draft.note.length} / ${ContentReport.NOTE_LIMIT}",Modifier.fillMaxWidth().clearAndSetSemantics {},style=MaterialTheme.typography.labelSmall)},
                modifier=Modifier.fillMaxWidth().testTag("report.note"),shape=MaterialTheme.shapes.small,
                colors=OutlinedTextFieldDefaults.colors(focusedBorderColor=role("focus"),unfocusedBorderColor=role("border"),focusedContainerColor=role("input"),unfocusedContainerColor=role("input")))
            if(sheet.subject.hasTypedText) ConsentCheck("mobile.report.include_text",draft.includeText) {checked -> if(!sending) edit {it.copy(includeText=checked)}}
            Text(tr("mobile.report.disclosure"),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
            val message=when(sheet.status) {
                ReportStatus.FAILED -> "mobile.report.failed"
                ReportStatus.RATE_LIMITED -> "mobile.report.rate_limited"
                ReportStatus.INVALID -> "mobile.report.invalid"
                else -> null
            }
            message?.let {Text(tr(it),color=role("error"),style=MaterialTheme.typography.bodyMedium,modifier=Modifier.testTag("report.status").semantics {liveRegion=LiveRegionMode.Polite})}
        }
        Row(Modifier.fillMaxWidth().padding(VSTheme.inset),horizontalArrangement=Arrangement.spacedBy(VSTheme.gap,Alignment.End),verticalAlignment=Alignment.CenterVertically) {
            TextButton(onClick=dismiss,shape=MaterialTheme.shapes.small,modifier=Modifier.heightIn(min=VSTheme.touchTarget)) {Text(tr("common.cancel"))}
            val label=when {
                sending -> "mobile.report.sending"
                sheet.status==ReportStatus.FAILED -> "mobile.report.retry"
                else -> "mobile.report.send"
            }
            Button(onClick=send,enabled=sheet.canSend,shape=MaterialTheme.shapes.small,modifier=Modifier.heightIn(min=VSTheme.touchTarget).testTag("report.send"),
                colors=ButtonDefaults.buttonColors(containerColor=role("messageAction"),contentColor=role("messageActionForeground"))) {
                if(sending) {CircularProgressIndicator(Modifier.size(16.dp),strokeWidth=2.dp,color=role("messageActionForeground"));Spacer(Modifier.width(VSTheme.gap))}
                Text(tr(label))
            }
        }
    }
}
