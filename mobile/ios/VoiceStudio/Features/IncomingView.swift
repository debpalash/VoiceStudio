import SwiftUI
import VoiceStudioKit

struct IncomingView: View {
    let incoming: PhoneIncoming
    @Environment(AppModel.self) private var model
    @State private var workflow: PhoneWorkflow?
    var body: some View {
        Page {
            Text(incoming.text ?? incoming.file?.lastPathComponent ?? "").textSelection(.enabled)
            Text(model.t("mobile.integration.review"))
            if let text = incoming.text { NavigationLink(model.t("mobile.tabs.speak")) { SpeakView(initialText: text) }.studioButton() }
            if let workflow { NavigationLink(model.t("workflows.title")) { WorkflowsView(initialWorkflow: workflow) }.studioButton(.primary) }
        }.navigationTitle(model.t("mobile.integration.received"))
        .task {
            await model.performPhone {
                var draft = PhoneWorkflow(name: model.t("mobile.integration.received"),steps: (incoming.file == nil ? ["normalize","speak"] : ["transcribe","normalize"]).map { PhoneWorkflowStep(kind: $0) })
                draft.inputText = incoming.text
                if let file = incoming.file,let store = model.phone.workflowStore { draft = try await store.importAudio(file,into: draft) }
                workflow = draft
            }
        }
    }
}
