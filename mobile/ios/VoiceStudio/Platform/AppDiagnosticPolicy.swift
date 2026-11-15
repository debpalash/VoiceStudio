/// App-owned classifications contain no messages, paths, URLs or parameter dictionaries.
struct AppDiagnosticFailure {
    let category: String
    let code: String?
    static func project(key: String?, status: Int?, stage: String) -> Self? {
        var category = ["generation": "generation_failed", "transcription": "generation_failed", "dub": "generation_failed", "batch": "generation_failed", "model_load": "model_load_failed", "install": "install_failed", "download": "download_failed", "engine": "engine_failed"][stage] ?? "operation_failed"
        guard let key else { return Self(category: category, code: nil) }
        if key == "mobile.local.runtime_failed" { return Self(category: "engine_failed", code: "ENGINE_DEPENDENCY_MISSING") }
        let codes = ["mobile.local.checksum_failed": "MODEL_CACHE_CORRUPT", "mobile.local.storage_required": "DISK_FULL", "mobile.local.model_incompatible": "UNSUPPORTED_PLATFORM", "mobile.errors.timeout": "NETWORK_ERROR", "mobile.connect.error_unreachable": "NETWORK_ERROR"]
        if let code = codes[key] {
            if key == "mobile.local.model_incompatible" { category = "incompatibility" }
            return Self(category: category, code: code)
        }
        if let status, status < 500 { return nil }
        let terminal: Set<String> = ["mobile.errors.backend", "mobile.errors.server", "mobile.errors.dub_failed", "mobile.errors.dictation", "mobile.errors.audio", "mobile.errors.invalid_response", "mobile.errors.file", "mobile.local.inference_failed", "mobile.local.download_failed", "mobile.local.catalogue_failed", "mobile.simple.clone_failed", "mobile.simple.checkpoint_failed"]
        guard terminal.contains(key) || (status ?? 0) >= 500 else { return nil }
        return Self(category: category, code: nil)
    }
}

/// A new continuous observation interval starts after consent/activity changes,
/// waiting/backoff, phase changes or observed progress. Slow busy work is not a stall.
struct AppDiagnosticDownloadStall {
    private var bytes: Int64?
    private var stage: String?
    private var since: Int64?
    private var reported = false
    mutating func reset() { bytes = nil; stage = nil; since = nil; reported = false }
    mutating func observe(received: Int64, stage: String, active: Bool, waiting: Bool, nowMs: Int64, thresholdMs: Int64) -> Bool {
        guard active, !waiting, stage == "downloading", received >= 0, nowMs >= 0 else { reset(); return false }
        if bytes != received || self.stage != stage || since == nil || nowMs < since! {
            bytes = received; self.stage = stage; since = nowMs; reported = false; return false
        }
        guard !reported, nowMs - since! >= max(600_000, thresholdMs) else { return false }
        reported = true; return true
    }
}

enum AppDiagnosticRecoveryWindow {
    static func milliseconds(_ seconds: Double) -> Int64? {
        guard seconds.isFinite, seconds >= 0, seconds < 9_000_000_000_000 else { return nil }
        return Int64(seconds * 1000)
    }
    static func permits(begin: Int64, end: Int64, enabledAt: Int64) -> Bool { begin >= enabledAt && end >= begin }
}
