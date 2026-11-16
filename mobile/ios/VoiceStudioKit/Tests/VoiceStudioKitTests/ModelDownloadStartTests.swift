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
    // These workers deliberately block on a lock/semaphore. Give them owned
    // threads so parallel tests cannot starve the shared GCD worker pool and
    // turn a dispatch delay into a failure of the ordering being verified.
    Thread.detachNewThread {
        _ = gate.start { entered.signal(); _ = release.wait(timeout: .now()+30) }
        started.signal()
    }
    #expect(entered.wait(timeout: .now()+5) == .success)
    Thread.detachNewThread {
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

@Test func backgroundCancellationCleanupRunsOnce() {
    let gate = ModelDownloadStartGate()
    var stops = 0
    gate.cancel { stops += 1 }
    gate.cancel { stops += 1 }
    #expect(stops == 1)
}
