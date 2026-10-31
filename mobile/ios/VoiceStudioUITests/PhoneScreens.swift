import XCTest

final class PhoneScreens: XCTestCase {
    @MainActor func testSpeechOffersAllEnginesAndExplainsReferenceOnlyModels() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); app.declineConsent()
        let speech = app.buttons["home.speak"]; app.reveal(speech); speech.tap()
        let picker = app.buttons["Models"]; app.reveal(picker); picker.tap()
        for name in ["Kitten", "Kokoro", "OmniVoice", "Piper", "Supertonic 3", "Pocket TTS", "ZipVoice"] {
            let option = app.buttons.matching(NSPredicate(format: "label == %@", name)).firstMatch
            app.reveal(option); XCTAssertTrue(option.exists)
        }
        // The standard ARM host has 7 GB RAM; explicit OmniVoice selection must stay available.
        let omni = app.buttons["choice.option.omnivoice-q4-k-m"]; app.reveal(omni)
        XCTAssertTrue(omni.isEnabled); omni.tap()
        XCTAssertTrue(app.staticTexts["Choose a saved voice to use this model. You can import one from Gallery."].exists)
        XCTAssertEqual(app.buttons["Voice"].value as? String, "Choose a voice")
        XCTAssertTrue(app.buttons["models.get"].isEnabled)
        if ProcessInfo.processInfo.physicalMemory < 8_000_000_000 {
            XCTAssertEqual(app.buttons["models.get"].label, "Try anyway")
            XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "RAM recommended")).firstMatch.exists)
        }
        XCTAssertFalse(app.buttons["speak.generate"].exists)
    }
    @MainActor func testGalleryBrowsesPresetsBeforeDownloadAndSearchesVoiceNames() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); app.declineConsent()
        let gallery = app.buttons["home.gallery"]
        app.reveal(gallery); gallery.tap()
        app.buttons["Gallery"].tap(); app.buttons["choice.option.phone"].tap()
        let first = app.buttons["gallery.preset.kitten-mini-en-fp16.0"]
        XCTAssertTrue(first.waitForExistence(timeout: 5))
        app.reveal(first); first.tap()
        XCTAssertTrue(app.buttons["models.get"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["speak.generate"].exists)
        app.buttons["BackButton"].tap()
        let search = app.textFields["search.field"]
        app.reveal(search, swipingDown: true); search.tap(); search.typeText("Voice 103")
        app.buttons["keyboard.done"].tap()
        let last = app.buttons["gallery.preset.kokoro-multi-int8.102"]
        XCTAssertTrue(last.waitForExistence(timeout: 5)); app.reveal(last); last.tap()
        XCTAssertEqual(app.buttons["Models"].value as? String,"Kokoro")
        XCTAssertEqual(app.buttons["Voice"].value as? String,"Voice 103")
        XCTAssertTrue(app.buttons["models.get"].isEnabled)
    }
    @MainActor func testCommunityGalleryShowsNamedVoicesAndOpensARealRecipeWithoutPairing() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); app.declineConsent()
        let gallery = app.buttons["home.gallery"]; app.reveal(gallery); gallery.tap()
        XCTAssertTrue(app.staticTexts["The Librarian"].waitForExistence(timeout: 5))
        let search = app.textFields["search.field"]
        search.tap(); search.typeText("Thalia"); app.buttons["keyboard.done"].tap()
        XCTAssertTrue(app.staticTexts["Thalia"].waitForExistence(timeout: 5))
        let use = app.buttons["Use voice"]; app.reveal(use); use.tap()
        let dialog = app.alerts.firstMatch
        XCTAssertTrue(dialog.waitForExistence(timeout: 5))
        XCTAssertTrue(dialog.buttons["I have permission"].exists)
        dialog.buttons["Cancel"].tap()
        app.reveal(search, swipingDown: true); search.tap(); search.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue,count: 6)+"The Librarian")
        app.buttons["keyboard.done"].tap()
        let recipe = app.buttons["gallery.community.use.feat_00_the_librarian"]
        app.reveal(recipe); recipe.tap()
        let script = app.descendants(matching: .any).matching(identifier: "design.script").firstMatch
        XCTAssertTrue(script.waitForExistence(timeout: 5))
        XCTAssertEqual(script.value as? String,"The valley had been quiet for a hundred years, and tonight, for the first time, something stirred beneath the old stone bridge.")
        XCTAssertEqual(app.buttons["Gender"].value as? String,"female")
    }
    @MainActor func testNavigationKeepsAllDestinationsReachableAfterScrolling() {
        let app = launch()
        app.swipeUp()
        for name in ["Home", "Voices", "Dub", "Dictate", "Settings"] {
            let tab = app.tabBars.buttons[name]
            XCTAssertTrue(tab.isHittable, name)
            XCTAssertGreaterThanOrEqual(tab.frame.height, 44)
        }
    }
    @MainActor func testFirstUseOffersSpeechSetupWithoutLosingTheDraft() {
        let app = launch()
        let editor = app.textViews["speak.text"]
        editor.tap(); editor.typeText("My first speech draft.")
        app.buttons["keyboard.done"].tap()
        let download = app.buttons["models.get"]
        XCTAssertTrue(download.isHittable)
        XCTAssertTrue(download.isEnabled)
        XCTAssertEqual(download.label, "Download")
        XCTAssertFalse(app.buttons["speak.generate"].exists)
        download.tap()
        XCTAssertTrue(app.buttons["models.accept"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["models.accept"].isEnabled)
        app.buttons["Close"].tap()
        XCTAssertEqual(editor.value as? String, "My first speech draft.")
    }
    @MainActor func testHomeCloningShortcutOpensCreationAndCancelReturnsHome() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); app.declineConsent()
        app.buttons["home.voices"].tap()
        XCTAssertTrue(app.textFields["voices.clone.name"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Create clone"].isEnabled)
        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.buttons["home.speak"].waitForExistence(timeout: 5))
    }
    @MainActor func testDictationExplainsSetupBeforeInvitingRecording() {
        let app = launch()
        app.tabButton("Dictate").tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "Download a model (")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Tap to start"].exists)
        XCTAssertFalse(app.buttons["dictate.record"].isEnabled)
        XCTAssertTrue(app.buttons["models.get"].isEnabled)
    }
    @MainActor private func launch(extra: [String] = []) -> XCUIApplication {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke","-AppleLanguages","(en)"] + extra
        app.launch(); app.declineConsent()
        app.openSpeak()
        return app
    }
    @MainActor func testRememberedComputerChoiceFallsBackWhileUnpaired() {
        let app = launch(extra: ["-run-on.speak","computer","-run-on.clone","computer","-run-on.dub","computer"])
        XCTAssertTrue(app.buttons["models.get"].exists)
        app.tabButton("Voices").tap(); app.buttons["voices.create"].tap()
        XCTAssertTrue(app.buttons["models.get"].waitForExistence(timeout: 5)); app.buttons["Cancel"].tap()
        app.tabButton("Dub").tap()
        XCTAssertTrue(app.buttons["Choose video or audio"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.textFields["dub.source_url"].exists)
    }
    @MainActor func testChangingSpeechModelResetsUnavailablePreset() {
        let app = launch()
        app.buttons["Models"].tap(); app.buttons["choice.option.kokoro-multi-int8"].tap()
        app.buttons["Voice"].tap()
        let search = app.textFields.firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 5)); search.tap(); search.typeText("Voice 103")
        app.buttons["choice.option.102"].tap()
        XCTAssertEqual(app.buttons["Voice"].value as? String,"Voice 103")
        app.buttons["Models"].tap(); app.buttons["choice.option.kitten-mini-en-fp16"].tap()
        app.buttons["Voice"].tap()
        XCTAssertEqual(app.buttons["choice.option.0"].value as? String,"Selected")
        XCTAssertFalse(app.buttons["choice.option.102"].exists)
    }
    @MainActor func testUnpairedCloneShowsModelGateAndConsent() {
        let app = launch(); app.tabButton("Voices").tap(); app.buttons["voices.create"].tap()
        XCTAssertTrue(app.textFields["voices.clone.name"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["models.get"].exists)
        XCTAssertTrue(app.switches["I have permission"].exists)
        XCTAssertFalse(app.buttons["Create clone"].isEnabled)
        XCTAssertFalse(app.staticTexts["This task currently runs on your paired computer."].exists)
    }
    @MainActor func testUnpairedDubKeepsSetupVisibleAndRequiresInput() {
        let app = launch(); app.tabButton("Dub").tap()
        XCTAssertTrue(app.buttons["Choose video or audio"].waitForExistence(timeout: 5))
        for label in ["Choose video or audio", "dub.languages", "Voice", "models.get"] {
            XCTAssertTrue(app.buttons[label].isHittable, label)
        }
        XCTAssertTrue(app.switches["I have permission"].isHittable)
        XCTAssertGreaterThanOrEqual(app.buttons["models.get"].frame.height, 44)
        XCTAssertFalse(app.buttons["dub.start"].isEnabled)
        XCTAssertLessThan(app.buttons["dub.start"].frame.height, 72)
        XCTAssertFalse(app.textFields["dub.source_url"].exists)
    }
    @MainActor func testModelsTermsRequireExplicitUncheckedConsent() {
        let app = launch(); app.tabButton("Settings").tap(); app.buttons["Models"].tap()
        let licence = app.buttons["model.licence.kitten-mini-en-fp16"]
        XCTAssertTrue(licence.waitForExistence(timeout: 5)); licence.tap()
        let accept = app.buttons["models.accept"]
        XCTAssertTrue(accept.waitForExistence(timeout: 5))
        XCTAssertTrue(accept.isHittable)
        let consent = app.switches["models.consent"]
        for _ in 0..<15 { if consent.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(accept.exists); XCTAssertFalse(accept.isEnabled)
        XCTAssertTrue(consent.exists)
        // A long native toggle label spans the row; activate the switch thumb.
        consent.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        let image = XCTAttachment(screenshot: app.screenshot()); image.name = "Licence-confirmation"; image.lifetime = .keepAlways; add(image)
        XCTAssertTrue(accept.wait(for: \.isEnabled, toEqual: true, timeout: 3))
        for _ in 0..<15 { if app.buttons["Close"].isHittable { break }; app.swipeDown() }
        app.buttons["Close"].tap()
        licence.tap()
        XCTAssertTrue(accept.waitForExistence(timeout: 5)); XCTAssertFalse(accept.isEnabled)
        app.buttons["Close"].tap()
    }
    @MainActor func testCompactDownloadsUseProductNamesAndOneDubAction() {
        let app = launch()
        XCTAssertEqual(app.buttons.matching(identifier: "models.get").count, 1)
        XCTAssertFalse(app.staticTexts["kitten-mini-en-fp16"].exists)
        XCTAssertTrue(app.buttons["models.open"].label.contains("Kitten"))
        app.buttons["models.get"].tap()
        XCTAssertTrue(app.buttons["models.accept"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["models.accept"].isEnabled)
        app.buttons["Close"].tap()
        app.tabButton("Dub").tap()
        XCTAssertEqual(app.buttons.matching(identifier: "models.get").count, 1)
        XCTAssertTrue(app.buttons["models.open"].label.contains("3 downloads"))
        XCTAssertFalse(app.staticTexts["NOASSERTION"].exists)
        XCTAssertFalse(app.navigationBars.buttons["Settings"].exists)
    }
    @MainActor func testPaletteSelectionAndDictationActionsClearTheTabBar() {
        let app = launch(); app.tabButton("Dictate").tap()
        let navigation = ["Home", "Voices", "Dub", "Dictate", "Settings"].map { app.tabButton($0) }
        for identifier in ["dictate.copy", "dictate.share"] {
            let action = app.buttons[identifier]
            XCTAssertTrue(action.exists)
            for tab in navigation {
                XCTAssertTrue(tab.exists)
                XCTAssertFalse(action.frame.intersects(tab.frame), "Dictation action overlaps navigation")
            }
        }
        app.tabButton("Settings").tap()
        app.buttons["settings.palette.light"].tap()
        let studio = app.buttons["choice.option.default"]
        XCTAssertTrue(studio.waitForExistence(timeout: 5)); XCTAssertEqual(studio.value as? String, "Selected")
        app.buttons["choice.option.canopy"].tap()
        app.buttons["settings.palette.light"].tap()
        XCTAssertEqual(app.buttons["choice.option.canopy"].value as? String, "Selected")
    }
    @MainActor func testConnectAndPrivacyKeepDetailsCollapsed() {
        let app = launch(); app.tabButton("Settings").tap()
        XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@","On-device tasks send nothing")).firstMatch.exists)
        app.buttons["settings.connect"].tap()
        XCTAssertTrue(app.buttons["Scan QR code"].exists)
        XCTAssertFalse(app.textFields["connect.url"].exists)
        app.buttons["Enter address"].tap(); XCTAssertTrue(app.textFields["connect.url"].exists)
        XCTAssertFalse(app.secureTextFields.firstMatch.exists)
        app.buttons["More"].tap(); XCTAssertTrue(app.secureTextFields.firstMatch.exists)
    }
}

/// Xcode test attachments are the reviewable screen tour; no screenshots are fabricated by CLT checks.
final class PhoneScreenshotTour: XCTestCase {
    @MainActor private func capture(_ name: String,_ app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
    @MainActor private func closeSheet(_ title: String, _ app: XCUIApplication) {
        let picker = app.navigationBars[title]
        let close = picker.buttons["Close"]
        XCTAssertTrue(close.wait(for: \.isHittable, toEqual: true, timeout: 5))
        close.tap()
        // The Settings rows remain in the hierarchy behind the native sheet.
        // Its disappearance, rather than a row's existence, marks dismissal.
        XCTAssertTrue(picker.waitForNonExistence(timeout: 5))
    }
    @MainActor private func openSettingsPage(_ row: XCUIElement, destination: XCUIElement, _ app: XCUIApplication) {
        XCTAssertTrue(app.navigationBars["Settings"].wait(for: \.isHittable, toEqual: true, timeout: 5))
        app.reveal(row)
        XCTAssertTrue(row.wait(for: \.isHittable, toEqual: true, timeout: 5))
        row.tap()
        XCTAssertTrue(destination.wait(for: \.isHittable, toEqual: true, timeout: 5))
    }
    @MainActor func testPhoneFirstScreens() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke","-AppleLanguages","(en)"]
        app.launch(); XCTAssertTrue(app.buttons["consent.no"].waitForExistence(timeout: 10)); XCTAssertTrue(app.staticTexts["This companion app records your choice on this phone. It sends no analytics."].exists); capture("Consent",app); app.declineConsent(); capture("Home",app); app.openSpeak()
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: 5)); capture("Speak-phone",app)
        app.tabButton("Voices").tap(); capture("Voices-phone",app)
        app.buttons["voices.create"].tap(); XCTAssertTrue(app.textFields["voices.clone.name"].waitForExistence(timeout: 5)); capture("Clone-phone",app); app.buttons["Cancel"].tap()
        app.tabButton("Dub").tap(); XCTAssertEqual(app.buttons.matching(identifier: "models.get").count, 1); capture("Dub-phone",app)
        app.tabButton("Dictate").tap(); capture("Dictate-phone",app)
        app.tabButton("Settings").tap(); capture("Settings",app)
        app.buttons["settings.palette.light"].tap()
        XCTAssertTrue(app.buttons["choice.option.default"].waitForExistence(timeout: 5)); capture("Palette-picker",app); closeSheet("Light palette",app)
        let language = app.buttons["settings.language"]
        app.reveal(language)
        XCTAssertTrue(language.wait(for: \.isHittable, toEqual: true, timeout: 5))
        language.tap(); capture("Language-picker",app); closeSheet("Language",app)
        let modelsNavigation = app.navigationBars["Models"]
        let modelLicence = app.buttons["model.licence.kitten-mini-en-fp16"]
        openSettingsPage(app.buttons["Models"], destination: modelLicence, app); capture("Models",app)
        modelLicence.tap(); capture("Model-terms",app); closeSheet("Review model licence",app)
        let adaptiveBack = app.buttons["BackButton"]
        if adaptiveBack.exists { adaptiveBack.tap() } else { modelsNavigation.buttons.firstMatch.tap() }
        // Settings rows can exist while their outgoing navigation page is still transitioning.
        XCTAssertTrue(modelsNavigation.waitForNonExistence(timeout: 5))
        openSettingsPage(app.buttons["settings.connect"], destination: app.buttons["Enter address"], app); capture("Connect",app)
        app.buttons["Enter address"].tap(); capture("Connect-address",app)
    }
    @MainActor func testPairedComputerScreens() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke","-AppleLanguages","(en)"]
        app.launch(); app.declineConsent()
        app.tabButton("Settings").tap()
        openSettingsPage(app.buttons["settings.connect"], destination: app.buttons["Enter address"], app)
        app.buttons["Enter address"].tap()
        let address = app.textFields["connect.url"]; address.tap(); address.typeText("http://127.0.0.1:3900")
        app.buttons["keyboard.done"].tap(); app.buttons["connect.submit"].tap(); app.openSpeak()
        XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: 10)); capture("Speak-computer",app)
        for tab in ["Voices","Dub","Dictate"] { app.tabButton(tab).tap(); capture(tab+"-computer",app) }
    }
}
