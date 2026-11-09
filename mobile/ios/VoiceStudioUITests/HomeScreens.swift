import XCTest

final class HomeScreens: XCTestCase {
    @MainActor func testHomeStartsOfflineAndOpensEveryWorkspace() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        app.launch(); app.declineConsent()
        XCTAssertTrue(app.navigationBars["Home"].waitForExistence(timeout: 5))
        let title = app.descendants(matching: .any)["navigation.title"].firstMatch
        XCTAssertTrue(title.exists)
        XCTAssertEqual(title.label, "Home")
        XCTAssertLessThan(title.frame.midX, app.frame.width / 2)
        XCTAssertTrue(app.staticTexts["VoiceStudio"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = "Home-dark"; attachment.lifetime = .keepAlways; add(attachment)
        for (route, title) in [("speak","Speech"),("voices","Clone a voice"),("dub","Dub"),("dictate","Dictate"),("models","Models"),("connect","Connect to VoiceStudio")] {
            let card = app.buttons["home."+route]
            app.reveal(card)
            XCTAssertTrue(card.isHittable, route); card.tap()
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5), route)
            if route == "voices" {
                app.buttons["Cancel"].tap()
                XCTAssertTrue(app.navigationBars[title].waitForNonExistence(timeout: 5))
                continue
            }
            // A Back tap during the push transition can be dropped; retry until Home shows,
            // and never tap again once it does (Home's first bar button is Projects).
            for _ in 0..<3 where !app.navigationBars["Home"].exists {
                if app.buttons["BackButton"].exists { app.buttons["BackButton"].tap() }
                else if app.navigationBars[title].exists { app.navigationBars[title].buttons.element(boundBy: 0).tap() }
                _ = app.navigationBars["Home"].waitForExistence(timeout: 3)
            }
            XCTAssertTrue(app.navigationBars["Home"].exists, "did not return Home from \(route)")
        }
        for _ in 0..<5 { if app.staticTexts["Your generations will appear here."].isHittable { break }; app.swipeUp() }
        XCTAssertTrue(app.staticTexts["Your generations will appear here."].exists)
        XCTAssertTrue(app.staticTexts["No projects yet"].exists)
        app.tabButton("Settings").tap()
        app.buttons["Mode"].tap(); app.buttons["choice.option.light"].tap()
        app.tabButton("Home").tap()
        app.reveal(app.buttons["home.speak"], swipingDown: true)
        let light = XCTAttachment(screenshot: app.screenshot()); light.name = "Home-light"; light.lifetime = .keepAlways; add(light)
        app.buttons["home.speak"].tap(); XCTAssertTrue(app.textViews["speak.text"].waitForExistence(timeout: 5))
    }
}

