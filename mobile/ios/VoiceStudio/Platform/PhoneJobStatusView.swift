import SwiftUI
import VoiceStudioKit

/// Capability is reported from the granted task, not inferred from the device or entitlement source file.
struct PhoneJobStatusView: View {
    let id: String
    let requiresGPU: Bool
    let cpuFallbackSupported: Bool
    var resumeIdentity: String? = nil
    var onResume: (@MainActor () -> Void)? = nil
    @Environment(AppModel.self) private var model
    private var jobs: PhoneJobs { .shared }
    var body: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap/2) {
            if let state = jobs.states[id],state.running || state.paused {
                Text(model.t(jobs.statusKey(id: id))).font(.caption)
            } else if requiresGPU {
                Text(model.t(jobs.supportsGPU ? "mobile.jobs.background_requestable" : "mobile.jobs.gpu_unavailable")).font(.caption)
            } else { Text(model.t("mobile.jobs.background_limited")).font(.caption) }
            if requiresGPU {
                Toggle(model.t("mobile.jobs.cpu_fallback"),isOn: Binding(get: { jobs.allowCPUFallback },set: { jobs.allowCPUFallback = $0 }))
                    .disabled(!cpuFallbackSupported || jobs.states[id]?.running == true)
                Text(model.t(cpuFallbackSupported ? "mobile.jobs.cpu_tradeoff" : "mobile.jobs.cpu_unavailable")).font(.caption)
            }
            if jobs.canResume(id: id,identity: resumeIdentity) {
                Button(model.t("mobile.jobs.resume_checkpoint"),systemImage: "play.fill") { if let onResume { onResume() } else { jobs.resume(id: id,identity: resumeIdentity) } }.studioButton(.tertiary)
            }
        }
        .onChange(of: resumeIdentity,initial: true) { _,identity in jobs.discardStaleResume(id: id,identity: identity) }
        .onChange(of: jobs.states[id]?.running) { _,_ in jobs.discardStaleResume(id: id,identity: resumeIdentity) }
    }
}
