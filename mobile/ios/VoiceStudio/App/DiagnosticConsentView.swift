import SwiftUI
import VoiceStudioKit

/// This choice is independent of the preceding usage-analytics choice.
struct DiagnosticConsentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: VSTokens.spacingGap * 2) {
                Image(systemName: "exclamationmark.bubble").font(.system(size: 52, weight: .medium)).foregroundStyle(theme.color("messageAction")).accessibilityHidden(true)
                Text(model.t("diagnostics.title")).font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader).accessibilityIdentifier("diagnostics.title")
                Text(model.t("diagnostics.body")).font(.title3).foregroundStyle(theme.color("textMuted"))
                if let key = model.diagnostics.errorKey { Text(model.t(key)).foregroundStyle(theme.color("error")) }
            }.padding(.vertical, VSTokens.spacingGap * 2)
        }.safeAreaInset(edge: .bottom) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: VSTokens.spacingGap) { choice(true); choice(false) }
                VStack(spacing: VSTokens.spacingGap) { choice(true); choice(false) }
            }.padding(Theme.compactInset).background(.bar)
        }
        .interactiveDismissDisabled(model.diagnostics.saving)
        .accessibilityAction(.escape) { Task { await model.diagnostics.setConsent(false) } }
    }
    private func choice(_ value: Bool) -> some View {
        Button { Task { await model.diagnostics.setConsent(value) } } label: {
            Text(model.t(value ? "diagnostics.allow" : "diagnostics.keep_off"))
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity)
        }.studioButton(.secondary).disabled(model.diagnostics.saving)
            .accessibilityIdentifier(value ? "diagnostics.allow" : "diagnostics.keep_off")
    }
}
