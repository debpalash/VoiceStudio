import XCTest

final class SupportPages: XCTestCase {
    @MainActor func testSupportPagesWorkWithoutPairingOrDownloads() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)",
                               "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch(); app.declineConsent(); app.tabButton("Settings").tap()
        for route in ["donate", "pro", "other-apps"] {
            let entry = app.buttons["settings." + route]
            app.reveal(entry); XCTAssertTrue(entry.isHittable); entry.tap()
            if route == "donate" {
                for id in ["github", "kofi", "paypal"] {
                    let link = app.descendants(matching: .any).matching(identifier: "support.donate." + id).firstMatch
                    app.reveal(link); XCTAssertTrue(link.isHittable)
                    XCTAssertGreaterThanOrEqual(link.frame.height, 44)
                }
            } else if route == "pro" {
                let scope = app.staticTexts["Pro adds production tools to VoiceStudio on your computer. The phone app’s creative tools remain free."]
                XCTAssertTrue(scope.exists)
                XCTAssertFalse(app.buttons["Buy Pro"].exists)
                XCTAssertFalse(app.textFields["Licence key"].exists)
            } else {
                for id in ["opengtm", "opal", "supermetry", "spicrawl"] {
                    let link = app.descendants(matching: .any).matching(identifier: "support.project." + id).firstMatch
                    app.reveal(link); XCTAssertTrue(link.isHittable)
                }
            }
            let image = XCTAttachment(screenshot: app.screenshot())
            image.name = "Support-" + route; image.lifetime = .keepAlways; add(image)
            XCTAssertFalse(app.buttons["models.accept"].exists)
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
    }
}
