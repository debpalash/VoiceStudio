import Foundation
import Testing
@testable import VoiceStudioKit

private actor GalleryHost: HTTPTransport {
    var requests: [URLRequest] = []
    func send(_ request: URLRequest) async throws -> HTTPReply {
        requests.append(request)
        let body = request.httpMethod == "POST" ? #"{"profile_id":"saved-voice","name":"Voice"}"# : #"{"total":1,"items":[{"id":"voice-1","name":"Voice","license":"CC0","author":"Author","language":"English"}]}"#
        return HTTPReply(data: Data(body.utf8))
    }
}
@Test func galleryUsesPairedOriginCredentialsAndExplicitImport() async throws {
    let host = GalleryHost(), api = APIClient(address: try PairingAddress("https://voice.example/studio/?pin=123456"),credentials: Credentials(session: "session"),transport: host)
    let page = try await api.community(offset: 100)
    #expect(page.items.first?.license == "CC0")
    #expect(await host.requests.count == 1) // Browsing must not materialize a profile.
    let voice = try await api.useCommunity("voice-1")
    #expect(voice.profile_id == "saved-voice")
    let requests = await host.requests
    #expect(requests[0].url?.path == "/studio/community/items")
    #expect(requests[0].url?.query == "limit=100&offset=100")
    #expect(requests[1].httpMethod == "POST")
    #expect(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer session" && $0.value(forHTTPHeaderField: "X-OmniVoice-Pin") == "123456" })
    await #expect(throws: (any Error).self) { try await api.useCommunity("../escape") }
    #expect(await host.requests.count == 2)
}
