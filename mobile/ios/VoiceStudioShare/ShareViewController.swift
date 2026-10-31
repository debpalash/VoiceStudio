@preconcurrency import UIKit
import UniformTypeIdentifiers

/// Stages content privately for review when VoiceStudio next opens. No inference runs in the extension.
final class ShareViewController: UIViewController {
    private var started = false
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard !started else { return }; started = true
        view.backgroundColor = .systemBackground
        let spinner = UIActivityIndicatorView(style: .large); spinner.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(spinner); NSLayoutConstraint.activate([spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor)]); spinner.startAnimating()
        guard let identifier = Bundle.main.object(forInfoDictionaryKey: "VoiceStudioAppGroupIdentifier") as? String,
              let group = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier),let item = extensionContext?.inputItems.first as? NSExtensionItem,let provider = item.attachments?.first else { finish(false); return }
        let directory = group.appendingPathComponent("Inbox")
        do { try FileManager.default.createDirectory(at: directory,withIntermediateDirectories: true) } catch { finish(false); return }
        let id = UUID().uuidString
        let marker = directory.appendingPathComponent(id+".json")
        if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier,options: nil) { value,error in
                var success = false
                if error == nil,let text = value as? String,let data = try? JSONSerialization.data(withJSONObject: ["text": String(text.prefix(20000))]) { success = (try? data.write(to: marker,options: .atomic)) != nil }
                Task { @MainActor in self.finish(success) }
            }
            return
        }
        guard let type = [UTType.audio.identifier,UTType.movie.identifier].first(where: provider.hasItemConformingToTypeIdentifier) else { finish(false); return }
        provider.loadFileRepresentation(forTypeIdentifier: type) { source,error in
            var success = false
            if error == nil,let source {
                let suffix = source.pathExtension.filter { $0.isASCII && $0.isLetter || $0.isNumber }
                let filename = id+"."+(suffix.isEmpty ? "media" : String(suffix.prefix(10)))
                let target = directory.appendingPathComponent(filename)
                do {
                    let attributes = try source.resourceValues(forKeys: [.fileSizeKey,.isRegularFileKey])
                    guard attributes.isRegularFile == true,let bytes = attributes.fileSize,bytes > 0,bytes <= 512*1024*1024 else { throw CocoaError(.fileReadTooLarge) }
                    try FileManager.default.copyItem(at: source,to: target)
                    try JSONSerialization.data(withJSONObject: ["file": filename]).write(to: marker,options: .atomic); success = true
                } catch { try? FileManager.default.removeItem(at: target) }
            }
            Task { @MainActor in self.finish(success) }
        }
    }
    private func finish(_ success: Bool) {
        if success { extensionContext?.completeRequest(returningItems: nil) }
        else { extensionContext?.cancelRequest(withError: CocoaError(.fileReadCorruptFile)) }
    }
}
