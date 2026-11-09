import Foundation

public enum DubVoiceChoice {
    public static let unselected = "_choose_voice_"
    public static let defaultLabelKey = "mobile.speak.voice_default"
    public static func validate(_ profile: String) throws {
        guard profile != unselected else { throw ClientError("mobile.dub.choose_voice") }
    }
}
