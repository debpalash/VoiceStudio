import XCTest

final class ProductionScreens: XCTestCase {
    @MainActor func testNativeProductionWorkspacesOpenOfflineWithoutDownloading() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent(timeout: 15)
        for (route,title) in [("transcriptions","Transcriptions"),("handoff","Project handoff and backup"),("design","Voice design"),("stories","Stories"),("audiobook","Audiobook"),("batch","Batch dubbing"),("gallery","Gallery"),("workflows","Workflows"),("calls","Calls"),("local-api","Phone speech API"),("tools","Tools")] {
            let card = app.buttons["home."+route]
            for _ in 0..<8 { if card.isHittable { break }; app.swipeUp() }
            XCTAssertTrue(card.isHittable,route); card.tap()
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 10),route)
            XCTAssertFalse(app.navigationBars["Connect to VoiceStudio"].exists,route)
            XCTAssertFalse(app.descendants(matching: .any)["error.notice"].exists,route)
            if route == "design" { XCTAssertTrue(app.descendants(matching: .any)["design.script"].firstMatch.exists) }
            let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = "Production-"+route; attachment.lifetime = .keepAlways; add(attachment)
            if route == "local-api" {
                app.switches["Allow transcription"].tap()
                app.switches["Allow translation"].tap()
                XCTAssertFalse(app.buttons["Start server"].isEnabled)
                XCTAssertFalse(app.descendants(matching: .any)["error.notice"].exists)
            }
            if route == "tools" {
                let editor = app.buttons["Audio editor"]
                for _ in 0..<6 { if editor.isHittable { break }; app.swipeDown() }
                XCTAssertTrue(editor.isHittable); editor.tap()
                XCTAssertTrue(app.navigationBars["Audio editor"].waitForExistence(timeout: 10))
                XCTAssertFalse(app.descendants(matching: .any)["error.notice"].exists)
                let editorScreenshot = XCTAttachment(screenshot: app.screenshot())
                editorScreenshot.name = "Production-audio-editor"; editorScreenshot.lifetime = .keepAlways; add(editorScreenshot)
            }
            for _ in 0..<3 where !app.navigationBars["Home"].exists {
                // Exercise the native back gesture; iOS 27 reports bar items as
                // both Other and Button in XCTest's legacy/modern attributes.
                let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.01,dy: 0.5))
                let destination = app.coordinate(withNormalizedOffset: CGVector(dx: 0.85,dy: 0.5))
                edge.press(forDuration: 0.05,thenDragTo: destination)
                _ = app.navigationBars["Home"].waitForExistence(timeout: 3)
            }
            XCTAssertTrue(app.navigationBars["Home"].exists)
            for _ in 0..<8 { if app.buttons["home.speak"].isHittable { break }; app.swipeDown() }
        }
    }
}
