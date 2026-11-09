import Foundation
import UniformTypeIdentifiers

/// The picked name remains independent of any staging or upload URL.
public struct SelectedMedia: Sendable {
    public let url: URL
    public let name: String
    public init(url: URL, name: String? = nil) {
        self.url = url
        self.name = name?.nilIfBlank ?? url.lastPathComponent
    }

    /// The host rejects uploads whose name has no media extension, and providers
    /// can omit it; add the file type's extension when the name lacks a matching one.
    public var uploadName: String {
        let type = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType
        let ext = (type?.preferredFilenameExtension ?? url.pathExtension).lowercased()
        guard !ext.isEmpty, (name as NSString).pathExtension.lowercased() != ext else { return name }
        return "\(name).\(ext)"
    }
}
