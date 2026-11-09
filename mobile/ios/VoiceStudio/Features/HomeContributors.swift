import SwiftUI
import VoiceStudioKit

/// Human GitHub contributors, mirroring
/// `electron/src/renderer/src/features/home/home-contributors-data.ts`.
/// Avatars and profile metadata are bundled: nothing is fetched at runtime.
enum HomeContributors {
    static let logins = ["debpalash", "velixio", "kevin9327", "jaketame", "psiberfunk", "rudycelekli", "paoloantinori", "Chang-Jin-Lee", "swadhinbiswas", "BichengWang", "nirmalchatur", "Shivendra-Coherent", "marreiradigital", "nvtoan0201-swe"]
    static let coreMaintainers: Set<String> = ["debpalash", "velixio"]
    static let repository = URL(string: "https://github.com/debpalash/VoiceStudio")!
    static func profile(_ login: String) -> URL { URL(string: "https://github.com/" + login)! }
    static let cards: [String: MaintainerCard] = {
        guard let url = Bundle.main.url(forResource: "maintainer-cards", withExtension: "json", subdirectory: "Catalogs"),
              let data = try? Data(contentsOf: url),
              let cards = try? JSONDecoder().decode([String: MaintainerCard].self, from: data) else { return [:] }
        return cards
    }()

}

struct MaintainerCard: Decodable {
    let name: String
    let role: String
    let links: [ProfileLink]

    struct ProfileLink: Decodable, Identifiable {
        enum Kind: String, Decodable { case website, github, x }
        let kind: Kind
        let handle: String
        let href: URL
        var id: String { kind.rawValue }
    }
}

private struct SelectedMaintainer: Identifiable {
    let id: String
    let card: MaintainerCard
}

struct ContributorCluster: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false
    @State private var selected: SelectedMaintainer?

    var body: some View {
        let maintainers = HomeContributors.logins.filter { HomeContributors.coreMaintainers.contains($0) }
        let others = HomeContributors.logins.filter { !HomeContributors.coreMaintainers.contains($0) } + ["all"]
        VStack(alignment: .trailing, spacing: 4) {
            Text(model.t("mobile.home.brought_by")).font(.caption)
                .foregroundStyle(theme.color("textMuted"))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("home.contributors.credit")
            HStack(spacing: 8) {
                ForEach(maintainers, id: \.self) { login in
                    Button {
                        if let card = HomeContributors.cards[login] { selected = SelectedMaintainer(id: login, card: card) }
                    } label: { portrait(login, size: 44).frame(width: 44, height: 44).contentShape(Rectangle()) }
                    .buttonStyle(.plain)
                    .accessibilityLabel("@" + login)
                    .accessibilityHint(model.t("homeUi.coreMaintainer"))
                    .accessibilityIdentifier("home.contributor." + login)
                }
            }
            ScrollView(.horizontal) {
                LazyHGrid(rows: [GridItem(.fixed(44), spacing: 4), GridItem(.fixed(44), spacing: 4)], spacing: 4) {
                    ForEach(others, id: \.self) { login in contributorLink(login) }
                }
            }
            .scrollIndicators(.hidden).frame(width: 112, height: 92)
            .accessibilityIdentifier("home.contributors.others")
        }
        .frame(width: 112)
        .scaleEffect(shown || reduceMotion ? 1 : 0.95).opacity(shown || reduceMotion ? 1 : 0)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: shown)
        .accessibilityElement(children: .contain).accessibilityLabel(model.t("homeUi.allContributors"))
        .onAppear { shown = true }
        .sheet(item: $selected) { item in MaintainerProfile(login: item.id, card: item.card) }
    }

    private func contributorLink(_ login: String) -> some View {
        Link(destination: login == "all" ? HomeContributors.repository.appendingPathComponent("graphs/contributors") : HomeContributors.profile(login)) {
            portrait(login, size: 22).frame(width: 44, height: 44).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(login == "all" ? model.t("homeUi.allContributors") : "GitHub · @" + login)
        .accessibilityIdentifier("home.contributor." + login)
    }

    private func portrait(_ login: String, size: CGFloat) -> some View {
        Group {
            if login == "all" {
                Text(model.t("homeUi.all")).font(.caption2.weight(.semibold)).foregroundStyle(theme.color("text"))
                    .minimumScaleFactor(0.7).lineLimit(1).frame(width: size, height: size).background(theme.color("surfaceRaised"), in: Circle())
            } else {
                Image("contributor-" + login).resizable().scaledToFill().frame(width: size, height: size).clipShape(Circle())
            }
        }
        .overlay(Circle().strokeBorder(theme.color("border")))
        .overlay(alignment: .bottomTrailing) {
            if HomeContributors.coreMaintainers.contains(login) {
                Image(systemName: "star.fill").font(.system(size: 8, weight: .semibold))
                    .foregroundStyle(theme.color("messageActionForeground"))
                    .frame(width: 16, height: 16)
                    .background(theme.color("messageAction"), in: Circle())
                    .overlay(Circle().strokeBorder(theme.color("canvas"), lineWidth: 1.5))
            }
        }
        .accessibilityHidden(true)
    }
}

private struct MaintainerProfile: View {
    let login: String
    let card: MaintainerCard
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 12) {
                        Image("contributor-" + login).resizable().scaledToFill()
                            .frame(width: 80, height: 80).clipShape(Circle())
                            .overlay(Circle().strokeBorder(theme.color("border"))).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.t(card.name)).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
                            Text("@" + login).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                            Label(model.t(card.role), systemImage: "star.fill").font(.subheadline)
                                .foregroundStyle(theme.color("messageAction"))
                        }
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(24).frame(maxWidth: .infinity, alignment: .leading)
                    .background(theme.color("messageSurface"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius))
                    VStack(spacing: 8) {
                        ForEach(card.links) { link in profileLink(link) }
                    }
                }
                .padding(24).frame(maxWidth: 560).frame(maxWidth: .infinity)
            }
            .background(theme.color("canvas"))
            .foregroundStyle(theme.color("text"))
            .navigationTitle("@" + login).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(model.t("common.close")) { dismiss() }
                        .frame(minWidth: 44, minHeight: 44).accessibilityIdentifier("home.maintainer.close")
                }
            }
        }
        .tint(theme.color("messageAction"))
        .presentationDetents(typeSize.isAccessibilitySize ? [.large] : [.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func profileLink(_ link: MaintainerCard.ProfileLink) -> some View {
        let label = link.kind == .website ? model.t("homeUi.website") : link.kind == .github ? "GitHub" : "X"
        return Link(destination: link.href) {
            HStack(alignment: .center, spacing: 12) {
                Group {
                    switch link.kind {
                    case .website: Image(systemName: "globe").resizable().scaledToFit()
                    case .github: Image("ContributorGitHub").renderingMode(.template).resizable().scaledToFit()
                    case .x: Image("ContributorX").renderingMode(.template).resizable().scaledToFit()
                    }
                }
                .frame(width: 24, height: 24).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(label).font(.body.weight(.semibold))
                    Text(link.handle).font(.subheadline).foregroundStyle(theme.color("textMuted"))
                    if link.kind != .website { Text(model.t("homeUi.follow")).font(.caption).foregroundStyle(theme.color("messageAction")) }
                }
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "arrow.up.right").font(.body).accessibilityHidden(true)
            }
            .padding(16).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background(theme.color("surfaceRaised"), in: RoundedRectangle(cornerRadius: VSTokens.iosCardRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label + " " + link.handle)
        .accessibilityIdentifier("home.maintainer." + login + "." + link.kind.rawValue)
    }
}
