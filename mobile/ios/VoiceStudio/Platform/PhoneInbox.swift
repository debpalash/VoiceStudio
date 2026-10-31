import Foundation
import Observation
import VoiceStudioKit

struct PhoneIncoming: Identifiable {
    let id = UUID()
    var text: String?
    var file: URL?
}
@MainActor @Observable final class PhoneInbox {
    static let shared = PhoneInbox()
    var incoming: PhoneIncoming?
    var route: String?
    func receive(_ url: URL) throws {
        if url.scheme == "voicestudio",url.host == "workspace" {
            let path = url.pathComponents.filter { $0 != "/" }
            guard path.count == 1,["speak","dictate","workflows","dub"].contains(path[0]) else { return }
            if let text = URLComponents(url: url,resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "text" })?.value { incoming = PhoneIncoming(text: String(text.prefix(20000))) }
            else { route = path[0] }; return
        }
        guard url.isFileURL else { return }
        let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }
        let values = try url.resourceValues(forKeys: [.fileSizeKey,.isRegularFileKey])
        guard values.isRegularFile == true,let size = values.fileSize,size > 0,size <= 512*1024*1024 else { throw ClientError("mobile.errors.file") }
        let destination = FileManager.default.temporaryDirectory.appendingPathComponent("shared-"+UUID().uuidString).appendingPathExtension(url.pathExtension)
        try FileManager.default.copyItem(at: url,to: destination)
        if ["txt","text"].contains(url.pathExtension.lowercased()) {
            let handle = try FileHandle(forReadingFrom: destination); defer { try? handle.close() }
            let bytes = try handle.read(upToCount: 80000) ?? Data()
            incoming = PhoneIncoming(text: String(String(decoding: bytes,as: UTF8.self).prefix(20000)))
            try? FileManager.default.removeItem(at: destination)
        }
        else { incoming = PhoneIncoming(file: destination) }
    }
    func collect() {
        guard incoming == nil,
              let identifier = Bundle.main.object(forInfoDictionaryKey: "VoiceStudioAppGroupIdentifier") as? String,
              let group = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier) else { return }
        let directory = group.appendingPathComponent("Inbox")
        guard let files = try? FileManager.default.contentsOfDirectory(at: directory,includingPropertiesForKeys: [.creationDateKey]) else { return }
        for marker in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) where marker.pathExtension == "json" {
            guard let data = try? Data(contentsOf: marker),let record = try? JSONDecoder().decode(InboxRecord.self,from: data) else { continue }
            if let text = record.text { incoming = PhoneIncoming(text: String(text.prefix(20000))) }
            else if let file = record.file, file == URL(fileURLWithPath: file).lastPathComponent { try? receive(directory.appendingPathComponent(file));try? FileManager.default.removeItem(at: directory.appendingPathComponent(file)) }
            try? FileManager.default.removeItem(at: marker)
            if incoming != nil { break }
        }
    }
}
private struct InboxRecord: Codable { var text: String?; var file: String? }
