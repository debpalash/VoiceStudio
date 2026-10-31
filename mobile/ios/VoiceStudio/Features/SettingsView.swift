import SwiftUI
import VoiceStudioKit

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                NavigationLink { ConnectView() } label: {
                    LabeledContent {
                        Text(model.connection.active?.label ?? "").foregroundStyle(theme.color("textMuted"))
                    } label: { Label(model.t("mobile.settings.connection"), systemImage: "link") }
                }.accessibilityIdentifier("settings.connect")
                NavigationLink { ModelsView() } label: { Label(model.t("mobile.local.models"),systemImage: "arrow.down.circle") }
            }
            if model.connection.client != nil {
                Section {
                    NavigationLink { ConnectView() } label: { Label(model.t("mobile.connect.saved_hosts"),systemImage: "desktopcomputer") }.disabled(model.busy || BackgroundUpload.shared.pending != nil)
                    Button(model.t("mobile.connect.disconnect"), systemImage: "personalhotspot.slash", role: .destructive) { Task { await model.perform(connectionChange: true) { model.pauseMonitor(); await BackgroundUpload.shared.cancel(); try await model.connection.disconnect(); model.profiles = []; model.history = []; model.dubHistory = []; model.inventory = nil } } }.disabled(model.busy)
                }
            }
                Section {
                    ChoicePicker(label: model.t("mobile.settings.mode"), selection: Binding(get: { model.theme.mode.rawValue }, set: { model.theme.mode = ThemeMode(rawValue: $0) ?? .dark }), options: ThemeMode.allCases.map { SelectionOption(id: $0.rawValue, title: model.t("mobile.settings.mode_" + $0.rawValue)) }, icon: "circle.lefthalf.filled")
                    palette(key: "mobile.settings.light_palette", selection: $model.theme.lightPalette)
                    palette(key: "mobile.settings.dark_palette", selection: $model.theme.darkPalette)

                }
                Section {
                    ChoicePicker(label: model.t("mobile.settings.language"), selection: $model.language, options:
                        [SelectionOption(id: "", title: model.t("mobile.ios.language_system"))] + L10n.languages.map { SelectionOption(id: $0, title: Locale(identifier: $0).localizedString(forIdentifier: $0) ?? $0) }, icon: "globe").accessibilityIdentifier("settings.language")
                    Toggle(isOn: Binding(get: { model.notifications }, set: { value in Task { await model.enableNotifications(value) } })) { Label(model.t("mobile.settings.notifications"),systemImage: "bell") }.frame(minHeight: 44)
                }
            Section {
                InfoButton(title: model.t("mobile.simple.privacy"), detail: model.t("mobile.local.privacy"), row: true, icon: "shield")
                Toggle(isOn: $model.analytics) { Label(model.t("mobile.simple.analytics"),systemImage: "chart.bar") }.frame(minHeight: 44).accessibilityIdentifier("settings.analytics")
            }
            Section(model.t("donate.title")) {
                NavigationLink { DonateView() } label: { Label(model.t("mobile.support.donate"), systemImage: "heart") }
                    .accessibilityIdentifier("settings.donate")
                NavigationLink { ProView() } label: { Label(model.t("proPage.title"), systemImage: "sparkles") }
                    .accessibilityIdentifier("settings.pro")
                NavigationLink { OtherAppsView() } label: { Label(model.t("mobile.support.other_apps"), systemImage: "square.grid.2x2") }
                    .accessibilityIdentifier("settings.other-apps")
            }
            Section {
                HStack { Label(model.t("mobile.ios.app_name"),systemImage: "info.circle"); Spacer(); Text(AppVersion.full(info: Bundle.main.infoDictionary) ?? model.t("common.unknown")) }
                NavigationLink { LicensesView() } label: { Label(model.t("mobile.settings.licenses"),systemImage: "doc.text") }
                if let host = model.connection.active {
                    Link(destination: host.url) { Label(model.t("mobile.ios.model_licenses"),systemImage: "doc.badge.gearshape") }
                }
            }
        }.scrollContentBackground(.hidden).background(theme.color("canvas").ignoresSafeArea())
            .studioNavigationTitle(model.t("mobile.tabs.settings"))
    }
    private func palette(key: String, selection: Binding<String>) -> some View {
        ChoicePicker(label: model.t(key), selection: selection, options: VSTokens.palettes.map { SelectionOption(id: $0.id, title: Theme.paletteLabel($0, l10n: model.l10n), swatch: Theme(palette: $0.id, appearance: key == "mobile.settings.light_palette" ? .light : .dark)) }, icon: "paintpalette").accessibilityIdentifier("settings.palette." + (key == "mobile.settings.light_palette" ? "light" : "dark"))
    }
}
struct LicensesView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        Page {
            Text(model.t("mobile.ios.licenses_app"))
            Text(model.t("mobile.ios.licenses_inter"))
            ForEach((Bundle.main.urls(forResourcesWithExtension: "txt", subdirectory: "NativeLicences") ?? []).sorted { $0.lastPathComponent < $1.lastPathComponent }, id: \.self) { url in
                DisclosureGroup(url.deletingPathExtension().lastPathComponent) {
                    if let text = try? String(contentsOf: url, encoding: .utf8) { Text(text).textSelection(.enabled) }
                }
            }
            if let url = Bundle.main.url(forResource: "OFL", withExtension: "txt"), let text = try? String(contentsOf: url, encoding: .utf8) { Text(text).textSelection(.enabled) }
            if let url = Bundle.main.url(forResource: "AGPL-3.0", withExtension: "txt"), let text = try? String(contentsOf: url, encoding: .utf8) { Text(text).textSelection(.enabled) }
        }.navigationTitle(model.t("mobile.settings.licenses"))
    }
}