final class MaintainerCardScreens: XCTestCase {
    @MainActor private func launch(largeText: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-smoke", "-AppleLanguages", "(en)"]
        if largeText { app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"] }
        app.launch()
        app.declineConsent()
        XCTAssertTrue(app.navigationBars["Home"].waitForExistence(timeout: 5))
        return app
    }

    @MainActor func testCoreProfilesOpenLocallyAndKeepOtherContributorsAccessible() {
        let app = launch()
        let palash = app.buttons["home.contributor.debpalash"]
        let shimul = app.buttons["home.contributor.velixio"]
        // The other-contributor grid is regenerated weekly from GitHub, so its order is
        // not fixed: read the visible cells instead of hardcoding which login lands where.
        let core: Set = ["home.contributor.debpalash", "home.contributor.velixio", "home.contributor.all"]
        var seen = Set<String>()
        let grid = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "home.contributor.")).allElementsBoundByIndex
            .filter { !core.contains($0.identifier) && seen.insert($0.identifier).inserted && $0.isHittable }
        XCTAssertGreaterThanOrEqual(grid.count, 4)
        guard let other = grid.min(by: { ($0.frame.minY, $0.frame.minX) < ($1.frame.minY, $1.frame.minX) }) else { return }
        XCTAssertTrue(palash.isHittable)
        XCTAssertTrue(shimul.isHittable)
        XCTAssertTrue(other.isHittable)
        XCTAssertGreaterThanOrEqual(palash.frame.height + 0.001, 44)
        XCTAssertGreaterThanOrEqual(other.frame.height + 0.001, 44)
        XCTAssertGreaterThanOrEqual(other.frame.width + 0.001, 44)
        XCTAssertLessThan(palash.frame.maxY, other.frame.minY)
        let wordmark = app.staticTexts["home.wordmark"]
        let subtitle = app.staticTexts["home.subtitle"]
        XCTAssertTrue(wordmark.isHittable)
        XCTAssertTrue(subtitle.isHittable)
        XCTAssertEqual(wordmark.label, "VoiceStudio")
        XCTAssertEqual(subtitle.label, "Open Source fully-local ElevenLabs alternative — voice cloning, voice design, video dubbing, dictation, transcription & audiobook creation in 646 languages.")
        XCTAssertLessThanOrEqual(wordmark.frame.maxX, palash.frame.minX)
        XCTAssertLessThanOrEqual(subtitle.frame.maxX, palash.frame.minX)
        XCTAssertLessThan(wordmark.frame.minY, other.frame.maxY)
        let credit = app.staticTexts["home.contributors.credit"]
        XCTAssertTrue(credit.isHittable)
        XCTAssertEqual(credit.label, "Brought to you by")
        XCTAssertLessThanOrEqual(credit.frame.maxY, palash.frame.minY)
        for visible in grid { XCTAssertGreaterThanOrEqual(visible.frame.height + 0.001, 44, visible.identifier) }
        // A compact two-row strip: exactly two row positions, the second below the first.
        let rows = Set(grid.map { ($0.frame.minY * 2).rounded() / 2 }).sorted()
        XCTAssertEqual(rows.count, 2, "\(rows)")
        let bottomRow = grid.map(\.frame.maxY).max() ?? other.frame.maxY
        XCTAssertGreaterThan(rows.last ?? 0, other.frame.minY)
        XCTAssertLessThanOrEqual(max(bottomRow, subtitle.frame.maxY) - min(wordmark.frame.minY, credit.frame.minY), 180)
        let compact = XCTAttachment(screenshot: app.screenshot())
        compact.name = "Home-compact-hero"; compact.lifetime = .keepAlways; add(compact)
        for (login, name, role, website, social) in [
            ("debpalash", "Palash Debnath", "Creator and Core Team", "palash.dev", "@idebpalash"),
            ("velixio", "Shimul Debnath", "Core Team", "shimul.me", "@shimuldn")
        ] {
            app.buttons["home.contributor." + login].tap()
            XCTAssertTrue(app.staticTexts[name].waitForExistence(timeout: 5))
            XCTAssertTrue(app.staticTexts[role].exists)
            for (kind, handle) in [("website", website), ("github", "@" + login), ("x", social)] {
                let link = app.descendants(matching: .any)["home.maintainer." + login + "." + kind].firstMatch
                for _ in 0..<5 { if link.isHittable { break }; app.swipeUp() }
                XCTAssertTrue(link.isHittable, kind)
                XCTAssertTrue(link.label.contains(handle))
                XCTAssertGreaterThanOrEqual(link.frame.height + 0.001, 44)
                XCTAssertGreaterThanOrEqual(link.frame.width + 0.001, 44)
            }
            let image = XCTAttachment(screenshot: app.screenshot())
            image.name = "Maintainer-" + login; image.lifetime = .keepAlways; add(image)
            app.buttons["home.maintainer.close"].tap()
            XCTAssertTrue(app.buttons["home.contributor." + login].waitForExistence(timeout: 5))
        }
        let contributors = app.scrollViews["home.contributors.others"]
        let lastContributor = app.descendants(matching: .any)["home.contributor.nvtoan0201-swe"].firstMatch
        for _ in 0..<12 { if lastContributor.isHittable { break }; contributors.swipeLeft() }
        XCTAssertTrue(lastContributor.isHittable)
        XCTAssertGreaterThanOrEqual(lastContributor.frame.height + 0.001, 44)
        let all = app.descendants(matching: .any)["home.contributor.all"].firstMatch
        for _ in 0..<3 { if all.isHittable { break }; contributors.swipeLeft() }
        XCTAssertTrue(all.isHittable)
    }

    @MainActor func testMaintainerLinksRemainReachableAtLargestTextSize() {
        let app = launch(largeText: true)
        let maintainer = app.buttons["home.contributor.velixio"]
        for _ in 0..<5 { if maintainer.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(maintainer.isHittable)
        maintainer.tap()
        XCTAssertTrue(app.staticTexts["Shimul Debnath"].waitForExistence(timeout: 5))
        for kind in ["website", "github", "x"] {
            let link = app.descendants(matching: .any)["home.maintainer.velixio." + kind].firstMatch
            for _ in 0..<8 { if link.isHittable { break }; app.swipeUp() }
            XCTAssertTrue(link.isHittable, kind)
            XCTAssertGreaterThanOrEqual(link.frame.height + 0.001, 44)
            XCTAssertGreaterThanOrEqual(link.frame.minX, 0)
            XCTAssertLessThanOrEqual(link.frame.maxX, app.frame.width)
        }
        XCTAssertTrue(app.buttons["home.maintainer.close"].isHittable)
        let image = XCTAttachment(screenshot: app.screenshot())
        image.name = "Maintainer-accessibility-text"; image.lifetime = .keepAlways; add(image)
        app.buttons["home.maintainer.close"].tap()
        XCTAssertTrue(maintainer.waitForExistence(timeout: 5))
    }
}
