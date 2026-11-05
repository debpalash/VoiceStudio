import SwiftUI
import VoiceStudioKit

private struct ThemeKey: EnvironmentKey { static let defaultValue = Theme() }
extension EnvironmentValues {
    var vsTheme: Theme { get { self[ThemeKey.self] } set { self[ThemeKey.self] = newValue } }
}
extension Theme {
    func color(_ role: String) -> Color { let c = self[role]; return Color(.sRGB, red: c.r, green: c.g, blue: c.b, opacity: c.a) }
}
enum VSButtonKind { case primary, secondary, tertiary }
/// Keep destructive roles visible and put the full touch target inside the Button.
private struct StudioPlainButtonStyle: ButtonStyle {
    @Environment(\.vsTheme) private var theme
    func makeBody(configuration: Configuration) -> some View {
        Group {
            if configuration.role == .destructive {
                configuration.label.foregroundStyle(theme.color("error"))
            } else {
                configuration.label
            }
        }.frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.65 : 1)
    }
}
/// Apply system button styles to the original control, retaining its action,
/// accessibility identity and List-row activation semantics.
private struct StudioButton: ViewModifier {
    let kind: VSButtonKind
    @ViewBuilder func body(content: Content) -> some View {
        if kind == .primary {
            content.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).controlSize(.large)
        } else if kind == .secondary {
            content.buttonStyle(.bordered).buttonBorderShape(.capsule).controlSize(.large)
        } else {
            content.buttonStyle(StudioPlainButtonStyle())
        }
    }
}
extension View {
    func studioButton(_ kind: VSButtonKind = .secondary) -> some View { modifier(StudioButton(kind: kind)) }
    func studioNavigationTitle(_ title: String) -> some View {
        navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Keep the navigation item's title for accessibility and Back,
                // while placing the visible heading beside the trailing actions.
                ToolbarItem(placement: .principal) {
                    Color.clear.frame(width: 1, height: 1).accessibilityHidden(true)
                }
                StudioLeadingTitle(title: title)
            }
    }
}
private struct StudioLeadingTitle: ToolbarContent {
    let title: String
    @ToolbarContentBuilder var body: some ToolbarContent {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            ToolbarItem(placement: .topBarLeading) { label }
                .sharedBackgroundVisibility(.hidden)
        } else {
            ToolbarItem(placement: .topBarLeading) { label }
        }
        #else
        ToolbarItem(placement: .topBarLeading) { label }
        #endif
    }
    private var label: some View {
        Text(title).font(.headline).fixedSize(horizontal: true, vertical: false)
            .accessibilityAddTraits(.isHeader).accessibilityIdentifier("navigation.title")
    }
}
struct VSFieldStyle: TextFieldStyle {
    @Environment(\.vsTheme) private var theme
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration.padding(Theme.compactInset).frame(minHeight: 44)
            .background(theme.color("input"), in: RoundedRectangle(cornerRadius: VSTokens.iosFieldRadius))
            .tint(theme.color("focus"))
    }
}
struct Page<Content: View>: View {
    @Environment(\.vsTheme) private var theme
    @Environment(AppModel.self) private var model
    let content: Content
    let backgroundRole: String
    let fill: Bool
    init(backgroundRole: String = "canvas", fill: Bool = false, @ViewBuilder content: () -> Content) {
        self.backgroundRole = backgroundRole; self.fill = fill; self.content = content()
    }
    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
                    if model.loading {
                        ProgressView(model.t("common.loading")).frame(minHeight: 44).accessibilityIdentifier("page.loading")
                    }
                    content
                }
                .frame(minHeight: fill ? max(0, geometry.size.height - Theme.compactInset * 2) : nil)
                    .padding(Theme.compactInset).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.color(backgroundRole).ignoresSafeArea())
        .keyboardAccessible()
    }
}
struct Card<Content: View>: View {
    @Environment(\.vsTheme) private var theme
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        VStack(alignment: .leading, spacing: VSTokens.spacingGap) { content }
            .frame(maxWidth: .infinity, alignment: .leading).padding(Theme.compactInset)
            .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
    }
}
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var scheme
    @State private var recovery: String?
    @State private var inbox = PhoneInbox.shared
    @State private var shortcut: String?
    var body: some View {
        let theme = Theme(palette: model.theme.palette(system: scheme == .dark ? .dark : .light), appearance: model.theme.appearance(system: scheme == .dark ? .dark : .light))
        TabView {
            NavigationStack { HomeView() }.tabItem { StudioTabIcon(title: model.t("nav.home"), systemImage: "house") }
            NavigationStack { VoicesView() }.tabItem { StudioTabIcon(title: model.t("mobile.tabs.voices"), systemImage: "person.wave.2") }
            NavigationStack { DubView() }.tabItem { StudioTabIcon(title: model.t("mobile.tabs.dub"), systemImage: "film") }
            NavigationStack { DictateView() }.tabItem { StudioTabIcon(title: model.t("mobile.tabs.dictate"), systemImage: "mic") }
            NavigationStack { SettingsView() }.tabItem { StudioTabIcon(title: model.t("mobile.tabs.settings"), systemImage: "gearshape") }
        }
        .id(model.hostState.scopeID)
        .modifier(StudioTabBehavior())
        .sheet(isPresented: Binding(get: { !model.consentAnswered }, set: { presented in
            if !presented && !model.consentAnswered { model.chooseConsent(false) }
        }), onDismiss: {
            if !model.consentAnswered { model.chooseConsent(false) }
        }) {
            ConsentView()
                .presentationBackground(theme.color("surfaceOverlay"))
                .presentationDragIndicator(.visible)
        }
        .task { await model.phone.refresh(); inbox.collect() }
        .onOpenURL { url in do { try inbox.receive(url) } catch { model.error = error as? ClientError ?? ClientError("mobile.errors.file") } }
        .onChange(of: inbox.route,initial: true) { _,value in if let value { shortcut = value; inbox.route = nil } }
        .sheet(item: Binding(get: { model.consentAnswered ? inbox.incoming : nil },set: { inbox.incoming = $0 })) { incoming in
            NavigationStack { IncomingView(incoming: incoming).toolbar { Button(model.t("common.close")) { inbox.incoming = nil } } }.environment(model).environment(\.vsTheme,theme)
        }
        .sheet(isPresented: Binding(get: { shortcut != nil && model.consentAnswered },set: { if !$0 { shortcut = nil } })) {
            NavigationStack {
                Group {
                    if shortcut == "dictate" { DictateView() }
                    else if shortcut == "workflows" { WorkflowsView() }
                    else if shortcut == "dub" { DubView() }
                    else { SpeakView() }
                }.toolbar { Button(model.t("common.close")) { shortcut = nil } }
            }.environment(model).environment(\.vsTheme,theme)
        }
        .onAppear { NativeTheme.apply(theme) }
        .onChange(of: model.theme) { _, _ in NativeTheme.apply(theme) }
        .onChange(of: scheme) { _, _ in NativeTheme.apply(theme) }
        .environment(\.vsTheme, theme)
        .font(.body)
        .foregroundStyle(theme.color("text")).tint(theme.color("messageAction"))
        .textFieldStyle(VSFieldStyle())
        .background(theme.color("canvas"))
        .sheet(isPresented: Binding(get: { recovery != nil },set: { if !$0 { recovery = nil } })) {
            NavigationStack {
                Group { if recovery == "models" { ModelsView() } else { ConnectView() } }
                    .toolbar { Button(model.t("common.close")) { recovery = nil } }
            }.environment(model).environment(\.vsTheme,theme)
        }
        .safeAreaInset(edge: .top) {
            if let error = model.error ?? model.phone.error {
                HStack(spacing: VSTokens.spacingGap) {
                    ErrorNotice(error: error).foregroundStyle(theme.color("error")).accessibilityIdentifier("error.notice")
                        .frame(maxWidth: .infinity,alignment: .leading)
                    if error.key.hasPrefix("mobile.local.") {
                        Button(model.t("mobile.simple.download_models")) { model.error = nil; model.phone.error = nil; recovery = "models" }
                    } else if error.key == "mobile.permissions.denied" {
                        Button(model.t("mobile.permissions.open_settings")) {
                            model.error = nil; model.phone.error = nil
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    } else if error.status == 401 || error.key.contains("offline") || error.key.contains("connection") {
                        Button(model.t("mobile.simple.reconnect")) { model.error = nil; model.phone.error = nil; recovery = "connect" }
                    } else {
                        Button(model.t("mobile.simple.retry")) {
                            model.error = nil; model.phone.error = nil
                            Task { await model.phone.refresh(); if model.connection.client != nil { await model.perform { try await model.refresh() } } }
                        }
                    }
                }.padding(Theme.compactInset).background(theme.color("errorSurface"))
                    .environment(\.vsTheme,theme).studioButton(.tertiary)
            } else if let notice = model.notice {
                HStack(spacing: VSTokens.spacingGap) {
                    Label(model.t(notice), systemImage: "checkmark.circle").frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("notice")
                    Button(model.t("common.close")) { model.notice = nil }
                }.padding(Theme.compactInset).background(theme.color("surfaceRaised"))
                    .environment(\.vsTheme,theme).studioButton(.tertiary)
                    .task(id: notice) {
                        AccessibilityNotification.Announcement(model.t(notice)).post()
                        try? await Task.sleep(for: .seconds(5))
                        if model.notice == notice { model.notice = nil }
                    }
            }

        }
    }

}
struct ErrorNotice: View {
    @Environment(AppModel.self) private var model
    let error: ClientError
    var titleKey: String?
    var body: some View {
        let message = ErrorPresentation(error,l10n: model.l10n,titleKey: titleKey)
        VStack(alignment: .leading,spacing: 2) {
            Text(message.title)
            if let detail = message.detail { Text(detail).font(.footnote) }
        }.fixedSize(horizontal: false,vertical: true)
    }
}
struct ConsentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: VSTokens.spacingGap * 2) {
                Image(systemName: "waveform").font(.system(size: 52, weight: .medium)).foregroundStyle(theme.color("messageAction")).accessibilityHidden(true)
                Text(model.t("consent.title")).font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader).accessibilityIdentifier("consent.title")
                Text(model.t("consent.body")).font(.title3).foregroundStyle(theme.color("textMuted"))
                Text(model.t("mobile.android.analytics_local")).foregroundStyle(theme.color("textMuted"))
            }.padding(.vertical, VSTokens.spacingGap * 2)
        }.safeAreaInset(edge: .bottom) {
            // Exactly two equal choices; dismissing, swiping or Escape records No.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: VSTokens.spacingGap) { choice(true); choice(false) }
                VStack(spacing: VSTokens.spacingGap) { choice(true); choice(false) }
            }
            .padding(Theme.compactInset).background(.bar)
        }
        .accessibilityAction(.escape) { model.chooseConsent(false) }
    }
    @ViewBuilder func choice(_ value: Bool) -> some View {
        let button = Button { model.chooseConsent(value) } label: {
            Text(model.t(value ? "consent.yes" : "consent.no"))
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity)
        }.studioButton(.secondary).accessibilityIdentifier(value ? "consent.yes" : "consent.no")
        if value { button } else { button.keyboardShortcut(.cancelAction) }
    }
}

private struct StudioTabBehavior: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) { content.tabBarMinimizeBehavior(.never) }
        else { content }
        #else
        content
        #endif
    }
}

/// Keep destination names visible so the dock is understandable before learning its icons.
private struct StudioTabIcon: View {
    let title: String
    let systemImage: String
    var body: some View {
        Label(title, systemImage: systemImage)
    }
}
