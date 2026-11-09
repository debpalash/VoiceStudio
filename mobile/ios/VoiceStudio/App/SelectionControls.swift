import SwiftUI
import VoiceStudioKit

struct SelectionOption: Identifiable {
    let id: String
    let title: String
    var subtitle: String? = nil
    var enabled = true
    var swatch: Theme? = nil
}

/// RAM recommendations guide a choice without preventing an explicit attempt.
@MainActor func phoneModelOption(_ item: PhoneModel, model: AppModel, subtitle: String? = nil) -> SelectionOption {
    SelectionOption(id: item.id, title: item.name,
                    subtitle: item.fits() ? subtitle : model.t("mobile.simple.ram", ["ram": String(item.min_ram_gb)]))
}

struct ModelRamWarning: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let items: [PhoneModel]
    var body: some View {
        if let item = items.filter({ model.phone.ready($0) && !$0.fits() }).max(by: { $0.min_ram_gb < $1.min_ram_gb }) {
            Text(model.t("mobile.local.ram_required", ["ram": String(item.min_ram_gb)]))
                .font(.callout).foregroundStyle(theme.color("textMuted"))
        }
    }
}

struct PhoneModelPicker: View {
    @Environment(AppModel.self) private var model
    let label: String
    @Binding var selection: String
    let items: [PhoneModel]
    var showCapability = false
    var showRamWarning = true
    var body: some View {
        VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
            ChoicePicker(label: label, selection: $selection, options: items.map {
                phoneModelOption($0, model: model, subtitle: showCapability ? model.t("mobile.simple." + $0.capability.rawValue) : nil)
            })
            if showRamWarning { ModelRamWarning(items: items.filter { $0.id == selection }) }
        }
    }
}

struct SelectionRow: View {
    @Environment(\.vsTheme) private var theme
    @Environment(\.dynamicTypeSize) private var typeSize
    let label: String
    let value: String
    var body: some View {
        HStack(spacing: VSTokens.spacingGap) {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: VSTokens.spacingGap) { Text(label); current }
                Spacer(minLength: 0)
            } else {
                Text(label).lineLimit(1).fixedSize(horizontal: true, vertical: false).layoutPriority(1)
                current.frame(maxWidth: .infinity, alignment: .trailing)
            }
            Image(systemName: "chevron.right").foregroundStyle(theme.color("iconMuted")).accessibilityHidden(true)
        }
        .frame(minHeight: 44).contentShape(Rectangle())
        .accessibilityElement(children: .ignore).accessibilityLabel(label).accessibilityValue(value)
    }
    private var current: some View {
        Text(value).foregroundStyle(theme.color("textMuted"))
            .lineLimit(1).truncationMode(.tail).multilineTextAlignment(.trailing)
    }
}

