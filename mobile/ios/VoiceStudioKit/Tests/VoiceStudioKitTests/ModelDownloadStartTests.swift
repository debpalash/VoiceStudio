import Foundation
import Testing
@testable import VoiceStudioKit

@Test func cancelledBackgroundDownloadCannotStartLater() {
    let gate = ModelDownloadStartGate()
    gate.cancel {}
    var started = false
    #expect(!gate.start { started = true })
    #expect(!started)
}

@Test func backgroundCancellationWaitsForTaskStartToFinish() {
    let gate = ModelDownloadStartGate()
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    let cancelling = DispatchSemaphore(value: 0)
    let stopped = DispatchSemaphore(value: 0)
    let started = DispatchSemaphore(value: 0)
    DispatchQueue.global().async {
        _ = gate.start { entered.signal(); release.wait() }
        started.signal()
    }
    #expect(entered.wait(timeout: .now()+5) == .success)
    DispatchQueue.global().async {
        cancelling.signal()
        gate.cancel { stopped.signal() }
    }
    #expect(cancelling.wait(timeout: .now()+5) == .success)
    let prematureStop = stopped.wait(timeout: .now()+0.05)
    release.signal()
    #expect(prematureStop == .timedOut)
    #expect(stopped.wait(timeout: .now()+5) == .success)
    #expect(started.wait(timeout: .now()+5) == .success)
    #expect(!gate.start {})
}
