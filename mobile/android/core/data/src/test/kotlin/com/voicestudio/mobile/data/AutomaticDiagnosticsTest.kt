package com.voicestudio.mobile.data

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.CancellationException
import org.junit.Assert.*
import org.junit.Test

class AutomaticDiagnosticsTest {
    @Test fun normalCancellationPromptsAndDeniedAuthenticationAreNotFailureReports() {
        assertNull(automaticDiagnosticInput(CancellationException("private content")))
        for (key in listOf("mobile.local.licence_required", "mobile.local.model_required", "mobile.local.wifi_required",
            "mobile.android.operation_busy", "mobile.permissions.denied", "mobile.connect.error_session_expired")) {
            assertNull(key, automaticDiagnosticInput(ClientFailure(key)))
        }
        assertNull(automaticDiagnosticInput(ClientFailure("private-key", mapOf("secret" to "do not send"), 401)))
    }

    @Test fun technicalFailuresUseClosedLabelsWithoutMessageOrParameters() {
        val failure = automaticDiagnosticInput(ClientFailure("mobile.local.runtime_failed", mapOf("token" to "private")))!!
        assertEquals("engine_failed", failure.category)
        assertEquals("ENGINE_DEPENDENCY_MISSING", failure.errorCode)
        assertEquals("engine", failure.stage)
        assertFalse(failure.toString().contains("private"))
        assertEquals("generation_failed", automaticDiagnosticInput(ClientFailure("mobile.local.inference_failed"))!!.category)
        assertEquals("download_failed", automaticDiagnosticInput(ClientFailure("mobile.local.download_failed"))!!.category)
        assertEquals("incompatibility", automaticDiagnosticInput(ClientFailure("mobile.local.model_incompatible"))!!.category)
    }

    @Test fun acceptsOnlyOurVersionedBoundedProcessSummary() {
        val epoch = "a".repeat(32)
        val good = """{"v":1,"e":"$epoch","a":"0.5.9-beta.2"}"""
        assertEquals(epoch to "0.5.9-beta.2", parseDiagnosticExitSummary(good.toByteArray()))
        assertNull(parseDiagnosticExitSummary(null))
        assertNull(parseDiagnosticExitSummary(ByteArray(129)))
        assertNull(parseDiagnosticExitSummary(good.replace("\"v\":1", "\"v\":\"1\"").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("$epoch", "private user path").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("0.5.9-beta.2", "user@example.com").toByteArray()))
        assertNull(parseDiagnosticExitSummary(good.replace("\"a\":", "\"extra\":true,\"a\":").toByteArray()))
    }
}
