import Foundation

/// Keep URLSession completion policy independent of the app lifecycle.
public enum UploadCompletion: Sendable {
    case retryInForeground
    case cancelled
    case finished(UploadReceipt)

    public static func resolve(pending: PendingUpload, reply: HTTPReply, error: (any Error)?, authenticated: Bool, background: Bool) -> Self {
        if error is CancellationError || (error as? URLError)?.code == .cancelled { return .cancelled }
        if background, let code = (error as? URLError)?.code,
           [URLError.backgroundSessionInUseByAnotherProcess, .backgroundSessionWasDisconnected, .backgroundSessionRequiresSharedContainer].contains(code) {
            return .retryInForeground
        }
        do {
            if error != nil { throw ClientError("mobile.ios.upload_failed") }
            _ = try APIClient.validate(reply, authenticated: authenticated)
            let upload = try JSONDecoder().decode(DubUpload.self, from: reply.data)
            return .finished(UploadReceipt(pending: pending, upload: upload, failure: nil))
        } catch {
            return .finished(UploadReceipt(pending: pending, upload: nil, failure: error as? ClientError ?? ClientError("mobile.ios.upload_failed")))
        }
    }
}
