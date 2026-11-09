import Foundation

public struct ComputerDubTrack: Decodable, Sendable { public let language: String?; public let duration: Double? }
public struct ComputerDubTracks: Decodable, Sendable { public let tracks: [String:ComputerDubTrack] }
public struct ComputerDiarization: Decodable, Sendable { public let installed: Bool; public let label: String? }
public struct ComputerCastSource: Decodable, Sendable { public let kind: String; public let duration: Double? }
public struct ComputerDubAnalysis: Sendable {
    public let segments: [Segment]
    public let warnings: [String]
    public let sources: [String:ComputerCastSource]
    /// Parse the backend's real final/warning events; incomplete or failed streams never become a cast.
    public static func parse(_ data: Data) throws -> ComputerDubAnalysis {
        guard data.count <= 16_777_216, let text = String(data: data,encoding: .utf8) else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        var final: [Segment]?, sources: [String:ComputerCastSource] = [:], warnings: [String] = [], terminal = false
        struct Final: Decodable { let segments: [Segment]; let cast_sources: [String:ComputerCastSource]? }
        struct Warning: Decodable { let detail: String? }
        for block in text.replacingOccurrences(of: "\r\n",with: "\n").components(separatedBy: "\n\n") {
            let lines = block.components(separatedBy: "\n")
            let event = lines.first { $0.hasPrefix("event:") }?.dropFirst(6).trimmingCharacters(in: .whitespaces) ?? ""
            let payload = lines.filter { $0.hasPrefix("data:") }.map { String($0.dropFirst(5)).trimmingCharacters(in: .whitespaces) }.joined(separator: "\n").data(using: .utf8) ?? Data()
            if ["error","aborted","cancelled"].contains(event) { throw ClientError("mobile.errors.dub_failed") }
            if event == "warning", let message = try? JSONDecoder().decode(Warning.self,from: payload).detail { warnings.append(String(message.prefix(2000))) }
            if event == "final" { let value = try JSONDecoder().decode(Final.self,from: payload); final = value.segments; sources = value.cast_sources ?? [:] }
            if event == "done" { terminal = true }
        }
        guard terminal, let final, !final.isEmpty, final.count <= 10000, Set(final.map(\.id)).count == final.count,
              final.allSatisfy({ $0.start.isFinite && $0.end.isFinite && $0.start >= 0 && $0.end > $0.start && $0.end <= 14400 && !$0.text.isEmpty && $0.text.utf16.count <= 4000 && ($0.speaker_id?.utf16.count ?? 0) <= 80 }) else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        let source = final.map { cue in var value = cue; value.text = cue.text_original ?? cue.text; return value }
        guard source.allSatisfy({ !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.text.utf16.count <= 4000 }) else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        return ComputerDubAnalysis(segments: source,warnings: Array(warnings.prefix(100)),sources: sources)
    }
    public static func sourceVoiceID(_ speaker: String) -> String {
        var slug = ""
        for scalar in speaker.lowercased().unicodeScalars { if CharacterSet.alphanumerics.contains(scalar) { slug.unicodeScalars.append(scalar) } else if scalar == " " || scalar == "-" { slug.append("_") } }
        return "auto:"+(slug.isEmpty ? "speaker" : slug)
    }
}
public struct ComputerDubExport: Sendable {
    public enum Kind: String, CaseIterable, Sendable { case media,srt,vtt,ass,clips,stems }
    public var kind: Kind = .media
    public var language = ""
    public var tracks: [String] = []
    public var original = false
    public var preserveBackground = false
    public var burn = false
    public var dual = false
    public var karaoke = false
    public var audioFormat = "m4a"
    public init() {}
    public func path(job: String) throws -> String {
        let id = try APIClient.component(job)
        guard !language.isEmpty, Self.safe(language), tracks.allSatisfy(Self.safe), ["wav","m4a","mp3","flac"].contains(audioFormat), !(dual && karaoke) else { throw ClientError("mobile.dub_advanced.invalid_export") }
        let endpoint: String
        switch kind { case .media: endpoint = "download"; case .srt: endpoint = "srt"; case .vtt: endpoint = "vtt"; case .ass: endpoint = "ass"; case .clips: endpoint = "export-segments"; case .stems: endpoint = "export-stems" }
        var query = [URLQueryItem(name: "lang",value: language)]
        if kind == .media {
            guard tracks.contains(language), !tracks.isEmpty else { throw ClientError("mobile.dub_advanced.invalid_export") }
            query += [URLQueryItem(name: "default_track",value: language),URLQueryItem(name: "include_tracks",value: (tracks+(original ? ["original"] : [])).joined(separator: ",")),URLQueryItem(name: "preserve_bg",value: preserveBackground ? "1" : "0"),URLQueryItem(name: "burn_subs",value: burn ? "1" : "0"),URLQueryItem(name: "dual",value: dual ? "1" : "0"),URLQueryItem(name: "karaoke",value: karaoke ? "1" : "0"),URLQueryItem(name: "out_format",value: audioFormat)]
        } else if kind == .srt || kind == .vtt { query.append(URLQueryItem(name: "dual",value: dual ? "1" : "0")) }
        var parts = URLComponents(); parts.queryItems = query
        return "/dub/"+endpoint+"/"+id+"?"+(parts.percentEncodedQuery ?? "")
    }
    private static func safe(_ code: String) -> Bool { !code.isEmpty && code.utf8.count <= 80 && code.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45,95].contains($0) } }
}
public extension APIClient {
    func computerDubTracks(_ job: String) async throws -> ComputerDubTracks { try await decode(ComputerDubTracks.self,"/dub/tracks/"+Self.component(job)) }
    func computerDiarization() async throws -> ComputerDiarization { try await decode(ComputerDiarization.self,"/engines/diarisation") }
    func computerDubAnalysis(_ job: String,speakers: Int = 0) async throws -> ComputerDubAnalysis {
        guard (0...20).contains(speakers) else { throw ClientError("mobile.dub_advanced.invalid_cast") }
        guard try await computerDiarization().installed else { throw ClientError("mobile.dub_advanced.install_on_computer") }
        let path = "/dub/transcribe-stream/"+(try Self.component(job))+"?reanalyze_speakers=true"+(speakers == 0 ? "" : "&num_speakers=\(speakers)")
        let reply = try await download(request(path), maxBytes: 16_777_216); defer { try? FileManager.default.removeItem(at: reply.file) }
        let handle = try FileHandle(forReadingFrom: reply.file); defer { try? handle.close() }
        let data = try handle.read(upToCount: 16_777_217) ?? Data(); return try ComputerDubAnalysis.parse(data)
    }
    func computerDubTexts(_ job: String,language: String) async throws -> [String:String] {
        struct Texts: Decodable,Sendable { let texts: [String:String] }
        var parts = URLComponents(); parts.queryItems = [URLQueryItem(name: "lang",value: language)]
        return try await decode(Texts.self,"/dub/segments-text/"+Self.component(job)+"?"+(parts.percentEncodedQuery ?? "")).texts
    }
    func computerDubCast(_ job: String,analysis: ComputerDubAnalysis,language: String,name: String,voices: [String:String],provider: TranslationEngine,onlineConfirmed: Bool = false,timing: String = "concise") async throws -> TaskReference {
        guard ["concise","smart_fit","stretch_video"].contains(timing) else { throw ClientError("mobile.dub_advanced.invalid_export") }
        guard !provider.requiresConfirmation || onlineConfirmed else { throw ClientError("mobile.dub.translation_setup") }
        let current = try await translationEngineForJob()
        guard current.id == provider.id, current.category == provider.category, current.consentIdentity == provider.consentIdentity, !current.requiresConfirmation || onlineConfirmed else { throw ClientError("mobile.dub.translation_changed") }
        let translated = try await translate(job,segments: analysis.segments,language: language,provider: current.id)
        guard translated.translated.count == analysis.segments.count, translated.translated.allSatisfy({ $0.error == nil }) else { throw ClientError("mobile.errors.translation") }
        let texts = Dictionary(translated.translated.map { ($0.id,$0.text) },uniquingKeysWith: { _,new in new })
        var segments: [Segment] = []
        for source in analysis.segments {
            guard let text = texts[source.id], !text.isEmpty else { throw ClientError("mobile.dub_advanced.missing_translation") }
            var cue = source; cue.text = text; cue.cue_source_id = nil; cue.target_lang = language; cue.profile_id = voices[source.speaker_id ?? ""] ?? ""; segments.append(cue)
        }
        struct Input: Encodable,Sendable { let segments: [Segment]; let segment_ids: [String]; let language_code: String; let language: String; let timing_strategy: String }
        return try await json("/dub/generate/"+Self.component(job),value: Input(segments: segments,segment_ids: segments.map(\.id),language_code: language,language: name,timing_strategy: timing),response: TaskReference.self)
    }
    func cancelComputerDubTask(_ task: String) async throws { _ = try await send(request("/tasks/cancel/"+Self.component(task),method: "POST")) }
}