struct ChoicePicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let label: String
    @Binding var selection: String
    let options: [SelectionOption]
    var compact = false
    var icon: String? = nil
    var tile = false
    @State private var presenting = false
    @State private var query = ""
    private var filtered: [SelectionOption] {
        options.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) || $0.id.localizedCaseInsensitiveContains(query) }
    }
    var body: some View {
        Button { query = ""; presenting = true } label: {
            if tile {
                TileLabel(icon: icon ?? "circle", label: label, value: options.first { $0.id == selection }?.title ?? "")
            } else if compact {
                HStack(spacing: 4) {
                    Text(options.first { $0.id == selection }?.title ?? label).lineLimit(1).truncationMode(.tail)
                    Image(systemName: "chevron.down").accessibilityHidden(true)
                }.frame(maxWidth: .infinity, minHeight: 44)
                    .accessibilityElement(children: .ignore).accessibilityLabel(label)
                    .accessibilityValue(options.first { $0.id == selection }?.title ?? label)
            } else {
                HStack {
                if let icon { Image(systemName: icon).accessibilityHidden(true) }
                SelectionRow(label: label, value: options.first { $0.id == selection }?.title ?? model.t("common.unknown"))
                }
            }
        }.studioButton(compact ? .secondary : .tertiary)
        .sheet(isPresented: $presenting) {
            NavigationStack {
                choices
                    .navigationTitle(label).navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.close")) { presenting = false } } }
            }
            .presentationDetents(options.count > 6 ? [.large] : [.medium, .large])
            .presentationDragIndicator(.visible)
        }
    }
    private var choices: some View {
        List {
            SearchField(prompt: model.t("common.search"), text: $query).listRowBackground(theme.color("surfaceOverlay"))
            if filtered.isEmpty {
                SearchEmptyState(query: query).listRowBackground(theme.color("surface"))
            }
            ForEach(filtered) { option in
                Button {
                    selection = option.id; presenting = false
                } label: {
                    HStack(spacing: VSTokens.spacingGap) {
                        if let swatch = option.swatch {
                            HStack(spacing: 0) {
                                swatch.color("canvas")
                                swatch.color("surface")
                                swatch.color("messageAction")
                            }.frame(width: 36, height: 24).clipShape(RoundedRectangle(cornerRadius: 4))
                                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(theme.color("border")))
                                .accessibilityHidden(true).accessibilityIdentifier("choice.swatch." + option.id)
                        }
                        VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
                            Text(option.title).foregroundStyle(theme.color(option.enabled ? "text" : "textMuted"))
                                .lineLimit(2).truncationMode(.tail)
                            if let subtitle = option.subtitle { Text(subtitle).font(.caption).foregroundStyle(theme.color("textMuted")) }
                        }
                        Spacer(minLength: 0)
                        if selection == option.id { Image(systemName: "checkmark").foregroundStyle(theme.color("text")).accessibilityIdentifier("choice.selected") }
                    }.frame(minHeight: 44).contentShape(Rectangle())
                }.studioButton(.tertiary).disabled(!option.enabled)
                    .accessibilityLabel(option.title)
                    .accessibilityValue(ListFormatter.localizedString(byJoining: [model.t(selection == option.id ? "mobile.ios.selected" : "mobile.ios.not_selected")] + (option.subtitle.map { [$0] } ?? [])))
                    .accessibilityAddTraits(selection == option.id ? .isSelected : [])
                    .accessibilityIdentifier("choice.option." + option.id).listRowBackground(theme.color("surface"))
            }
        }
        .scrollContentBackground(.hidden).background(theme.color("surfaceOverlay"))
        .keyboardAccessible()
    }
}

struct SearchEmptyState: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let query: String
    var body: some View {
        Text(query.isEmpty ? model.t("common.no_matches") : model.t("mobile.ios.no_matches", ["query": query]))
            .foregroundStyle(theme.color("textMuted"))
            .accessibilityIdentifier("search.empty")
    }
}

/// The modal owns the draft. Parent tasks only change when Confirm is pressed.
struct LanguageSelectionSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.dismiss) private var dismiss
    let includesUnavailable: Bool
    let confirm: (Set<String>) -> Void
    @State private var draft: Set<String>
    @State private var query = ""

    init(selection: Set<String>, includesUnavailable: Bool, confirm: @escaping (Set<String>) -> Void) {
        self.includesUnavailable = includesUnavailable
        self.confirm = confirm
        _draft = State(initialValue: selection)
    }
    var body: some View {
        NavigationStack {
            List {
                SearchField(prompt: model.t("common.search"), text: $query)
                    .listRowBackground(theme.color("surfaceOverlay"))
                let matches = model.languages.search(query, includeAuto: false)
                    .filter { includesUnavailable || $0.code != nil }
                if matches.isEmpty { SearchEmptyState(query: query) }
                ForEach(matches) { language in
                    let selected = language.code.map { draft.contains($0) } ?? false
                    Button {
                        if let code = language.code {
                            if draft.contains(code) { draft.remove(code) } else { draft.insert(code) }
                        }
                    } label: {
                        HStack(spacing: VSTokens.spacingGap) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(language.name)
                                if language.code == nil {
                                    Text(model.t("mobile.dub.language_unavailable"))
                                        .font(.caption).foregroundStyle(theme.color("textMuted"))
                                }
                            }
                            Spacer(minLength: 0)
                            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(theme.color("messageAction")).accessibilityHidden(true)
                        }.frame(minHeight: 44).contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(language.code == nil)
                    .accessibilityLabel(language.name)
                    .accessibilityValue(model.t(selected ? "mobile.ios.selected" : "mobile.ios.not_selected"))
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .listRowBackground(theme.color("surface"))
                }
            }.scrollContentBackground(.hidden).background(theme.color("surfaceOverlay"))
            .keyboardAccessible()
            .navigationTitle(model.t("mobile.dub.target_languages")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(model.t("common.cancel")) { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button(model.t("common.confirm")) { confirm(draft); dismiss() } }
            }
        }.presentationDetents([.large]).presentationDragIndicator(.visible)
    }
}

