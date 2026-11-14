#if VS_DIAGNOSTICS_APP_TESTS
import Foundation
import Testing
@testable import VoiceStudioKit

private final class DiagnosticControllerStore: DiagnosticStorage, @unchecked Sendable {
    private let lock=NSLock()
    private var files: [String:Data]=[:]
    private var fail=true
    func allowWrites() { lock.withLock { fail=false } }
    func read(_ name: String,maxBytes: Int) throws -> Data? { lock.withLock { files[name] } }
    func remove(_ name: String) throws { _ = lock.withLock { files.removeValue(forKey: name) } }
    func write(_ name: String,data: Data) throws {
        try lock.withLock { if fail { throw DiagnosticFailure.storageUnavailable };files[name]=data }
    }
}
private final class DiagnosticControllerTransport: DiagnosticTransport, @unchecked Sendable {
    private let lock=NSLock()
    private var sends=0
    var count: Int { lock.withLock { sends } }
    func send(destination: DiagnosticDestination,body: Data,allowed: @escaping @Sendable () -> Bool) async throws -> Int { lock.withLock { sends += 1 };return 201 }
    func cancelAll() {}
}
@MainActor @Test func diagnosticAppControllerRetriesTheDesiredEnabledChoice() async throws {
    var root=URL(fileURLWithPath: #filePath)
    for _ in 0..<6 { root.deleteLastPathComponent() }
    let policy=try DiagnosticPolicy(data: Data(contentsOf: root.appendingPathComponent("config/diagnostics.json")))
    let store=DiagnosticControllerStore(), transport=DiagnosticControllerTransport()
    let reports=try DiagnosticReports(directory: URL(fileURLWithPath: "/unused-test-store"),policy: policy,destination: DiagnosticDestination(site: "localtest123",host: "http://127.0.0.1:1",appUrl: "https://app.test.invalid"),appVersion: "0.5.9-beta.2",transport: transport,storage: store,disabled: { false })
    let controller=AppDiagnostics(reporter: reports)
    await controller.load();await controller.setConsent(true)
    #expect(!controller.enabled);#expect(controller.errorKey=="diagnostics.save_failed")
    store.allowWrites();await controller.retryConsent()
    #expect(controller.enabled);#expect(controller.errorKey==nil)
    await controller.setConsent(false);await reports.close()
    #expect(transport.count==0)
}
#endif
