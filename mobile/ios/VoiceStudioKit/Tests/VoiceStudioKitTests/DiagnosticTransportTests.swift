#if os(macOS)
import Foundation
import Testing
@testable import VoiceStudioKit

private func boundedDiagnosticHost(_ mode: String) throws -> TestProcess {
    let script = """
import http.server,socket,sys
socket.getfqdn=lambda host:host
mode=sys.argv[1]
requests=0
class Host(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def do_POST(self):
  global requests
  requests+=1
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  exact=mode.startswith('exact') or requests>1
  body=(b'{"accepted":1,"dropped":[]}' + b' '*16384)[:16384] if exact else b'x'*(8192 if mode=='cancel' else 16385)
  chunked=mode.endswith('chunked') or mode=='cancel'
  self.send_response(202)
  self.send_header('Connection','close')
  self.send_header('Transfer-Encoding','chunked') if chunked else self.send_header('Content-Length',str(len(body) if exact else 1048576))
  self.end_headers()
  try:
   self.wfile.write((('%x\\r\\n'%len(body)).encode()+body+b'\\r\\n'+(b'0\\r\\n\\r\\n' if exact else b'')) if chunked else body)
   self.wfile.flush()
   print('BODY_SENT',flush=True)
   if not exact:
    self.connection.settimeout(5)
    self.connection.recv(1)
  except OSError:pass
 def log_message(self,*args):pass
host=http.server.HTTPServer(('127.0.0.1',0),Host)
print(host.server_port,flush=True)
host.serve_forever()
"""
    return try TestProcess(["python3", "-u", "-c", script, mode])
}

private func boundedDiagnosticDestination(_ host: TestProcess) throws -> DiagnosticDestination {
    let address = "http://127.0.0.1:\(try host.port(within: 5))"
    return try DiagnosticDestination(site: "fixture01", host: address, appUrl: "https://voicestudio.sh", app: DiagnosticAppConfiguration(appID: "fixture01", ingestKey: "smk_" + String(repeating: "a", count: 39), host: address))
}

struct DiagnosticTransportTests {
    @Test(arguments: ["oversized-length", "oversized-chunked"])
    func oversizedRealHTTPResponseStopsBeforeTheServerFinishesItsBody(_ mode: String) async throws {
        let host = try boundedDiagnosticHost(mode); defer { host.stop() }
        let transport = URLSessionDiagnosticTransport(); defer { transport.cancelAll() }
        // The server holds EOF after crossing the response cap. Completion-only
        // buffering waits for the request timeout instead of ending at the cap.
        let reply = try await transport.sendApp(destination: boundedDiagnosticDestination(host), body: Data("{}".utf8), userAgent: "VoiceStudio/0.5.9-beta.3 (ios; arm64)", allowed: { true })
        #expect(reply.status == 202)
        #expect(reply.body.isEmpty)
        #expect(diagnosticAppCompleted(reply, count: 1).isEmpty)
    }

    @Test(arguments: ["exact-length", "exact-chunked"])
    func exactResponseBoundaryRemainsUsable(_ mode: String) async throws {
        let host = try boundedDiagnosticHost(mode); defer { host.stop() }
        let transport = URLSessionDiagnosticTransport(); defer { transport.cancelAll() }
        let reply = try await transport.sendApp(destination: boundedDiagnosticDestination(host), body: Data("{}".utf8), userAgent: "VoiceStudio/0.5.9-beta.3 (ios; arm64)", allowed: { true })
        #expect(reply.status == 202)
        #expect(reply.body.count == 16384)
        #expect(diagnosticAppCompleted(reply, count: 1) == [0])
    }

    @Test(arguments: [false, true])
    func cancellationStopsAnIncompleteResponseAndTheTransportCanBeReused(_ all: Bool) async throws {
        let host = try boundedDiagnosticHost("cancel"); defer { host.stop() }
        let destination = try boundedDiagnosticDestination(host)
        let transport = URLSessionDiagnosticTransport(); defer { transport.cancelAll() }
        let pending = Task { try await transport.sendApp(destination: destination, body: Data("{}".utf8), userAgent: "VoiceStudio/0.5.9-beta.3 (ios; arm64)", allowed: { true }) }
        for _ in 0..<200 where !host.output.text.contains("BODY_SENT") { try await Task.sleep(for: .milliseconds(5)) }
        #expect(host.output.text.contains("BODY_SENT"))
        if all { transport.cancelAll() } else { pending.cancel() }
        do { _ = try await pending.value; Issue.record("Expected request cancellation") }
        catch let error as URLError { #expect(error.code == .cancelled) }
        catch DiagnosticFailure.cancelled { }
        let reply = try await transport.sendApp(destination: destination, body: Data("{}".utf8), userAgent: "VoiceStudio/0.5.9-beta.3 (ios; arm64)", allowed: { true })
        #expect(reply.status == 202)
        #expect(diagnosticAppCompleted(reply, count: 1) == [0])
    }
}
#endif
