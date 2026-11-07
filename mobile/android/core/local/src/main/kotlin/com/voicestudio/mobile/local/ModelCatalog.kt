package com.voicestudio.mobile.local

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.*
import java.io.File

object ModelAssetSource {
    fun validUrl(text:String):Boolean=runCatching {
        val u=java.net.URI(text);val parts=u.path.trim('/').split('/')
        u.scheme=="https"&&u.userInfo==null&&u.port==-1&&u.query==null&&u.fragment==null&&parts.all {it!="."&&it!=".."&&it.matches(Regex("[A-Za-z0-9_.-]+"))}&&
            ((u.host=="github.com"&&parts.size==6&&parts.take(4)==listOf("k2-fsa","sherpa-onnx","releases","download")&&parts.last().endsWith(".onnx"))||
             (u.host=="huggingface.co"&&parts.size>=5&&parts[2]=="resolve"&&parts[3].matches(Regex("[a-f0-9]{40}"))))
    }.getOrDefault(false)
    fun validBundled(path:String)=!path.startsWith('/')&&path.split('/').all {it!="."&&it!=".."&&it.matches(Regex("[A-Za-z0-9_.-]+"))}
}
@Serializable data class ModelFile(val path: String, val size: Long, val sha256: String, val role: String,val url:String?=null,val bundled:String?=null)
@Serializable data class ModelLicence(val id: String, val category: String,
    @SerialName("requires_acceptance") val requiresAcceptance: Boolean,
    val credit: String, @SerialName("rights_holder") val rightsHolder: String,
    @SerialName("terms_fingerprint") val termsFingerprint: String, val terms: JsonObject = JsonObject(emptyMap()))
/** Plain-language reading of declared terms; the raw terms and fingerprint remain the accepted record. */
data class LicenceReview(val rights: List<Pair<String, String>>, val notes: String?, val evidenceUrl: String?, val sourceUrl: String?)
/** One declared term as label and value; every key of `terms` appears, matching what the fingerprint covers. */
data class TermLine(val key: String, val label: String, val value: String)
val ModelLicence.termLines: List<TermLine> get() {
    fun text(value: JsonElement): String = when (value) {
        is JsonNull -> "–"
        is JsonPrimitive -> value.content.ifEmpty { "–" }
        is JsonArray -> if (value.isEmpty()) "–" else value.joinToString(", ") { text(it) }
        is JsonObject -> if (value.isEmpty()) "–" else value.keys.sorted().joinToString("; ") { "$it: ${text(value.getValue(it))}" }
    }
    return terms.keys.sorted().map { key -> TermLine(key, key.replace('_', ' ').replaceFirstChar { it.uppercase() }, text(terms.getValue(key))) }
}
val ModelLicence.review: LicenceReview get() {
    fun text(key: String) = (terms[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
    // Only plain https links are tappable.
    fun link(key: String) = text(key)?.takeIf { value ->
        runCatching { java.net.URI(value) }.getOrNull()?.let { it.scheme == "https" && !it.host.isNullOrEmpty() && it.userInfo == null } == true
    }
    val fields = listOf("commercial" to "commercial_inference", "outputs" to "commercial_outputs", "redistribution" to "redistribution", "voice" to "voice_rights")
    return LicenceReview(fields.map { (key, field) -> key to if (text(field) == "restricted") "restricted" else "unknown" },
        text("notes")?.trim()?.takeIf { it.isNotEmpty() }, link("evidence_url"), link("source_url"))
}
@Serializable data class PhoneModel(val id: String, val capability: String, val runtime: String, val repo: String, val revision: String,
    val files: List<ModelFile>, @SerialName("total_bytes") val totalBytes: Long, val languages: List<String>,
    @SerialName("min_ram_gb") val minRamGb: Double, val licence: ModelLicence, val config: JsonObject = JsonObject(emptyMap()), val name: String = "") {
    val supportsSpeech get()=capability=="tts"||capability=="clone"
    val supportsCloning get()=capability=="clone"
    val requiresReference get()=capability=="clone"
    val supportsDesign get()=runtime=="omnivoice.cpp"
    fun supports(value:String)=when(value) {"tts"->supportsSpeech;"clone"->supportsCloning;else->capability==value}
    val gated: Boolean get() = licence.category != "commercial-ok" || licence.requiresAcceptance
    fun fits(ramBytes: Long): Boolean = ramBytes.toDouble() >= minRamGb * 1_000_000_000
    fun option(name: String, fallback: String = ""): String = config[name]?.jsonPrimitive?.contentOrNull ?: fallback
    fun role(directory: File, role: String): String = files.firstOrNull { it.role == role }?.let { File(directory,it.path).absolutePath }.orEmpty()
}
@Serializable data class ModelCatalog(@SerialName("schema_version") val schemaVersion: Int, val models: List<PhoneModel>) {
    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        fun decode(text: String): ModelCatalog = json.decodeFromString<ModelCatalog>(text).also { catalog ->
            require(catalog.schemaVersion == 1)
            require(catalog.models.map {it.id}.distinct().size == catalog.models.size)
            catalog.models.forEach { m ->
                require(m.name.isNotBlank())
                require(Regex("[a-zA-Z0-9_-]+").matches(m.id))
                require(Regex("[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+").matches(m.repo))
                require(Regex("[0-9a-f]{40}").matches(m.revision))
                require(m.capability in setOf("tts","asr","asr-stream","clone","translate"))
                require(if(m.capability=="clone") m.runtime=="omnivoice.cpp"||(m.runtime=="sherpa-onnx"&&m.option("model_type") in setOf("pocket","zipvoice")) else m.runtime==if(m.capability=="translate") "llama.cpp" else "sherpa-onnx")
                require(m.minRamGb > 0 && m.minRamGb.isFinite() && m.files.isNotEmpty())
                require(m.licence.termsFingerprint.matches(Regex("[0-9a-f]{64}")))
                require(m.files.map {it.path}.distinct().size == m.files.size)
                m.files.forEach { f ->
                    require(f.path.isNotBlank() && !f.path.startsWith('/') && f.path.split('/').none {it in setOf("..", ".", "")} && '\\' !in f.path && ':' !in f.path && f.path.none {it.code<32} && f.path.split('/').none {it.startsWith(".verified") || it.endsWith(".part")})
                    require(f.url==null||ModelAssetSource.validUrl(f.url))
                    require(f.bundled==null||(f.url==null&&ModelAssetSource.validBundled(f.bundled)))
                    require(f.size > 0 && f.sha256.matches(Regex("[0-9a-f]{64}")))
                }
                require(m.totalBytes>0)
                require(m.totalBytes == m.files.sumOf {it.size})
            }
        }
    }
}

enum class RunLocation { PHONE, COMPUTER, DOWNLOAD }
fun routeTask(remembered: String?, installed: Boolean, paired: Boolean): RunLocation = when {
    remembered == "phone" -> if(installed) RunLocation.PHONE else RunLocation.DOWNLOAD
    remembered == "computer" && paired -> RunLocation.COMPUTER
    installed -> RunLocation.PHONE
    paired -> RunLocation.COMPUTER
    else -> RunLocation.DOWNLOAD
}
