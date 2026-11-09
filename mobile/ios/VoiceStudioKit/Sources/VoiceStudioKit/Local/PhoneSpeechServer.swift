import Foundation
import Network
import Security
import CoreFoundation

/// Serial admission remains closed after stop, even when Network delivers a queued callback.
internal final class PhoneServerLifetime: @unchecked Sendable {
    private let lock = NSLock()
    private var serial: UInt64 = 0
    private var active: UInt64?
    private var cancellations: [UUID: @Sendable () -> Void] = [:]
    func begin() throws -> UInt64 { try lock.withLock {
        guard active == nil else { throw ClientError("mobile.errors.file") }; serial &+= 1; active = serial; return serial
    } }
    func isCurrent(_ generation: UInt64) -> Bool { lock.withLock { active == generation } }
    @discardableResult func register(_ id: UUID,generation: UInt64,cancel: @escaping @Sendable () -> Void) -> Bool {
        let accepted = lock.withLock { guard active == generation else { return false }; cancellations[id] = cancel; return true }
        if !accepted { cancel() }; return accepted
    }
    func remove(_ id: UUID) { lock.withLock { cancellations[id] = nil } }
    @discardableResult func stop(generation: UInt64? = nil) -> UInt64? {
        let stopped: (UInt64,[@Sendable () -> Void])? = lock.withLock {
            guard let current = active, generation == nil || generation == current else { return nil }
            active = nil; let callbacks = Array(cancellations.values); cancellations.removeAll(); return (current,callbacks)
        }
        stopped?.1.forEach { $0() }; return stopped?.0
    }
}
/// Small foreground server; stop invalidates admission and cancels requests before returning.
public final class PhoneSpeechServer: @unchecked Sendable {
    public typealias Provider = @Sendable (String, Float) async throws -> Data
    public let token: String
    private let queue = DispatchQueue(label: "voicestudio.phone.http")
    private let router: SpeechRouter
    private let lifetime = PhoneServerLifetime()
    private var listener: (generation: UInt64,value: NWListener)?
    private var connections: [UUID: (generation: UInt64,value: NWConnection)] = [:]
    private var tasks: [UUID: (generation: UInt64,value: Task<Void,Never>)] = [:]
    private var configuration: Task<Void,Never>?
    internal var listenerStarter: (@Sendable (NWListener,DispatchQueue) -> Void)?
    public private(set) var port: UInt16 = 0
    public private(set) var addresses: [String] = []
    public init(model: String, capabilities: PhoneServerCapabilities = .init(), provider: @escaping Provider) throws {
        var bytes = [UInt8](repeating: 0,count: 24)
        guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes) == errSecSuccess else { throw ClientError("mobile.errors.file") }
        token = bytes.map { String(format: "%02x",$0) }.joined()
        router = SpeechRouter(model: model,token: token,capabilities: capabilities,provider: provider)
    }
    public func start(lan: Bool = false) async throws {
        let generation = try lifetime.begin()
        do {
            try await withTaskCancellationHandler {
                try Task.checkCancellation()
                let parameters = NWParameters.tcp
                parameters.requiredLocalEndpoint = .hostPort(host: lan ? "0.0.0.0" : "127.0.0.1",port: .any)
                let listener = try NWListener(using: parameters), hosts = Self.localHosts(lan: lan)
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void,Error>) in
                    let start = SpeechListenerStart(continuation)
                    let listenerID = UUID()
                    guard lifetime.register(listenerID,generation: generation,cancel: { listener.cancel(); start.finish(.failure(CancellationError())) }) else { return }
                    queue.async { [self] in
                        guard lifetime.isCurrent(generation) else { listener.cancel(); start.finish(.failure(CancellationError())); return }
                        self.listener?.value.stateUpdateHandler = nil; self.listener?.value.newConnectionHandler = nil
                        self.listener?.value.cancel(); self.listener = (generation,listener)
                        listener.stateUpdateHandler = { [self] state in
                            switch state {
                            case .ready:
                                guard lifetime.isCurrent(generation), let bound = listener.port else { return }
                                self.port = bound.rawValue
                                self.addresses = hosts.filter { $0 != "localhost" }.sorted().map { "http://\($0):\(bound.rawValue)" }
                                let previous = configuration
                                configuration = Task {
                                    if let previous { await previous.value }
                                    guard lifetime.isCurrent(generation) else { start.finish(.failure(CancellationError())); return }
                                    await router.configure(hosts: hosts,port: bound.rawValue)
                                    guard lifetime.isCurrent(generation) else { start.finish(.failure(CancellationError())); return }
                                    start.finish(.success(()))
                                }
                            case .failed(let error): start.finish(.failure(error)); self.stop(generation: generation)
                            case .cancelled: start.finish(.failure(CancellationError()))
                            default: break
                            }
                        }
                        listener.newConnectionHandler = { [weak self] connection in
                            guard let self else { connection.cancel(); return }; self.accept(connection,generation: generation)
                        }
                        if let listenerStarter { listenerStarter(listener,queue) } else { listener.start(queue: queue) }
                    }
                }
            } onCancel: { self.stop(generation: generation) }
        } catch { stop(generation: generation); throw error }
    }
    public func stop() { stop(generation: nil) }
    private func stop(generation: UInt64?) {
        guard let stopped = lifetime.stop(generation: generation) else { return }
        queue.async { [self] in
            if listener?.generation == stopped { listener?.value.stateUpdateHandler = nil; listener?.value.newConnectionHandler = nil; listener?.value.cancel(); listener = nil }
            for id in Array(connections.keys) where connections[id]?.generation == stopped { connections.removeValue(forKey: id)?.value.cancel() }
            for id in Array(tasks.keys) where tasks[id]?.generation == stopped { tasks.removeValue(forKey: id)?.value.cancel() }
        }
    }
    private func accept(_ connection: NWConnection,generation: UInt64) {
        guard lifetime.isCurrent(generation), connections.count < 8 else { connection.cancel(); return }
        let id = UUID()
        guard lifetime.register(id,generation: generation,cancel: { connection.cancel() }) else { return }
        connections[id] = (generation,connection); connection.start(queue: queue)
        let session = SpeechConnection(connection: connection)
        session.receive { [weak self] request in
            guard let self, self.lifetime.isCurrent(generation), self.connections[id]?.value === connection else { connection.cancel(); return }
            // Admission is published before the task can reach a provider. Stop also cancels tasks
            // that have been allocated but have not yet entered their body.
            let (admission,permit) = AsyncStream<Void>.makeStream()
            let task = Task {
                for await _ in admission { break }
                guard !Task.isCancelled, self.lifetime.isCurrent(generation) else { return }
                let response = if let request { await self.router.respond(request) } else { SpeechReply(status: 400) }
                self.queue.async { [self] in
                    guard lifetime.isCurrent(generation), connections[id]?.value === connection else { connection.cancel(); return }
                    connection.send(content: response.wire,completion: .contentProcessed { [weak self] _ in
                        connection.cancel(); self?.connections.removeValue(forKey: id); self?.tasks.removeValue(forKey: id); self?.lifetime.remove(id)
                    })
                }
            }
            if self.lifetime.register(id,generation: generation,cancel: { task.cancel(); connection.cancel() }) { self.tasks[id] = (generation,task) }
            permit.yield(); permit.finish()
        }
    }
    private static func localHosts(lan: Bool) -> Set<String> {
        var result: Set<String> = ["127.0.0.1","localhost"]
        guard lan else { return result }
        var pointer: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&pointer) == 0 else { return result }
        defer { freeifaddrs(pointer) }
        var current = pointer
        while let entry = current {
            defer { current = entry.pointee.ifa_next }
            guard let address = entry.pointee.ifa_addr, address.pointee.sa_family == UInt8(AF_INET) else { continue }
            var buffer = [CChar](repeating: 0,count: Int(NI_MAXHOST))
            if getnameinfo(address,socklen_t(address.pointee.sa_len),&buffer,socklen_t(buffer.count),nil,0,NI_NUMERICHOST) == 0 { result.insert(String(cString: buffer)) }
        }
        return result
    }
}
public struct SpeechRequest: Sendable {
    public let method: String, path: String
    public let headers: [String:String]
    public let body: Data
    public init(method: String,path: String,headers: [String:String],body: Data = Data()) { self.method = method; self.path = path; self.headers = headers; self.body = body }
    /// Nil means more bytes are needed. Invalid framing never reaches the router.
    public static func parse(_ data: Data) throws -> SpeechRequest? {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else { guard data.count < 16384 else { throw ClientError("mobile.errors.invalid_response") }; return nil }
        guard end.upperBound <= 16384, let text = String(data: data[..<end.lowerBound],encoding: .ascii) else { throw ClientError("mobile.errors.invalid_response") }
        let lines = text.components(separatedBy: "\r\n"), first = lines[0].split(separator: " ",omittingEmptySubsequences: false)
        guard first.count == 3, first[2] == "HTTP/1.1", first[1].hasPrefix("/"), first[1].count <= 2048, !first[1].contains("#"), lines.count <= 65 else { throw ClientError("mobile.errors.invalid_response") }
        var headers: [String:String] = [:]
        for line in lines.dropFirst() {
            guard let index = line.firstIndex(of: ":") else { throw ClientError("mobile.errors.invalid_response") }
            let key = String(line[..<index]).lowercased(), value = line[line.index(after: index)...].trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty, key.utf8.allSatisfy({ (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }), headers[key] == nil, value.utf8.allSatisfy({ (32...126).contains($0) }) else { throw ClientError("mobile.errors.invalid_response") }
            headers[key] = value
        }
        guard headers["transfer-encoding"] == nil else { throw ClientError("mobile.errors.invalid_response") }
        let rawLength = headers["content-length"] ?? "0"
        guard !rawLength.isEmpty, rawLength.count <= 7, rawLength.utf8.allSatisfy({ (48...57).contains($0) }), let length = Int(rawLength), length <= PhoneServerAudio.maxRequestBytes, first[0] != "POST" || headers["content-length"] != nil else { throw ClientError("mobile.errors.invalid_response") }
        let size = end.upperBound+length
        guard data.count <= size else { throw ClientError("mobile.errors.invalid_response") }
        guard data.count == size else { return nil }
        return SpeechRequest(method: String(first[0]),path: String(first[1]),headers: headers,body: Data(data[end.upperBound...]))
    }
}
public struct SpeechReply: Sendable {
    public let status: Int
    public let body: Data
    public let type: String
    public init(status: Int,body: Data = Data(),type: String = "application/json") { self.status = status; self.body = body; self.type = type }
    public var wire: Data { Data("HTTP/1.1 \(status) Response\r\nContent-Type: \(type)\r\nContent-Length: \(body.count)\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n".utf8)+body }
}
private final class SpeechListenerStart: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void,Error>?
    init(_ continuation: CheckedContinuation<Void,Error>) { self.continuation = continuation }
    func finish(_ result: Result<Void,Error>) {
        let pending = lock.withLock { let value = continuation; continuation = nil; return value }
        pending?.resume(with: result)
    }
}
private final class SpeechConnection: @unchecked Sendable {
    let connection: NWConnection
    var bytes = Data()
    var done = false
    var timeout: DispatchWorkItem?
    init(connection: NWConnection) { self.connection = connection }
    func receive(_ completion: @escaping @Sendable (SpeechRequest?)->Void) {
        let timeout = DispatchWorkItem { [weak self] in self?.connection.cancel() }
        self.timeout = timeout
        DispatchQueue.global().asyncAfter(deadline: .now()+10,execute: timeout)
        read(completion)
    }
    private func read(_ completion: @escaping @Sendable (SpeechRequest?)->Void) {
        connection.receive(minimumIncompleteLength: 1,maximumLength: 8192) { [self] content,_,closed,error in
            guard !done else { return }
            if let content { bytes.append(content) }
            do {
                if let request = try SpeechRequest.parse(bytes) { done = true; timeout?.cancel(); completion(request) }
                else if closed || error != nil { done = true; timeout?.cancel(); completion(nil) }
                else { read(completion) }
            } catch { done = true; timeout?.cancel(); completion(nil) }
        }
    }
}
public actor SpeechRouter {
    let model: String, token: String, provider: PhoneSpeechServer.Provider, capabilities: PhoneServerCapabilities
    var hosts: Set<String> = [], port: UInt16 = 0, busy = false
    var activeRequest: (String, Task<Data,Error>)?
    public init(model: String,token: String,capabilities: PhoneServerCapabilities = .init(),provider: @escaping PhoneSpeechServer.Provider) {
        self.model = model; self.token = token; self.capabilities = capabilities; self.provider = provider
    }
    public func configure(hosts: Set<String>,port: UInt16) { self.hosts = hosts; self.port = port }
    public func respond(_ request: SpeechRequest) async -> SpeechReply {
        let h = request.headers
        let expected = Array(("Bearer "+token).utf8), supplied = Array((h["authorization"] ?? "").utf8)
        var difference = expected.count ^ supplied.count
        for index in expected.indices { difference |= Int(expected[index] ^ (index < supplied.count ? supplied[index] : 0)) }
        guard difference == 0 else { return .init(status: 401) }
        let authorities = Set(hosts.map { "\($0):\(port)" })
        guard authorities.contains(h["host"] ?? ""), h["origin"] == nil || Set(authorities.map { "http://"+$0 }).contains(h["origin"]!) else { return .init(status: 403) }
        if request.method == "GET", request.path == "/health" { return json(["status":"ok","service":"voicestudio-phone"]) }
        if request.method == "GET", request.path == "/v1/models" {
            var models: [[String:String]] = []
            if capabilities.speech { models.append(["id":model,"object":"model","capability":"speech"]) }
            if capabilities.transcribe != nil, let id = capabilities.transcriptionModel { models.append(["id":id,"object":"model","capability":"transcription"]) }
            if capabilities.translate != nil, let id = capabilities.translationModel { models.append(["id":id,"object":"model","capability":"translation"]) }
            return json(["object":"list","data":models])
        }
        guard request.method == "POST" else { return .init(status: 405) }
        guard h["content-type"]?.components(separatedBy: ";")[0].trimmingCharacters(in: .whitespaces).lowercased() == "application/json" else { return .init(status: 415) }
        guard request.body.count <= PhoneServerAudio.maxRequestBytes, let body = (try? JSONSerialization.jsonObject(with: request.body)) as? [String:Any] else { return .init(status: 400) }
        if request.path == "/v1/audio/speech" {
            guard capabilities.speech else { return .init(status: 404) }
            guard Set(body.keys).isSubset(of: ["input","model","voice","response_format","speed"]), let text = body["input"] as? String, let speed = speed(body) else { return .init(status: 400) }
            if let requested = body["model"] { guard requested as? String == model else { return .init(status: 400) } }
            if let requested = body["voice"] { guard requested as? String == "default" else { return .init(status: 400) } }
            guard let format = format(body["response_format"]) else { return .init(status: 400) }
            return await speech(text,speed: speed,format: format)
        }
        if request.path == "/v1/audio/transcriptions" {
            guard capabilities.transcribe != nil else { return .init(status: 404) }
            guard Set(body.keys).isSubset(of: ["audio","model"]) else { return .init(status: 400) }
            if let requested = body["model"] { guard requested as? String == capabilities.transcriptionModel else { return .init(status: 400) } }
            return await transcribe(body["audio"] as? String)
        }
        if request.path == "/v1/text/translations" {
            guard capabilities.translate != nil else { return .init(status: 404) }
            guard Set(body.keys).isSubset(of: ["input","language","model"]) else { return .init(status: 400) }
            if let requested = body["model"] { guard requested as? String == capabilities.translationModel else { return .init(status: 400) } }
            return await translate(body["input"] as? String,language: body["language"] as? String)
        }
        guard request.path == "/mcp" else { return .init(status: 404) }
        guard h["mcp-protocol-version"] == nil || h["mcp-protocol-version"] == "2025-11-25" else { return .init(status: 400) }
        let id = body["id"] ?? NSNull()
        func failure(_ code: Int) -> SpeechReply { json(["jsonrpc":"2.0","id":id,"error":["code":code,"message":"Invalid request"]]) }
        guard body["jsonrpc"] as? String == "2.0", let method = body["method"] as? String else { return failure(-32600) }
        if body["id"] == nil {
            if method == "notifications/cancelled", let params = body["params"] as? [String:Any], let key = requestKey(params["requestId"]), activeRequest?.0 == key { activeRequest?.1.cancel() }
            return method.hasPrefix("notifications/") ? .init(status: 202) : failure(-32600)
        }
        guard id is String || ((id as? NSNumber).map { CFGetTypeID($0) != CFBooleanGetTypeID() && $0.doubleValue.isFinite && $0.doubleValue.rounded() == $0.doubleValue } ?? false) else { return failure(-32600) }
        var result: [String:Any]
        switch method {
        case "initialize":
            guard let params = body["params"] as? [String:Any], params["protocolVersion"] is String else { return failure(-32602) }
            result = ["protocolVersion":"2025-11-25","capabilities":["tools":[:]],"serverInfo":["name":"voicestudio-phone","version":"1"]]
        case "ping": result = [:]
        case "tools/list": result = ["tools":tools()]
        case "tools/call":
            guard let params = body["params"] as? [String:Any], let name = params["name"] as? String, let args = params["arguments"] as? [String:Any] else { return failure(-32602) }
            let requestID = requestKey(id)
            let response: SpeechReply
            switch name {
            case "speak" where capabilities.speech:
                guard Set(args.keys).isSubset(of: ["text","speed","format"]), let text = args["text"] as? String, let speed = speed(args), let format = format(args["format"]) else { return failure(-32602) }
                response = await speech(text,speed: speed,format: format,requestID: requestID)
            case "transcribe" where capabilities.transcribe != nil:
                guard Set(args.keys) == ["audio"] else { return failure(-32602) }
                response = await transcribe(args["audio"] as? String,requestID: requestID)
            case "translate" where capabilities.translate != nil:
                guard Set(args.keys) == ["text","language"] else { return failure(-32602) }
                response = await translate(args["text"] as? String,language: args["language"] as? String,requestID: requestID)
            default: return failure(-32602)
            }
            guard response.status == 200 else { return failure(response.status == 400 ? -32602 : response.status == 429 ? -32000 : -32603) }
            if response.type.hasPrefix("audio/") { result = ["content":[["type":"audio","mimeType":response.type,"data":response.body.base64EncodedString()]]] }
            else {
                guard let output = (try? JSONDecoder().decode(TextOutput.self,from: response.body)) else { return failure(-32603) }
                result = ["content":[["type":"text","text":output.text]]]
            }
        default: return failure(-32601)
        }
        return json(["jsonrpc":"2.0","id":id,"result":result])
    }
    private struct TextOutput: Codable,Sendable { let text: String }
    private func tools() -> [[String:Any]] {
        var tools: [[String:Any]] = []
        let text: [String:Any] = ["type":"string","minLength":1,"maxLength":2000]
        if capabilities.speech {
            let formats = capabilities.encodeMP3 == nil ? ["wav"] : ["wav","mp3"]
            tools.append(["name":"speak","description":"Generate speech with the selected installed phone voice.","inputSchema":["type":"object","properties":["text":text,"speed":["type":"number","minimum":0.5,"maximum":2],"format":["type":"string","enum":formats]],"required":["text"],"additionalProperties":false]])
        }
        if capabilities.transcribe != nil {
            tools.append(["name":"transcribe","description":"Transcribe base64 PCM16 mono WAV (8–24 kHz, up to 30 seconds) using the enabled installed model.","inputSchema":["type":"object","properties":["audio":["type":"string","maxLength":2000000]],"required":["audio"],"additionalProperties":false]])
        }
        if capabilities.translate != nil {
            tools.append(["name":"translate","description":"Translate text using the enabled installed phone model.","inputSchema":["type":"object","properties":["text":text,"language":["type":"string","enum":capabilities.translationLanguages]],"required":["text","language"],"additionalProperties":false]])
        }
        return tools
    }
    private func speed(_ value: [String:Any]) -> Float? {
        guard let raw = value["speed"] else { return 1 }
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let speed = number.floatValue; return speed.isFinite && (0.5...2).contains(speed) ? speed : nil
    }
    private func format(_ value: Any?) -> String? {
        guard let value else { return "wav" }
        guard let string = value as? String else { return nil }
        if string == "wav" { return string }
        return string == "mp3" && capabilities.encodeMP3 != nil ? string : nil
    }
    private func requestKey(_ value: Any?) -> String? {
        if let string = value as? String { return "s:"+string }
        if let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() { return "n:"+number.stringValue }
        return nil
    }
    private func speech(_ text: String,speed: Float,format: String,requestID: String? = nil) async -> SpeechReply {
        guard valid(text) else { return .init(status: 400) }
        let provider = provider, encoder = capabilities.encodeMP3
        return await perform(type: format == "mp3" ? "audio/mpeg" : "audio/wav",requestID: requestID) {
            let wave = try await provider(text,speed)
            if format == "mp3", let encoder { return try await encoder(wave) }
            return wave
        }
    }
    private func transcribe(_ base64: String?,requestID: String? = nil) async -> SpeechReply {
        guard let provider = capabilities.transcribe, let base64, base64.utf8.count <= 2_000_000, let wave = Data(base64Encoded: base64), let pcm = try? PhoneServerAudio.decodeWave(wave) else { return .init(status: 400) }
        return await perform(requestID: requestID) { try JSONEncoder().encode(TextOutput(text: try await provider(pcm))) }
    }
    private func translate(_ text: String?,language: String?,requestID: String? = nil) async -> SpeechReply {
        guard let provider = capabilities.translate, let text, valid(text), let language, let selected = capabilities.translationLanguages.first(where: { $0.caseInsensitiveCompare(language) == .orderedSame }) else { return .init(status: 400) }
        return await perform(requestID: requestID) { try JSONEncoder().encode(TextOutput(text: try await provider(text,selected))) }
    }
    private func valid(_ text: String) -> Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.utf16.count <= 2000 }
    private func perform(type: String = "application/json",requestID: String?,operation: @escaping @Sendable () async throws -> Data) async -> SpeechReply {
        guard !busy else { return .init(status: 429) }; busy = true; defer { busy = false }
        let work = Task { try await operation() }
        if let requestID { activeRequest = (requestID,work) }
        defer { activeRequest = nil }
        do {
            let data = try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
            try Task.checkCancellation(); return .init(status: 200,body: data,type: type)
        } catch { return .init(status: 500) }
    }
    private func json(_ value: [String:Any]) -> SpeechReply { .init(status: 200,body: (try? JSONSerialization.data(withJSONObject: value,options: [.sortedKeys])) ?? Data()) }
}
