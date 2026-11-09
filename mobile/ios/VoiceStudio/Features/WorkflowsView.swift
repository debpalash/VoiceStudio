import SwiftUI
import UniformTypeIdentifiers
import VoiceStudioKit

struct WorkflowsView: View {
    var initialWorkflow: PhoneWorkflow? = nil
    @Environment(AppModel.self) private var model
    @State private var workflow = PhoneWorkflow(name: "")
    @State private var library: [PhoneWorkflow] = []
    @State private var deleting: PhoneWorkflow?
    @State private var input = ""
    @State private var voice = "preset:0"
    @State private var operation = "normalize"
    @State private var result: PhoneWorkflowResult?
    @State private var resultSubject: ContentReportSubject?
    @State private var importing = false
    @State private var importingWorkflow = false
    @State private var runs: [WorkflowGraphRun] = []
    private var resumeConfiguration: String {
        [tts,clone].compactMap { $0 }.map { (try? phoneModelPin($0)) ?? "" }.joined(separator: "|")+"|"+voice+"|"+((try? translator.map(phoneModelPin)) ?? "")+"|"+((try? recognizer.map(phoneModelPin)) ?? "")
    }
    private var resumeIdentity: String? {
        var snapshot = workflow; snapshot.inputText = input; snapshot.voiceSelection = voice
        return try? PhoneJobResumeIdentity.fingerprint(snapshot,configuration: resumeConfiguration)
    }
    private var jobID: String { "workflow:"+workflow.id.uuidString }
    private var jobState: PhoneJobState? { PhoneJobs.shared.states[jobID] }
    private var running: Bool { jobState?.running == true }
    @State private var work: Task<Void,Never>?
    @State private var progress = 0.0
    @State private var shared: SharedFile?
    @State private var reporting: ContentReportSubject?
    @State private var player = AudioPlayer()
    private var tts: PhoneModel? { model.phone.selectedModel("production-tts",.tts) }
    private var clone: PhoneModel? { model.phone.selectedModel("production-clone",.clone) }
    private var recognizer: PhoneModel? { model.phone.selectedModel("workflow-asr",.asr) }
    private var translator: PhoneModel? { model.phone.selectedModel("workflow-translate",.translate) }
    private var graphReady: Bool {
        guard let graph = workflow.graph else { return true }
        return graph.steps.filter { ["speak","convert"].contains($0.kind) }.allSatisfy { step in voices.contains { $0.id == step.voiceId } && ((step.voiceId ?? "").hasPrefix("phone:") ? clone != nil : tts != nil) && (step.kind != "convert" || (step.voiceId ?? "").hasPrefix("phone:")) }
    }
    private var needed: [PhoneModel] {
        if let graph = workflow.graph {
            let nodes = graph.steps
            return [nodes.contains { ["transcribe","convert"].contains($0.kind) } ? recognizer : nil,
                    nodes.contains { $0.kind == "translate" } ? translator : nil,
                    nodes.contains { $0.kind == "convert" || ($0.kind == "speak" && ($0.voiceId ?? "").hasPrefix("phone:")) } ? clone : nil,
                    nodes.contains { $0.kind == "speak" && ($0.voiceId ?? "").hasPrefix("preset:") } ? tts : nil].compactMap { $0 }
        }
        return [workflow.steps.contains { $0.kind == "transcribe" } ? recognizer : nil,
                workflow.steps.contains { $0.kind == "translate" } ? translator : nil,
                workflow.steps.contains { $0.kind == "speak" } ? (voice.hasPrefix("phone:") ? clone : tts) : nil].compactMap { $0 }
    }
    private var validInput: Bool {
        if let graph = workflow.graph { return graphReady && (graph.needsAudio ? workflow.inputFile != nil : !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) && (!graph.steps.contains { ["transcribe","convert"].contains($0.kind) } || recognizer != nil) && (!graph.steps.contains { $0.kind == "translate" } || translator != nil) && needed.allSatisfy(model.phone.ready) }
        return !(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && workflow.steps.first?.kind != "transcribe") && (workflow.steps.first?.kind != "transcribe" || (workflow.inputFile != nil && recognizer != nil)) && (!workflow.steps.contains { $0.kind == "translate" } || translator != nil) && (!workflow.steps.contains { $0.kind == "speak" } || voices.contains { $0.id == voice })
    }
    private var voices: [SelectionOption] {
        (0..<min(128,Int(tts?.config["num_speakers"]?.number ?? 1))).map { SelectionOption(id: "preset:\($0)",title: model.t("mobile.local.preset_voice",["number": String($0+1)])) } + model.phone.voices.map { SelectionOption(id: "phone:"+$0.id.uuidString,title: $0.name) }
    }
    var body: some View {
        Page {
            Text(model.t("mobile.local.this_phone"))
            editor.disabled(running)
            PhoneJobStatusView(id: jobID,requiresGPU: needed.contains { $0.runtime != .sherpa },cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: needed),resumeIdentity: resumeIdentity)
            if !needed.allSatisfy(model.phone.ready) { LocalDownloadPrompt(items: needed,feature: "workflows.title") }
            if running {
                ProgressView(value: jobState?.progress ?? 0)
                Text(model.t("mobile.jobs.checkpointed")).font(.caption)
                Button(model.t("common.cancel"),systemImage: "stop") { PhoneJobs.shared.cancel(id: jobID) }.studioButton(.tertiary)
            }
            if let result {
                Card {
                    Text(result.text).textSelection(.enabled)
                    if let audio = result.audio {
                        Button(model.t("common.play"),systemImage: "play.fill") { Task { await model.performPhone { try await player.load(file: audio); player.play() } } }.studioButton(.tertiary)
                        Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { shared = SharedFile(url: audio) }.studioButton(.tertiary)
                        if let resultSubject { ReportButton(subject: resultSubject,reporting: $reporting) }
                    }
                }
            }
            if player.file != nil { AudioPlayerView(player: player) }
            if !runs.isEmpty {
                Text(model.t("mobile.workflowParity.runs")).font(.headline)
                ForEach(runs) { run in
                    Card {
                        Text(Date(timeIntervalSince1970: run.updatedAt/1000).formatted(date: .abbreviated,time: .shortened)).font(.headline)
                        Text(model.t(run.done ? "mobile.android.stage_done" : run.failure ?? "mobile.simple.cancelled"))
                        Text(model.t("mobile.workflowParity.saved_steps",["count":String(run.visited.count)]))
                        if !run.text.isEmpty { Text(run.text).lineLimit(4).textSelection(.enabled) }
                        if run.audio != nil { Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { Task { await model.performPhone { if let file = try await model.phone.workflowStore?.graphRunAudio(workflow,run) { shared = SharedFile(url: file) } } } }.studioButton(.tertiary) }
                    }
                }
            }
            Text(model.t("workflows.library")).font(.headline)
            Button(model.t("workflows.new"),systemImage: "plus") { workflow = fresh(); input = ""; voice = "preset:0"; result = nil; player.clear() }.disabled(running)
            ForEach(library) { item in
                HStack {
                    Button(item.name) { workflow = item; input = item.inputText ?? ""; voice = item.voiceSelection ?? "preset:0"; result = nil; player.clear() }.frame(maxWidth: .infinity,alignment: .leading)
                    Button(model.t("common.delete"),systemImage: "trash",role: .destructive) { deleting = item }.labelStyle(.iconOnly)
                }.disabled(running).frame(minHeight: 44)
            }
        }
        .navigationTitle(model.t("workflows.title")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("workflowRun.run"),systemImage: "play.fill") { run() }.studioButton(.primary)
                .disabled(running || !workflow.valid || input.utf16.count > 20000 || !validInput || !needed.allSatisfy(model.phone.ready))
                .padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .task { await model.phone.refresh(); if workflow.name.isEmpty { workflow = initialWorkflow ?? fresh(); input = workflow.inputText ?? ""; voice = workflow.voiceSelection ?? "preset:0" }; await reload() }
        .sheet(item: $deleting) { item in DeleteConfirmation(title: model.t("stories.deleteProjectConfirm")) {
            Task { await model.performPhone {
                try await model.phone.workflowStore?.delete(item)
                if workflow.id == item.id { workflow = fresh(); result = nil; player.clear() }
                await reload()
            } }
        } }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .contentReportSheet($reporting)
        .fileImporter(isPresented: $importingWorkflow,allowedContentTypes: [.json]) { response in Task { await model.performPhone {
            let url = try response.get(),access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
            let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
            let data = try handle.read(upToCount: 4*1024*1024+1) ?? Data()
            let graph = try WorkflowGraphInterchange.decode(data)
            var draft = PhoneWorkflow(name: graph.name); draft.graph = graph; draft.inputText = graph.inputText
            workflow = draft; input = graph.inputText; result = nil; player.clear(); runs = []
        } } }
        .task(id: workflow.id) { await reloadRuns() }
        .fileImporter(isPresented: $importing,allowedContentTypes: [.audio,.movie]) { response in
            Task { await model.performPhone {
                let url = try response.get(); let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
                guard let store = model.phone.workflowStore else { return }
                workflow = try await store.importAudio(url,into: workflow)
            } }
        }
        .task(id: jobState?.running) {
            guard jobState?.running == false else { return }
            if let error = jobState?.error { model.error = error }
            if let finished = jobState?.result {
                result = PhoneWorkflowResult(text: finished.text ?? "",audio: finished.file)
                if let file = finished.file { resultSubject = .phone(.speak,id: file.path,repo: finished.repo,revision: finished.revision) }
                await reloadRuns()
            }
        }
        .onDisappear { player.clear() }
    }
    private var editor: some View {
        VStack(alignment: .leading,spacing: VSTokens.spacingGap) {
            TextField(model.t("stories.projectName"),text: $workflow.name)
            TextField(model.t("clone.script"),text: $input,axis: .vertical).lineLimit(3...8)
            Button(model.t("mobile.flow.import_media"),systemImage: "doc.badge.plus") { importing = true }.studioButton(.tertiary)
            if let name = workflow.inputName { Text(name).font(.caption).lineLimit(2) }
            HStack {
                ForEach(["clean","translate","transcribe"],id: \.self) { kind in
                    Button(model.t(templateKey(kind))) { workflow = template(kind) }.studioButton(.tertiary)
                }
            }
            HStack {
                Button(model.t("mobile.workflowParity.import_json"),systemImage: "square.and.arrow.down") { importingWorkflow = true }.studioButton(.tertiary)
                Button(model.t("mobile.workflowParity.export_json"),systemImage: "square.and.arrow.up") { Task { await model.performPhone {
                    guard var graph = workflow.graph else { return }; graph.name = workflow.name
                    let data = try WorkflowGraphInterchange.encode(graph,input: input),file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".json")
                    try data.write(to: file,options: .atomic); shared = SharedFile(url: file)
                } } }.studioButton(.tertiary).disabled(workflow.graph == nil || !workflow.valid)
            }
            HStack {
                Button(model.t("mobile.workflowParity.branch_template")) { workflow = branchTemplate(); input = "" }.studioButton(.tertiary)
                Button(model.t("workflows.step_convert")) { var draft = PhoneWorkflow(name: model.t("workflows.step_convert")); draft.graph = WorkflowGraph(name: draft.name,kinds: ["audio","convert","normalize","end"]); workflow = draft; input = "" }.studioButton(.tertiary)
            }
            if workflow.graph != nil {
                Text(model.t("mobile.workflowParity.bind_hint")).font(.caption)
                WorkflowGraphEditor(graph: Binding(get: { workflow.graph! },set: { var updated = $0; updated.updatedAt = Date().timeIntervalSince1970*1000; workflow.graph = updated }),voices: voices)
            } else {
            ForEach(workflow.steps) { step in
                if let index = workflow.steps.firstIndex(where: { $0.id == step.id }) {
                Card {
                    HStack {
                        Text(model.t(stepKey(workflow.steps[index].kind))).frame(maxWidth: .infinity,alignment: .leading)
                        Button(model.t("stories.moveUp"),systemImage: "arrow.up") { workflow.steps.swapAt(index,index-1) }.disabled(index == 0 || step.kind == "speak").labelStyle(.iconOnly)
                        Button(model.t("stories.moveDown"),systemImage: "arrow.down") { workflow.steps.swapAt(index,index+1) }.disabled(index == workflow.steps.count-1 || workflow.steps[index+1].kind == "speak").labelStyle(.iconOnly)
                        Button(model.t("common.delete"),systemImage: "trash") { workflow.steps.remove(at: index) }.labelStyle(.iconOnly)
                    }
                    if step.kind == "condition" {
                        TextField(model.t("mobile.flow.contains"),text: Binding(get: { workflow.steps.first { $0.id == step.id }?.containsText ?? "" },set: { value in if let i = workflow.steps.firstIndex(where: { $0.id == step.id }) { workflow.steps[i].containsText = value } }))
                        Stepper(model.t("mobile.flow.skip",["count": String(step.skipSteps ?? 1)]),value: Binding(get: { workflow.steps.first { $0.id == step.id }?.skipSteps ?? 1 },set: { value in if let i = workflow.steps.firstIndex(where: { $0.id == step.id }) { workflow.steps[i].skipSteps = value } }),in: 1...20)
                    }
                    if ["transcribe","translate","speak"].contains(step.kind) {
                        Stepper(model.t("mobile.flow.retries",["count": String(step.retries ?? 0)]),value: Binding(get: { workflow.steps.first { $0.id == step.id }?.retries ?? 0 },set: { value in if let i = workflow.steps.firstIndex(where: { $0.id == step.id }) { workflow.steps[i].retries = value } }),in: 0...3)
                    }
                    if workflow.steps[index].kind == "translate" {
                        ChoicePicker(label: model.t("mobile.speak.language"),selection: Binding(get: { workflow.steps.first { $0.id == step.id }?.language ?? "en" },set: { value in if let current = workflow.steps.firstIndex(where: { $0.id == step.id }) { workflow.steps[current].language = value } }),options: model.languages.dubOptions.map { SelectionOption(id: $0.code,title: $0.name) })
                    }
                }
                }
            }
            ChoicePicker(label: model.t("workflows.add_step"),selection: $operation,options: ["normalize","translate","transcribe","condition","speak"].map { SelectionOption(id: $0,title: model.t(stepKey($0))) })
            Button(model.t("workflows.add_step"),systemImage: "plus") {
                let index = operation == "transcribe" ? 0 : operation != "speak" && workflow.steps.last?.kind == "speak" ? workflow.steps.count-1 : workflow.steps.count
                workflow.steps.insert(PhoneWorkflowStep(kind: operation,containsText: operation == "condition" ? "?" : nil),at: index)
            }.studioButton(.tertiary).disabled(workflow.steps.count >= 20 || (["speak","transcribe"].contains(operation) && workflow.steps.contains { $0.kind == operation }))
            ChoicePicker(label: model.t("mobile.speak.voice"),selection: $voice,options: voices)
            }
            modelPicker("production-tts",.tts); modelPicker("production-clone",.clone); modelPicker("workflow-translate",.translate); modelPicker("workflow-asr",.asr)
            Button(model.t("common.save"),systemImage: "checkmark") { Task { await model.performPhone { var saved = workflow; saved.inputText = input; saved.voiceSelection = voice; try await model.phone.workflowStore?.save(saved); workflow = saved; await reload() } } }.studioButton(.tertiary).disabled(workflow.name.isEmpty)
        }
    }
    private func modelPicker(_ task: String,_ capability: ModelCapability) -> some View {
        PhoneModelPicker(label: model.t("mobile.simple."+capability.rawValue),selection: Binding(get: { model.phone.selectedModel(task,capability)?.id ?? "" },set: { model.phone.chooseModel(task,$0) }), items: model.phone.candidates(capability))
    }
    private func stepKey(_ kind: String) -> String {
        switch kind {
        case "normalize": return "mobile.production.normalize_text"
        case "transcribe": return "mobile.flow.step_transcribe"
        case "condition": return "mobile.flow.step_condition"
        case "translate": return "workflows.step_translate"
        default: return "workflows.step_speak"
        }
    }
    private func templateKey(_ kind: String) -> String {
        switch kind {
        case "translate": return "mobile.flow.template_translate"
        case "transcribe": return "mobile.flow.template_transcribe"
        default: return "mobile.flow.template_clean"
        }
    }
    private func fresh() -> PhoneWorkflow { var result = PhoneWorkflow(name: model.t("workflows.untitled")); result.graph = WorkflowGraph(name: result.name); return result }
    private func branchTemplate() -> PhoneWorkflow {
        var draft = PhoneWorkflow(name: model.t("mobile.workflowParity.branch_template"))
        var graph = WorkflowGraph(name: draft.name,kinds: ["start","condition","speak","speak","end"])
        graph.steps[1].text = "?"; let n = graph.steps
        graph.connections = [WorkflowGraphConnection(source: n[0].id,target: n[1].id),WorkflowGraphConnection(source: n[1].id,target: n[2].id,sourceHandle: "yes"),WorkflowGraphConnection(source: n[1].id,target: n[3].id,sourceHandle: "no"),WorkflowGraphConnection(source: n[2].id,target: n[4].id),WorkflowGraphConnection(source: n[3].id,target: n[4].id)]
        draft.graph = graph; return draft
    }
    private func reloadRuns() async { runs = (try? await model.phone.workflowStore?.graphRuns(workflow)) ?? [] }
    private func reload() async { do { library = try await model.phone.workflowStore?.list() ?? [] } catch { model.error = ClientError("mobile.errors.file") } }
    private func template(_ kind: String) -> PhoneWorkflow {
        PhoneWorkflow(name: model.t(templateKey(kind)),steps: (kind == "clean" ? ["normalize","speak"] : kind == "translate" ? ["normalize","translate","speak"] : ["transcribe","normalize","translate","speak"]).map { PhoneWorkflowStep(kind: $0) })
    }
    private func run() {
        guard let store = model.phone.workflowStore,let engine = model.phone.engine,let voicesStore = model.phone.voiceStore,let resumeIdentity else { return }
        let renderer = ProductionVoiceRenderer(engine: engine,store: voicesStore,voices: model.phone.voices,tts: tts,clone: clone)
        var submitted = workflow; submitted.inputText = input; submitted.voiceSelection = voice
        let snapshot = submitted, text = input,selection = voice,translator = translator,recognizer = recognizer
        let producer = workflow.graph == nil ? (voice.hasPrefix("phone:") ? clone : tts) : nil
        let languages = model.languages.dubOptions
        let configuration = renderer.configuration+"|"+selection+"|"+((try? translator.map(phoneModelPin)) ?? "")+"|"+((try? recognizer.map(phoneModelPin)) ?? "")
        result = nil; resultSubject = nil; player.clear()
        let executor = WorkflowGraphExecutor(engine: engine,voices: voicesStore,library: model.phone.voices,tts: tts,clone: clone,asr: recognizer,translator: translator)
        PhoneJobs.shared.start(id: jobID,title: workflow.name,requiresGPU: needed.contains { $0.runtime != .sherpa },cpuFallbackSupported: NativeLocalEngine.cpuFallbackAvailable(models: needed),resumeIdentity: resumeIdentity) { report in
            if snapshot.graph != nil {
                let result = try await store.runGraph(snapshot,text: text,configuration: configuration,progress: { value in await report(EngineProgress(stage: .running,fraction: value)) },execute: executor.execute)
                return PhoneJobResult(file: result.audio,text: result.text)
            }
            let result = try await store.run(snapshot,text: text,configuration: configuration,progress: { value in await report(EngineProgress(stage: .running,fraction: value)) }) { step,text in
                if step.kind == "transcribe",let recognizer {
                    let source = try await store.source(snapshot)
                    return WorkflowValue(text: try await WorkflowTranscription.run(source: source,model: recognizer,engine: engine,decoder: AVPhoneAudioDecoder()))
                }
                if step.kind == "translate",let translator,let target = languages.first(where: { $0.code == step.language }) {
                    return WorkflowValue(text: try await engine.translate(model: translator,text: text,sourceLanguage: "auto",targetLanguage: target.name,progress: { _ in }))
                }
                guard step.kind == "speak" else { throw ClientError("mobile.local.model_required") }
                return WorkflowValue(text: text,audio: try await renderer.render(ProductionSegment(text: text),selection))
            }
            return PhoneJobResult(file: result.audio,text: result.text,repo: producer?.repo,revision: producer?.revision)
        }
    }
}
