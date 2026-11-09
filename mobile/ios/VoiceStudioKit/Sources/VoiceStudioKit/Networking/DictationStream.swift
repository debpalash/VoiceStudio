import Foundation

/// Small transport seam for replaying the host protocol without microphone or sockets.
public protocol DictationSocket: Sendable {
    var closeCode: URLSessionWebSocketTask.CloseCode { get }
    func resume()
    func receive() async throws -> URLSessionWebSocketTask.Message
    func send(_ message: URLSessionWebSocketTask.Message) async throws
    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?)
}
extension URLSessionWebSocketTask: DictationSocket {}

public actor DictationStream {
    private var socket: (any DictationSocket)?
    private var receiver: Task<Void, Never>?
    private var revision = UUID()
    private let makeSocket: @Sendable (URLRequest) -> any DictationSocket
    public init(socketFactory: (@Sendable (URLRequest) -> any DictationSocket)? = nil) {
        let c = URLSessionConfiguration.ephemeral
        c.httpCookieStorage = nil; c.urlCredentialStorage = nil
        let session = URLSession(configuration: c, delegate: HostSessionDelegate(), delegateQueue: nil)
        makeSocket = socketFactory ?? { session.webSocketTask(with: $0) }
    }
    public func start(api: APIClient) async throws -> AsyncThrowingStream<DictationMessage, any Error> {
        close()
        let revision = self.revision
        let request = try await api.dictationRequest()
        try Task.checkCancellation()
        guard revision == self.revision else { throw CancellationError() }
        let socket = makeSocket(request)
        self.socket = socket; socket.resume()
        let (stream, continuation) = AsyncThrowingStream<DictationMessage, any Error>.makeStream(bufferingPolicy: .bufferingOldest(64))
        receiver = Task {
            do {
                while !Task.isCancelled {
                    let message = try await socket.receive()
                    try Task.checkCancellation()
                    let bytes: Data
                    switch message {
                    case .string(let text): bytes = Data(text.utf8)
                    case .data(let data): bytes = data
                    @unknown default: continue
                    }
                    let decoded = try JSONDecoder().decode(DictationMessage.self, from: bytes)
                    if decoded.type == "error" { throw ClientError("mobile.errors.dictation") }
                    continuation.yield(decoded)
                    if decoded.isSessionFinal { continuation.finish(); return }
                }
                continuation.finish()
            } catch {
                if socket.closeCode.rawValue == 1008 { continuation.finish(throwing: ClientError("mobile.errors.forbidden")) }
                else if !Task.isCancelled { continuation.finish(throwing: ClientError("mobile.errors.dictation")) }
                else { continuation.finish() }
            }
        }
        continuation.onTermination = { @Sendable [weak self] _ in Task { await self?.close(revision: revision) } }
        return stream
    }
    public func send(_ pcm: Data) async throws { guard let socket else { throw ClientError("mobile.errors.dictation") }; try await socket.send(.data(pcm)) }
    public func finish() async throws { try await socket?.send(.string("EOF")) }
    private func close(revision: UUID) { if self.revision == revision { close() } }
    public func close() { revision = UUID(); receiver?.cancel(); receiver = nil; socket?.cancel(with: .normalClosure, reason: nil); socket = nil }
}
