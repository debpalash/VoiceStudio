#if os(macOS)
import Foundation

/// A child process for tests in which every wait has a deadline.
///
/// `Process.waitUntilExit()` blocks in the run loop until it sees the exit. On a Swift
/// concurrency thread that wakeup can be lost: the child is gone, but the test blocks
/// forever (this hung CI's VoiceStudioKit step until its 15 minute limit). Exit is
/// therefore observed through `terminationHandler`, and output is read without blocking.
final class TestProcess: @unchecked Sendable {
    struct Failure: Error, CustomStringConvertible { let description: String }

    let process = Process()
    let output = TestStream(), errors = TestStream()
    private let exitSignal = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var exited = false, launched = false
    private let label: String

    init(_ arguments: [String]) throws {
        label = arguments.first == "python3" && arguments.contains("-c") ? "python3 -c" : arguments.prefix(2).joined(separator: " ")
        let out = Pipe(), err = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = out
        process.standardError = err
        process.terminationHandler = { [exitSignal] _ in exitSignal.signal() }
        output.drain(out); errors.drain(err)
        try process.run()
        lock.withLock { launched = true }
    }
    deinit { stop() }

    /// Whether the child exited within `seconds`.
    func waitForExit(seconds: Double) -> Bool {
        if lock.withLock({ exited }) { return true }
        guard exitSignal.wait(timeout: .now() + seconds) == .success else { return false }
        // The pipes drain on their own handlers; give them a moment to deliver the last
        // bytes so diagnostics are complete, but never wait on them unbounded.
        let drained = Date(timeIntervalSinceNow: 2)
        output.waitForClose(until: drained); errors.waitForClose(until: drained)
        lock.withLock { exited = true }
        return true
    }
    /// Runs to completion, or fails with the child's output instead of waiting forever.
    func finish(within seconds: Double) throws -> Int32 {
        guard waitForExit(seconds: seconds) else {
            stop()
            throw Failure(description: "\(label) did not exit within \(Int(seconds)) s\(diagnostics)")
        }
        return process.terminationStatus
    }
    /// The port a loopback host prints on its first stdout line.
    func port(within seconds: Double = 30) throws -> Int {
        if let line = output.firstLine(within: seconds), let port = Int(line.trimmingCharacters(in: .whitespaces)) { return port }
        stop()
        throw Failure(description: "\(label) printed no port within \(Int(seconds)) s\(diagnostics)")
    }
    /// SIGTERM, then SIGKILL; never blocks for more than about ten seconds.
    func stop() {
        guard lock.withLock({ launched && !exited }) else { return }
        if process.isRunning { process.terminate() }
        if waitForExit(seconds: 5) { return }
        kill(process.processIdentifier, SIGKILL)
        _ = waitForExit(seconds: 5)
    }
    var diagnostics: String {
        let state = lock.withLock { exited } ? "exit status \(process.terminationStatus)" : "still running"
        return " (\(state)); stdout: \(output.text.suffix(2000)); stderr: \(errors.text.suffix(4000))"
    }
}

/// Collects a pipe on a background handler so the child never blocks on a full pipe
/// and the test never blocks on a read.
final class TestStream: @unchecked Sendable {
    private let condition = NSCondition()
    private var data = Data()
    private var closed = false

    func drain(_ pipe: Pipe) {
        pipe.fileHandleForReading.readabilityHandler = { [self] handle in
            let chunk = handle.availableData
            condition.lock()
            if chunk.isEmpty { closed = true; handle.readabilityHandler = nil } else { data.append(chunk) }
            condition.broadcast()
            condition.unlock()
        }
    }
    /// The first line, or nil when the stream closes or the deadline passes first.
    func firstLine(within seconds: Double) -> String? {
        let deadline = Date(timeIntervalSinceNow: seconds)
        condition.lock(); defer { condition.unlock() }
        while true {
            if let end = data.firstIndex(of: 10) { return String(decoding: data[..<end], as: UTF8.self) }
            if closed || !condition.wait(until: deadline) { return nil }
        }
    }
    func waitForClose(until deadline: Date) {
        condition.lock(); defer { condition.unlock() }
        while !closed, condition.wait(until: deadline) {}
    }
    var text: String { condition.lock(); defer { condition.unlock() }; return String(decoding: data, as: UTF8.self) }
}
#endif
