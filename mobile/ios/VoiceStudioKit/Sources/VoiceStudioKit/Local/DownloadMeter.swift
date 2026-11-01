import Foundation

public struct ModelDownloadProgress: Sendable {
    public var received: Int64
    public let total: Int64
    public var bytesPerSecond: Double = 0
    public var etaSeconds: Double?
    public var stage: String = "downloading"
    public var retrySeconds: Int = 0
    public init(received: Int64,total: Int64,bytesPerSecond: Double = 0,etaSeconds: Double? = nil,stage: String = "downloading",retrySeconds: Int = 0) {
        self.received = received; self.total = total; self.bytesPerSecond = bytesPerSecond
        self.etaSeconds = etaSeconds; self.stage = stage; self.retrySeconds = retrySeconds
    }
    public var fraction: Double { min(1, Double(received) / Double(max(1,total))) }
}

/// Persisted bytes are the baseline, never a burst of apparent network speed.
actor DownloadMeter {
    let total: Int64
    let clock: @Sendable () -> TimeInterval
    let publish: @Sendable (ModelDownloadProgress) async -> Void
    var ranges: [String: Int64] = [:]
    var sampledAt: TimeInterval
    var sampledBytes: Int64 = 0
    var speed = 0.0
    var publishedAt: TimeInterval = -.infinity
    var publishedStage = ""
    init(total: Int64, clock: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }, publish: @escaping @Sendable (ModelDownloadProgress) async -> Void) {
        self.total = total; self.clock = clock; self.publish = publish; sampledAt = clock()
    }
    func seed(_ key: String, bytes: Int64) { ranges[key] = bytes; sampledBytes = ranges.values.reduce(0,+); sampledAt = clock() }
    func replaceFile(_ key: String,rangePrefix: String,bytes: Int64) {
        ranges = ranges.filter { !$0.key.hasPrefix(rangePrefix) }
        ranges[key] = bytes
    }
    func update(_ key: String, bytes: Int64, stage: String = "downloading", retry: Int = 0) async {
        ranges[key] = max(ranges[key,default: 0],bytes)
        let received = min(total,ranges.values.reduce(0,+)), now = clock(), elapsed = now-sampledAt
        if elapsed >= 0.5 {
            let current = Double(max(0,received-sampledBytes))/elapsed
            speed = speed == 0 ? current : speed*0.65+current*0.35
            sampledAt = now; sampledBytes = received
        }
        if stage != "downloading" { speed = 0; sampledAt = now; sampledBytes = received }
        guard now-publishedAt >= 0.5 || publishedStage != stage || received == total else { return }
        publishedAt = now; publishedStage = stage
        await publish(ModelDownloadProgress(received: received,total: total,bytesPerSecond: speed,
            etaSeconds: speed > 0 && stage == "downloading" ? Double(total-received)/speed : nil,stage: stage,retrySeconds: retry))
    }
}

public struct ModelRangeUnsupported: Error, Sendable { public init() {} }
