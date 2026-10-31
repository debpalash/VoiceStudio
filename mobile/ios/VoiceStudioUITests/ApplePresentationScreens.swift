import XCTest

final class ApplePresentationScreens: XCTestCase {
    @MainActor func testLargeTextKeepsCreationAndLicenceActionsReachable() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)",
                               "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        app.declineConsent()
        app.openSpeak()
        let download = app.buttons["models.get"]
        XCTAssertTrue(download.waitForExistence(timeout: 5))
        XCTAssertTrue(download.isHittable)
        XCTAssertGreaterThanOrEqual(download.frame.height, 44)
        XCTAssertTrue(download.isEnabled)
        app.tabButton("Settings").tap()
        let models = app.buttons["Models"]
        for _ in 0..<8 { if models.isHittable { break }; app.swipeUp() }
        models.tap()
        app.buttons["model.licence.kitten-mini-en-fp16"].tap()
        let accept = app.buttons["models.accept"]
        XCTAssertTrue(accept.waitForExistence(timeout: 5))
        XCTAssertTrue(accept.isHittable)
        XCTAssertGreaterThanOrEqual(accept.frame.height, 44)
        XCTAssertFalse(accept.isEnabled)
        XCTAssertTrue(app.buttons["Close"].isHittable)
        let image = XCTAttachment(screenshot: app.screenshot())
        image.name = "Licence-accessibility-text"; image.lifetime = .keepAlways; add(image)
        app.buttons["Close"].tap()
        XCTAssertTrue(app.navigationBars["Models"].waitForExistence(timeout: 5))
    }

    @MainActor func testCancellingLanguageSelectionDiscardsDraft() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent()
        app.tabButton("Dub").tap()
        app.buttons["dub.languages"].tap()
        let french = app.buttons["French"]
        if !french.isHittable {
            let search = app.textFields["search.field"]
            search.tap(); search.typeText("French")
            app.buttons["keyboard.done"].tap()
        }
        french.tap()
        XCTAssertEqual(french.value as? String, "Selected")
        app.buttons["Cancel"].tap()
        app.buttons["dub.languages"].tap()
        let reopenedSearch = app.textFields["search.field"]
        reopenedSearch.tap(); reopenedSearch.typeText("French")
        app.buttons["keyboard.done"].tap()
        XCTAssertEqual(app.buttons["French"].value as? String, "Not selected")
        app.buttons["Cancel"].tap()
    }
}
