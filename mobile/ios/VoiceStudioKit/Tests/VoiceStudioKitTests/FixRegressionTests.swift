import Foundation
import Testing
@testable import VoiceStudioKit

@Test func serverDetailsSurviveEveryRESTPath() async throws {
    let host = MockHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: host)
    let message = "KittenTTS can't use reference audio; choose an engine that supports voice cloning"
    await host.setResponse(HTTPReply(data: try JSONSerialization.data(withJSONObject: ["detail": message]), status: 403))
    for download in [false, true] {
        do {
            if download { _ = try await api.download(api.request("/dub/download/job")) }
            else { _ = try await api.generate(text: "Sample", profile: "clone", engine: "kittentts", language: "") }
            Issue.record("Expected backend error")
        } catch let error as ClientError {
            #expect(error.key == "mobile.errors.server")
            #expect(error.params["message"] == message)
            #expect(error.status == 403)
        }
    }
}

@Test func structuredLicenceDetailsShowNamesAndComputerAction() throws {
    let reply = HTTPReply(data: data(#"{"detail":{"code":"model_licence_required","message":"Review terms","models":[{"repo_id":"org/voice","license_name":"Research licence"}]}}"#), status: 403)
    do { _ = try APIClient.validate(reply, authenticated: true); Issue.record("Expected licence error") }
    catch let error as ClientError {
        let l10n = try L10n(catalogs: catalogs())
        let text = l10n.t(error.key, error.params)
        #expect(text.contains("Research licence"))
        #expect(!text.contains("Review terms"))
        #expect(text.contains("computer"))
        #expect(text.contains("Model Catalogue"))
    }
}

@Test(arguments: [
    "Licence not accepted for org/voice, org/other. Review and accept it in Model Catalogue.",
    "Research licence and Community licence need acceptance.",
    "Review terms",
    ""
])
func licenceMessageListsModelsAndComputerActionOnce(_ message: String) throws {
    let payload: [String: Any] = ["detail": [
        "code": "model_licence_required", "message": message,
        "models": [
            ["repo_id": "org/voice", "license_name": "Research licence"],
            ["repo_id": "org/other", "license": "Community licence"],
            ["repo_id": "org/voice", "license_name": "Research licence"]
        ]
    ]]
    let reply = HTTPReply(data: try JSONSerialization.data(withJSONObject: payload), status: 403)
    do { _ = try APIClient.validate(reply, authenticated: true); Issue.record("Expected licence error") }
    catch let error as ClientError {
        let rendered = try L10n(catalogs: catalogs()).t(error.key, error.params)
        #expect(rendered == "org/voice · Research licence\norg/other · Community licence\nAccept it on your computer in VoiceStudio → Model Catalogue")
    }
}

@Test func licenceMessageWithoutModelRecordsPreservesServerDiagnostic() throws {
    let diagnostic = "Model licence records are unreadable. Reinstall or update VoiceStudio."
    let payload: [String: Any] = ["detail": ["code": "model_licence_required", "message": diagnostic, "models": []]]
    let error = try #require(ServerFailure.parse(JSONSerialization.data(withJSONObject: payload), status: 403))
    let rendered = try L10n(catalogs: catalogs()).t(error.key, error.params)
    #expect(rendered == diagnostic + "\nAccept it on your computer in VoiceStudio → Model Catalogue")
}

@Test func presetGenerationOmitsProfileAndAutomaticLanguage() async throws {
    let host = MockHost(), api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: MockHost())
    let client = APIClient(address: api.address, transport: host)
    _ = try await client.generate(text: "Sample", profile: "", engine: "kittentts", language: "")
    let body = String(decoding: try #require(await host.requests.first?.httpBody), as: UTF8.self)
    #expect(!body.contains("name=\"profile_id\""))
    #expect(!body.contains("name=\"language\""))
}

@Test func interpolationTreatsICUAndReplacementCharactersLiterally() throws {
    let l10n = try L10n(locale: "en", catalogs: ["en": data(#"{"value":"{{name}} / {{count}} / {{ name }}"}"#)])
    #expect(l10n.t("value", ["name": "$1\\path{literal}", "count": "2"]) == "$1\\path{literal} / 2 / $1\\path{literal}")
}

@Test func engineMetadataAndCloneSelectionUseHostCapabilities() throws {
    let inventory = try JSONDecoder().decode(EngineInventory.self, from: data(#"{"tts":{"active":"kittentts","backends":[{"id":"kittentts","display_name":"KittenTTS","available":true,"supports_cloning":false},{"id":"omnivoice","display_name":"OmniVoice","available":true,"supports_cloning":true},{"id":"mlx-audio","display_name":"MLX Audio","available":false,"reason":"Requires Apple Silicon","hint":"Choose an installed engine","supports_cloning":true}]}}"#))
    let profiles = try JSONDecoder().decode([Profile].self, from: data(#"[{"id":"clone","name":"My voice","language":"es","kind":"clone","ref_audio_path":"voice.wav"},{"id":"design","name":"Designed voice","kind":"design","locked_audio_path":"locked.wav"}]"#))
    let catalog = LanguageCatalog(names: ["Auto", "English", "Spanish"], codes: ["Spanish":"es"])
    let l10n = try L10n(catalogs: catalogs())
    var selection = SpeechSelection()
    selection.reconcile(inventory: inventory, profiles: profiles)
    #expect(selection.engineID == "kittentts")
    #expect(selection.language.isEmpty)
    #expect(selection.profileID.isEmpty)
    #expect(selection.canGenerate(in: inventory, profiles: profiles))
    #expect(inventory.tts.backends[0].display_name == "KittenTTS")
    selection.chooseProfile("clone", profiles: profiles, inventory: inventory, catalog: catalog)
    #expect(selection.language == "spanish")
    #expect(selection.engineID == "omnivoice")
    #expect(!inventory.tts.backends[0].selectable(for: profiles[0]))
    #expect(inventory.tts.backends[0].explanation(for: profiles[0], l10n: l10n)?.contains("voice cloning") == true)
    #expect(profiles[1].hasReferenceAudio)
    #expect(!inventory.tts.backends[2].selectable(for: nil))
    #expect(inventory.tts.backends[2].explanation(for: nil, l10n: l10n) == "Requires Apple Silicon Choose an installed engine")
    selection.chooseProfile("", profiles: profiles, inventory: inventory, catalog: catalog)
    #expect(selection.language.isEmpty)
    #expect(inventory.tts.backends[0].selectable(for: nil))
}

@Test func desktopLanguagesPreserveAutoNamesSearchAndDubTargets() throws {
    let names = try JSONDecoder().decode([String].self, from: Data(contentsOf: repository.appendingPathComponent("electron/src/shared/languages.json")))
    let catalog = LanguageCatalog(names: names, codes: ["English":"en", "Spanish":"es", "Chinese (Simplified)":"cmn-Hans"])
    #expect(catalog.languages.map(\.name) == names)
    #expect(catalog.languages.first?.name == "Auto")
    #expect(catalog.languages.first?.value == "")
    #expect(catalog.language("spanish")?.name == "Spanish")
    #expect(catalog.language("es")?.value == "spanish")
    #expect(catalog.search("sPaNiSh").map(\.name) == ["Spanish"])
    #expect(!catalog.search("", includeAuto: false).contains { $0.automatic })
    #expect(!catalog.dubOptions.contains { $0.name == "Auto" })
    #expect(catalog.dubOptions.first { $0.name == "Spanish" }?.code == "es")
    #expect(catalog.dubOptions.allSatisfy { !$0.code.isEmpty && !$0.name.isEmpty })
}

@Test func readinessNamesRecommendedModelAndInstallLocation() throws {
    let status = try JSONDecoder().decode(Readiness.self, from: data(#"{"ready":false,"missing":{"error":"asr_model_missing","missing_repo_id":"org/model","recommended":{"label":"Whisper Small","size_gb":0.46}}}"#))
    let l10n = try L10n(catalogs: catalogs())
    let reason = status.reason(l10n: l10n)
    for part in ["Whisper Small", "0.46 GB", "computer", "Settings", "Models / Dictation"] { #expect(reason.contains(part)) }
    #expect(!reason.contains("API key"))
    let unknown = try JSONDecoder().decode(Readiness.self, from: data(#"{"ready":false,"missing":{"error":"asr_model_missing","recommended":null}}"#))
    #expect(unknown.reason(l10n: l10n).contains("Models / Dictation"))
    let ready = try JSONDecoder().decode(Readiness.self, from: data(#"{"ready":true,"missing":null}"#))
    #expect(ready.reason(l10n: l10n).isEmpty)
}

@Test func paletteNamesMatchGeneratedDesktopLabels() throws {
    let l10n = try L10n(catalogs: catalogs())
    for palette in VSTokens.palettes {
        #expect(Theme.paletteLabel(palette, l10n: l10n) == (palette.id == "default" ? l10n.t("themeAppearance.default") : palette.label))
    }
    #expect(Theme.paletteLabel(try #require(VSTokens.palettes.first { $0.id == "signal" }), l10n: l10n) == "VoiceStudio Original")
}

@Test(arguments: [(0.0, "0:00"), (1.0, "0:01"), (61.9, "1:01"), (3601.0, "60:01"), (-3.0, "0:00"), (Double.nan, "0:00"), (Double.infinity, "0:00")])
func playbackUsesMinutesAndSeconds(_ sample: (Double, String)) { #expect(PlaybackTime.format(sample.0) == sample.1) }

@Test func serverFallbacksAndAuthenticationKeepSpecificMappings() throws {
    for (status, authenticated, body, expected) in [
        (401, true, #"{"detail":"expired"}"#, "mobile.connect.error_session_expired"),
        (401, false, #"{"detail":"PIN required"}"#, "mobile.connect.error_pin"),
        (401, false, #"{"detail":"key rejected"}"#, "mobile.connect.error_api_key"),
        (403, false, "{}", "mobile.errors.forbidden"),
        (500, false, "invalid JSON", "mobile.errors.backend"),
        (422, false, #"{"detail":{"code":"other","message":"Choose a supported engine"}}"#, "mobile.errors.server")
    ] {
        do { _ = try APIClient.validate(HTTPReply(data: data(body), status: status), authenticated: authenticated); Issue.record("Expected failure") }
        catch let error as ClientError { #expect(error.key == expected); #expect(error.status == status) }
    }
}

#if os(macOS)
@Test func buildPhaseBundlesAuthoritativeLanguagesAndVersion() throws {
    let output = repository.appendingPathComponent("mobile/ios/.generated/resources-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: output) }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = ["python3", repository.appendingPathComponent("mobile/ios/scripts/prepare_resources.py").path,
                         "--output", output.path, "--info-output", output.appendingPathComponent("Info.plist").path]
    try process.run(); process.waitUntilExit()
    #expect(process.terminationStatus == 0)
    let source = try Data(contentsOf: repository.appendingPathComponent("electron/src/shared/languages.json"))
    #expect(try Data(contentsOf: output.appendingPathComponent("Catalogs/languages.json")) == source)
    let codes = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: output.appendingPathComponent("Catalogs/language-codes.json")))
    #expect(codes["English"] == "en")
    #expect(codes["Spanish"] == "es")
    #expect(codes["Chinese (Simplified)"] == "cmn-Hans")
    let names = try JSONDecoder().decode([String].self, from: source)
    let catalog = LanguageCatalog(names: names, codes: codes)
    #expect(catalog.language("es")?.name == "Spanish")
    let rootVersion = try #require((JSONSerialization.jsonObject(with: Data(contentsOf: repository.appendingPathComponent("package.json"))) as? [String: Any])?["version"] as? String)
    let info = try #require(PropertyListSerialization.propertyList(from: Data(contentsOf: output.appendingPathComponent("Info.plist")), format: nil) as? [String: Any])
    // App Store Connect takes only X.Y.Z, so a beta bundles as its release triple.
    #expect(info["CFBundleShortVersionString"] as? String == rootVersion.split(separator: "-").first.map(String.init))
    // Content reports send the full root version, beta suffix included.
    #expect(info[ContentReportClient.appVersionInfoKey] as? String == rootVersion)
    #expect(ContentReportClient.appVersion(info: info) == rootVersion)
    for locale in L10n.languages {
        let strings = try #require(PropertyListSerialization.propertyList(from: Data(contentsOf: output.appendingPathComponent(locale + ".lproj/InfoPlist.strings")), format: nil) as? [String: String])
        #expect(strings["NSMicrophoneUsageDescription"]?.isEmpty == false)
    }
}
#endif

@Test func interpolationDoesNotRecursivelyExpandParameterText() throws {
    let l10n = try L10n(locale: "en", catalogs: ["en": data(#"{"value":"{{a}} {{b}} {{unknown}}"}"#)])
    #expect(l10n.t("value", ["a":"{{b}}", "b":"$1\\folder"]) == "{{b}} $1\\folder {{unknown}}")
}
