import XCTest

/// Walks every screen against the mocked host and keeps a screenshot of each, so
/// the iOS app can be reviewed visually from CI (the mobile workflow exports
/// these as the `ios-screenshots` artifact). Asserts only that each screen loads.
final class ScreenshotTour: XCTestCase {
    @MainActor func testScreenshotTour() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()

        XCTAssertTrue(app.buttons["consent.no"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["consent.title"].exists)
        XCTAssertTrue(app.buttons["consent.yes"].exists)
        XCTAssertFalse(app.buttons["consent.dismiss"].exists)
        shot(app, "01-consent")
        app.declineConsent()

        XCTAssertTrue(app.navigationBars["Home"].waitForExistence(timeout: 5))
        shot(app, "02-home")
        app.tabBars.buttons["Settings"].tap()
        app.buttons["settings.connect"].tap()
        app.buttons["Enter address"].tap()
        let address = app.textFields["connect.url"]
        XCTAssertTrue(address.waitForExistence(timeout: 5))
        shot(app, "03-connect")
        address.tap(); address.typeText("http://127.0.0.1:3900")
        shot(app, "03a-connect-keyboard")
        if app.buttons["keyboard.done"].waitForExistence(timeout: 3) { app.buttons["keyboard.done"].tap() }
        app.swipeUp()
        app.buttons["connect.submit"].tap()
        app.openSpeak()

        let editor = app.textViews["speak.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        XCTAssertEqual(editor.label, "Type what you want to hear…")
        shot(app, "04-speak")
        editor.tap(); editor.typeText("Hello from the VoiceStudio iPhone app.")
        shot(app, "05-speak-keyboard")
        if app.buttons["keyboard.done"].waitForExistence(timeout: 3) { app.buttons["keyboard.done"].tap() }
        let engine = app.buttons["speak.engine"]
        XCTAssertTrue(engine.waitForExistence(timeout: 5))
        engine.tap()
        shot(app, "06-engine-picker")
        XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: 3))
        app.buttons["Close"].tap()
        app.swipeUp()
        let generate = app.buttons["speak.generate"]
        XCTAssertTrue(generate.waitForExistence(timeout: 5))
        generate.tap()
        XCTAssertTrue(app.buttons["audio.play"].waitForExistence(timeout: 10))
        shot(app, "07-speak-generated")

        for (tab, name) in [("Voices", "08-voices"), ("Dub", "09-dub"), ("Dictate", "10-dictate"), ("Settings", "11-settings")] {
            app.tabBars.buttons[tab].tap()
            sleep(1)
            if tab == "Dub" {
                XCTAssertEqual(app.buttons["dub.languages"].value as? String, "Choose")
                XCTAssertEqual(app.staticTexts["dub.metadata.smoke-dub"].label, "Spanish dub, 2:05 long")
            }
            if tab == "Dictate" { XCTAssertFalse(app.staticTexts["Hold to talk"].exists) }
            shot(app, name)
        }
        app.buttons["settings.palette.light"].tap()
        shot(app, "11a-palette-picker")
        app.buttons["Close"].tap()
        app.swipeUp()
        shot(app, "12-settings-more")

    }

    @MainActor private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
