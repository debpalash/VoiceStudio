import Foundation

public struct CallVoice: Decodable, Identifiable, Sendable {
    public let id: String, name: String
    public let kind: String?
    public let verified_own_voice: Bool?
    public var eligible: Bool { kind == "design" || verified_own_voice == true }
    private enum CodingKeys: String,CodingKey { case id,name,kind,verified_own_voice }
    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self,forKey: .id)
        name = try values.decode(String.self,forKey: .name)
        kind = try values.decodeIfPresent(String.self,forKey: .kind)
        if let flag = try? values.decode(Bool.self,forKey: .verified_own_voice) { verified_own_voice = flag }
        else { verified_own_voice = (try? values.decode(Int.self,forKey: .verified_own_voice)) == 1 }
    }
}
public struct CallReadiness: Decodable, Identifiable, Sendable { public let id: String; public let ok: Bool; public let detail: String }
public struct CallOptions: Decodable, Sendable { public let disclosure: String }
public struct CallTurn: Decodable, Sendable { public let speaker: String; public let text: String; public let t: Double }
public struct CallRecord: Decodable, Identifiable, Sendable {
    public let id: String, to_masked: String, status: String
    public let summary: String?
    public let transcript: [CallTurn]?
    public let takeover: Bool?
    public var live: Bool { !["completed","busy","no_answer","failed","canceled"].contains(status) }
}
public struct NewCall: Encodable, Sendable {
    public let to: String, brief: String, profile_id: String, disclosure: String
    public let max_minutes: Int
    public init(to: String,brief: String,profile_id: String,disclosure: String,max_minutes: Int) { self.to = to; self.brief = brief; self.profile_id = profile_id; self.disclosure = disclosure; self.max_minutes = max_minutes }
}
private struct CallList: Decodable, Sendable { let calls: [CallRecord] }
private struct StartedCall: Decodable, Sendable { let call: CallRecord }
private struct SayCall: Encodable, Sendable { let text: String }
private struct TakeoverCall: Encodable, Sendable { let enabled: Bool }
extension APIClient {
    public func callVoices() async throws -> [CallVoice] { try await decode([CallVoice].self,"/profiles") }
    public func callReadiness() async throws -> [CallReadiness] { try await decode([CallReadiness].self,"/calls/readiness") }
    public func callOptions() async throws -> CallOptions { try await decode(CallOptions.self,"/calls/options") }
    public func recentCalls() async throws -> [CallRecord] { try await decode(CallList.self,"/calls").calls }
    public func call(_ id: String) async throws -> CallRecord { try await decode(CallRecord.self,"/calls/"+Self.callID(id)) }
    public func startCall(_ value: NewCall) async throws -> CallRecord { try await json("/calls",value: value,response: StartedCall.self).call }
    public func controlCall(_ id: String,action: String,text: String = "",enabled: Bool = false) async throws {
        let data: Data
        switch action {
        case "say": data = try JSONEncoder().encode(SayCall(text: text))
        case "takeover": data = try JSONEncoder().encode(TakeoverCall(enabled: enabled))
        case "hangup": data = Data("{}".utf8)
        default: throw ClientError.invalidURL
        }
        _ = try await send(request("/calls/"+Self.callID(id)+"/"+action,method: "POST",body: data,contentType: "application/json"))
    }
    private static func callID(_ id: String) throws -> String { guard !id.isEmpty, id.utf8.allSatisfy({(48...57).contains($0)||(65...90).contains($0)||(97...122).contains($0)||$0==45||$0==95}) else {throw ClientError.invalidURL};return id }
}
