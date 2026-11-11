import XCTest

/// Slow CI simulators can take far longer than a developer machine to render a screen,
/// and while it renders the accessibility hierarchy is empty. Every interaction therefore
/// goes through these waits instead of a bare `.tap()`, and a miss names what was missing.
let uiReadyTimeout: TimeInterval = 45

extension XCUIElement {
    /// Waits for the element to exist, then taps it. XCTest scrolls an element into view
    /// only once it is in the accessibility hierarchy; a row of a lazy stack, grid or list
    /// that has not been scrolled to is not, so reach those with `reveal` instead.
    @MainActor func tapReady(timeout: TimeInterval = uiReadyTimeout, file: StaticString = #filePath, line: UInt = #line) {
        guard waitReady(timeout: timeout, file: file, line: line) else { return }
        tap()
    }
    /// Waits for the element to exist; reports which element and which screen when it never does.
    /// Lazy content that is not on screen never appears here: use `reveal` for it.
    @MainActor @discardableResult func waitReady(timeout: TimeInterval = uiReadyTimeout, file: StaticString = #filePath, line: UInt = #line) -> Bool {
        if waitForExistence(timeout: timeout) { return true }
        let app = XCUIApplication()
        let bar = app.navigationBars.firstMatch
        let screen = bar.exists ? bar.description : "(no navigation bar)"
        // `description` names the query (e.g. `"home.gallery" Button`) without resolving it.
        // `identifier` or `label` would resolve the missing element, and that failure
        // ("Failed to get matching snapshot") would replace this message.
        XCTFail("Element \(description) did not appear within \(Int(timeout))s on screen \(screen)", file: file, line: line)
        return false
    }
}

extension XCUIApplication {
    /// Home remains in the hierarchy while the first-run sheet covers it.
    @MainActor func declineConsent(timeout: TimeInterval = uiReadyTimeout, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(wait(for: .runningForeground, timeout: timeout), "App did not reach the foreground within \(Int(timeout))s", file: file, line: line)
        let no = buttons["consent.no"]
        XCTAssertTrue(no.waitForExistence(timeout: timeout), "Consent sheet (consent.no) did not appear within \(Int(timeout))s", file: file, line: line)
        XCTAssertTrue(no.wait(for: \.isHittable, toEqual: true, timeout: timeout), file: file, line: line)
        no.tap()
        XCTAssertTrue(no.waitForNonExistence(timeout: 10), file: file, line: line)
        XCTAssertTrue(navigationBars["Home"].waitForExistence(timeout: timeout), "Home did not render after consent within \(Int(timeout))s", file: file, line: line)
    }
    /// Native TabView can expose either a bottom tab bar or an adaptive side rail.
    @MainActor func tabButton(_ label: String) -> XCUIElement {
        let tab = tabBars.buttons[label]
        // The bar is empty until the screen has rendered; give it a moment before probing the other layouts.
        if tab.exists || tab.waitForExistence(timeout: 5) { return tab }
        // iOS 26+ collapses the bar after scrolling. Its selected tab expands it.
        let selected = tabBars.buttons.firstMatch
        if selected.exists, (selected.value as? String)?.contains("Collapsed") == true {
            selected.tap()
            if tab.waitForExistence(timeout: 3) { return tab }
        }
        let symbols = ["Home": "house", "Voices": "person.wave.2", "Dub": "film", "Dictate": "mic", "Settings": "gearshape"]
        return buttons.matching(NSPredicate(format: "label == %@ AND identifier == %@", label, symbols[label] ?? label)).firstMatch
    }
    /// Scrolls until `element` sits wholly between the navigation bar and the tab bar.
    /// Content scrolled under those translucent bars can still report `isHittable`,
    /// and a tap there lands on the bar instead of the element. Once the element is on
    /// screen it is nudged toward the visible band with slow, measured drags in whichever
    /// direction it lies: a full-screen flick can carry a lazily-loaded card past the
    /// band and out of the hierarchy, where it can no longer be found.
    @MainActor func reveal(_ element: XCUIElement, swipingDown: Bool = false, attempts: Int = 8) {
        for _ in 0..<attempts {
            let bar = navigationBars.firstMatch, tab = tabBars.firstMatch
            let top = bar.exists ? bar.frame.maxY : frame.minY
            let bottom = tab.exists && tab.frame.height < frame.height / 3 ? tab.frame.minY : frame.maxY
            guard element.exists, !element.frame.isEmpty else {
                if swipingDown { swipeDown() } else { swipeUp() }
                continue
            }
            let target = element.frame
            if target.minY >= top - 0.5 && target.maxY <= bottom + 0.5 { return }
            let band = bottom - top
            // Positive moves content up (the element lies below the band), negative down.
            let needed = target.maxY > bottom + 0.5 && target.height <= band ? target.maxY - bottom + 16 : target.minY - top - 16
            let distance = max(-band * 0.6, min(band * 0.6, needed))
            let origin = coordinate(withNormalizedOffset: .zero)
            let start = origin.withOffset(CGVector(dx: frame.midX, dy: top + band / 2 + distance / 2))
            let end = origin.withOffset(CGVector(dx: frame.midX, dy: top + band / 2 - distance / 2))
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.3)
        }
    }
    @MainActor func openSpeak() {
        tabButton("Home").tap()
        if textViews["speak.text"].exists { return }
        for _ in 0..<5 {
            if buttons["home.speak"].exists { break }
            if buttons["BackButton"].exists { buttons["BackButton"].tap() }
            else { navigationBars.buttons.element(boundBy: 0).tap() }
        }
        for _ in 0..<8 { if buttons["home.speak"].isHittable { break }; swipeUp() }
        buttons["home.speak"].tap()
    }

}

