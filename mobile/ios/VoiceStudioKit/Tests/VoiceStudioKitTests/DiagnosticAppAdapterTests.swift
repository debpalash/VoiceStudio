#if os(macOS)
import Foundation
import Testing

/// Exercise the production app policy directly, without a simulator, network,
/// model cache, mocked implementation or platform-only test hooks.
@Test func diagnosticAppFailureStallAndRecoveryPolicy() throws {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<6 { root.deleteLastPathComponent() }
    let source = root.appendingPathComponent("mobile/ios/VoiceStudio/Platform/AppDiagnosticPolicy.swift")
    #expect(FileManager.default.fileExists(atPath: source.path))
    let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("diagnostic-app-policy-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporary) }
    let harness = temporary.appendingPathComponent("main.swift")
    let executable = temporary.appendingPathComponent("policy-test")
    let program = #"""
func check(_ condition: @autoclosure () -> Bool) { precondition(condition()) }
let native=AppDiagnosticFailure.project(key:nil,status:nil,stage:"generation")
check(native?.category=="generation_failed")
check(AppDiagnosticFailure.project(key:"mobile.local.runtime_failed",status:nil,stage:"generation")?.category=="engine_failed")
check(AppDiagnosticFailure.project(key:"mobile.local.runtime_failed",status:nil,stage:"generation")?.code=="ENGINE_DEPENDENCY_MISSING")
check(AppDiagnosticFailure.project(key:"mobile.local.checksum_failed",status:nil,stage:"download")?.code=="MODEL_CACHE_CORRUPT")
check(AppDiagnosticFailure.project(key:"mobile.local.storage_required",status:nil,stage:"install")?.code=="DISK_FULL")
check(AppDiagnosticFailure.project(key:"mobile.local.model_incompatible",status:nil,stage:"model_load")?.category=="incompatibility")
for key in ["mobile.errors.model_licence_required","mobile.local.model_required","mobile.local.wifi_required","mobile.jobs.power_paused","mobile.jobs.background_paused"] {
 check(AppDiagnosticFailure.project(key:key,status:nil,stage:"generation")==nil)
}
for code in [400,401,403,409,422,429] { check(AppDiagnosticFailure.project(key:"mobile.errors.server",status:code,stage:"generation")==nil) }
check(AppDiagnosticFailure.project(key:"mobile.errors.server",status:500,stage:"generation")?.category=="generation_failed")
check(AppDiagnosticFailure.project(key:"mobile.errors.dub_failed",status:nil,stage:"dub")?.category=="generation_failed")
check(AppDiagnosticFailure.project(key:"hf_PRIVATE_SECRET",status:nil,stage:"generation")==nil)
var stall=AppDiagnosticDownloadStall()
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:0,thresholdMs:600000))
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:599999,thresholdMs:600000))
check(stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:600000,thresholdMs:600000))
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:700000,thresholdMs:600000))
check(!stall.observe(received:20,stage:"downloading",active:true,waiting:false,nowMs:700001,thresholdMs:600000))
check(!stall.observe(received:20,stage:"downloading",active:true,waiting:false,nowMs:1300000,thresholdMs:600000))
check(stall.observe(received:20,stage:"downloading",active:true,waiting:false,nowMs:1300001,thresholdMs:600000))
for phase in ["verifying","waiting","waiting_network","retrying","backoff","paused","cancelled"] {
 stall.reset()
 check(!stall.observe(received:0,stage:phase,active:true,waiting:false,nowMs:0,thresholdMs:1))
 check(!stall.observe(received:0,stage:phase,active:true,waiting:false,nowMs:99999999,thresholdMs:1))
}
stall.reset()
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:true,nowMs:0,thresholdMs:600000))
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:9999999,thresholdMs:600000))
check(!stall.observe(received:10,stage:"downloading",active:false,waiting:false,nowMs:99999999,thresholdMs:600000))
check(!stall.observe(received:10,stage:"downloading",active:true,waiting:false,nowMs:999999999,thresholdMs:600000))
check(!AppDiagnosticRecoveryWindow.permits(begin:99,end:200,enabledAt:100))
check(!AppDiagnosticRecoveryWindow.permits(begin:200,end:199,enabledAt:100))
check(AppDiagnosticRecoveryWindow.permits(begin:100,end:200,enabledAt:100))
check(AppDiagnosticRecoveryWindow.milliseconds(.nan)==nil)
check(AppDiagnosticRecoveryWindow.milliseconds(.infinity)==nil)
check(AppDiagnosticRecoveryWindow.milliseconds(-1)==nil)
check(AppDiagnosticRecoveryWindow.milliseconds(100)==100000)
print("adapter controls passed")
"""#
    try program.write(to: harness, atomically: true, encoding: .utf8)
    let compile = try TestProcess(["swiftc", "-swift-version", "6", "-module-cache-path", temporary.appendingPathComponent("modules").path, source.path, harness.path, "-o", executable.path])
    #expect(try compile.finish(within: 30) == 0)
    let run = try TestProcess([executable.path])
    #expect(try run.finish(within: 5) == 0)
    #expect(run.output.text.contains("adapter controls passed"))
}
#endif
