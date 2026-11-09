import SwiftUI
import VoiceStudioKit

struct CallsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var voices: [CallVoice] = []
    @State private var readiness: [CallReadiness] = []
    @State private var disclosure = ""
    @State private var history: [CallRecord] = []
    @State private var current: CallRecord?
    @State private var number = ""
    @State private var brief = ""
    @State private var voice = ""
    @State private var minutes = "10"
    @State private var say = ""
    @State private var confirm = false
    @State private var busy = false
    @State private var optionsLoaded = false
    @State private var polling: Task<Void,Never>?
    private var authenticated: Bool { model.connection.client?.credentials.session != nil }
    private var valid: Bool { authenticated && optionsLoaded && !busy && !readiness.isEmpty && readiness.allSatisfy(\.ok) && number.range(of: "^\\+[1-9][0-9]{6,14}$",options: .regularExpression) != nil && !brief.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && brief.count <= 4000 && voices.contains { $0.id == voice } }
    var body: some View {
        Page {
            Text(model.t("mobile.calls.hint"))
            if !authenticated {
                Text(model.t("mobile.calls.authentication"))
                NavigationLink(model.t("mobile.simple.reconnect")) { ConnectView() }
            } else {
                Button(model.t("calls.recheck"),systemImage: "arrow.clockwise") { Task { await load() } }.studioButton(.tertiary).disabled(busy)
                ForEach(readiness.filter { !$0.ok }) { item in Text(model.t("calls.ready_"+item.id)+": "+item.detail).font(.footnote) }
                TextField(model.t("calls.number_label"),text: $number).keyboardType(.phonePad)
                TextField(model.t("calls.brief_label"),text: $brief,axis: .vertical).lineLimit(3...6)
                ChoicePicker(label: model.t("calls.voice_label"),selection: $voice,options: voices.map { SelectionOption(id: $0.id,title: $0.name) })
                Text(model.t("calls.voice_rule")).font(.footnote)
                ChoicePicker(label: model.t("calls.max_duration"),selection: $minutes,options: [2,5,10,15,30].map { SelectionOption(id: String($0),title: model.t("calls.minutes",["minutes":String($0)])) })
                Text(model.t("calls.first_line")).font(.headline)
                Text(disclosure).font(.subheadline)
                Button(model.t("calls.call"),systemImage: "phone") { confirm = true }.studioButton(.primary).disabled(!valid)
                if let current {
                    Card {
                        Text(current.to_masked+" · "+current.status).font(.headline)
                        if current.live {
                            Toggle(model.t("calls.take_over"),isOn: Binding(get: { current.takeover ?? false },set: { takeover in control("takeover",enabled: takeover) })).disabled(busy)
                            TextField(model.t("calls.say_label"),text: $say,axis: .vertical)
                            Button(model.t("calls.say"),systemImage: "waveform") { control("say",text: say) }.studioButton(.tertiary).disabled(busy || say.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || say.count > 4000)
                            Button(model.t("calls.hang_up"),systemImage: "phone.down.fill") { control("hangup") }.studioButton(.primary).disabled(busy)
                        }
                        ForEach(Array((current.transcript ?? []).enumerated()),id: \.offset) { _,line in Text(model.t(line.speaker == "agent" ? "calls.speaker_agent" : "calls.speaker_caller")+": "+line.text) }
                        if let summary = current.summary { Text(summary) }
                    }
                }
                Text(model.t("calls.history")).font(.headline)
                ForEach(history) { item in Button(item.to_masked+" · "+item.status) { current = item; beginPolling() }.studioButton(.tertiary) }
            }
        }
        .navigationTitle(model.t("calls.title")).navigationBarTitleDisplayMode(.inline)
        .task(id: model.hostState.scopeID) { clearHost(); await load() }
        .onChange(of: phase) { _,value in if value == .active { beginPolling() } else { polling?.cancel() } }
        .onDisappear { polling?.cancel() }
        .confirmationDialog(model.t("calls.confirm_title",["number":number]),isPresented: $confirm,titleVisibility: .visible) {
            Button(model.t("calls.confirm_call")) { place() }
            Button(model.t("common.cancel"),role: .cancel) {}
        } message: { Text(model.t("calls.confirm_body",["voice":voices.first { $0.id == voice }?.name ?? "","minutes":minutes])+"\n"+disclosure+"\n"+model.t("mobile.calls.billing")) }
    }
    private func clearHost() { polling?.cancel(); current = nil; history = []; voices = []; readiness = []; disclosure = ""; optionsLoaded = false; confirm = false }
    private func load() async {
        guard authenticated else { return }
        let scope = model.hostState.scopeID
        await model.perform {
            let api = try model.api()
            async let ready = api.callReadiness(), options = api.callOptions(), list = api.recentCalls(), profiles = api.callVoices()
            let values = try await (ready,options,list,profiles)
            guard scope == model.hostState.scopeID else { throw CancellationError() }
            readiness = values.0; disclosure = values.1.disclosure; history = values.2; voices = values.3.filter(\.eligible); optionsLoaded = true
            if !voices.contains(where: { $0.id == voice }) { voice = voices.first?.id ?? "" }
        }
    }
    private func place() {
        guard valid else { return }
        let value = NewCall(to: number,brief: brief,profile_id: voice,disclosure: disclosure,max_minutes: Int(minutes) ?? 10), scope = model.hostState.scopeID
        busy = true
        Task {
            defer { busy = false }
            await model.perform {
                let result = try await model.api().startCall(value)
                guard scope == model.hostState.scopeID else { throw CancellationError() }; current = result; beginPolling()
            }
        }
    }
    private func control(_ action: String,text: String = "",enabled: Bool = false) {
        guard let current, !busy else { return }; busy = true
        let scope = model.hostState.scopeID
        Task {
            defer { busy = false }
            await model.perform {
                let api = try model.api(); try await api.controlCall(current.id,action: action,text: text,enabled: enabled)
                let updated = try await api.call(current.id)
                guard scope == model.hostState.scopeID else { throw CancellationError() }; self.current = updated; if action == "say" { say = "" }
            }
        }
    }
    private func beginPolling() {
        polling?.cancel()
        guard let current, authenticated, phase == .active else { return }
        let scope = model.hostState.scopeID
        polling = Task {
            guard let api = try? model.api() else { return }
            do { while !Task.isCancelled {
                let record = try await api.call(current.id)
                guard scope == model.hostState.scopeID else { return }; self.current = record
                if !record.live { await load(); return }
                try await Task.sleep(for: .seconds(2))
            } } catch is CancellationError {} catch { if scope == model.hostState.scopeID { model.error = error as? ClientError ?? ClientError("mobile.errors.invalid_response") } }
        }
    }
}
