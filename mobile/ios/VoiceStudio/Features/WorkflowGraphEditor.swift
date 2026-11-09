import SwiftUI
import VoiceStudioKit

func workflowGraphStepKey(_ kind: String) -> String {
    switch kind {
    case "start": return "workflows.step_start"
    case "audio": return "workflows.step_audio"
    case "speak": return "workflows.step_speak"
    case "transcribe": return "workflows.step_transcribe"
    case "translate": return "workflows.step_translate"
    case "convert": return "workflows.step_convert"
    case "normalize": return "workflows.step_normalize"
    case "condition": return "workflows.step_condition"
    default: return "workflows.step_end"
    }
}
struct WorkflowGraphEditor: View {
    @Binding var graph: WorkflowGraph
    let voices: [SelectionOption]
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @State private var kind = "condition"
    private var languages: [SelectionOption] { model.languages.dubOptions.map { .init(id: $0.code,title: $0.name) } }
    var body: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
            Text(model.t("mobile.workflowParity.graph_hint")).font(.caption)
            ForEach(Array(graph.steps.enumerated()),id: \.element.id) { number,step in
                Card {
                    HStack {
                        Text("\(number+1) · "+model.t(workflowGraphStepKey(step.kind))).font(.headline)
                        Spacer()
                        if !["start","audio"].contains(step.kind) { Button(model.t("workflows.remove_step"),systemImage: "trash",role: .destructive) { graph.steps.removeAll { $0.id == step.id }; graph.connections.removeAll { $0.source == step.id || $0.target == step.id } }.labelStyle(.iconOnly) }
                    }
                    TextField(model.t("workflows.step_name"),text: field(step.id,\.title,""))
                    if ["start","audio"].contains(step.kind) {
                        ChoicePicker(label: model.t("mobile.workflowParity.input_type"),selection: field(step.id,\.kind),options: [SelectionOption(id: "start",title: model.t("clone.script")),SelectionOption(id: "audio",title: model.t("mobile.flow.import_media"))])
                    }
                    if step.kind == "condition" {
                        TextField(model.t("workflows.condition_phrase"),text: field(step.id,\.text,""))
                        ChoicePicker(label: model.t("workflows.condition_match"),selection: field(step.id,\.match,"contains"),options: [SelectionOption(id: "contains",title: model.t("workflows.match_contains")),SelectionOption(id: "equals",title: model.t("workflows.match_equals")),SelectionOption(id: "starts",title: model.t("workflows.match_starts")),SelectionOption(id: "ends",title: model.t("workflows.match_ends"))])
                        Text(model.t("workflows.condition_hint")).font(.caption)
                        connection(step.id,"yes","mobile.workflowParity.yes_route")
                        connection(step.id,"no","mobile.workflowParity.no_route")
                    } else if step.kind != "end" { connection(step.id,nil,"mobile.workflowParity.next_route") }
                    if ["speak","convert"].contains(step.kind) {
                        ChoicePicker(label: model.t("mobile.speak.voice"),selection: field(step.id,\.voiceId,""),options: [SelectionOption(id: "",title: model.t("mobile.workflowParity.bind_voices"),enabled: false)]+voices.filter { step.kind != "convert" || $0.id.hasPrefix("phone:") })
                        if step.kind == "speak" { ChoicePicker(label: model.t("stories.global_speed"),selection: Binding(get: { String(graph.steps.first { $0.id == step.id }?.speed ?? 1) },set: { value in edit(step.id) { $0.speed = Double(value) } }),options: [0.5,0.75,1,1.25,1.5,2].map { .init(id: String($0),title: "\($0)×") }) }
                    }
                    if step.kind == "translate" {
                        ChoicePicker(label: model.t("mobile.workflowParity.source_language"),selection: field(step.id,\.sourceLanguage,"auto"),options: [SelectionOption(id: "auto",title: model.t("languagePicker.auto"))]+languages)
                        ChoicePicker(label: model.t("mobile.speak.language"),selection: field(step.id,\.language,"en"),options: languages)
                    }
                    if step.kind == "speak" { ChoicePicker(label: model.t("mobile.speak.language"),selection: field(step.id,\.language,"Auto"),options: [SelectionOption(id: "Auto",title: model.t("languagePicker.auto"))]+languages.map { SelectionOption(id: $0.id,title: $0.title,enabled: !(step.voiceId ?? "").hasPrefix("phone:")) }) }
                    if step.kind == "normalize" { ChoicePicker(label: model.t("workflowRun.peak"),selection: Binding(get: { String(graph.steps.first { $0.id == step.id }?.targetDb ?? -2) },set: { value in edit(step.id) { $0.targetDb = Double(value) } }),options: [-24.0,-18,-12,-6,-2,-1].map { .init(id: String($0),title: "\($0) dBFS") }) }
                    if !["start","audio","end","condition"].contains(step.kind) {
                        Stepper(model.t("mobile.flow.retries",["count":String(step.retries ?? 0)]),value: Binding(get: { graph.steps.first { $0.id == step.id }?.retries ?? 0 },set: { value in edit(step.id) { $0.retries = value } }),in: 0...3)
                    }
                }
            }
            ChoicePicker(label: model.t("workflows.add_step"),selection: $kind,options: ["speak","condition","translate","transcribe","convert","normalize","end"].map { .init(id: $0,title: model.t(workflowGraphStepKey($0))) })
            Button(model.t("workflows.add_step"),systemImage: "plus") {
                var step = WorkflowGraphStep(kind: kind,text: kind == "condition" ? "?" : nil,voiceId: kind == "speak" ? "preset:0" : nil)
                if kind == "translate" { step.language = "en"; step.sourceLanguage = "auto" }
                graph.steps.append(step)
            }.studioButton(.tertiary).disabled(graph.steps.count >= 40)
            if let error = validation { Text(model.t(error.key,error.params)).font(.caption).foregroundStyle(theme.color("error")) }
        }
    }
    private var validation: ClientError? { do { try graph.validate(); return nil } catch { return error as? ClientError ?? ClientError("mobile.workflowParity.invalid_graph") } }
    private func edit(_ id: String,_ body: (inout WorkflowGraphStep)->Void) { if let i = graph.steps.firstIndex(where: { $0.id == id }) { body(&graph.steps[i]) } }
    private func field(_ id: String,_ key: WritableKeyPath<WorkflowGraphStep,String?>,_ fallback: String) -> Binding<String> { Binding(get: { graph.steps.first { $0.id == id }?[keyPath: key] ?? fallback },set: { value in edit(id) { $0[keyPath: key] = value } }) }
    private func field(_ id: String,_ key: WritableKeyPath<WorkflowGraphStep,String>) -> Binding<String> { Binding(get: { graph.steps.first { $0.id == id }?[keyPath: key] ?? "" },set: { value in edit(id) { $0[keyPath: key] = value } }) }
    private func connection(_ source: String,_ branch: String?,_ label: String) -> some View {
        ChoicePicker(label: model.t(label),selection: Binding(get: { graph.destination(source,branch) ?? "" },set: { graph.connect(source,to: $0,branch: branch) }),options: [SelectionOption(id: "",title: model.t("mobile.workflowParity.choose_next"))]+graph.steps.enumerated().filter { $0.element.id != source }.map { .init(id: $0.element.id,title: "\($0.offset+1) · "+(($0.element.title?.isEmpty == false ? $0.element.title : nil) ?? model.t(workflowGraphStepKey($0.element.kind)))) })
    }
}
