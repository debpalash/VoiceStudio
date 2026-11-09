import SwiftUI
import VoiceStudioKit

/// The Report action on a generated result (SPEC.md → Reporting generated content).
struct ReportButton: View {
    @Environment(AppModel.self) private var model
    let subject: ContentReportSubject
    @Binding var reporting: ContentReportSubject?
    /// The result's title, so VoiceOver says what is being reported.
    var context: String? = nil
    var body: some View {
        Button(model.t("mobile.report.action"), systemImage: "flag") {
            model.reports.open(subject); reporting = subject
        }
        .accessibilityLabel(context.map { model.t("mobile.report.action") + ": " + $0 } ?? model.t("mobile.report.action"))
        .accessibilityIdentifier("report." + subject.id)
    }
}

extension View {
    func contentReportSheet(_ subject: Binding<ContentReportSubject?>) -> some View {
        sheet(item: subject) { ContentReportSheet(subject: $0) }
    }
}

struct ContentReportSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.dismiss) private var dismiss
    let subject: ContentReportSubject
    private var phase: ContentReports.Phase { model.reports.phase(for: subject) }
    private var draft: ContentReportDraft { model.reports.draft(for: subject) ?? ContentReportDraft(idempotencyKey: "") }
    private var binding: Binding<ContentReportDraft> {
        Binding(get: { draft }, set: { model.reports.update($0, for: subject) })
    }
    private var statusKey: String? {
        switch phase {
        case .failed: ContentReportOutcome.failed.messageKey
        case .rateLimited: ContentReportOutcome.rateLimited.messageKey
        case .invalid: ContentReportOutcome.invalid.messageKey
        case .editing, .sending: nil
        }
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ForEach(ContentReportReason.allCases, id: \.self) { reason in
                        let selected = draft.reason == reason
                        Button { binding.wrappedValue.reason = reason } label: {
                            HStack(spacing: VSTokens.spacingGap) {
                                Text(model.t(reason.labelKey)).frame(maxWidth: .infinity, alignment: .leading)
                                Image(systemName: "checkmark").foregroundStyle(theme.color("messageAction"))
                                    .opacity(selected ? 1 : 0).accessibilityHidden(true)
                            }.frame(minHeight: 44).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(selected ? [.isSelected] : [])
                        .accessibilityIdentifier("report.reason." + reason.rawValue)
                    }
                } header: { Text(model.t("mobile.report.reason")) }
                .listRowBackground(theme.color("surface"))
                Section {
                    TextField(model.t("mobile.report.note"), text: binding.note, axis: .vertical)
                        .lineLimit(3...8).textFieldStyle(.plain).frame(minHeight: 44)
                        .accessibilityLabel(model.t("mobile.report.note"))
                        .accessibilityIdentifier("report.note")
                    if subject.hasTypedText {
                        Toggle(model.t("mobile.report.include_text"), isOn: binding.includeText)
                            .frame(minHeight: 44).accessibilityIdentifier("report.include_text")
                    }
                } footer: { Text(model.t("mobile.report.disclosure")) }
                .listRowBackground(theme.color("surface"))
                if let statusKey {
                    Section {
                        Text(model.t(statusKey)).foregroundStyle(theme.color("error"))
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("report.status")
                    }.listRowBackground(theme.color("errorSurface"))
                }
            }
            .disabled(phase == .sending)
            .scrollContentBackground(.hidden).background(theme.color("surfaceOverlay").ignoresSafeArea())
            .keyboardAccessible()
            .navigationTitle(model.t("mobile.report.title")).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.cancel")) { dismiss() } } }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                Button { send() } label: {
                    Label(model.t(phase == .sending ? "mobile.report.sending" : (phase == .failed ? "mobile.report.retry" : "mobile.report.send")),
                          systemImage: phase == .failed ? "arrow.clockwise" : "paperplane")
                        .frame(maxWidth: .infinity)
                }
                .studioButton(.primary)
                .disabled(!draft.canSend || phase == .sending || phase == .invalid)
                .accessibilityIdentifier("report.send")
                .padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
            }
        }
        .presentationDetents([.large]).presentationDragIndicator(.visible)
        .onAppear { model.reports.open(subject) }
    }
    private func send() {
        let reports = model.reports, report = subject, app = model, close = dismiss
        // Not tied to the sheet: if it is closed mid-send, the outcome still lands on the draft.
        Task { @MainActor in
            guard await reports.send(report) == .sent else { return }
            app.notice = ContentReportOutcome.sent.messageKey
            close()
        }
    }
}