final class SmokeTests: XCTestCase {
    @MainActor func testCloneRemembersItsRunLocationAfterDismissal() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); connect(app)
        app.tabButton("Voices").tapReady(); app.buttons["voices.create"].tapReady()
        let phone = app.segmentedControls.buttons["This phone"]
        XCTAssertTrue(phone.waitForExistence(timeout: uiReadyTimeout)); phone.tap()
        app.buttons["Cancel"].tapReady()
        app.buttons["voices.create"].tapReady()
        XCTAssertTrue(phone.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(phone.isSelected)
        app.buttons["Cancel"].tapReady()
    }

    @MainActor func testDestructivePhoneHistoryAction() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "--ui-phone-history", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent()
        app.openSpeak()
        let saved = app.staticTexts["Saved audio for deletion review"]
        XCTAssertTrue(saved.waitForExistence(timeout: uiReadyTimeout))
        let delete = app.buttons["Delete"]
        let primaryAction = app.buttons["speak.generate"].exists ? app.buttons["speak.generate"] : app.buttons["models.get"]
        for _ in 0..<8 {
            if delete.isHittable && delete.frame.maxY < primaryAction.frame.minY { break }
            app.swipeUp()
        }
        XCTAssertTrue(delete.isHittable)
        XCTAssertGreaterThanOrEqual(delete.frame.height, 44)
        let image = XCTAttachment(screenshot: app.screenshot())
        image.name = "Destructive-history-action"; image.lifetime = .keepAlways; add(image)
        delete.tap()
        XCTAssertTrue(saved.wait(for: \.exists, toEqual: false, timeout: 5))
    }

    @MainActor func testUnpairedTabsAndModelDownloadPrompt() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent()
        app.openSpeak()
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(app.buttons["models.open"].exists)
        for tab in ["Voices", "Dub", "Dictate", "Settings"] {
            app.tabButton(tab).tap()
            XCTAssertTrue(app.navigationBars[tab].waitForExistence(timeout: uiReadyTimeout))
            let title = app.descendants(matching: .any)["navigation.title"].firstMatch
            XCTAssertEqual(title.label, tab)
            XCTAssertLessThan(title.frame.midX, app.frame.width / 2)
        }
        XCTAssertTrue(app.buttons["settings.connect"].exists)
    }

    @MainActor func testConsentCopyEqualChoicesAndSwipeDefaultsToNo() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        let no = app.buttons["consent.no"]
        XCTAssertTrue(no.waitForExistence(timeout: uiReadyTimeout))
        // SwiftUI sheets aren't XCUIElement.sheets; identify the two choices directly.
        XCTAssertTrue(app.buttons["consent.yes"].exists)
        XCTAssertEqual(app.staticTexts["consent.title"].label, "Help improve VoiceStudio?")
        let yes = app.buttons["consent.yes"]
        XCTAssertEqual(yes.label, "Yes, share anonymous stats")
        XCTAssertEqual(no.label, "No thanks")
        XCTAssertEqual(yes.frame.width, no.frame.width, accuracy: 1)
        XCTAssertFalse(app.buttons["consent.dismiss"].exists)
        XCTAssertTrue(app.staticTexts["This companion app records your choice on this phone. It sends no analytics."].exists)
        // First opt in, then prove passive dismissal replaces a prior Yes with No.
        yes.tap()
        XCTAssertTrue(app.tabBars.buttons["Settings"].waitForExistence(timeout: uiReadyTimeout))
        app.tabBars.buttons["Settings"].tapReady()
        XCTAssertEqual(app.switches["settings.analytics"].value as? String, "1")
        app.terminate(); app.launch()
        XCTAssertTrue(app.buttons["consent.no"].waitForExistence(timeout: uiReadyTimeout))
        // Drag the sheet down, as a person would. A single fast swipe is sometimes not
        // registered as a dismissal on the simulator, so drag from the sheet's top edge
        // to the bottom of the screen and repeat if the sheet is still up.
        for _ in 0..<3 where app.buttons["consent.no"].exists {
            let top = app.staticTexts["consent.title"].coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0))
            top.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.98)))
            _ = app.buttons["consent.no"].waitForNonExistence(timeout: 3)
        }
        // The phone works on its own, so dismissing the sheet lands on Home (which sits behind the sheet).
        XCTAssertTrue(app.buttons["consent.no"].waitForNonExistence(timeout: 8))
        XCTAssertTrue(app.navigationBars["Home"].waitForExistence(timeout: uiReadyTimeout))
        app.tabBars.buttons["Settings"].tapReady()
        XCTAssertEqual(app.switches["settings.analytics"].value as? String, "0")
    }

    @MainActor func testDictationHasOneControlAndPalettesHaveNoExtraActions() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        connect(app)
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: uiReadyTimeout))
        app.tabBars.buttons["Dictate"].tapReady()
        let record = app.buttons["dictate.record"]
        XCTAssertTrue(record.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertEqual(record.label, "Tap to start")
        XCTAssertFalse(app.staticTexts["Hold to talk"].exists)
        XCTAssertEqual(app.buttons.matching(identifier: "dictate.record").count, 1)
        app.tabBars.buttons["Settings"].tapReady()
        let light = app.buttons["settings.palette.light"]
        XCTAssertTrue(light.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(app.buttons["settings.palette.dark"].exists)
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "label == %@", "Light palette")).count, 1)
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "label == %@", "Dark palette")).count, 1)
        light.tap()
        app.buttons["choice.option.default"].tapReady()
        XCTAssertEqual(light.value as? String, "Studio")
        light.tap()
        app.buttons["choice.option.signal"].tapReady()
        XCTAssertEqual(light.value as? String, "VoiceStudio Original")
    }

    @MainActor func testEveryTabRendersWhileHostInventoryIsLoading() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "--ui-delayed-inventory", "-AppleLanguages", "(en)"]
        app.launch()
        connect(app)
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(app.descendants(matching: .any)["page.loading"].firstMatch.exists)
        app.tabButton("Dub").tapReady()
        XCTAssertTrue(app.textFields["dub.source_url"].waitForExistence(timeout: uiReadyTimeout))
        app.tabButton("Voices").tapReady()
        XCTAssertTrue(app.buttons["voices.create"].waitForExistence(timeout: uiReadyTimeout))
        app.tabButton("Dictate").tapReady()
        XCTAssertTrue(app.buttons["dictate.record"].waitForExistence(timeout: uiReadyTimeout))
        app.tabButton("Settings").tapReady()
        XCTAssertTrue(app.buttons["settings.language"].waitForExistence(timeout: uiReadyTimeout))
    }

    @MainActor func testSearchEmptyStatesAndCompactDubHistory() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        connect(app)
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: uiReadyTimeout))
        app.tabButton("Voices").tapReady()
        let search = app.textFields["search.field"]
        XCTAssertTrue(search.waitForExistence(timeout: uiReadyTimeout))
        search.tapReady(); search.typeText("unmatched-voice")
        XCTAssertTrue(app.staticTexts["search.empty"].exists)
        XCTAssertEqual(app.staticTexts["search.empty"].label, "No matches for \"unmatched-voice\".")
        dismissUsingDone(app)
        app.openSpeak()
        app.buttons["speak.engine"].tapReady()
        XCTAssertTrue(search.waitForExistence(timeout: uiReadyTimeout))
        search.tapReady(); search.typeText("unmatched-engine")
        XCTAssertTrue(app.staticTexts["search.empty"].exists)
        dismissUsingDone(app)
        app.buttons["Close"].tapReady()
        app.tabButton("Dub").tapReady()
        XCTAssertEqual(app.buttons["dub.languages"].value as? String, "Choose")
        app.buttons["dub.languages"].tapReady()
        XCTAssertTrue(search.waitForExistence(timeout: uiReadyTimeout))
        search.tapReady(); search.typeText("unmatched-language")
        XCTAssertTrue(app.staticTexts["search.empty"].exists)
        dismissUsingDone(app)
        app.buttons["Confirm"].tapReady()
        let download = app.buttons["dub.download.smoke-dub"]
        for _ in 0..<4 { if download.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(download.exists)
        XCTAssertTrue(app.buttons["dub.share.smoke-dub"].exists)
        XCTAssertEqual(download.label, "Download: vs-dub-test.wav")
        XCTAssertLessThanOrEqual(download.frame.width, download.frame.height * 1.5)
        XCTAssertTrue(app.staticTexts["vs-dub-test.wav"].exists)
        XCTAssertEqual(app.staticTexts["dub.metadata.smoke-dub"].label, "Spanish dub, 2:05 long")
    }

    @MainActor private func connect(_ app: XCUIApplication) {
        app.declineConsent()
        app.tabButton("Settings").tapReady()
        app.buttons["settings.connect"].tapReady()
        app.buttons["Enter address"].tapReady()
        let address = app.textFields["connect.url"]
        XCTAssertTrue(address.waitForExistence(timeout: uiReadyTimeout))
        address.tapReady(); address.typeText("http://127.0.0.1:3900")
        dismissUsingDone(app)
        app.swipeUp()
        app.buttons["connect.submit"].tapReady()
        app.openSpeak()
    }

    @MainActor func testConsentConnectGeneratePlay() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent()
        XCTAssertTrue(app.tabButton("Home").exists)
        app.tabButton("Settings").tapReady()
        app.buttons["settings.connect"].tapReady()
        app.buttons["Enter address"].tapReady()
        let address = app.textFields["connect.url"]
        XCTAssertTrue(address.waitForExistence(timeout: uiReadyTimeout))
        address.tapReady(); address.typeText("http://127.0.0.1:3900")
        dismissUsingDone(app)
        app.swipeUp()
        app.buttons["connect.submit"].tapReady()
        app.openSpeak()
        let editor = app.textViews["speak.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertEqual(editor.label, "Type what you want to hear…")
        XCTAssertTrue((editor.value as? String ?? "").isEmpty)
        XCTAssertEqual(app.alerts.count, 0)
        let engine = app.buttons["speak.engine"]
        XCTAssertTrue(engine.waitForExistence(timeout: uiReadyTimeout))
        engine.tap()
        let preset = app.buttons["choice.option.kittentts"]
        XCTAssertTrue(preset.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertTrue(preset.label.contains("KittenTTS"))
        XCTAssertFalse(app.buttons["choice.option.unavailable"].isEnabled)
        preset.tap()
        let rowHeight = engine.frame.height
        engine.tap()
        let longName = "OuteTTS (MLX Audio, Apple Silicon)"
        let longOption = app.buttons["choice.option.mlx-audio"]
        XCTAssertTrue(longOption.waitForExistence(timeout: uiReadyTimeout))
        XCTAssertEqual(longOption.label, longName)
        longOption.tap()
        XCTAssertEqual(engine.value as? String, longName)
        XCTAssertEqual(engine.frame.height, rowHeight, accuracy: 1)
        app.buttons["speak.language"].tapReady()
        let search = app.textFields["search.field"]
        XCTAssertTrue(search.waitForExistence(timeout: uiReadyTimeout))
        search.tapReady(); search.typeText("Spanish")
        XCTAssertTrue(app.buttons["choice.option.spanish"].exists)
        app.buttons["Close"].tapReady()
        editor.tapReady(); editor.typeText("VoiceStudio smoke test")
        XCTAssertEqual(editor.value as? String, "VoiceStudio smoke test")
        dismissUsingDone(app)
        app.swipeUp()
        app.buttons["speak.generate"].tapReady()
        let play = app.buttons["audio.play"]
        XCTAssertTrue(play.waitForExistence(timeout: uiReadyTimeout))
        // Generation inserts the player below the editor. Reveal its controls
        // above the pinned Generate action before attempting playback.
        for _ in 0..<5 {
            if play.isHittable && play.frame.maxY < app.buttons["speak.generate"].frame.minY { break }
            app.swipeUp()
        }
        XCTAssertLessThan(play.frame.maxY, app.buttons["speak.generate"].frame.minY)
        play.tap()
        XCTAssertTrue(play.wait(for: \.label, toEqual: "Pause", timeout: 3))
        let playback = XCTAttachment(screenshot: app.screenshot())
        playback.name = "Generated-audio-playback"; playback.lifetime = .keepAlways; add(playback)
        XCTAssertTrue(app.staticTexts["0:05"].exists)
    }

    @MainActor func testKeyboardDismissalAcrossFormsAndSearch() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch()
        app.declineConsent()
        app.tabButton("Settings").tapReady()
        app.buttons["settings.connect"].tapReady()
        app.buttons["Enter address"].tapReady()
        let address = app.textFields["connect.url"]
        XCTAssertTrue(address.waitForExistence(timeout: uiReadyTimeout))
        address.tapReady(); address.typeText("http://127.0.0.1:3900")
        // A field tap must retain focus; a tap on surrounding chrome dismisses it.
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: uiReadyTimeout))
        app.navigationBars.firstMatch.tap()
        assertKeyboardDismissed(app)
        address.tapReady(); address.typeText("\n")
        assertKeyboardDismissed(app)
        app.swipeUp()
        app.buttons["connect.submit"].tapReady()
        app.openSpeak()
        let editor = app.textViews["speak.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: uiReadyTimeout))
        editor.tapReady(); editor.typeText("An editable multiline message\nwith a second line")
        dismissUsingDone(app)
        app.swipeUp()
        XCTAssertTrue(app.buttons["speak.generate"].isHittable)

        app.tabButton("Voices").tapReady()
        app.buttons["voices.actions.smoke-profile"].tapReady()
        app.buttons["Rename"].tapReady()
        let rename = app.textFields["voices.rename.name"]
        XCTAssertTrue(rename.waitForExistence(timeout: uiReadyTimeout))
        rename.tap(); rename.typeText(" updated\n")
        assertKeyboardDismissed(app)
        app.buttons["Cancel"].tapReady()
        app.buttons["voices.create"].tapReady()
        let name = app.textFields["voices.clone.name"]
        XCTAssertTrue(name.waitForExistence(timeout: uiReadyTimeout))
        name.tapReady(); name.typeText("Sample voice")
        dismissUsingDone(app)
        app.buttons["Cancel"].tapReady()

        app.tabButton("Dub").tapReady()
        let source = app.textFields["dub.source_url"]
        XCTAssertTrue(source.waitForExistence(timeout: uiReadyTimeout))
        source.tap(); source.typeText("https://media.example/sample\n")
        assertKeyboardDismissed(app)

        app.tabButton("Settings").tapReady()
        let language = app.buttons["settings.language"]
        for _ in 0..<5 {
            if language.isHittable { break }
            app.swipeUp()
        }
        language.tap()
        let search = app.textFields["search.field"]
        XCTAssertTrue(search.waitForExistence(timeout: uiReadyTimeout))
        search.tapReady(); search.typeText("English")
        dismissUsingDone(app)
        XCTAssertTrue(app.buttons["choice.option.en"].isHittable)
    }

    @MainActor private func dismissUsingDone(_ app: XCUIApplication) {
        // A visible keyboard is the negative control for the dismissal assertion.
        XCTAssertTrue(app.keyboards.firstMatch.wait(for: \.isHittable, toEqual: true, timeout: keyboardWait))
        let done = app.buttons["keyboard.done"]
        XCTAssertTrue(done.wait(for: \.isHittable, toEqual: true, timeout: keyboardWait))
        done.tap()
        assertKeyboardDismissed(app)
    }

    @MainActor private func assertKeyboardDismissed(_ app: XCUIApplication) {
        // iOS may retain an accessibility keyboard container after it is visually hidden.
        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false OR hittable == false"), object: app.keyboards.firstMatch)
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: keyboardWait), .completed)
    }

    /// One keyboard query on a loaded CI simulator can take over 4 s, so a 5 s budget
    /// timed out after a single check while the keyboard was already gone. The waits
    /// return as soon as the condition holds, so a longer budget costs nothing.
    private let keyboardWait: TimeInterval = 20
}