struct SelectedFileRow: View {
    @Environment(AppModel.self) private var model
    let name: String
    let remove: () -> Void
    var body: some View {
        HStack(spacing: VSTokens.spacingGap) {
            Text(name).lineLimit(1).truncationMode(.middle).frame(maxWidth: .infinity, alignment: .leading)
            Button(model.t("mobile.ios.remove_file"), systemImage: "xmark", action: remove)
                .studioButton(.tertiary)
                .accessibilityLabel(model.t("common.remove", ["term": name]))
                .accessibilityIdentifier("source.remove")
        }
    }
}

/// In-content search. The system `.searchable` bar never gets SwiftUI's keyboard
/// toolbar (no Done) and hides sheet toolbar items while active, so every search
/// in the app uses this plain field instead.
struct SearchField: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let prompt: String
    @Binding var text: String
    var body: some View {
        HStack(spacing: VSTokens.spacingGap) {
            Image(systemName: "magnifyingglass").foregroundStyle(theme.color("textMuted")).accessibilityHidden(true)
            TextField(prompt, text: $text)
                .textFieldStyle(.plain)
                .frame(minHeight: 44)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel(prompt)
                .accessibilityIdentifier("search.field")
            if !text.isEmpty {
                Button { text = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .labelStyle(.iconOnly).foregroundStyle(theme.color("textMuted"))
                    .frame(minWidth: 44, minHeight: 44).accessibilityLabel(model.t("common.clear"))
            }
        }
        .padding(.horizontal, VSTokens.spacingGap).frame(minHeight: 44)
        .background(theme.color("input"), in: RoundedRectangle(cornerRadius: VSTokens.iosFieldRadius))
    }
}

/// Destructive confirmations use the same token surfaces/shapes as every other sheet.
struct DeleteConfirmation: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.dismiss) private var dismiss
    let title: String
    let onDelete: () -> Void
    var body: some View {
        NavigationStack {
            Page(backgroundRole: "surfaceOverlay") {
                Text(title)
                Button(model.t("common.delete"), role: .destructive) { onDelete(); dismiss() }
                    .studioButton(.secondary)
            }.navigationTitle(model.t("common.delete")).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.cancel")) { dismiss() } } }
        }.presentationDragIndicator(.visible).presentationDetents([.medium, .large])
    }
}

struct InfoButton: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let title: String
    let detail: String
    var row = false
    var icon = "info.circle"
    @State private var showing = false
    var body: some View {
        Button { showing = true } label: {
            if row { Label(title, systemImage: icon) }
            else { Image(systemName: icon).accessibilityLabel(title) }
        }.labelStyle(.titleAndIcon)
            .studioButton(.tertiary)
            .sheet(isPresented: $showing) {
                NavigationStack {
                    Page(backgroundRole: "surfaceOverlay") { Text(detail).textSelection(.enabled) }
                        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.t("common.close")) { showing = false } } }
                }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
            }
    }
}
struct EmptyState: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let key: String
    var icon = "waveform"
    var body: some View {
        Label(model.t(key), systemImage: icon).foregroundStyle(theme.color("textMuted"))
            .frame(maxWidth: .infinity, minHeight: 44)
    }
}
