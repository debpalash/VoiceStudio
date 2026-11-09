import Foundation

/// One explicit Get action, with a separate acceptance boundary for every model.
public struct ModelDownloadQueue: Sendable {
    public enum Step: Sendable { case licence(PhoneModel), download(PhoneModel) }
    private var pending: [PhoneModel]
    public init(_ models: [PhoneModel]) {
        var seen = Set<String>()
        pending = models.filter { seen.insert($0.id).inserted }
    }
    public mutating func next(installed: Set<String>, accepted: Set<String>) -> Step? {
        while let model = pending.first {
            // Never consume this entry until the receipt has actually been saved.
            if model.licence.gated && !accepted.contains(model.id) { return .licence(model) }
            pending.removeFirst()
            if !installed.contains(model.id) { return .download(model) }
        }
        return nil
    }
}
