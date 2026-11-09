#if DEBUG
import SwiftUI
import VoiceStudioKit

/// Live previews (Xcode canvas) run the same offline mock host as the UI tests, so
/// screens render with realistic data and update as the file is edited.
private struct PreviewHost<Content: View>: View {
    @State private var model = AppModel(mock: true)
    @Environment(\.colorScheme) private var scheme
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        let theme = Theme(palette: model.theme.palette(system: scheme == .dark ? .dark : .light), appearance: model.theme.appearance(system: scheme == .dark ? .dark : .light))
        NavigationStack { content }
            .environment(model).environment(\.vsTheme, theme)
            .textFieldStyle(VSFieldStyle()).tint(theme.color("messageAction"))
            .foregroundStyle(theme.color("text")).background(theme.color("canvas"))
            .task { NativeTheme.apply(theme); await model.phone.refresh() }
    }
}
private struct LicencePreview: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        if let item = model.phone.models.first { ModelLicenceSheet(item: item) } else { ProgressView() }
    }
}

#Preview("Home") { PreviewHost { HomeView() }.preferredColorScheme(.dark) }
#Preview("Dub") { PreviewHost { DubView() }.preferredColorScheme(.dark) }
#Preview("Dub, large text") { PreviewHost { DubView() }.preferredColorScheme(.dark).environment(\.dynamicTypeSize, .accessibility2) }
#Preview("Dub, light") { PreviewHost { DubView() }.preferredColorScheme(.light) }
#Preview("Models") { PreviewHost { ModelsView() }.preferredColorScheme(.dark) }
#Preview("Licence") { PreviewHost { LicencePreview() }.preferredColorScheme(.dark) }
#Preview("Dub glyph") {
    PreviewHost { VStack(spacing: 24) { DubGlyph(size: 96); DubGlyph(size: 48, active: true) } }.preferredColorScheme(.dark)
}
#endif
