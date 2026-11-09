import SwiftUI
import VoiceStudioKit

/// Vector dub mark: the frame draws itself once, then the waveform breathes.
/// Static under Reduce Motion; `active` speeds it up while a job is running.
struct DubGlyph: View {
    @Environment(\.vsTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var size: CGFloat = 72
    var active = false
    @State private var drawn = false
    private static let base: [Double] = [0.35, 0.62, 1, 0.62, 0.35]
    var body: some View {
        let accent = theme.color("messageAction")
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.3, style: .continuous).fill(accent.opacity(0.10))
            RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
                .trim(from: 0, to: drawn ? 1 : 0)
                .stroke(accent.opacity(0.85), style: StrokeStyle(lineWidth: max(1.5, size * 0.035), lineCap: .round))
            TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: reduceMotion)) { timeline in
                Canvas { context, canvas in
                    let t = reduceMotion ? 0 : timeline.date.timeIntervalSinceReferenceDate
                    let speed = active ? 6.0 : 2.4
                    let barWidth = canvas.width / 9
                    for index in 0..<Self.base.count {
                        let wave = reduceMotion ? 1.0 : 0.72 + 0.28 * sin(t * speed + Double(index) * 0.9)
                        let height = canvas.height * Self.base[index] * wave
                        let rect = CGRect(x: barWidth * CGFloat(index * 2), y: (canvas.height - height) / 2, width: barWidth, height: height)
                        context.fill(Path(roundedRect: rect, cornerRadius: barWidth / 2), with: .color(accent))
                    }
                }
            }.frame(width: size * 0.46, height: size * 0.4)
        }
        .frame(width: size, height: size)
        .onAppear { if reduceMotion { drawn = true } else { withAnimation(.easeOut(duration: 0.9)) { drawn = true } } }
        .accessibilityHidden(true)
    }
}

/// The one big call to action on Dub: pick the media to dub.
struct DubSourceButton: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack(spacing: VSTokens.spacingGap) {
                DubGlyph(size: 76)
                Text(model.t("mobile.dub.pick_media")).font(.headline)
            }
            .frame(maxWidth: .infinity, minHeight: 168).padding(Theme.compactInset)
            .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous)
                .strokeBorder(theme.color("messageAction").opacity(0.35), style: StrokeStyle(lineWidth: 1.5, dash: [7, 7])))
            .contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("dub.pick")
    }
}

/// The chosen file, in the same card slot as the picker.
struct DubFileCard: View {
    @Environment(\.vsTheme) private var theme
    let name: String
    let remove: () -> Void
    var body: some View {
        HStack(spacing: VSTokens.spacingGap) {
            Image(systemName: "film.fill").font(.title3).foregroundStyle(theme.color("messageAction"))
                .frame(width: 44, height: 44).background(theme.color("messageSurface"), in: Circle())
                .symbolEffect(.bounce, value: name).accessibilityHidden(true)
            SelectedFileRow(name: name, remove: remove)
        }
        .padding(Theme.compactInset)
        .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
    }
}

/// A setting as a tile: icon, name and current value. Used inside `DubTiles`.
struct TileLabel: View {
    @Environment(\.vsTheme) private var theme
    let icon: String
    let label: String
    let value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icon).font(.title3.weight(.medium)).foregroundStyle(theme.color("messageAction"))
                .frame(width: 40, height: 40).background(theme.color("messageSurface"), in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(.caption).foregroundStyle(theme.color("textMuted"))
                Text(value.isEmpty ? "–" : value).font(.subheadline.weight(.semibold)).lineLimit(2).multilineTextAlignment(.leading)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading).padding(14)
        .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore).accessibilityLabel(label).accessibilityValue(value)
    }
}

/// Two tiles side by side; stacked at accessibility text sizes.
struct DubTiles<Content: View>: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: VSTokens.spacingGap)) : AnyLayout(HStackLayout(spacing: VSTokens.spacingGap))
        layout { content }
    }
}

struct DubConsentRow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Binding var isOn: Bool
    var body: some View {
        HStack(spacing: VSTokens.spacingGap) {
            Image(systemName: isOn ? "checkmark.shield.fill" : "shield").font(.title3)
                .foregroundStyle(theme.color(isOn ? "messageAction" : "iconMuted"))
                .contentTransition(.symbolEffect(.replace)).symbolEffect(.bounce, value: isOn).accessibilityHidden(true)
            Toggle(model.t("mobile.simple.voice_consent"), isOn: $isOn).font(.subheadline).frame(minHeight: 44)
            InfoButton(title: model.t("mobile.voices.consent_title"), detail: model.t("mobile.voices.consent_body"))
        }
        .padding(.horizontal, Theme.compactInset)
        .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
    }
}

/// A running or paused job: live glyph, stage, percent and a thin progress bar.
struct DubJobCard<Actions: View>: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    let title: String
    let stage: String
    let fraction: Double
    var running = true
    let actions: Actions
    init(title: String, stage: String, fraction: Double, running: Bool = true, @ViewBuilder actions: () -> Actions) {
        self.title = title; self.stage = stage; self.fraction = fraction; self.running = running; self.actions = actions()
    }
    var body: some View {
        VStack(alignment: .leading, spacing: VSTokens.spacingGap) {
            HStack(spacing: VSTokens.spacingGap) {
                DubGlyph(size: 48, active: running)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline.weight(.semibold)).lineLimit(1).truncationMode(.middle)
                    Text(stage).font(.caption).foregroundStyle(theme.color("textMuted")).lineLimit(1)
                }.frame(maxWidth: .infinity, alignment: .leading)
                Text(model.t("mobile.simple.percent", ["percent": String(Int((fraction * 100).rounded()))]))
                    .font(.subheadline.monospacedDigit()).foregroundStyle(theme.color("textMuted"))
                    .contentTransition(.numericText()).animation(.default, value: fraction)
            }
            ProgressView(value: fraction).tint(theme.color("messageAction")).accessibilityLabel(model.t("mobile.ios.progress"))
            HStack(spacing: VSTokens.spacingGap) { actions }.labelStyle(.titleAndIcon).studioButton(.tertiary)
        }
        .padding(Theme.compactInset)
        .background(theme.color("surface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius, style: .continuous))
    }
}

struct DubStartButton: View {
    @Environment(AppModel.self) private var model
    var busy = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Label { Text(model.t("mobile.dub.start")).font(.headline) } icon: {
                Image(systemName: "waveform").symbolEffect(.variableColor.iterative.reversing, isActive: busy)
            }.frame(maxWidth: .infinity)
        }.studioButton(.primary).accessibilityIdentifier("dub.start")
            .padding(.horizontal, Theme.compactInset).padding(.vertical, VSTokens.spacingGap / 2)
    }
}
