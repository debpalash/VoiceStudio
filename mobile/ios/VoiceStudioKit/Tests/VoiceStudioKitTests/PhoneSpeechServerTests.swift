import Foundation
import Testing
@testable import VoiceStudioKit

private func serverRequest(_ path: String = "/v1/audio/speech",body: String = "{\"input\":\"Hello\"}",headers: [String:String] = [:],method: String = "POST") -> SpeechRequest {
    SpeechRequest(method: method,path: path,headers: ["authorization":"Bearer test-token","host":"localhost:9000","content-type":"application/json"].merging(headers) { _,new in new },body: Data(body.utf8))
}
private func testRouter() async -> SpeechRouter {
    let router = SpeechRouter(model: "voice",token: "test-token") { _,_ in try LocalAudio(samples: [0.1,0.2,0.1],sampleRate: 24000).markSynthetic() }
    await router.configure(hosts: ["localhost","127.0.0.1"],port: 9000)
    return router
}
@Test func phoneServerRequiresAuthenticationAndRejectsDNSRebinding() async throws {
    let router = await testRouter()
    #expect(await router.respond(serverRequest(headers: ["authorization":""])).status == 401)
    #expect(await router.respond(serverRequest(headers: ["host":"attacker.example:9000"])).status == 403)
    #expect(await router.respond(serverRequest(headers: ["origin":"https://attacker.example"])).status == 403)
    #expect(await router.respond(serverRequest(headers: ["origin":"null"])).status == 403)
    #expect(await router.respond(serverRequest(headers: ["origin":"http://localhost:9000"])).status == 200)
    let wave = await router.respond(serverRequest())
    #expect(wave.type == "audio/wav")
    #expect(wave.body.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
}
@Test func phoneServerValidatesSpeechWithoutFilesDownloadsOrUnknownModels() async {
    let router = await testRouter()
    for body in ["{}","{\"input\":\"\"}","{\"input\":\"hi\",\"speed\":true}","{\"input\":\"hi\",\"speed\":3}","{\"input\":\"hi\",\"model\":\"download-me\"}","{\"input\":\"hi\",\"file\":\"/etc/passwd\"}"] {
        #expect(await router.respond(serverRequest(body: body)).status == 400)
    }
}
@Test func phoneServerRejectsAmbiguousHTTPFraming() throws {
    for header in ["Content-Length: 2\r\nContent-Length: 2","Transfer-Encoding: chunked","Content-Length: -1","Host: localhost\r\n Host: evil"] {
        #expect(throws: (any Error).self) { try SpeechRequest.parse(Data(("POST /mcp HTTP/1.1\r\n"+header+"\r\n\r\n{}").utf8)) }
    }
    #expect(try SpeechRequest.parse(Data("POST /mcp HTTP/1.1\r\nContent-Length: 2\r\n\r\n{".utf8)) == nil)
    #expect(throws: (any Error).self) { try SpeechRequest.parse(Data("POST /mcp HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}GET /".utf8)) }
}
@Test func phoneMCPInitializesListsAndReturnsMarkedAudio() async throws {
    let router = await testRouter()
    let initBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\"}}"
    let response = await router.respond(serverRequest("/mcp",body: initBody))
    #expect(String(decoding: response.body,as: UTF8.self).contains("2025-11-25"))
    #expect(await router.respond(serverRequest("/mcp",body: "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}")).status == 202)
    let audio = await router.respond(serverRequest("/mcp",body: "{\"jsonrpc\":\"2.0\",\"id\":\"speech\",\"method\":\"tools/call\",\"params\":{\"name\":\"speak\",\"arguments\":{\"text\":\"Hi\"}}}"))
    let object = try #require(try JSONSerialization.jsonObject(with: audio.body) as? [String:Any])
    #expect(object["id"] as? String == "speech")
    let result = try #require(object["result"] as? [String:Any]), content = try #require(result["content"] as? [[String:Any]])
    #expect(content.first?["mimeType"] as? String == "audio/wav")
    let data = try #require(Data(base64Encoded: content.first?["data"] as? String ?? ""))
    #expect(data.range(of: Data("VoiceStudio synthetic audio".utf8)) != nil)
}
@Test func phoneServerBoundsConcurrentInferenceAndCancels() async throws {
    let router = SpeechRouter(model: "voice",token: "test-token") { _,_ in try await Task.sleep(for: .seconds(20));return Data() }
    await router.configure(hosts: ["localhost"],port: 9000)
    let first = Task { await router.respond(serverRequest()) }
    try await Task.sleep(for: .milliseconds(30))
    #expect(await router.respond(serverRequest()).status == 429)
    #expect(await router.respond(serverRequest("/health",method: "GET")).status == 200)
    first.cancel(); _ = await first.value
}

@Test func phoneSpeechListenerServesAuthenticatedRequestsAndStops() async throws {
    let server = try PhoneSpeechServer(model: "voice") { _,_ in try LocalAudio(samples: [0.1],sampleRate: 24000).markSynthetic() }
    try await server.start()
    defer { server.stop() }
    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 3
    let session = URLSession(configuration: config)
    defer { session.invalidateAndCancel() }
    var request = URLRequest(url: URL(string: "http://127.0.0.1:\(server.port)/health")!)
    let (_,unauthorized) = try await session.data(for: request)
    #expect((unauthorized as? HTTPURLResponse)?.statusCode == 401)
    request.setValue("Bearer "+server.token,forHTTPHeaderField: "Authorization")
    let (body,response) = try await session.data(for: request)
    #expect((response as? HTTPURLResponse)?.statusCode == 200)
    #expect(String(decoding: body,as: UTF8.self).contains("voicestudio-phone"))
    server.stop()
    try await Task.sleep(for: .milliseconds(50))
    await #expect(throws: (any Error).self) { try await session.data(for: request) }
}

private actor SpeechCancellationGate {
    var entered = false
    var cancelled = false
    func start() { entered = true }
    func didCancel() { cancelled = true }
}
@Test func phoneMCPCancelNotificationCancelsOnlyMatchingRequest() async throws {
    let gate = SpeechCancellationGate()
    let router = SpeechRouter(model: "voice",token: "test-token") { _,_ in
        await gate.start()
        do { try await Task.sleep(for: .seconds(30)); return Data() }
        catch { await gate.didCancel(); throw error }
    }
    await router.configure(hosts: ["localhost"],port: 9000)
    let first = Task { await router.respond(serverRequest("/mcp",body: #"{"jsonrpc":"2.0","id":"active","method":"tools/call","params":{"name":"speak","arguments":{"text":"Hello"}}}"#)) }
    let deadline = ContinuousClock.now + .seconds(2)
    while !(await gate.entered), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
    #expect(await gate.entered)
    let unrelated = await router.respond(serverRequest("/mcp",body: #"{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":"different"}}"#))
    #expect(unrelated.status == 202)
    #expect(await router.respond(serverRequest()).status == 429)
    _ = await router.respond(serverRequest("/mcp",body: #"{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":"active"}}"#))
    _ = await first.value
    #expect(await gate.cancelled)
}
