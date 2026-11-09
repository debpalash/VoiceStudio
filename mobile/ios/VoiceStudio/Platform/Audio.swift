import Foundation
import SwiftUI
@preconcurrency import AVFoundation
import Observation
import VoiceStudioKit
import UniformTypeIdentifiers

@MainActor func activatePlaybackSession() throws {
    try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
    try AVAudioSession.sharedInstance().setActive(true)
}

@MainActor @Observable final class AudioPlayer {
    private var player: AVPlayer?
    private var ticker: Task<Void, Never>?
    private var loader: HostMediaLoader?
    private var revision = UUID()
    var file: URL?
    var duration: Double = 0
    var position: Double = 0
    var playing = false
    var error: ClientError?
    var waveform: [Float] = []
    func load(_ data: Data, extension ext: String) async throws {
        clear()
        let revision = self.revision
        try Task.checkCancellation()
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension(ext)
        try data.write(to: path, options: .atomic)
        try await prepare(path,revision: revision)
    }
    /// Books stay on disk; playback does not allocate a whole-book Data buffer.
    func load(file source: URL) async throws {
        clear()
        let revision = self.revision
        try Task.checkCancellation()
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension(source.pathExtension)
        try FileManager.default.copyItem(at: source,to: path)
        try await prepare(path,revision: revision)
    }
    private func prepare(_ path: URL,revision: UUID) async throws {
        let asset = AVURLAsset(url: path)
        let duration: Double, waveform: [Float]
        do {
            duration = try await asset.load(.duration).seconds
            waveform = try await Self.waveform(url: path)
            try Task.checkCancellation()
            guard revision == self.revision else { throw CancellationError() }
        } catch { try? FileManager.default.removeItem(at: path); throw error }
        // `file` shows the player view, so it is set only once the player can play.
        if let old = file { try? FileManager.default.removeItem(at: old) }
        file = path
        self.duration = duration.isFinite ? duration : 0; position = 0
        self.waveform = waveform
        player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
    }
    func stream(request: URLRequest, api: APIClient) async throws {
        clear()
        let revision = self.revision
        try Task.checkCancellation()
        guard let hostURL = request.url else { throw ClientError.invalidURL }
        var components = URLComponents(url: hostURL, resolvingAgainstBaseURL: false)!
        components.scheme = "voicestudio-media"
        let resource = HostMediaLoader(request: request, api: api)
        let asset = AVURLAsset(url: components.url!)
        asset.resourceLoader.setDelegate(resource, queue: resource.queue)
        loader = resource; waveform = []; position = 0; duration = 0
        player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        play()
        // Save/share uses a separate authenticated host request, never a credential-bearing URL.
        let reply = try await api.download(request)
        defer { try? FileManager.default.removeItem(at: reply.file) }
        try Task.checkCancellation()
        guard revision == self.revision else { throw CancellationError() }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        try FileManager.default.moveItem(at: reply.file, to: url)
        if let old = file { try? FileManager.default.removeItem(at: old) }; file = url
        let saved = AVURLAsset(url: url)
        let seconds = try await saved.load(.duration).seconds
        let waveform = try await Self.waveform(url: url)
        try Task.checkCancellation()
        guard revision == self.revision else { throw CancellationError() }
        duration = seconds.isFinite ? seconds : 0; self.waveform = waveform
    }
    func play() {
        guard let player else { return }
        do {
            try activatePlaybackSession()
        } catch { self.error = ClientError("mobile.errors.audio"); return }
        if duration > 0 && position >= duration { seek(0) }
        player.play(); playing = true
        ticker?.cancel()
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if player.currentItem?.status == .failed { error = ClientError("mobile.errors.audio"); playing = false; return }
                let value = player.currentTime().seconds
                position = value.isFinite ? value : 0
                if duration > 0 && position >= duration { playing = false; return }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }
    func clear() {
        revision = UUID(); pause(); player = nil; loader?.cancel(); loader = nil
        if let file { try? FileManager.default.removeItem(at: file) }
        file = nil; duration = 0; position = 0; waveform = []; error = nil
    }
    func pause() { player?.pause(); playing = false; ticker?.cancel(); ticker = nil }
    func seek(_ seconds: Double) { player?.seek(to: CMTime(seconds: seconds, preferredTimescale: 600)); position = seconds }
    private nonisolated static func waveform(url: URL) async throws -> [Float] {
        let asset = AVURLAsset(url: url)
        guard let track = try await asset.loadTracks(withMediaType: .audio).first else { return [] }
        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsNonInterleaved: false])
        reader.add(output); reader.startReading()
        var peaks: [Float] = []
        defer { if reader.status == .reading { reader.cancelReading() } }
        while let sample = output.copyNextSampleBuffer(), let block = CMSampleBufferGetDataBuffer(sample) {
            try Task.checkCancellation()
            let count = CMBlockBufferGetDataLength(block) / MemoryLayout<Float>.size
            var values = [Float](repeating: 0, count: count)
            let status = values.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: $0.count, destination: $0.baseAddress!) }
            if status == kCMBlockBufferNoErr { peaks.append(values.reduce(0) { max($0, abs($1)) }) }
            if peaks.count > 4096 { peaks = stride(from: 0, to: peaks.count - 1, by: 2).map { max(peaks[$0], peaks[$0 + 1]) } }
        }
        return peaks
    }
}

