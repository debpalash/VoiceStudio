import UIKit
import VoiceStudioKit

@MainActor enum NativeTheme {
    static func apply(_ theme: Theme) {
        func color(_ role: String) -> UIColor {
            let c = theme[role]
            return UIColor(red: c.r, green: c.g, blue: c.b, alpha: c.a)
        }
        let navigation = UINavigationBarAppearance()
        navigation.configureWithDefaultBackground()
        let font = UIFont.preferredFont(forTextStyle: .headline)
        navigation.titleTextAttributes = [.foregroundColor: color("toolbarForeground"), .font: font]
        navigation.largeTitleTextAttributes = [.foregroundColor: color("toolbarForeground"), .font: UIFont.preferredFont(forTextStyle: .largeTitle)]
        UINavigationBar.appearance().standardAppearance = navigation
        let edge = UINavigationBarAppearance()
        edge.configureWithTransparentBackground()
        edge.titleTextAttributes = navigation.titleTextAttributes
        edge.largeTitleTextAttributes = navigation.largeTitleTextAttributes
        UINavigationBar.appearance().scrollEdgeAppearance = edge
        UINavigationBar.appearance().compactAppearance = navigation
        UINavigationBar.appearance().tintColor = color("messageAction")
        let tab = UITabBarAppearance()
        tab.configureWithDefaultBackground()
        let tabFont = UIFont.preferredFont(forTextStyle: .caption1)
        for item in [tab.stackedLayoutAppearance, tab.inlineLayoutAppearance, tab.compactInlineLayoutAppearance] {
            item.normal.iconColor = color("toolbarForeground")
            item.normal.titleTextAttributes = [.foregroundColor: color("toolbarForeground"), .font: tabFont]
            item.selected.iconColor = color("messageAction")
            item.selected.titleTextAttributes = [.foregroundColor: color("messageAction"), .font: tabFont]
        }
        UITabBar.appearance().standardAppearance = tab
        UITabBar.appearance().scrollEdgeAppearance = tab
    }
}
