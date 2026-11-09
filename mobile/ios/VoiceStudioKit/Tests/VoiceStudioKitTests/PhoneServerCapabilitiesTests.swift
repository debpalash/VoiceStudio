import Foundation
import Testing
@testable import VoiceStudioKit

private func featureRequest(_ path: String,body: [String:Any],token: String = "token") throws -> SpeechRequest {
    SpeechRequest(method: "POST",path: path,headers: ["host":"localhost:1234","authorization":"Bearer "+token,"content-type":"application/json"],body: try JSONSerialization.data(withJSONObject: body))
}
private func featureWave() throws -> Data { try LocalAudio(samples: [0,0.5,-0.5,0],sampleRate: 24000).markSynthetic() }
private func featureRouter() async -> SpeechRouter {
    var features = PhoneServerCapabilities()
    features.transcriptionModel = "whisper"; features.translationModel = "translator"; features.translationLanguages = ["fr","de"]
    features.transcribe = { audio in guard audio.sampleRate == 24000, audio.samples.count == 4 else { throw ClientError("mobile.errors.audio") }; return "Hello" }
    features.translate = { text,language in text+" → "+language }
    features.encodeMP3 = { _ in Data("mp3 fixture".utf8) }
    let router = SpeechRouter(model: "voice",token: "token",capabilities: features) { _,_ in try featureWave() }
    await router.configure(hosts: ["localhost"],port: 1234)
    return router
}
@Test func phoneServerExposesOnlyExplicitlyEnabledCapabilities() async throws {
    let router = await featureRouter()
    let request = try featureRequest("/mcp",body: ["jsonrpc":"2.0","id":1,"method":"tools/list"])
    let response = await router.respond(request)
    let text = String(decoding: response.body,as: UTF8.self)
    #expect(text.contains("transcribe") && text.contains("translate") && text.contains("speak"))
    let basic = SpeechRouter(model: "voice",token: "token") { _,_ in try featureWave() }
    await basic.configure(hosts: ["localhost"],port: 1234)
    let disabled = await basic.respond(try featureRequest("/v1/audio/transcriptions",body: ["audio":try featureWave().base64EncodedString()]))
    #expect(disabled.status == 404)
    let basicTools = await basic.respond(request)
    #expect(!String(decoding: basicTools.body,as: UTF8.self).contains("transcribe"))
}
@Test func phoneServerTranscribesBoundedWaveAndTranslatesOnlySelectedLanguages() async throws {
    let router = await featureRouter()
    let request = try featureRequest("/v1/audio/transcriptions",body: ["audio":try featureWave().base64EncodedString(),"model":"whisper"])
    let result = await router.respond(request)
    #expect(result.status == 200)
    #expect(String(decoding: result.body,as: UTF8.self).contains("Hello"))
    #expect(await router.respond(try featureRequest("/v1/audio/transcriptions",body: ["audio":"not WAV"])).status == 400)
    #expect(await router.respond(try featureRequest("/v1/audio/transcriptions",body: ["audio":try featureWave().base64EncodedString(),"model":"download-me"])).status == 400)
    let translated = await router.respond(try featureRequest("/v1/text/translations",body: ["input":"Hello","language":"FR"]))
    #expect(translated.status == 200)
    #expect(String(decoding: translated.body,as: UTF8.self).contains("fr"))
    #expect(await router.respond(try featureRequest("/v1/text/translations",body: ["input":"Hello","language":"not-installed"])).status == 400)
    #expect(await router.respond(try featureRequest("/v1/text/translations",body: ["input":"Hello","language":"fr"],token: "bad")).status == 401)
}
@Test func phoneMCPTextToolsAndMP3UseSameHandlersAndAuthentication() async throws {
    let router = await featureRouter()
    let transcribe = await router.respond(try featureRequest("/mcp",body: ["jsonrpc":"2.0","id":"a","method":"tools/call","params":["name":"transcribe","arguments":["audio":try featureWave().base64EncodedString()]]]))
    #expect(String(decoding: transcribe.body,as: UTF8.self).contains("Hello"))
    let translation = await router.respond(try featureRequest("/mcp",body: ["jsonrpc":"2.0","id":2,"method":"tools/call","params":["name":"translate","arguments":["text":"Hello","language":"de"]]]))
    #expect(String(decoding: translation.body,as: UTF8.self).contains("de"))
    let mp3 = await router.respond(try featureRequest("/v1/audio/speech",body: ["input":"Hello","response_format":"mp3"]))
    #expect(mp3.status == 200 && mp3.type == "audio/mpeg")
    #expect(mp3.body == Data("mp3 fixture".utf8))
}
@Test func phoneWaveParserRejectsMalformedOversizedAndUnsupportedAudio() throws {
    var malformed = try featureWave(); malformed[4] = 0
    #expect(throws: (any Error).self) { try PhoneServerAudio.decodeWave(malformed) }
    var stereo = try featureWave(); stereo[22] = 2
    #expect(throws: (any Error).self) { try PhoneServerAudio.decodeWave(stereo) }
    let long = try LocalAudio(samples: Array(repeating: 0,count: 24000*31),sampleRate: 24000).markSynthetic()
    #expect(throws: (any Error).self) { try PhoneServerAudio.decodeWave(long) }
    #expect(try PhoneServerAudio.decodeWave(featureWave()).samples.count == 4)
}
private actor CapabilityGate {
    var entered = false
    func mark() { entered = true }
}
@Test func phoneCapabilitiesShareOneInferenceSlotAndCanRunWithoutSpeech() async throws {
    let gate = CapabilityGate()
    var features = PhoneServerCapabilities(); features.speech = false; features.transcriptionModel = "whisper"
    features.transcribe = { _ in await gate.mark(); try await Task.sleep(for: .seconds(20)); return "Hello" }
    let router = SpeechRouter(model: "unused",token: "token",capabilities: features) { _,_ in Issue.record("Speech disabled"); return Data() }
    await router.configure(hosts: ["localhost"],port: 1234)
    let request = try featureRequest("/v1/audio/transcriptions",body: ["audio":try featureWave().base64EncodedString()])
    let task = Task { await router.respond(request) }
    while !(await gate.entered) { try await Task.sleep(for: .milliseconds(5)) }
    #expect(await router.respond(request).status == 429)
    #expect(await router.respond(try featureRequest("/v1/audio/speech",body: ["input":"Hello"])).status == 404)
    task.cancel(); _ = await task.value
}