struct AudioPlayerView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @Bindable var player: AudioPlayer
    var body: some View {
        Card {
            Canvas { context, size in
                let samples = player.waveform
                guard !samples.isEmpty else { return }
                let step = max(1, samples.count / max(1, Int(size.width)))
                var path = Path()
                for i in stride(from: 0, to: samples.count, by: step) {
                    let x = size.width * Double(i) / Double(samples.count)
                    let height = Double(samples[i]) * size.height / 2
                    path.move(to: CGPoint(x: x, y: size.height / 2 - height)); path.addLine(to: CGPoint(x: x, y: size.height / 2 + height))
                }
                context.stroke(path, with: .color(theme.color("messageAction")))
            }.frame(height: VSTokens.textEditor.lineHeight * 2).accessibilityLabel(model.t("mobile.ios.waveform"))
            HStack(spacing: VSTokens.spacingGap) {
                Button(model.t(player.playing ? "common.pause" : "common.play"), systemImage: player.playing ? "pause.fill" : "play.fill") {
                    if player.playing { player.pause() } else { player.play() }
                }.labelStyle(.iconOnly).studioButton(.tertiary).accessibilityIdentifier("audio.play")
                Slider(value: Binding(get: { min(player.position, max(1, player.duration)) }, set: { player.seek($0) }), in: 0...max(1, player.duration))
                    .accessibilityLabel(model.t("player.seek"))
                    .accessibilityValue(PlaybackTime.format(player.position))
                if let file = player.file {
                    ShareLink(item: file) { Label(model.t("mobile.speak.share"), systemImage: "square.and.arrow.up") }
                        .labelStyle(.iconOnly).studioButton(.tertiary)
                }
            }
            HStack {
                Text(PlaybackTime.format(player.position))
                Spacer(minLength: 0)
                Text(PlaybackTime.format(player.duration))
            }.font(.caption)
                .monospacedDigit().foregroundStyle(theme.color("textMuted"))
        }
        .onChange(of: player.error) { _, error in if let error { model.error = error; player.error = nil } }
    }
}

@MainActor @Observable final class Recorder {
    private var recorder: AVAudioRecorder?
    private var starting = false
    var recording = false
    var file: URL?
    var seconds = 0
    private var timer: Task<Void, Never>?
    func start() async throws {
        guard !recording, !starting else { return }
        starting = true; defer { starting = false }
        guard await AVAudioApplication.requestRecordPermission() else { throw ClientError("mobile.permissions.denied") }
        try Task.checkCancellation()
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker]); try session.setActive(true)
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        let recorder = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 24000, AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16])
        guard recorder.record(forDuration: 29) else { throw ClientError("mobile.errors.audio") }
        self.recorder = recorder; file = url; recording = true; seconds = 0
        timer = Task { [weak self] in while !Task.isCancelled { try? await Task.sleep(for: .seconds(1)); guard let self, recording else { return }; seconds += 1 } }
    }
    func stop() { recorder?.stop(); recorder = nil; recording = false; timer?.cancel(); timer = nil; try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
}
