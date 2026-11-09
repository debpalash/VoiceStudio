#if os(macOS)
import Foundation
import Testing
@testable import VoiceStudioKit

private final class BoundedDownloadHost {
    let process = Process()
    let port: Int
    init() throws {
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3","-u","-c",Self.script]
        process.standardOutput = pipe
        try process.run()
        var line = Data()
        while let byte = try pipe.fileHandleForReading.read(upToCount: 1), !byte.isEmpty {
            if byte == Data([10]) { break }; line.append(byte)
        }
        guard let port = Int(String(decoding: line,as: UTF8.self)) else { process.terminate(); throw ClientError("mobile.errors.invalid_response") }
        self.port = port
    }
    deinit { if process.isRunning { process.terminate(); process.waitUntilExit() } }
    private static let script = #"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
sent = 0
class Host(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        global sent
        if self.path == '/sent':
            body=str(sent).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location','http://127.0.0.1:1/leak'); self.send_header('Content-Length','0'); self.end_headers(); return
        if self.headers.get('Authorization') != 'Bearer private-session':
            self.send_response(401); self.send_header('Content-Length','0'); self.end_headers(); return
        total = 16*1024*1024 if self.path.startswith('/large') or self.path in ('/cancel','/multi') else 65536
        self.send_response(200)
        if self.path != '/large-unknown': self.send_header('Content-Length',str(total))
        self.end_headers()
        sent=0
        try:
            while sent < total:
                chunk=b'x'*min(16384,total-sent)
                self.wfile.write(chunk); self.wfile.flush(); sent += len(chunk)
                if total > 65536 and self.path != '/multi': time.sleep(.01)
        except (BrokenPipeError, ConnectionResetError): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Host)
print(server.server_port,flush=True)
server.serve_forever()
"""#
}

@Test(arguments: ["/large-known","/large-unknown"])
func boundedDownloadCancelsOversizeBeforeBodyCompletes(_ path: String) async throws {
    let host = try BoundedDownloadHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:\(host.port)"), credentials: Credentials(session: "private-session"))
    do {
        _ = try await api.download(api.request(path),maxBytes: 65536)
        Issue.record("Oversized response accepted")
    } catch let error as ClientError { #expect(error.key == "mobile.errors.file") }
    let count = try await api.send(api.request("/sent"))
    #expect((Int(String(decoding: count.data,as: UTF8.self)) ?? Int.max) < 16*1024*1024)
}

@Test func boundedDownloadAcceptsExactLimitAndRejectsRedirects() async throws {
    let host = try BoundedDownloadHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:\(host.port)"), credentials: Credentials(session: "private-session"))
    let result = try await api.download(api.request("/exact"),maxBytes: 65536)
    defer { try? FileManager.default.removeItem(at: result.file) }
    #expect(try Data(contentsOf: result.file) == Data(repeating: 120,count: 65536))
    do { _ = try await api.download(api.request("/redirect"),maxBytes: 65536); Issue.record("Followed redirect") }
    catch let error as ClientError { #expect(error.key == "mobile.errors.redirect") }
    let unauthenticated = APIClient(address: api.address)
    do { _ = try await unauthenticated.download(unauthenticated.request("/exact"),maxBytes: 65536); Issue.record("Missing auth accepted") }
    catch let error as ClientError { #expect(error.key == "mobile.connect.error_api_key") }
}

@Test func boundedDownloadStreamsMultipleChunksWithoutTruncation() async throws {
    let host = try BoundedDownloadHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:\(host.port)"), credentials: Credentials(session: "private-session"))
    let start = ContinuousClock.now
    let result = try await api.download(api.request("/multi"),maxBytes: 16*1024*1024)
    print("Bounded 16 MiB download elapsed: \(start.duration(to: .now))")
    defer { try? FileManager.default.removeItem(at: result.file) }
    #expect(try Data(contentsOf: result.file) == Data(repeating: 120,count: 16*1024*1024))
}

@Test func boundedDownloadPreservesCancellation() async throws {
    let host = try BoundedDownloadHost()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:\(host.port)"), credentials: Credentials(session: "private-session"))
    let task = Task { try await api.download(api.request("/cancel"),maxBytes: 32*1024*1024) }
    try await Task.sleep(for: .milliseconds(100))
    task.cancel()
    await #expect(throws: CancellationError.self) { try await task.value }
    let count = try await api.send(api.request("/sent"))
    #expect((Int(String(decoding: count.data,as: UTF8.self)) ?? Int.max) < 16*1024*1024)
}

private actor BoundRecordingTransport: HTTPTransport {
    private(set) var limits: [Int] = []
    func send(_ request: URLRequest) async throws -> HTTPReply {
        guard request.url?.path == "/engines/diarisation" else { throw ClientError("mobile.errors.invalid_response") }
        return HTTPReply(data: Data(#"{"installed":true}"#.utf8))
    }
    func download(_ request: URLRequest,maxBytes: Int) async throws -> DownloadReply {
        limits.append(maxBytes)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let data: Data
        if request.url?.path.hasPrefix("/dub/transcribe-stream/") == true {
            data = Data("event: final\ndata: {\"segments\":[{\"id\":0,\"start\":0,\"end\":1,\"text\":\"Speech\"}]}\n\nevent: done\ndata: {}\n\n".utf8)
        } else { data = Data([1]) }
        try data.write(to: file)
        return DownloadReply(file: file,status: 200,headers: [:])
    }
}
@Test func voiceProjectAndAnalysisDownloadsSupplyTheirStorageBudgets() async throws {
    let transport = BoundRecordingTransport()
    let api = APIClient(address: try PairingAddress("http://127.0.0.1"),transport: transport)
    let voice = try await api.downloadPhoneVoiceBundle("profile")
    defer { try? FileManager.default.removeItem(at: voice) }
    let project = try await api.receiveHandoff(UUID().uuidString.lowercased())
    defer { try? FileManager.default.removeItem(at: project) }
    let analysis = try await api.computerDubAnalysis("job")
    #expect(analysis.segments.count == 1)
    #expect(await transport.limits == [64*1024*1024,ProjectArchive.maxBytes+8_388_608,16_777_216])
}
#endif
