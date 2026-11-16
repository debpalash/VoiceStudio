package com.voicestudio.mobile.network

import kotlinx.serialization.json.*

/** Build configuration only; no credential is ever placed in a stored report. */
class DiagnosticAppConfiguration(appID: String, ingestKey: String, val host: String, val sessionPings: Boolean = false) {
    val appID=appID.trim()
    internal val ingestKey=ingestKey.trim()
    init {
        require(Regex("[a-z0-9]{6,32}").matches(this.appID))
        require(Regex("smk_[a-z2-7]{39}").matches(this.ingestKey))
        DiagnosticDestination(this.appID, host, "https://voicestudio.sh")
    }
    companion object {
        fun resolve(generated: String?, environment: Map<String,String>, fallbackHost: String): DiagnosticAppConfiguration? {
            val raw = runCatching { Json.parseToJsonElement(generated ?: "{}").jsonObject }.getOrDefault(JsonObject(emptyMap()))
            fun selected(env: String, field: String) = environment[env] ?: (raw[field] as? JsonPrimitive)?.takeIf { it.isString }?.content
            val host = selected("SUPERMETRY_APP_HOST", "host") ?: fallbackHost
            val validHost = runCatching { DiagnosticDestination("native01",host,"https://voicestudio.sh") }.isSuccess
            return runCatching { DiagnosticAppConfiguration(selected("SUPERMETRY_APP_ID","app_id") ?: "", selected("SUPERMETRY_INGEST_KEY","ingest_key") ?: "", if(validHost) host else fallbackHost,
                (raw["diagnostics"] as? JsonObject)?.get("sessionPings") == JsonPrimitive(true)) }.getOrNull()
        }
    }
}

data class DiagnosticTransportReply(val status: Int, val body: ByteArray = byteArrayOf())
data class DiagnosticDeliveryResult(val id:String,val status:Int,val reason:String,val warningTag:String?=null)
private val diagnosticAppTagOrder=listOf("category","source","stage","error_code","engine_id","uptime_bucket","elapsed_bucket","memory_bucket","disk_bucket","schema_version")

/** ONE mapping from the existing closed projection; no exception/content enters here. */
internal fun diagnosticAppEvent(props: JsonObject, id: String, timestamp: Long, installID: String, sessionID: String? = null, session: Boolean = false): JsonObject = buildJsonObject {
    fun string(key: String) = (props[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
    val category = string("category") ?: "other"
    val crash = category in setOf("app_crash","backend_crash","renderer_crash")
    val kind = when { crash -> "crash"; category in setOf("install_failed","install_stalled","incompatibility") -> category
        category in setOf("generation_failed","model_load_failed","engine_failed","download_failed","operation_failed") -> "operation_failed"; else -> "error" }
    put("id",id);put("ts",timestamp);put("install_id",installID);put("runtime","kotlin");put("kind",if(session) "session" else kind)
    val platform = when(val p=string("platform")) { "darwin","mac","osx"->"macos";"win32","win"->"windows";"windows","macos","linux","ios","android","web"->p;else->"other" }
    val arch = when(string("arch")) {"x64","amd64","x86_64"->"x64";"arm64","aarch64"->"arm64";"ia32","x86"->"x86";"arm","armv7l"->"arm";else->"other"}
    put("platform",platform);put("arch",arch)
    sessionID?.let {put("session_id",it)}
    fun copyString(source: String, target: String, cap: Int) { string(source)?.takeIf {it.isNotEmpty() && it.toByteArray(Charsets.UTF_8).size<=cap}?.let {put(target,it)} }
    copyString("app_version","release",64)
    if(!session) {
        put("level",if(crash) "fatal" else "error");put("handled",!crash)
        copyString("build_commit","build",64);copyString("device_family","device_class",32);copyString("reason","exit_reason",32);copyString("error_type","error_type",64);copyString("signal","signal",32)
        for(key in listOf("exit_code","frames","fingerprint","occurrences")) props[key]?.let {put(key,it)}
        put("tags",buildJsonObject {
            for(key in diagnosticAppTagOrder) {
                val value=props[key] as? JsonPrimitive ?: continue
                if(value.content.isNotEmpty() && (!value.isString || value.content.toByteArray(Charsets.UTF_8).size<=200))put(key,value)
            }
        })
    }
}

internal fun diagnosticAppBody(site: String, events: List<JsonObject>) = buildJsonObject {put("site",site);put("events",JsonArray(events))}.toString().toByteArray(Charsets.UTF_8)

/** Never infer accepted indices from a malformed or ambiguous acknowledgement. */
internal fun diagnosticAppCompleted(reply: DiagnosticTransportReply, count: Int): Set<Int> {
    return diagnosticAppAcknowledgement(reply,count)?.completed.orEmpty()
}
internal data class DiagnosticAppAcknowledgement(val completed:Set<Int>,val reasons:Map<Int,String>,val warnings:List<Pair<Int,String>> = emptyList())
internal fun diagnosticAppAcknowledgement(reply: DiagnosticTransportReply, count: Int):DiagnosticAppAcknowledgement? {
    if(reply.status==400)return DiagnosticAppAcknowledgement((0 until count).toSet(),(0 until count).associateWith {"invalid"})
    if(reply.status!=202)return null
    return runCatching {
        val raw=Json.parseToJsonElement(reply.body.toString(Charsets.UTF_8)).jsonObject
        val accepted=(raw["accepted"] as? JsonPrimitive)?.takeIf {!it.isString}?.intOrNull ?: return null
        require(accepted in 0..count)
        val drops=raw.getValue("dropped").jsonArray
        val indices=mutableSetOf<Int>();val terminal=mutableSetOf<Int>();val reasons=mutableMapOf<Int,String>()
        for(drop in drops) {
            val entry=drop.jsonObject
            val index=(entry["index"] as? JsonPrimitive)?.takeIf {!it.isString}?.intOrNull ?: return null
            val reason=(entry["reason"] as? JsonPrimitive)?.takeIf {it.isString}?.content
            require(index in 0 until count && indices.add(index) && reason in setOf("too_old","invalid","duplicate","rate_limited"))
            if(reason!="rate_limited")terminal.add(index)
            reasons[index]=requireNotNull(reason)
        }
        require(accepted+indices.size==count)
        for(index in (0 until count).toSet()-indices)reasons[index]="accepted"
        val warnings=(raw["warnings"] as? JsonArray).orEmpty().take(20).mapNotNull {warning->
            val entry=warning as? JsonObject ?: return@mapNotNull null
            val index=(entry["index"] as? JsonPrimitive)?.takeIf {!it.isString}?.intOrNull ?: return@mapNotNull null
            val tag=(entry["tag"] as? JsonPrimitive)?.takeIf {it.isString}?.content
            if(index in 0 until count && entry["reason"]==JsonPrimitive("tag_not_allowed") && tag in diagnosticAppTagOrder)index to requireNotNull(tag) else null
        }
        DiagnosticAppAcknowledgement((0 until count).toSet()-indices+terminal,reasons,warnings)
    }.getOrNull()
}
