import SwiftUI
import VoiceStudioKit
@preconcurrency import BackgroundTasks

@main struct VoiceStudioApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            RootView().environment(model)
                .environment(\.locale, Locale(identifier: model.language.isEmpty ? Locale.current.identifier : model.language))
                .preferredColorScheme(model.theme.mode == .system ? nil : (model.theme.mode == .light ? .light : .dark))
                .task {
                    BackgroundUpload.shared.onReceipt = { model.acceptUpload() }
                    BackgroundMonitor.model = model
                    await model.connection.restore()
                    if model.connection.client != nil { await model.perform { try await model.refresh() }; model.monitor() }
                    model.acceptUpload()
                }
                .onChange(of: phase) { _, phase in
                    if phase == .active {
                        model.acceptUpload()
                        PhoneInbox.shared.collect()
                        model.monitor()
                        Task { await model.phone.refresh() }
                    } else if phase == .inactive { PhoneJobs.shared.enteredInactive() }
                    else if phase == .background { PhoneJobs.shared.enteredBackground(); BackgroundMonitor.schedule() }
                }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        BackgroundMonitor.register(); return true
    }
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        let boxed = ModelEventsCompletion(completionHandler)
        if !ModelDownloadSessions.handle(identifier,completion: { boxed.finish() }) {
            BackgroundUpload.shared.eventsCompletion = completionHandler
        }
    }
}

@MainActor enum BackgroundMonitor {
    static weak var model: AppModel?
    static let identifier = "com.voicestudio.mobile.dub-monitor"
    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: .main) { task in
            let boxed = ScheduledTask(task)
            Task { @MainActor in
                guard let processing = boxed.value as? BGProcessingTask, let model else { boxed.value.setTaskCompleted(success: false); return }
                let work = Task { @MainActor in
                    if model.connection.client == nil { await model.connection.restore() }
                    model.monitor()
                    await model.monitorTask?.value
                    processing.setTaskCompleted(success: model.dub.checkpoint?.stage == .done)
                    schedule()
                }
                processing.expirationHandler = { work.cancel(); Task { @MainActor in model.pauseMonitor() } }
            }
        }
    }
    static func schedule() {
        guard let model, let cp = model.dub.checkpoint, ![.done, .cancelled, .failed].contains(cp.stage) else { return }
        let request = BGProcessingTaskRequest(identifier: identifier)
        request.requiresNetworkConnectivity = true
        request.earliestBeginDate = Date(timeIntervalSinceNow: 60)
        try? BGTaskScheduler.shared.submit(request)
    }
}

private final class ScheduledTask: @unchecked Sendable {
    let value: BGTask
    init(_ value: BGTask) { self.value = value }
}

private final class ModelEventsCompletion: @unchecked Sendable {
    private let completion: () -> Void
    init(_ completion: @escaping () -> Void) { self.completion = completion }
    func finish() { completion() }
}
