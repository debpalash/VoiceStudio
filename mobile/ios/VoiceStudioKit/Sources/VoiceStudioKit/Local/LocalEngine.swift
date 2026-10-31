import Foundation
import VSNative

public struct PCMChunk: Sendable {
    public let samples: [Float]
    public let sampleRate: Int
    public init(samples: [Float], sampleRate: Int) { self.samples = samples; self.sampleRate = sampleRate }
    public init(pcm16: Data, sampleRate: Int = 16000) throws {
        guard pcm16.count.isMultiple(of: 2) else { throw ClientError("mobile.errors.audio") }
        self.sampleRate = sampleRate
        samples = pcm16.withUnsafeBytes { bytes in
            stride(from: 0,to: bytes.count,by: 2).map { index in
                Float(Int16(bitPattern: UInt16(bytes[index]) | UInt16(bytes[index + 1]) << 8)) / 32768
            }
        }
    }
}
public struct LocalAudio: Sendable {
    public let samples: [Float]
    public let sampleRate: Int
    public init(samples: [Float], sampleRate: Int) { self.samples = samples; self.sampleRate = sampleRate }
    /// All locally generated speech passes this synthetic-audio marking chokepoint.
    public func markSynthetic() throws -> Data {
        guard sampleRate > 0, sampleRate < 384001, samples.count < Int(UInt32.max) / 2 - 128 else { throw ClientError("mobile.errors.audio") }
        var result = Data()
        func ascii(_ text: String) { result.append(contentsOf: text.utf8) }
        func u16(_ v: UInt16) { var x = v.littleEndian; withUnsafeBytes(of: &x) { result.append(contentsOf: $0) } }
        func u32(_ v: UInt32) { var x = v.littleEndian; withUnsafeBytes(of: &x) { result.append(contentsOf: $0) } }
        let tag = Data("VoiceStudio synthetic audio\0".utf8)
        let padded = tag.count + tag.count % 2
        ascii("RIFF"); u32(UInt32(36 + samples.count * 2 + 20 + padded)); ascii("WAVEfmt "); u32(16); u16(1); u16(1)
        u32(UInt32(sampleRate)); u32(UInt32(sampleRate * 2)); u16(2); u16(16)
        ascii("LIST"); u32(UInt32(12 + padded)); ascii("INFOICMT"); u32(UInt32(tag.count)); result.append(tag); if tag.count % 2 != 0 { result.append(0) }
        ascii("data"); u32(UInt32(samples.count * 2))
        for sample in samples { u16(UInt16(bitPattern: Int16((min(1, max(-1, sample.isFinite ? sample : 0)) * 32767).rounded()))) }
        return result
    }
}
public struct EngineProgress: Sendable {
    public enum Stage: String, Sendable { case loading, running, finished }
    public let stage: Stage
    public let fraction: Double
    public init(stage: Stage, fraction: Double) { self.stage = stage; self.fraction = fraction }
}
public typealias EngineProgressHandler = @Sendable (EngineProgress) async -> Void
public struct TranscriptUpdate: Sendable {
    public let text: String
    public let isFinal: Bool
    public let finished: Bool
}
public struct SpeechSegment: Codable, Sendable, Equatable {
    public let start: Double
    public let end: Double
    public let text: String
    public init(start: Double,end: Double,text: String) { self.start = start; self.end = end; self.text = text }
}
/// Pause that starts a new utterance; shorter after sentence-final punctuation.
let utteranceGap = 1.0, sentenceGap = 0.4
/// One 20 s recognition window can hold several sentences with long pauses. Dubbing speaks each segment
/// at its start, so later sentences would play early; split at pauses so each keeps its own timing.
/// `ends[i] <= timestamps[i]` means token i has no duration. Falls back to one segment when tokens and
/// timestamps do not line up.
func splitUtterances(text: String,tokens: [String],timestamps: [Double],ends: [Double],window: Double) -> [SpeechSegment] {
    let ok = !tokens.isEmpty && tokens.count == timestamps.count && timestamps.allSatisfy { $0.isFinite && $0 >= 0 && $0 < window }
    let valid = timestamps.filter { $0.isFinite && $0 >= 0 && $0 < window }
    func tail(_ i: Int) -> Double { i < ends.count && ends.count == timestamps.count && ends[i].isFinite && ends[i] > timestamps[i] ? ends[i]-timestamps[i] : 0.5 }
    func single() -> [SpeechSegment] {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return [] }
        let first = min(max(valid.min() ?? 0,0),max(0,window-0.02))
        let end = valid.max().map { min(window,max(first+0.01,$0+(ok ? tail(timestamps.count-1) : 0.5))) } ?? window
        return [SpeechSegment(start: first,end: end,text: clean)]
    }
    guard ok else { return single() }
    func endOf(_ i: Int) -> Double { min(window,timestamps[i]+tail(i)) }
    var groups: [[Int]] = [[0]]
    for i in 1..<max(1,tokens.count) {
        let gap = timestamps[i]-endOf(i-1)
        let sentenceEnd = tokens[i-1].trimmingCharacters(in: .whitespaces).last.map { ".!?\u{3002}\u{FF01}\u{FF1F}".contains($0) } ?? false
        if gap >= utteranceGap || (sentenceEnd && gap >= sentenceGap) { groups.append([i]) } else { groups[groups.count-1].append(i) }
    }
    var pieces: [SpeechSegment] = []
    for (n,group) in groups.enumerated() {
        let piece = group.map { tokens[$0] }.joined().trimmingCharacters(in: .whitespacesAndNewlines)
        if piece.isEmpty { continue }
        let start = timestamps[group[0]]
        let nextStart = n+1 < groups.count ? timestamps[groups[n+1][0]] : window
        pieces.append(SpeechSegment(start: start,end: min(window,min(max(endOf(group[group.count-1]),start+0.01),max(start+0.01,nextStart))),text: piece))
    }
    return pieces.count <= 1 ? single() : pieces
}
public protocol LocalEngine: Sendable {
    func design(model: PhoneModel, text: String, instruction: String, seed: UInt64, progress: @escaping EngineProgressHandler) async throws -> LocalAudio
    func synthesize(model: PhoneModel, text: String, speaker: Int, speed: Float, progress: @escaping EngineProgressHandler) async throws -> LocalAudio
    func clone(model: PhoneModel, text: String, reference: PCMChunk, referenceText: String, progress: @escaping EngineProgressHandler) async throws -> LocalAudio
    func transcribe(model: PhoneModel, audio: PCMChunk, progress: @escaping EngineProgressHandler) async throws -> String
    func transcribeSegments(model: PhoneModel, audio: PCMChunk, progress: @escaping EngineProgressHandler) async throws -> [SpeechSegment]
    func streamTranscribe(model: PhoneModel, chunks: AsyncThrowingStream<PCMChunk, any Error>, progress: @escaping EngineProgressHandler) async throws -> AsyncThrowingStream<TranscriptUpdate, any Error>
    func translate(model: PhoneModel, text: String, sourceLanguage: String, targetLanguage: String, progress: @escaping EngineProgressHandler) async throws -> String
}
public extension LocalEngine {
    func design(model: PhoneModel, text: String, instruction: String, seed: UInt64, progress: @escaping EngineProgressHandler) async throws -> LocalAudio { throw ClientError("mobile.local.model_incompatible") }
    func transcribeSegments(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler = { _ in }) async throws -> [SpeechSegment] {
        let text = try await transcribe(model: model,audio: audio,progress: progress).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? [] : [SpeechSegment(start: 0,end: Double(audio.samples.count)/Double(audio.sampleRate),text: text)]
    }
}
/// One native inference at a time per process, whichever engine instance asks (Android
/// uses the same rule). Concurrent sherpa/OmniVoice/llama jobs exceed phone memory, and
/// running OmniVoice and llama together crashed the process (SIGBUS) in a real-model run.
actor NativeLease {
    static let shared = NativeLease()
    private var busy = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func acquire() async throws {
        if busy { await withCheckedContinuation { waiters.append($0) } } else { busy = true }
        // Hand the lease straight back if the caller was cancelled while waiting.
        if Task.isCancelled { release(); throw CancellationError() }
    }
    func release() {
        if waiters.isEmpty { busy = false } else { waiters.removeFirst().resume() }
    }
}
/// C allocations never escape the worker. Only the atomic cancellation flag crosses tasks.
final class NativeCancellation: @unchecked Sendable {
    let pointer: OpaquePointer
    private let lock = NSLock()
    private var finished = false
    init() throws { guard let p = vs_cancel_create() else { throw ClientError("mobile.errors.audio") }; pointer = p }
    deinit { vs_cancel_free(pointer) }
    func cancel() { vs_cancel_set(pointer) }
    func finish() { lock.withLock { finished = true } }
    var done: Bool { lock.withLock { finished } }
}
private final class NativeConfiguration {
    var value = VSConfig()
    private var strings: [UnsafeMutablePointer<CChar>] = []
    init(model: PhoneModel, root: URL, library: String) {
        func c(_ text: String) -> UnsafePointer<CChar> { let p = strdup(text)!; strings.append(p); return UnsafePointer(p) }
        value.library = c(library)
        value.family = c(model.option("model_type", fallback: model.option("family", fallback: model.id.contains("kitten") ? "kitten" : model.id.contains("kokoro") ? "kokoro" : "whisper")))
        value.model = c(model.path(role: "model", root: root)); value.encoder = c(model.path(role: "encoder", root: root))
        value.decoder = c(model.path(role: "decoder", root: root)); value.joiner = c(model.path(role: "joiner", root: root))
        value.tokens = c(model.path(role: "tokens", root: root)); value.voices = c(model.path(role: "voices", root: root))
        let data = model.option("data_dir", fallback: model.option("espeak_data_dir", fallback: "espeak-ng-data"))
        value.data_dir = c(root.appendingPathComponent(data).path)
        func paths(_ key: String, role: String) -> String {
            let option = model.option(key)
            let names = option.isEmpty ? model.files.filter { $0.role == role }.map(\.path) : option.split(separator: ",").map(String.init)
            return names.map { root.appendingPathComponent($0).path }.joined(separator: ",")
        }
        value.lexicon = c(paths("lexicon", role: "lexicon")); value.rule_fsts = c(paths("rule_fsts", role: "rule-fst"))
        value.language = c(model.option("lang", fallback: model.option("language")))
        let codec = model.path(role: "codec", root: root)
        value.codec = c(codec.isEmpty ? model.path(role: "tokenizer", root: root) : codec)
        value.threads = Int32(max(1, min(4, ProcessInfo.processInfo.activeProcessorCount)))
        value.sample_rate = Int32(model.config["sample_rate"]?.number ?? 16000)
        value.cpu_only = NativeExecutionContext.compute == .cpuOnly ? 1 : 0
        value.duration_predictor = c(model.path(role: "duration-predictor",root: root)); value.text_encoder = c(model.path(role: "text-encoder",root: root))
        value.vector_estimator = c(model.path(role: "vector-estimator",root: root)); value.vocoder = c(model.path(role: "vocoder",root: root))
        value.tts_config = c(model.path(role: "tts-config",root: root)); value.unicode_indexer = c(model.path(role: "unicode-indexer",root: root))
        value.lm_flow = c(model.path(role: "lm-flow",root: root)); value.lm_main = c(model.path(role: "lm-main",root: root))
        value.text_conditioner = c(model.path(role: "text-conditioner",root: root)); value.vocab = c(model.path(role: "vocab",root: root)); value.token_scores = c(model.path(role: "token-scores",root: root))
        value.num_steps = Int32(model.config["num_steps"]?.number ?? 8)

    }
    deinit { for p in strings { free(p) } }
}
public actor NativeLocalEngine: LocalEngine {
    private let store: ModelStore
    private let libraries: [ModelRuntime: String]
    public init(store: ModelStore, libraries: [ModelRuntime: String] = [:]) { self.store = store; self.libraries = libraries }
    public nonisolated static func runtimeAvailable(_ runtime: ModelRuntime, library: String = "") -> Bool { vs_runtime_available(runtime.rawValue, library) != 0 }
    public nonisolated static func cpuRuntimeAvailable(_ runtime: ModelRuntime, library: String = "") -> Bool { vs_cpu_runtime_available(runtime.rawValue, library) != 0 }
    public nonisolated static func cpuFallbackAvailable(models: [PhoneModel]) -> Bool { models.allSatisfy { cpuRuntimeAvailable($0.runtime) } }
    private static func check(_ rc: Int32) throws {
        switch rc {
        case 0: return
        case -4: throw CancellationError()
        case -1: throw ClientError("mobile.local.runtime_failed")
        case -2: throw ClientError("mobile.local.model_incompatible")
        default: throw ClientError("mobile.local.inference_failed")
        }
    }
    private static func audio(_ value: VSAudio) throws -> LocalAudio {
        guard let samples = value.samples, value.count > 0 else { throw ClientError("mobile.local.inference_failed") }
        return LocalAudio(samples: Array(UnsafeBufferPointer(start: samples, count: Int(value.count))), sampleRate: Int(value.sample_rate))
    }
    func execute<T: Sendable>(_ model: PhoneModel, capability: ModelCapability, progress: @escaping EngineProgressHandler,
                              body: @escaping @Sendable (PhoneModel, URL, String, NativeCancellation) throws -> T) async throws -> T {
        guard model.supports(capability) else { throw ClientError("mobile.local.model_incompatible") }
        _ = try await store.acquire(model)
        let token: NativeCancellation
        do { token = try NativeCancellation() } catch { await store.release(model); throw error }
        let library = libraries[model.runtime] ?? "", store = store
        // The worker owns both leases. Awaiter cancellation cannot release memory in use by C.
        let work = NativeExecutionContext.detached {
            do { try await NativeLease.shared.acquire() }
            catch { token.finish(); await store.release(model); throw error }
            let result: Result<T, any Error>
            do {
                // Another queued load may have invalidated stamps or lost licence permission.
                let root = try await store.authorize(model)
                try Task.checkCancellation()
                result = .success(try body(model,root,library,token))
            }
            catch { result = .failure(error) }
            token.finish() // body has returned and all native defer cleanup has completed.
            if case .failure(let error) = result, !(error is CancellationError) { await store.invalidateVerification(model) }
            await store.release(model)
            await NativeLease.shared.release()
            return try result.get()
        }
        let result = try await withTaskCancellationHandler {
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    await progress(EngineProgress(stage: .loading,fraction: 0))
                    while !token.done {
                        try Task.checkCancellation()
                        await progress(EngineProgress(stage: .running,fraction: Double(vs_progress(token.pointer))))
                        try await Task.sleep(for: .milliseconds(100))
                    }
                }
                defer { group.cancelAll() }
                let result = try await work.value
                try Task.checkCancellation()
                return result
            }
        } onCancel: { token.cancel(); work.cancel() }
        try await store.checkPermission(model)
        await progress(EngineProgress(stage: .finished,fraction: 1))
        return result
    }
    public func synthesize(model: PhoneModel, text: String, speaker: Int = 0, speed: Float = 1, progress: @escaping EngineProgressHandler = { _ in }) async throws -> LocalAudio {
        guard !model.requiresReference else { throw ClientError("mobile.speak.reference_required") }

        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, speaker >= 0, speaker <= Int(Int32.max), speed.isFinite, speed > 0, speed <= 4, speaker < Int(model.config["num_speakers"]?.number ?? 1) else { throw ClientError("mobile.local.model_incompatible") }
        return try await execute(model, capability: .tts, progress: progress) { m, root, library, token in
            let config = NativeConfiguration(model: m, root: root, library: library)
            var output = VSAudio(); defer { vs_audio_free(&output) }
            try Self.check(vs_synthesize(&config.value, text, Int32(speaker), speed, token.pointer, &output))
            return try Self.audio(output)
        }
    }
    public func transcribe(model: PhoneModel, audio: PCMChunk, progress: @escaping EngineProgressHandler = { _ in }) async throws -> String {
        try Self.validate(audio)
        return try await execute(model, capability: .asr, progress: progress) { m, root, library, token in
            let config = NativeConfiguration(model: m, root: root, library: library)
            var output: UnsafeMutablePointer<CChar>?; defer { vs_text_free(output) }
            let rc = audio.samples.withUnsafeBufferPointer { vs_transcribe(&config.value, $0.baseAddress, Int32($0.count), Int32(audio.sampleRate), token.pointer, &output) }
            try Self.check(rc); return output.map { String(cString: $0) } ?? ""
        }
    }
    public func transcribeSegments(model: PhoneModel,audio: PCMChunk,progress: @escaping EngineProgressHandler = { _ in }) async throws -> [SpeechSegment] {
        try Self.validate(audio)
        return try await execute(model,capability: .asr,progress: progress) { m,root,library,token in
            let config = NativeConfiguration(model: m,root: root,library: library)
            var output = VSSegments(); defer { vs_segments_free(&output) }
            try audio.samples.withUnsafeBufferPointer { try Self.check(vs_transcribe_segments(&config.value,$0.baseAddress,Int32($0.count),Int32(audio.sampleRate),token.pointer,&output)) }
            guard output.count == 0 || output.segments != nil else { throw ClientError("mobile.local.inference_failed") }
            let total = Double(audio.samples.count)/Double(audio.sampleRate)
            var result: [SpeechSegment] = [], window = -1, tokens: [(String,Double,Double)] = []
            func flush() {
                guard window >= 0, !tokens.isEmpty else { tokens = []; return }
                let base = Double(window)*20, length = min(20,max(0,total-base))
                result += splitUtterances(text: tokens.map(\.0).joined(),tokens: tokens.map(\.0),timestamps: tokens.map(\.1),ends: tokens.map(\.2),window: length)
                    .map { SpeechSegment(start: base+$0.start,end: base+$0.end,text: $0.text) }
                tokens = []
            }
            for index in 0..<Int(output.count) {
                let segment = output.segments![index]
                let text = segment.text.map { String(cString: $0) } ?? ""
                if segment.token == 0 {
                    flush()
                    let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !clean.isEmpty { result.append(SpeechSegment(start: Double(segment.window)*20,end: min(total,Double(segment.window)*20+20),text: clean)) }
                    continue
                }
                if Int(segment.window) != window { flush(); window = Int(segment.window) }
                tokens.append((text,segment.start,segment.end))
            }
            flush()
            return result
        }
    }
    public func clone(model: PhoneModel, text: String, reference: PCMChunk, referenceText: String, progress: @escaping EngineProgressHandler = { _ in }) async throws -> LocalAudio {
        try Self.validate(reference)
        guard reference.sampleRate == 24000, reference.samples.count <= 720000, !text.isEmpty, text.utf16.count <= 20000, (!referenceText.isEmpty || model.option("model_type") == "pocket") else { throw ClientError("mobile.local.model_incompatible") }
        return try await execute(model, capability: .clone, progress: progress) { m, root, library, token in
            let config = NativeConfiguration(model: m, root: root, library: library)
            guard m.runtime == .sherpa || !m.path(role: "codec", root: root).isEmpty || !m.path(role: "tokenizer", root: root).isEmpty else { throw ClientError("mobile.local.model_incompatible") }
            var output = VSAudio(); defer { vs_audio_free(&output) }
            let rc = reference.samples.withUnsafeBufferPointer { samples in
                if m.runtime == .sherpa { return vs_sherpa_clone(&config.value,text,referenceText,samples.baseAddress,Int32(samples.count),Int32(reference.sampleRate),token.pointer,&output) }
                return vs_clone(&config.value,text,referenceText,samples.baseAddress,Int32(samples.count),token.pointer,&output)
            }
            try Self.check(rc); return try Self.audio(output)
        }
    }
    public func design(model: PhoneModel, text: String, instruction: String, seed: UInt64, progress: @escaping EngineProgressHandler = { _ in }) async throws -> LocalAudio {
        guard model.supportsDesign else { throw ClientError("mobile.local.model_incompatible") }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.utf16.count <= 2000, instruction.utf8.count <= 2000 else { throw ClientError("mobile.local.model_incompatible") }
        return try await execute(model,capability: .clone,progress: progress) { m,root,library,token in
            let config = NativeConfiguration(model: m,root: root,library: library)
            var output = VSAudio(); defer { vs_audio_free(&output) }
            try Self.check(vs_design(&config.value,text,instruction,seed,token.pointer,&output))
            return try Self.audio(output)
        }
    }
    public func translate(model: PhoneModel, text: String, sourceLanguage: String, targetLanguage: String, progress: @escaping EngineProgressHandler = { _ in }) async throws -> String {
        let template = model.option("prompt_template")
        guard template.contains("{text}"), template.contains("{target_language}"), !text.isEmpty else { throw ClientError("mobile.local.model_incompatible") }
        let prompt = template.replacingOccurrences(of: "{source_language}", with: sourceLanguage).replacingOccurrences(of: "{target_language}", with: targetLanguage).replacingOccurrences(of: "{text}", with: text)
        return try await execute(model, capability: .translate, progress: progress) { m, root, library, token in
            let config = NativeConfiguration(model: m, root: root, library: library)
            var output: UnsafeMutablePointer<CChar>?; defer { vs_text_free(output) }
            try Self.check(vs_translate(&config.value, prompt, Int32(max(1, min(1024, m.config["max_tokens"]?.number ?? 512))), token.pointer, &output))
            return output.map { String(cString: $0) } ?? ""
        }
    }
    public func streamTranscribe(model: PhoneModel, chunks: AsyncThrowingStream<PCMChunk, any Error>, progress: @escaping EngineProgressHandler = { _ in }) async throws -> AsyncThrowingStream<TranscriptUpdate, any Error> {
        guard model.capability == .stream else { throw ClientError("mobile.local.model_incompatible") }
        _ = try await store.acquire(model)
        let token: NativeCancellation
        do { token = try NativeCancellation() } catch { await store.release(model); throw error }
        let library = libraries[model.runtime] ?? ""
        let store = store
        let (output, continuation) = AsyncThrowingStream<TranscriptUpdate, any Error>.makeStream(bufferingPolicy: .bufferingNewest(32))
        // Signals once the worker holds the inference slot and the recognizer exists, so the
        // caller starts the microphone only then (the capture buffer is bounded).
        let (ready, readiness) = AsyncThrowingStream<Void, any Error>.makeStream(bufferingPolicy: .bufferingNewest(1))
        let work = Task.detached(priority: .userInitiated) {
            do {
                try await NativeLease.shared.acquire()
            } catch { readiness.finish(throwing: error); continuation.finish(throwing: error); await store.release(model); return }
            do {
                let root = try await store.authorize(model)
                try Task.checkCancellation()
                await progress(EngineProgress(stage: .loading, fraction: 0))
                let config = NativeConfiguration(model: model, root: root, library: library)
                guard let stream = vs_stream_create(&config.value) else { throw ClientError("mobile.local.inference_failed") }
                defer { vs_stream_free(stream) }
                readiness.yield(); readiness.finish()
                var finals: [String] = []
                func accept(_ chunk: PCMChunk, finished: Bool) throws {
                    try Task.checkCancellation()
                    var text: UnsafeMutablePointer<CChar>?; var endpoint: Int32 = 0
                    defer { vs_text_free(text) }
                    try chunk.samples.withUnsafeBufferPointer { buffer in
                        try Self.check(vs_stream_accept(stream, buffer.baseAddress, Int32(buffer.count), Int32(chunk.sampleRate), finished ? 1 : 0, token.pointer, &text, &endpoint))
                    }
                    let partial = text.map { String(cString: $0) } ?? ""
                    if endpoint != 0, !partial.isEmpty { finals.append(partial) }
                    let value = (finals + (endpoint == 0 && !partial.isEmpty ? [partial] : [])).joined(separator: " ")
                    continuation.yield(TranscriptUpdate(text: value, isFinal: endpoint != 0, finished: finished))
                }
                for try await chunk in chunks {
                    try Self.validate(chunk); try await store.checkPermission(model)
                    try accept(chunk, finished: false)
                    await progress(EngineProgress(stage: .running, fraction: 0))
                }
                try await store.checkPermission(model)
                try accept(PCMChunk(samples: [], sampleRate: 16000), finished: true)
                await progress(EngineProgress(stage: .finished, fraction: 1))
                continuation.finish()
            } catch {
                if !(error is CancellationError) { await store.invalidateVerification(model) }
                readiness.finish(throwing: error) // no-op once ready was signalled
                continuation.finish(throwing: error)
            }
            // The stream and recognizer have been freed by the inner scope's defer.
            await NativeLease.shared.release()
            await store.release(model)
        }
        continuation.onTermination = { _ in token.cancel(); work.cancel() }
        do {
            try await withTaskCancellationHandler {
                for try await _ in ready { return }
                try Task.checkCancellation()
                throw ClientError("mobile.local.inference_failed")
            } onCancel: { token.cancel(); work.cancel() }
        } catch { token.cancel(); work.cancel(); throw error }
        return output
    }
    private static func validate(_ pcm: PCMChunk) throws {
        guard !pcm.samples.isEmpty, pcm.samples.count <= Int(Int32.max), (8000...192000).contains(pcm.sampleRate), pcm.samples.allSatisfy(\.isFinite) else { throw ClientError("mobile.errors.audio") }
    }
}
