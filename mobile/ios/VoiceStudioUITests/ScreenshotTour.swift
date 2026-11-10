import XCTest

/// Walks every screen against the mocked host and keeps a screenshot of each, so
/// the iOS app can be reviewed visually from CI (the mobile workflow exports
/// these as the `ios-screenshots` artifact). Asserts only that each screen loads.
final class ScreenshotTour: XCTestCase {
    @MainActor func testScreenshotTour() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()

        XCTAssertTrue(app.buttons["consent.no"].waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(app.staticTexts["consent.title"].exists)
        XCTAssertTrue(app.buttons["consent.yes"].exists)
        XCTAssertFalse(app.buttons["consent.dismiss"].exists)
        shot(app, "01-consent")
        app.declineConsent()

        XCTAssertTrue(app.navigationBars["Home"].waitForExistence(timeout: uiReadyTimeout))
        shot(app, "02-home")
        app.tabBars.buttons["Settings"].tapReady()
        app.buttons["settings.connect"].tapReady()
        app.buttons["Enter address"].tapReady()
        let address = app.textFields["connect.url"]
        XCTAssertTrue(address.waitForExistence(timeout: uiReadyTimeout))
        shot(app, "03-connect")
        address.tapReady(); address.typeText("http://127.0.0.1:3900")
        shot(app, "03a-connect-keyboard")
        if app.buttons["keyboard.done"].waitForExistence(timeout: 3) { app.buttons["keyboard.done"].tapReady() }
        app.swipeUp()
        app.buttons["connect.submit"].tapReady()
        app.openSpeak()

        let editor = app.textViews["speak.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertEqual(editor.label, "Type what you want to hear…")
        shot(app, "04-speak")
        editor.tapReady(); editor.typeText("Hello from the VoiceStudio iPhone app.")
        shot(app, "05-speak-keyboard")
        if app.buttons["keyboard.done"].waitForExistence(timeout: 3) { app.buttons["keyboard.done"].tapReady() }
        let engine = app.buttons["speak.engine"]
        XCTAssertTrue(engine.waitForExistence(timeout: uiReadyTimeout))
        engine.tap()
        shot(app, "06-engine-picker")
        XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: uiReadyTimeout))
        app.buttons["Close"].tapReady()
        app.swipeUp()
        let generate = app.buttons["speak.generate"]
        XCTAssertTrue(generate.waitForExistence(timeout: uiReadyTimeout))
        generate.tap()
        XCTAssertTrue(app.buttons["audio.play"].waitForExistence(timeout: uiReadyTimeout))
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
        app.buttons["settings.palette.light"].tapReady()
        shot(app, "11a-palette-picker")
        app.buttons["Close"].tapReady()
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
