package com.voicestudio.mobile.network

import kotlinx.serialization.json.*

/** Every REST path (including uploads/downloads) preserves the host's actionable detail. */
object BackendErrors {
    fun parse(status: Int, body: String): ClientFailure {
        val detail = runCatching { Json.parseToJsonElement(body).jsonObject["detail"] }.getOrNull()
        val objectDetail = detail as? JsonObject
        fun JsonObject.text(key: String) = (get(key) as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
        val message = (detail as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
            ?: objectDetail?.text("message")
        if(status == 401) return ClientFailure(if(message?.contains("PIN", true) == true)
            "mobile.connect.error_pin" else "mobile.connect.error_session_expired", status = status)
        if(objectDetail?.text("code") == "model_licence_required") {
            val models = (objectDetail["models"] as? JsonArray).orEmpty().mapNotNull { model ->
                when(model) {
                    is JsonPrimitive -> model.contentOrNull
                    is JsonObject -> listOfNotNull(model.text("name") ?: model.text("repo_id"),
                        model.text("license") ?: model.text("licence_name")).distinct().joinToString(" · ").takeIf { it.isNotBlank() }
                    else -> null
                }
            }.filter { it.isNotBlank() }.distinct().joinToString("\n")
            // The standard server sentence repeats both the model list and the
            // catalogue instruction. Use its structured list once, followed by
            // one localized action. Keep raw message metadata for diagnostics.
            // An empty list can mean unreadable licence records: preserve that
            // server recovery advice instead of inventing an acceptance action.
            if(models.isEmpty() && message != null)
                return ClientFailure("mobile.errors.server", mapOf("message" to message), status)
            return ClientFailure("mobile.errors.model_licence_required", mapOf("message" to message.orEmpty(), "models" to models), status)
        }
        return if(message != null) ClientFailure("mobile.errors.server", mapOf("message" to message), status)
        else ClientFailure("mobile.android.request_failed", mapOf("code" to status.toString()), status)
    }
}
