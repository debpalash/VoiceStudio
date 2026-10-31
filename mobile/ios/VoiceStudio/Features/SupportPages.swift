import SwiftUI
import VoiceStudioKit

/// Bundled content only. Opening a page never contacts a sponsor or payment provider.
private struct SupportCatalog: Decodable, Sendable {
    struct Donation: Decodable, Identifiable, Sendable {
        let id: String
        let name: String
        let url: URL
        let detailKey: String
    }
    struct Project: Decodable, Identifiable, Sendable {
        let id: String
        let name: String
        let url: URL
        let detailKey: String
        let tierKey: String
        let artwork: String
    }
    let donations: [Donation]
    let projects: [Project]
    static let bundled: Self = {
        guard let url = Bundle.main.url(forResource: "support", withExtension: "json", subdirectory: "Catalogs"),
              let data = try? Data(contentsOf: url),
              let catalog = try? JSONDecoder().decode(Self.self, from: data) else {
            return Self(donations: [], projects: [])
        }
        return catalog
    }()
}

struct DonateView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        SupportPage(titleKey: "mobile.support.donate", icon: "heart", headingKey: "donate.hero_title", detailKey: "donate.hero_desc") {
            Text(model.t("mobile.support.gift_note")).foregroundStyle(theme.color("textMuted"))
            ForEach(SupportCatalog.bundled.donations) { donation in
                SupportExternalCard(name: donation.name, detailKey: donation.detailKey, url: donation.url, icon: "heart")
                    .accessibilityIdentifier("support.donate." + donation.id)
            }
            Card {
                Text(model.t("support.other_ways")).font(.headline)
                Link(model.t("support.star_github"), destination: HomeContributors.repository)
                    .frame(minHeight: 44)
                Link(model.t("support.join_discord"), destination: URL(string: "https://discord.gg/bzQavDfVV9")!)
                    .frame(minHeight: 44)
            }
        }.accessibilityIdentifier("support.donate")
    }
}

struct ProView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    private let benefits = [
        ("automation", "square.stack.3d.up"), ("history", "clock.arrow.circlepath"),
        ("delivery", "shippingbox"), ("preflight", "checkmark.shield")
    ]
    var body: some View {
        SupportPage(titleKey: "proPage.title", icon: "sparkles", headingKey: "proPage.title", detailKey: "mobile.support.pro_scope") {
            Card {
                Label(model.t("proPage.free_forever"), systemImage: "checkmark.seal").font(.headline)
                Text(model.t("proPage.free_body"))
                Text(model.t("proPage.free_note")).foregroundStyle(theme.color("textMuted"))
            }
            Text(model.t("proPage.features_title")).font(.title3.weight(.semibold)).accessibilityAddTraits(.isHeader)
            ForEach(benefits, id: \.0) { benefit in
                Card {
                    Label(model.t("proPage." + benefit.0 + "_title"), systemImage: benefit.1).font(.headline)
                    Text(model.t("proPage." + benefit.0 + "_body")).foregroundStyle(theme.color("textMuted"))
                }
            }
            Text(model.t("proPage.commercial_body")).font(.footnote).foregroundStyle(theme.color("textMuted"))
        }.accessibilityIdentifier("support.pro")
    }
}

struct OtherAppsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        SupportPage(titleKey: "mobile.support.other_apps", icon: "square.grid.2x2", headingKey: "mobile.support.other_apps", detailKey: "mobile.support.other_apps_intro") {
            ForEach(SupportCatalog.bundled.projects) { project in
                Link(destination: project.url) {
                    Card {
                        HStack(alignment: .top, spacing: 16) {
                            Image("support-" + project.id).resizable().scaledToFit().frame(width: 48, height: 48).accessibilityHidden(true)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(project.name).font(.headline).foregroundStyle(theme.color("text"))
                                Text(model.t(project.tierKey)).font(.caption).foregroundStyle(theme.color("messageAction"))
                            }.frame(maxWidth: .infinity, alignment: .leading)
                            Image(systemName: "arrow.up.right").foregroundStyle(theme.color("textMuted")).accessibilityHidden(true)
                        }
                        Text(model.t(project.detailKey)).foregroundStyle(theme.color("textMuted"))
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityIdentifier("support.project." + project.id)
            }
        }.accessibilityIdentifier("support.other-apps")
    }
}

private struct SupportPage<Content: View>: View {
    let titleKey: String
    let icon: String
    let headingKey: String
    let detailKey: String
    @ViewBuilder let content: () -> Content
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: icon).font(.largeTitle).foregroundStyle(theme.color("messageAction")).accessibilityHidden(true)
                    Text(model.t(headingKey)).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
                    Text(model.t(detailKey)).foregroundStyle(theme.color("textMuted"))
                }.frame(maxWidth: .infinity, alignment: .leading).padding(24)
                    .background(theme.color("messageSurface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius))
                content()
            }.fixedSize(horizontal: false, vertical: true).padding(16).frame(maxWidth: 600).frame(maxWidth: .infinity)
        }.foregroundStyle(theme.color("text")).background(theme.color("canvas").ignoresSafeArea())
            .navigationTitle(model.t(titleKey)).navigationBarTitleDisplayMode(.inline)
    }
}

private struct SupportExternalCard: View {
    let name: String
    let detailKey: String
    let url: URL
    let icon: String
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    var body: some View {
        Link(destination: url) {
            Card {
                HStack(spacing: 12) {
                    Image(systemName: icon).foregroundStyle(theme.color("messageAction")).accessibilityHidden(true)
                    Text(name).font(.headline).foregroundStyle(theme.color("text"))
                    Spacer()
                    Image(systemName: "arrow.up.right").foregroundStyle(theme.color("textMuted")).accessibilityHidden(true)
                }
                Text(model.t(detailKey)).foregroundStyle(theme.color("textMuted"))
            }.frame(minHeight: 44).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
}
