import Foundation
import Testing
@testable import VoiceStudioKit

private actor FlakySettingsHost: HTTPTransport {
    func send(_ request: URLRequest) async throws -> HTTPReply {
        switch request.url?.path {
        case "/engines/translation":
            return HTTPReply(data: Data("""
            {"active":"openai","engines":[{"id":"openai","display_name":"LLM","category":"llm","installed":true,"ready":true,"configured_via":"OpenAI"}]}
            """.utf8))
        default:
            return HTTPReply(data: Data("{}".utf8), status: 503)
        }
    }
}

/// A slow or overloaded host must surface as retryable, not as an "unknown" route:
/// unknown changes the job's consent fingerprint and fails a durable dub.
@Test func transientLLMLookupFailureIsRetryableNotUnknown() async throws {
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"), transport: FlakySettingsHost())
    do {
        _ = try await api.translationEngineForJob()
        Issue.record("expected a retryable failure")
    } catch let error as ClientError {
        #expect(error.isTransient)
    }
}
