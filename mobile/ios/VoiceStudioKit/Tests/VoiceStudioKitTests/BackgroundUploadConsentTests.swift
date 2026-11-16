#if os(macOS)
import Foundation
import Testing
@testable import VoiceStudioKit

private final class UploadConsentTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var sent: [Data] = []
    var bodies: [Data] { lock.withLock { sent } }
    func send(destination: DiagnosticDestination, body: Data, allowed: @escaping @Sendable () -> Bool) async throws -> Int {
        guard allowed() else { throw DiagnosticFailure.cancelled }
        lock.withLock { sent.append(body) }
        return 201
    }
    func cancelAll() {}
}

private final class UploadCallbackProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var calls: Int { lock.withLock { count } }
    func record() { lock.withLock { count += 1 } }
}

@MainActor private func uploadConsentWait(_ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(5))
    while !condition() && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    #expect(condition())
}

@MainActor private func subscribeUpload(_ uploader: BackgroundUpload, _ reports: DiagnosticReports) throws {
    let context = try #require(reports.recoveryContext)
    uploader.setDiagnosticFailureHandler { input, stamp in
        _ = reports.captureRecovered(input, occurredAtMs: stamp, consentEpoch: context.epoch,
                                     recoveryKey: "ios:\(stamp):unknown", reportedAppVersion: context.appVersion)
    }
}

@Test(arguments: ["off-on", "regrant", "withdraw", "same-grant", "cancel"])
@MainActor func backgroundUploadKeepsItsPermissionAcrossFailedResponseBody(_ scenario: String) async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("upload-consent-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let headers = directory.appendingPathComponent("headers"), release = directory.appendingPathComponent("release")
    let script = #"""
import http.server,pathlib,socket,sys,time
socket.getfqdn = lambda name='': name
headers,release=map(pathlib.Path,sys.argv[1:])
class Host(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  body=b'{"detail":"PRIVATE_HELD_UPLOAD_BODY"}'
  self.send_response(500);self.send_header('Content-Length',str(len(body)));self.end_headers()
  self.wfile.write(body[:1]);self.wfile.flush();headers.touch()
  deadline=time.monotonic()+10
  while not release.exists() and time.monotonic()<deadline:time.sleep(.01)
  try:self.wfile.write(body[1:]);self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError):pass
 def log_message(self,*args):pass
host=http.server.HTTPServer(('127.0.0.1',0),Host)
print(host.server_port,flush=True);host.serve_forever()
"""#
    let host = try TestProcess(["python3", "-u", "-c", script, headers.path, release.path])
    defer { host.stop() }
    let port = try host.port(within: 5)
    let suite = "upload-consent-" + UUID().uuidString
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let uploader = BackgroundUpload(configuration: .ephemeral, defaults: defaults, stagingDirectory: directory)
    let transport = UploadConsentTransport()
    let reports = try DiagnosticReports(directory: directory.appendingPathComponent("diagnostics"),
        policy: DiagnosticPolicy(data: Data(contentsOf: repository.appendingPathComponent("config/diagnostics.json"))),
        destination: DiagnosticDestination(site: "localtest123", host: "http://127.0.0.1:1", appUrl: "https://app.test.invalid"),
        appVersion: "0.5.9-beta.3", transport: transport, disabled: { false })
    await reports.load()
    if scenario != "off-on" { try await reports.setConsent(true); try subscribeUpload(uploader, reports) }
    let source = directory.appendingPathComponent("clip.wav")
    try Data("RIFF-fixture".utf8).write(to: source)
    let api = APIClient(address: try PairingAddress("http://127.0.0.1:\(port)/"))
    try await uploader.start(source: source, api: api, languages: [DubLanguage(code: "es", name: "Spanish")], profile: "", audioOnly: true)
    try await uploadConsentWait { FileManager.default.fileExists(atPath: headers.path) }
    #expect(uploader.receipt == nil)
    if scenario == "regrant" || scenario == "withdraw" {
        uploader.setDiagnosticFailureHandler(nil); try await reports.setConsent(false)
    }
    if scenario == "off-on" || scenario == "regrant" {
        try await reports.setConsent(true); try subscribeUpload(uploader, reports)
    }
    if scenario == "cancel" { await uploader.cancel() }
    try Data().write(to: release)
    if scenario != "cancel" { try await uploadConsentWait { uploader.receipt != nil } }
    await reports.flush()
    #expect(transport.bodies.count == (scenario == "same-grant" ? 1 : 0))
    #expect(!transport.bodies.contains { String(decoding: $0, as: UTF8.self).contains("PRIVATE_HELD_UPLOAD_BODY") })
    uploader.clearReceipt()
    if scenario == "withdraw" { try await reports.setConsent(true); try subscribeUpload(uploader, reports) }
    let prior = transport.bodies.count
    try await uploader.start(source: source, api: api, languages: [DubLanguage(code: "es", name: "Spanish")], profile: "", audioOnly: true)
    try await uploadConsentWait { uploader.receipt != nil }
    await reports.flush()
    #expect(transport.bodies.count == prior + 1)
    uploader.setDiagnosticFailureHandler(nil)
    await reports.close()
}

@Test @MainActor func restoredUploadCompletionHasNoNewPermissionObservation() async throws {
    let suite = "unknown-upload-consent-" + UUID().uuidString
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let file = FileManager.default.temporaryDirectory.appendingPathComponent("restored-upload-" + UUID().uuidString)
    let meta = PendingUpload(host: "http://127.0.0.1:3900/", jobID: "restored", languages: [], profile: "", file: file)
    try DubUploadLedger(defaults: defaults).begin(meta)
    let uploader = BackgroundUpload(configuration: .ephemeral, defaults: defaults)
    let session = URLSession(configuration: .ephemeral)
    defer { session.invalidateAndCancel() }
    let task = session.uploadTask(with: URLRequest(url: URL(string: meta.host)!), from: Data())
    task.taskDescription = String(data: try JSONEncoder().encode(meta), encoding: .utf8)
    let calls = UploadCallbackProbe()
    uploader.setDiagnosticFailureHandler { _, _ in calls.record() }
    uploader.urlSession(session, task: task, didCompleteWithError: URLError(.cannotConnectToHost))
    try await uploadConsentWait { uploader.receipt != nil }
    #expect(calls.calls == 0)
    uploader.setDiagnosticFailureHandler(nil)
}
#endif
