import Foundation
import Testing
@testable import VoiceStudioKit

private actor CallsHost: HTTPTransport {
    var requests: [URLRequest] = []
    func send(_ request: URLRequest) async throws -> HTTPReply {
        requests.append(request)
        let record = #"{"id":"call-1","to_masked":"+1***1111","status":"ringing","transcript":[]}"#
        let body: String
        if request.url?.path.hasSuffix("/profiles") == true { body = #"[{"id":"raw","name":"Raw","kind":"clone"},{"id":"own","name":"Own","kind":"clone","verified_own_voice":1},{"id":"design","name":"Design","kind":"design"}]"# }
        else if request.httpMethod == "POST" && request.url?.path.hasSuffix("/calls") == true { body = "{\"call\":"+record+"}" }
        else if request.httpMethod == "GET" { body = record }
        else { body = "{}" }
        return HTTPReply(data: Data(body.utf8))
    }
}
@Test func callsRemainExplicitAuthenticatedAndScopedToPairedOrigin() async throws {
    let host = CallsHost()
    let api = APIClient(address: try PairingAddress("https://voice.example/studio/"),credentials: Credentials(session: "session"),transport: host)
    let voices = try await api.callVoices()
    #expect(voices.filter(\.eligible).map(\.id) == ["own","design"])
    #expect(await host.requests.count == 1)
    let result = try await api.startCall(NewCall(to: "+15550001111",brief: "Ask opening hours",profile_id: "own",disclosure: "AI assistant",max_minutes: 5))
    #expect(result.live)
    try await api.controlCall(result.id,action: "hangup")
    let requests = await host.requests
    #expect(requests[1].url?.path == "/studio/calls")
    #expect(requests[2].url?.path == "/studio/calls/call-1/hangup")
    #expect(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer session" })
    await #expect(throws: (any Error).self) { try await api.controlCall("../settings",action: "hangup") }
    #expect(await host.requests.count == 3)
}
