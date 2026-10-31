import SwiftUI
import VoiceStudioKit

struct DictateView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var preference: TaskLocation?
    private var phoneModel: PhoneModel? { model.phone.preferred(.stream) }
    private var localReady: Bool { phoneModel.map(model.phone.ready) ?? false }
    private var route: TaskRoute { LocalRouting.route(preference: preference, installed: localReady, paired: model.connection.client != nil) }
    @State private var holding = false
    @State private var suppressTap = false
    @State private var text = ""
    @State private var sessionID = UUID().uuidString.lowercased()
    @State private var sessionText = ""
    @State private var transcript = DictationTranscript()
    @State private var previousText = ""
    @State private var checking = true
    @State private var ready = false
    @State private var reason = ""
    @State private var listening = false
    @State private var finalizing = false
    @State private var capturing: Task<Void, Never>?
    @State private var receiving: Task<Void, Never>?
    @State private var audio = DictationAudio()
    @State private var stream = DictationStream()
    var body: some View {
        Page(fill: true) {
            NavigationLink(model.t("transcriptions.title")) { TranscriptsView() }.disabled(listening || finalizing)
            RunOnPicker(preference: $preference, task: "dictate", installed: localReady).disabled(listening || finalizing)
            if route == .connect { NavigationLink(model.t("mobile.settings.connection")) { ConnectView() } }
            if checking { ProgressView(model.t("common.loading")) }
            else if !ready && route == .computer {
                Text(reason).fixedSize(horizontal: false, vertical: true)
                Button(model.t("mobile.simple.retry")) { Task { await readiness() } }
            }
            if text.isEmpty {
                Spacer(minLength: 0)
                ContentUnavailableView {
                    Label(model.t("mobile.ios.transcript"), systemImage: "waveform")
                } description: {
                    if route == .download, let phoneModel {
                        Text(model.t("mobile.local.inline_download", ["size": ByteCountFormatter.string(fromByteCount: phoneModel.total_bytes, countStyle: .file)]))
                    } else if listening { Text(model.t("mobile.dictate.listening")) }
                    else if ready { Text(model.t("mobile.dictate.tap_to_start")) }
                }
                Spacer(minLength: 0)
            } else {
                Text(text).font(.title3).frame(maxWidth: .infinity, minHeight: 240, alignment: .topLeading)
                    .textSelection(.enabled).accessibilityLabel(model.t("mobile.ios.transcript"))
            }
        }.studioNavigationTitle(model.t("mobile.tabs.dictate"))
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: VSTokens.spacingGap) {
                if route == .download {
                    LocalDownloadPrompt(items: phoneModel.map { [$0] } ?? [], feature: "mobile.tabs.dictate")
                        .padding(.horizontal, Theme.compactInset)
                }
                HStack(spacing: VSTokens.spacingGap) {
                    Button(model.t("mobile.dictate.copy"), systemImage: "doc.on.doc") { UIPasteboard.general.string = text }
                        .labelStyle(.iconOnly).studioButton(.tertiary).disabled(text.isEmpty)
                        .frame(maxWidth: .infinity).accessibilityIdentifier("dictate.copy")
                    recordControl
                    ShareLink(item: text) { Label(model.t("mobile.speak.share"), systemImage: "square.and.arrow.up") }
                        .labelStyle(.iconOnly).studioButton(.tertiary).disabled(text.isEmpty)
                        .frame(maxWidth: .infinity).accessibilityIdentifier("dictate.share")
                }.padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
            }.background(.bar)
        }
        .task { preference = model.phone.preference("dictate"); await model.phone.refresh(); await readiness() }
        .onChange(of: preference) { _, _ in Task { await readiness() } }
        .onChange(of: route) { _, _ in Task { await readiness() } }
        .refreshable { await readiness() }
        .onDisappear { saveTranscript(); capturing?.cancel(); receiving?.cancel(); audio.stop(); listening = false; finalizing = false; Task { await stream.close() } }
    }
    private var recordControl: some View {
            Button {
                guard !suppressTap else { return }
                if listening { stop() } else { start() }
            } label: {
                Image(systemName: listening ? "stop.fill" : "mic.fill")
                    .font(.largeTitle).symbolEffect(.pulse, isActive: listening && !reduceMotion).frame(width: 88, height: 88)
                    .foregroundStyle(theme.color("messageActionForeground"))
                    .background(theme.color("messageAction"), in: Circle())
            }.buttonStyle(.plain).frame(maxWidth: .infinity, minHeight: 104)
                .disabled(!ready || finalizing || model.phone.activeDub != nil)
                .accessibilityLabel(model.t(listening ? "common.stop" : "mobile.dictate.tap_to_start")).accessibilityHint(model.t("mobile.dictate.hold_to_talk"))
                .accessibilityIdentifier("dictate.record")
                .onLongPressGesture(minimumDuration: 0.2, pressing: { pressed in
                    if !pressed && holding {
                        holding = false; if listening { stop() }
                        Task { try? await Task.sleep(for: .milliseconds(150)); suppressTap = false }
                    }
                }, perform: { holding = true; suppressTap = true; if !listening && ready { start() } })
    }
    private func readiness() async {
        let scope = model.hostState.scopeID
        let selectedRoute = route
        checking = true; defer { checking = false }
        if route != .computer {
            ready = route == .phone; reason = ready ? "" : model.t("mobile.local.model_required")
            return
        }
        do {
            let status = try await model.api().readiness()
            guard scope == model.hostState.scopeID, selectedRoute == route else { return }
            ready = status.ready; reason = status.reason(l10n: model.l10n)
        }
        catch is CancellationError {}
        catch let error as ClientError { guard scope == model.hostState.scopeID, selectedRoute == route else { return }; ready = false; reason = model.t(error.key, error.params); await model.connection.handle(error) }
        catch { guard scope == model.hostState.scopeID, selectedRoute == route else { return }; ready = false; reason = model.t("mobile.errors.offline") }
    }
    private func start() {
        guard ready, !listening, !finalizing else { return }
        let scope = model.hostState.scopeID
        listening = true; previousText = text; transcript = DictationTranscript(); sessionID = UUID().uuidString.lowercased(); sessionText = ""
        if route == .phone { startLocal(); return }
        let id = sessionID
        capturing = Task {
            // Per session: a finished `receiving` from an earlier session must not count.
            var receiverCreated = false
            do {
                let api = try model.api()
                let messages = try await stream.start(api: api)
                // A stop during startup never creates `receiving`, so nothing else clears `finalizing`.
                guard scope == model.hostState.scopeID, listening, !Task.isCancelled else { finalizing = false; await stream.close(); return }
                receiving = Task {
                    var latest = ""
                    defer { saveTranscript(id: id,text: latest,modelID: nil) }
                    do {
                        for try await message in messages {
                            try Task.checkCancellation()
                            guard scope == model.hostState.scopeID,id == sessionID else { throw CancellationError() }
                            transcript.receive(message)
                            latest = transcript.text; sessionText = latest
                            text = [previousText, transcript.text].filter { !$0.isEmpty }.joined(separator: "\n")
                            if transcript.finished { audio.stop(); listening = false; finalizing = false }
                        }
                    } catch { audio.stop(); listening = false; finalizing = false; if !Task.isCancelled && scope == model.hostState.scopeID { model.error = error as? ClientError ?? ClientError("mobile.errors.dictation") } }
                }
                receiverCreated = true
                let chunks = try await audio.start()
                guard scope == model.hostState.scopeID, listening, !Task.isCancelled else { audio.stop(); try? await stream.finish(); return }
                for try await chunk in chunks { try Task.checkCancellation(); try await stream.send(chunk) }
                try await stream.finish()
            } catch is CancellationError { if !receiverCreated { finalizing = false } } catch {
                audio.stop(); listening = false; finalizing = false; await stream.close()
                if scope == model.hostState.scopeID { model.error = error as? ClientError ?? ClientError("mobile.errors.audio") }
            }
        }
    }
    private func startLocal() {
        let id = sessionID
        capturing = Task {
            defer { audio.stop(); listening = false; finalizing = false }
            do {
                guard let phoneModel, let engine = model.phone.engine else { throw ClientError("mobile.local.model_required") }
                let (chunks, continuation) = AsyncThrowingStream<PCMChunk, any Error>.makeStream(bufferingPolicy: .bufferingOldest(64))
                // The inference slot and recognizer come first; the bounded capture buffer would overflow while waiting.
                let updates = try await engine.streamTranscribe(model: phoneModel, chunks: chunks, progress: { _ in })
                let bytes: AsyncThrowingStream<Data, any Error>
                do {
                    bytes = try await audio.start()
                    guard listening, !Task.isCancelled else { audio.stop(); continuation.finish(); return }
                } catch { continuation.finish(throwing: error); throw error } // releases the slot
                receiving = Task {
                    var latest = ""
                    defer { saveTranscript(id: id,text: latest,modelID: phoneModel.id) }
                    do {
                        for try await update in updates {
                            try Task.checkCancellation()
                            guard id == sessionID else { throw CancellationError() }
                            latest = update.text; sessionText = latest
                            text = [previousText, update.text].filter { !$0.isEmpty }.joined(separator: "\n")
                        }
                    } catch is CancellationError {} catch { model.error = error as? ClientError ?? ClientError("mobile.local.inference_failed") }
                    audio.stop()
                }
                do {
                    for try await chunk in bytes {
                        try Task.checkCancellation()
                        if case .dropped = continuation.yield(try PCMChunk(pcm16: chunk)) { throw ClientError("mobile.errors.audio") }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error); throw error }
                await receiving?.value
            } catch is CancellationError {} catch { model.error = error as? ClientError ?? ClientError("mobile.errors.audio") }
        }
    }
    private func saveTranscript() {
        saveTranscript(id: sessionID,text: sessionText,modelID: route == .phone ? phoneModel?.id : nil)
    }
    private func saveTranscript(id: String,text: String,modelID: String?) {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty,let store = model.phone.transcriptStore else { return }
        let title = String((value.components(separatedBy: .newlines).first ?? value).prefix(120))
        Task { await model.performPhone { _ = try await store.saveDictation(id: id,title: title,text: value,modelID: modelID) } }
    }
    private func stop() { finalizing = true; audio.stop(); listening = false }
}
