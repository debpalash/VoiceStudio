import Foundation
import VoiceStudioKit

#if DEBUG
/// Only selected by the UI test launch argument. No socket is opened.
actor SmokeVault: CredentialVault {
    private var values: [String: Credentials] = [:]
    func read(host: String) -> Credentials? { values[host] }
    func write(_ credentials: Credentials?, host: String) { values[host] = credentials }
}
struct SmokeTransport: HTTPTransport {
    func send(_ request: URLRequest) async throws -> HTTPReply {
        let path = request.url?.path ?? ""
        switch path {
        case "/health": return reply(#"{"status":"ok","device":"cpu","version":"test"}"#, headers: ["x-omnivoice-backend": "test"])
        case "/profiles": return reply(#"[{"id":"smoke-profile","name":"Smoke voice","language":"english","kind":"clone"}]"#)
        case "/history": return reply("[]")
        case "/dub/history": return reply(#"[{"id":"smoke-dub","filename":"vs-dub-test.wav","language":"Spanish","duration":125,"created_at":1700000000}]"#)
        case "/dictation/readiness": return reply(#"{"ready":true}"#)
        case "/engines":
            try await Task.sleep(for: .seconds(ProcessInfo.processInfo.arguments.contains("--ui-delayed-inventory") ? 8 : 1))
            return reply(#"{"tts":{"active":"kittentts","backends":[{"id":"kittentts","display_name":"KittenTTS","available":true,"supports_cloning":false,"supported_language_names":["English"]},{"id":"mlx-audio","display_name":"OuteTTS (MLX Audio, Apple Silicon)","available":true,"supports_cloning":false},{"id":"unavailable","display_name":"Unavailable engine","available":false,"reason":"Install on host","supports_cloning":true}]}}"#)
        case "/generate":
            return HTTPReply(data: Self.wav(), headers: ["content-type": "audio/wav", "x-audio-id": "smoke"])
        default: return HTTPReply(data: Data(), status: 204)
        }
    }
    private func reply(_ json: String, headers: [String: String] = [:]) -> HTTPReply { HTTPReply(data: Data(json.utf8), headers: headers) }
    private static func wav() -> Data {
        let count: UInt32 = 16000 * 5
        var result = Data()
        func ascii(_ text: String) { result.append(Data(text.utf8)) }
        func u32(_ n: UInt32) { var value = n.littleEndian; withUnsafeBytes(of: &value) { result.append(contentsOf: $0) } }
        func u16(_ n: UInt16) { var value = n.littleEndian; withUnsafeBytes(of: &value) { result.append(contentsOf: $0) } }
        ascii("RIFF"); u32(36 + count * 2); ascii("WAVEfmt "); u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16); ascii("data"); u32(count * 2)
        for i in 0..<count { let sample = Int16(sin(Double(i) * 2 * .pi * 440 / 16000) * 4000); u16(UInt16(bitPattern: sample)) }
        return result
    }
}
#endif
