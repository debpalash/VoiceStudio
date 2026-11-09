package com.voicestudio.mobile.local

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import com.voicestudio.mobile.network.ClientFailure

@Serializable data class LicenceReceipt(val fingerprint: String, val appVersion: String, val time: Long, val accepted: Boolean)
/** Private no-backup storage; persist before exposing acceptance. Fail closed. */
class LicenceGate(private val file: File, private val appVersion: String) {
    private val json = Json { ignoreUnknownKeys = true }
    private fun records(): Map<String,List<LicenceReceipt>> = if(!file.exists()) emptyMap() else try {
        json.decodeFromString<Map<String,List<LicenceReceipt>>>(file.readText())
    } catch(e: Exception) { throw ClientFailure("mobile.local.licence_storage") }
    @Synchronized fun history(model: PhoneModel): List<LicenceReceipt> = records()[model.id].orEmpty()
    @Synchronized fun accepted(model: PhoneModel): Boolean = !model.gated || records()[model.id]?.lastOrNull()?.let {
        it.accepted && it.fingerprint == model.licence.termsFingerprint
    } == true
    @Synchronized fun requireAccepted(model: PhoneModel) { if(!accepted(model)) throw ClientFailure("mobile.local.licence_required") }
    @Synchronized fun record(model: PhoneModel, accepted: Boolean, shownFingerprint: String = model.licence.termsFingerprint) {
        if(shownFingerprint != model.licence.termsFingerprint) throw ClientFailure("mobile.local.licence_required")
        val updated = records().toMutableMap()
        updated[model.id] = (updated[model.id].orEmpty() + LicenceReceipt(shownFingerprint,appVersion,System.currentTimeMillis(),accepted)).takeLast(20)
        file.parentFile!!.mkdirs()
        val temp = File(file.parentFile,file.name+".tmp")
        temp.outputStream().use { it.write(json.encodeToString(updated).toByteArray());it.fd.sync() }
        if(!temp.renameTo(file)) throw ClientFailure("mobile.local.licence_storage")
    }
}
