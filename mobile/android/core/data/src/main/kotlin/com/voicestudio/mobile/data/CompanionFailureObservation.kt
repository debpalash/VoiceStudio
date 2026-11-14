package com.voicestudio.mobile.data

import com.voicestudio.mobile.network.JobEvents
import kotlinx.serialization.json.*

/** Local authority only; never serialized into checkpoints or report properties. */
data class CompanionDiagnosticObservation(val revision: Long, val epoch: String)

internal class CompanionTaskFailureGate {
    private var task: String? = null
    private var observation: CompanionDiagnosticObservation? = null
    private var sequence = 0
    private var serverFloor: Double? = null
    fun observe(taskID: String, page: JobEvents, before: CompanionDiagnosticObservation?,
        after: CompanionDiagnosticObservation?, submitted: CompanionDiagnosticObservation?): CompanionDiagnosticObservation? {
        if(before == null || before != after) { observation = null; return null }
        if(submitted == after) return after // This POST was submitted under the unchanged grant.
        if(task != taskID || observation != after) {
            task = taskID; observation = after
            sequence = page.events.maxOfOrNull {it.seq} ?: 0
            serverFloor = (page.events.mapNotNull {it.createdAt} + listOfNotNull(page.job.updatedAt,page.job.createdAt))
                .filter {it.isFinite() && it > 0}.maxOrNull()
            return null // The first page is history, even if the task is already terminal.
        }
        val floor = serverFloor ?: return null
        return after.takeIf {page.events.any {event ->
            event.seq > sequence && event.createdAt?.let {it.isFinite() && it >= floor} == true &&
                runCatching {Json.parseToJsonElement(event.payload.removePrefix("data:").trim()).jsonObject["type"]?.jsonPrimitive?.content in setOf("error","failed")}.getOrDefault(false)
        }}
    }
}
