package com.voicestudio.mobile.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

@Serializable data class CallVoice(val id:String,val name:String,val kind:String?=null,private val verified_own_voice:JsonElement=JsonPrimitive(false)) {val eligible get()=kind=="design"||verified_own_voice==JsonPrimitive(true)||verified_own_voice==JsonPrimitive(1)}
@Serializable data class CallReadiness(val id:String,val ok:Boolean,val detail:String)
@Serializable data class CallOptions(val disclosure:String)
@Serializable data class CallTurn(val speaker:String,val text:String,val t:Double)
@Serializable data class CallRecord(val id:String,val to_masked:String,val status:String,val summary:String?=null,val transcript:List<CallTurn> = emptyList(),val takeover:Boolean=false) {val live get()=status !in setOf("completed","busy","no_answer","failed","canceled")}
@Serializable private data class CallList(val calls:List<CallRecord>)
@Serializable private data class StartedCall(val call:CallRecord)
class CallsApi(private val api:VoiceStudioApi) {
    private suspend inline fun <reified T> get(path:String):T=withContext(Dispatchers.IO) {api.raw(path).use {api.json.decodeFromString<T>(it.body!!.string())}}
    private suspend inline fun <reified T> post(path:String,body:JsonObject):T=withContext(Dispatchers.IO) {api.raw(path,"POST",body.toString().toRequestBody("application/json".toMediaType())).use {api.json.decodeFromString<T>(it.body!!.string())}}
    suspend fun voices():List<CallVoice> = get("/profiles")
    suspend fun readiness():List<CallReadiness> = get("/calls/readiness")
    suspend fun options():CallOptions = get("/calls/options")
    suspend fun recent():List<CallRecord> = get<CallList>("/calls").calls
    suspend fun call(id:String):CallRecord = get("/calls/"+id(id))
    suspend fun start(to:String,brief:String,voice:String,disclosure:String,minutes:Int):CallRecord=post<StartedCall>("/calls",buildJsonObject {put("to",to);put("brief",brief);put("profile_id",voice);put("disclosure",disclosure);put("max_minutes",minutes)}).call
    suspend fun control(id:String,action:String,text:String="",enabled:Boolean=false) {
        require(action in setOf("say","takeover","hangup"))
        withContext(Dispatchers.IO) {api.raw("/calls/"+id(id)+"/"+action,"POST",buildJsonObject {if(action=="say") put("text",text);if(action=="takeover") put("enabled",enabled)}.toString().toRequestBody("application/json".toMediaType())).close()}
    }
    private fun id(value:String):String {require(value.isNotBlank()&&value.matches(Regex("[a-zA-Z0-9_-]+")));return value}
}
