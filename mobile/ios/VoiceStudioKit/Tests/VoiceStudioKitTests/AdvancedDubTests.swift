import Foundation
import Testing
@testable import VoiceStudioKit

private let computerCastStream = "event: warning\ndata: {\"detail\":\"Check the speaker labels\"}\n\nevent: final\ndata: {\"segments\":[{\"id\":0,\"start\":0,\"end\":1,\"text\":\"Previous translation\",\"text_original\":\"Original speech\",\"speaker_id\":\"Speaker 1\"}],\"cast_sources\":{\"Speaker 1\":{\"kind\":\"speaker\",\"duration\":4}}}\n\nevent: done\ndata: {}\n\n"
private actor AdvancedDubHost: HTTPTransport {
    let stream: String
    init(stream: String = computerCastStream) { self.stream = stream }
    var installed = true
    var engine = "argos"
    var category = "offline"
    private(set) var requests: [URLRequest] = []
    func missing() { installed = false }
    func replaceEngine() { engine = "argos"; category = "online" }
    func send(_ request: URLRequest) throws -> HTTPReply {
        requests.append(request)
        let body: String
        switch request.url!.path {
        case "/engines/diarisation": body = "{\"installed\":\(installed),\"label\":\"Installed diarizer\"}"
        case "/dub/transcribe-stream/job": body = stream
        case "/engines/translation": body = "{\"active\":\"\(engine)\",\"engines\":[{\"id\":\"\(engine)\",\"display_name\":\"Translator\",\"category\":\"\(category)\",\"installed\":true,\"ready\":true}]}"
        case "/dub/translate": body = "{\"translated\":[{\"id\":\"0\",\"text\":\"Bonjour\"}]}"
        case "/dub/generate/job": body = "{\"task_id\":\"new-task\"}"
        default: throw ClientError("mobile.errors.invalid_response")
        }
        return HTTPReply(data: Data(body.utf8))
    }
}
@Test func advancedComputerDiarizationUsesInstalledRuntimeAndRetainsRealWarningsAndSourceText() async throws {
    let host = AdvancedDubHost(), client = APIClient(address: try PairingAddress("http://127.0.0.1:3900"),credentials: Credentials(session: "session"),transport: host)
    let result = try await client.computerDubAnalysis("job",speakers: 2)
    #expect(result.segments[0].text == "Original speech"); #expect(result.segments[0].speaker_id == "Speaker 1"); #expect(result.warnings == ["Check the speaker labels"])
    let requests = await host.requests
    #expect(requests.count == 2); #expect(requests[1].url?.query == "reanalyze_speakers=true&num_speakers=2")
    #expect(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer session" })
    let missing = AdvancedDubHost(); await missing.missing()
    let blocked = APIClient(address: client.address,transport: missing)
    await #expect(throws: (any Error).self) { try await blocked.computerDubAnalysis("job") }
    #expect(await missing.requests.count == 1)
    #expect(throws: (any Error).self) { try ComputerDubAnalysis.parse(Data(computerCastStream.replacingOccurrences(of: "event: done\ndata: {}\n\n",with: "").utf8)) }
    #expect(throws: (any Error).self) { try ComputerDubAnalysis.parse(Data(computerCastStream.replacingOccurrences(of: "Original speech",with: "   ").utf8)) }
    #expect(throws: (any Error).self) { try ComputerDubAnalysis.parse(Data(computerCastStream.replacingOccurrences(of: "Original speech",with: String(repeating: "x",count: 4001)).utf8)) }
}
@Test func repeatedComputerSpeakerDetectionRequestsFreshAnalysisIncludingAutomaticCount() async throws {
    let host = AdvancedDubHost(), client = APIClient(address: try PairingAddress("http://127.0.0.1:3900"),credentials: Credentials(session: "session"),transport: host)
    for speakers in [0,3,0] {
        let result = try await client.computerDubAnalysis("job",speakers: speakers)
        #expect(result.segments[0].text == "Original speech")
    }
    let requests = await host.requests
    #expect(requests.count == 6)
    for (index,speakers) in [0,3,0].enumerated() {
        #expect(requests[index*2].url?.path == "/engines/diarisation")
        let request = requests[index*2+1]
        let query = try #require(URLComponents(url: request.url!,resolvingAgainstBaseURL: false)?.queryItems)
        #expect(request.httpMethod == "GET")
        #expect(query.first { $0.name == "reanalyze_speakers" }?.value == "true")
        #expect(query.first { $0.name == "num_speakers" }?.value == (speakers == 0 ? nil : String(speakers)))
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer session")
    }
}
@Test func advancedComputerCastingRetranslatesFreshSourceAndSendsStableIdsAndCastProfiles() async throws {
    let host = AdvancedDubHost(), client = APIClient(address: try PairingAddress("http://127.0.0.1:3900"),transport: host)
    let source = try ComputerDubAnalysis.parse(Data(computerCastStream.utf8))
    let provider = try JSONDecoder().decode(TranslationEngine.self,from: Data(#"{"id":"argos","display_name":"Argos","category":"offline","installed":true,"ready":true}"#.utf8))
    let result = try await client.computerDubCast("job",analysis: source,language: "fr",name: "French",voices: ["Speaker 1":"profile-1"],provider: provider,timing: "smart_fit")
    #expect(result.task_id == "new-task")
    let requests = await host.requests
    let translation = try #require(requests.first { $0.url?.path == "/dub/translate" })
    #expect(String(decoding: translation.httpBody!,as: UTF8.self).contains("Original speech")); #expect(!String(decoding: translation.httpBody!,as: UTF8.self).contains("Previous translation"))
    let render = try #require(requests.first { $0.url?.path == "/dub/generate/job" })
    let body = try #require(try JSONSerialization.jsonObject(with: render.httpBody!) as? [String:Any])
    #expect(body["segment_ids"] as? [String] == ["0"]); #expect(body["timing_strategy"] as? String == "smart_fit")
    let segments = try #require(body["segments"] as? [[String:Any]])
    #expect(segments[0]["text"] as? String == "Bonjour"); #expect(segments[0]["profile_id"] as? String == "profile-1")
    let switched = AdvancedDubHost(); await switched.replaceEngine()
    let switchedClient = APIClient(address: client.address,transport: switched)
    await #expect(throws: (any Error).self) { try await switchedClient.computerDubCast("job",analysis: source,language: "fr",name: "French",voices: [:],provider: provider) }
    #expect(await switched.requests.count == 1)
}
@Test func computerCastPreservesSavedRenderSettingsWhileLegacySegmentsOmitOverrides() async throws {
    func expectSettings(_ cue: [String:Any], gain: Double?) {
        #expect(cue["gain"] as? Double == gain)
        #expect(cue["speed"] as? Double == (gain == nil ? nil : 0.85))
        #expect(cue["direction"] as? String == (gain == nil ? nil : "Whisper urgently"))
        #expect(cue["effect_preset"] as? String == (gain == nil ? nil : "raw"))
        #expect(cue["instruct"] as? String == (gain == nil ? nil : "Calm delivery"))
        for key in ["gain","speed","direction","effect_preset","instruct"] { #expect(cue.keys.contains(key) == (gain != nil)) }
    }
    for gain in [Double?](arrayLiteral: 0,0.35,1,nil) {
        let stream = gain.map { computerCastStream.replacingOccurrences(of: "\"speaker_id\":",with: "\"gain\":\($0),\"speed\":0.85,\"direction\":\"Whisper urgently\",\"effect_preset\":\"raw\",\"instruct\":\"Calm delivery\",\"speaker_id\":") } ?? computerCastStream
        let host = AdvancedDubHost(stream: stream), client = APIClient(address: try PairingAddress("http://127.0.0.1:3900"),transport: host)
        let analysis = try await client.computerDubAnalysis("job")
        let saved = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(analysis.segments[0])) as? [String:Any])
        expectSettings(saved,gain: gain)
        let provider = try JSONDecoder().decode(TranslationEngine.self,from: Data(#"{"id":"argos","display_name":"Argos","category":"offline","installed":true,"ready":true}"#.utf8))
        _ = try await client.computerDubCast("job",analysis: analysis,language: "fr",name: "French",voices: [:],provider: provider)
        let request = try #require(await host.requests.last)
        let payload = try #require(try JSONSerialization.jsonObject(with: request.httpBody!) as? [String:Any])
        let cue = try #require((payload["segments"] as? [[String:Any]])?.first)
        expectSettings(cue,gain: gain)
        #expect(cue["text"] as? String == "Bonjour")
    }
}
@Test func advancedExportQueriesAreBoundedAndExplicitAboutBackgroundTracksAndBurn() throws {
    var export = ComputerDubExport(); export.language = "fr"; export.tracks = ["fr","de"]; export.original = true; export.preserveBackground = true; export.burn = true; export.karaoke = true
    let query = try #require(URLComponents(string: "http://localhost"+export.path(job: "job-1"))?.queryItems)
    let values = Dictionary(query.map { ($0.name,$0.value ?? "") },uniquingKeysWith: { _,new in new })
    #expect(values["include_tracks"] == "fr,de,original"); #expect(values["default_track"] == "fr"); #expect(values["burn_subs"] == "1"); #expect(values["karaoke"] == "1"); #expect(values["preserve_bg"] == "1")
    #expect(throws: (any Error).self) { try export.path(job: "../../private") }
    export.dual = true; #expect(throws: (any Error).self) { try export.path(job: "job") }
    export.dual = false; export.kind = .ass
    #expect(try export.path(job: "job").hasPrefix("/dub/ass/job?"))
}
@Test func nativeASSStylesEscapeTextAndKeepBothLanguagesWithoutInventedWordTimings() throws {
    let cue = PhoneDubSegment(speech: SpeechSegment(start: 1.125,end: 2.5,text: "Source {text}"),translations: ["fr":"Hello {\\pos(0,0)}\nNext"],speaker: "A, B")
    var style = PhoneDubSubtitleStyle(); style.color = "FFFF00"; style.fontSize = 48; style.top = true; style.dual = true
    let ass = try PhoneDubASS.export([cue],target: "fr",style: style)
    #expect(ass.contains("Style: Default,Arial,48,&H0000FFFF")); #expect(ass.contains("Dialogue: 0,0:00:01.13,0:00:02.50,Default,A  B"))
    #expect(ass.contains("\\{\\\\pos(0,0)\\}")); #expect(ass.contains("\\NNext\\N{\\i1}Source \\{text\\}{\\i0}")); #expect(!ass.contains("\\kf"))
    style.color = "FFFFFF,Injected"; #expect(throws: (any Error).self) { try PhoneDubASS.export([cue],target: "fr",style: style) }
}
@Test func nativeCastAssignmentsKeepExplicitAndLegacyLineOverrides() throws {
    let a = PhoneDubSegment(speech: SpeechSegment(start: 0,end: 1,text: "First"),speaker: "Alex")
    let b = PhoneDubSegment(speech: SpeechSegment(start: 1,end: 2,text: "Second"),referenceFile: "private.pcm",speaker: "Alex")
    let cast = PhoneDubCast.assigning([a,b],speaker: "Alex",voiceID: "voice-1")
    #expect(cast[0].voiceID == "voice-1"); #expect(cast[0].referenceOrigin == "cast"); #expect(cast[1].referenceFile == "private.pcm"); #expect(cast[1].voiceID == nil)
    let replaced = PhoneDubCast.assigning(cast,speaker: "Alex",voiceID: "voice-2",replaceOverrides: true)
    #expect(replaced.allSatisfy { $0.voiceID == "voice-2" })
    var different = a; different.speaker = "Other"
    #expect(throws: (any Error).self) { try PhoneDubEditing.merge([a,different],index: 0) }
}

@Test func sourceSpeakerVoiceIDsMatchBackendLowercasePortableNames() {
    #expect(ComputerDubAnalysis.sourceVoiceID("Speaker 1") == "auto:speaker_1")
    #expect(ComputerDubAnalysis.sourceVoiceID("SPEAKER-2!") == "auto:speaker_2")
    #expect(ComputerDubAnalysis.sourceVoiceID("___") == "auto:speaker")
}
