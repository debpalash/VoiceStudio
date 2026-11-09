package com.voicestudio.mobile.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.security.MessageDigest

/** Loose responses verified against translation_engines.py and settings.py. */
@Serializable data class TranslationEngine(
    val id: String,
    @SerialName("display_name") val displayName: String = "",
    val category: String = "",
    val installed: Boolean = false,
    val ready: Boolean = false,
    @SerialName("configured_via") val configuredVia: String? = null,
    @Transient val llmRoute: LlmRoute? = null,
) {
    val requiresConsent get() = category == "online" || (category == "llm" && llmRoute?.isLoopback != true)
    val consentName get() = llmRoute?.displayName?.ifBlank { null }
        ?: configuredVia?.takeUnless { it.isBlank() || it == "env" } ?: displayName.ifBlank { id }
    // Bind approval to the resolved provider AND endpoint, including local-to-cloud changes.
    val routingIdentity get() = if(category == "llm") llmRoute?.identity ?: configuredVia.orEmpty() else ""
    fun requireReady(): TranslationEngine {
        if(id.isBlank() || !installed || !ready || category !in setOf("offline", "online", "llm"))
            throw ClientFailure("mobile.dub.translation_setup")
        return this
    }
    fun authorize(onlineConfirmed: Boolean = false): TranslationAuthorization? {
        requireReady()
        if(requiresConsent && !onlineConfirmed) return null
        return TranslationAuthorization(id, category, requiresConsent && onlineConfirmed, routingIdentity, requiresConsent)
    }
}
@Serializable data class TranslationEngines(val active: String = "", val engines: List<TranslationEngine> = emptyList()) {
    fun activeEngine(): TranslationEngine = engines.firstOrNull { it.id == active && active.isNotBlank() }?.requireReady()
        ?: throw ClientFailure("mobile.dub.translation_setup")
}

/** The backend's provider-local flag alone is insufficient: its URL can be overridden. */
data class LlmRoute(val provider: String, val displayName: String, val baseUrl: String) {
    val isLoopback: Boolean get() {
        val url = baseUrl.toHttpUrlOrNull() ?: return false
        if(url.username.isNotEmpty() || url.password.isNotEmpty()) return false
        val host = url.host
        return host == "localhost" || host == "::1" || host.split('.').let { parts ->
            parts.size == 4 && parts[0] == "127" && parts.all { it.toIntOrNull() in 0..255 }
        }
    }
    // Persist a fingerprint, never the settings URL (which could contain credentials).
    val identity get() = MessageDigest.getInstance("SHA-256").digest("$provider\n$baseUrl".toByteArray())
        .joinToString("") { "%02x".format(it) }
}
@Serializable internal data class LlmSkills(val skills: List<LlmSkill> = emptyList())
@Serializable internal data class LlmSkill(val id: String, val provider: String? = null, val ready: Boolean = false, val enabled: Boolean = false)
@Serializable internal data class LlmProviders(val providers: List<LlmProvider> = emptyList())
@Serializable internal data class LlmProvider(val id: String, @SerialName("display_name") val displayName: String = "",
    @SerialName("base_url") val baseUrl: String = "")

/** Job-scoped approval, never a preference or an implicit provider fallback. */
class TranslationAuthorization internal constructor(val provider: String, val category: String, val onlineConfirmed: Boolean,
    val routingIdentity: String = "", internal val requiresConsent: Boolean = category in setOf("online", "llm"))
