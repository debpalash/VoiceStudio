import Foundation

extension String {
    var nilIfBlank: String? { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self }
}

/// The sole backend detail parser, shared by REST, file downloads and background upload receipts.
enum ServerFailure {
    static func parse(_ data: Data, status: Int) -> ClientError? {
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let message = (body["detail"] as? String)?.nilIfBlank {
            return ClientError("mobile.errors.server", params: ["message": message], status: status)
        }
        guard let detail = body["detail"] as? [String: Any] else { return nil }
        let message = (detail["message"] as? String)?.nilIfBlank
        if detail["code"] as? String == "model_licence_required" {
            let models = (detail["models"] as? [Any] ?? []).compactMap { model -> String? in
                if let name = model as? String { return name.nilIfBlank }
                guard let record = model as? [String: Any] else { return nil }
                let name = (record["license_name"] as? String)?.nilIfBlank ?? (record["license"] as? String)?.nilIfBlank
                let repo = (record["repo_id"] as? String)?.nilIfBlank
                return [repo, name].compactMap { $0 }.joined(separator: " · ").nilIfBlank
            }
            var seen = Set<String>()
            let list = models.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { seen.insert($0.lowercased()).inserted }.joined(separator: "\n")
            // The typed list replaces the server's desktop acceptance prose. Concatenating
            // both repeats model names and the instruction to open Model Catalogue.
            // Preserve server diagnostics when structured model details are unavailable.
            return ClientError("mobile.errors.model_licence_required", params: ["models": list.nilIfBlank ?? message ?? ""], status: status)
        }
        return message.map { ClientError("mobile.errors.server", params: ["message": $0], status: status) }
    }
}
