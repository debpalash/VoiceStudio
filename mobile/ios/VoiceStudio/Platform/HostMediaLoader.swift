import Foundation
@preconcurrency import AVFoundation
import UniformTypeIdentifiers
import VoiceStudioKit

/// Native AVPlayer byte-range loading with explicit PIN/session headers and the client's redirect policy.
final class HostMediaLoader: NSObject, AVAssetResourceLoaderDelegate, @unchecked Sendable {
    let queue = DispatchQueue(label: "com.voicestudio.mobile.media")
    let request: URLRequest
    let api: APIClient
    private var work: [ObjectIdentifier: Task<Void, Never>] = [:] // delegate queue only
    init(request: URLRequest, api: APIClient) { self.request = request; self.api = api }
    func cancel() {
        queue.async {
            for task in self.work.values { task.cancel() }
            self.work.removeAll()
        }
    }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource loading: AVAssetResourceLoadingRequest) -> Bool {
        let id = ObjectIdentifier(loading)
        var request = request
        let offset = loading.dataRequest?.currentOffset ?? 0
        let length = loading.dataRequest?.requestedLength ?? 2
        request.setValue("bytes=\(offset)-\(offset + Int64(max(1, length)) - 1)", forHTTPHeaderField: "Range")
        let boxed = LoadingRequest(loading)
        work[id] = Task { [api, request, queue] in
            do {
                let reply = try await api.send(request)
                try Task.checkCancellation()
                queue.async {
                    let loading = boxed.value
                    let contentLength = reply.headers["content-range"]?.split(separator: "/").last.flatMap { Int64($0) } ?? Int64(reply.data.count)
                    loading.contentInformationRequest?.contentType = UTType.wav.identifier
                    loading.contentInformationRequest?.contentLength = contentLength
                    loading.contentInformationRequest?.isByteRangeAccessSupported = reply.status == 206
                    let data: Data
                    if reply.status == 200, offset > 0 { data = Data(reply.data.dropFirst(Int(offset)).prefix(length)) } else { data = reply.data }
                    loading.dataRequest?.respond(with: data); loading.finishLoading()
                    self.work.removeValue(forKey: id)
                }
            } catch {
                queue.async { boxed.value.finishLoading(with: error); self.work.removeValue(forKey: id) }
            }
        }
        return true
    }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, didCancel loadingRequest: AVAssetResourceLoadingRequest) { work.removeValue(forKey: ObjectIdentifier(loadingRequest))?.cancel() }
    private final class LoadingRequest: @unchecked Sendable { let value: AVAssetResourceLoadingRequest; init(_ value: AVAssetResourceLoadingRequest) { self.value = value } }
}

