import Foundation
import Network
import Testing
@testable import VoiceStudioKit

private final class LifetimeProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    private var completion: Bool?
    func cancel() { lock.withLock { count += 1 } }
    func finish(_ cancelled: Bool) { lock.withLock { completion = cancelled } }
    var cancellations: Int { lock.withLock { count } }
    var finished: Bool? { lock.withLock { completion } }
}
@Test func phoneServerStopRejectsLateAdmissionAndNeverCancelsANewGeneration() throws {
    let life = PhoneServerLifetime(), probe = LifetimeProbe(), first = try life.begin()
    let initial = life.register(UUID(),generation: first,cancel: { probe.cancel() })
    #expect(initial)
    #expect(life.stop() == first); #expect(probe.cancellations == 1)
    // A receive callback queued before stop must cancel its new task rather than registering it.
    let late = life.register(UUID(),generation: first,cancel: { probe.cancel() })
    #expect(!late)
    #expect(probe.cancellations == 2)
    let second = try life.begin(), id = UUID()
    let replacement = life.register(id,generation: second,cancel: { probe.cancel() })
    #expect(replacement)
    #expect(life.stop(generation: first) == nil); #expect(life.isCurrent(second)); #expect(probe.cancellations == 2)
    life.remove(id); #expect(life.stop() == second); #expect(probe.cancellations == 2)
}
@Test func phoneServerCancelsStartupWithoutWaitingForANetworkStateCallback() async throws {
    let probe = LifetimeProbe(), (started,signal) = AsyncStream<Void>.makeStream()
    let server = try PhoneSpeechServer(model: "fixture") { _,_ in Issue.record("Canceled startup served speech"); return Data() }
    // Model a listener waiting for OS/network permission: it emits no ready/failed/cancelled state.
    server.listenerStarter = { _,_ in signal.yield(); signal.finish() }
    let task = Task {
        do { try await server.start(); probe.finish(false) }
        catch is CancellationError { probe.finish(true) }
        catch { probe.finish(false) }
    }
    for await _ in started { break }
    task.cancel()
    for _ in 0..<100 { if probe.finished != nil { break }; try await Task.sleep(for: .milliseconds(5)) }
    #expect(probe.finished == true)
    server.stop()
    if probe.finished != nil { await task.value }
}
