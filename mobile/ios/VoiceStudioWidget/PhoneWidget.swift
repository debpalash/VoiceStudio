import SwiftUI
import WidgetKit

private struct Entry: TimelineEntry { let date: Date }
private struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: .now) }
    func getSnapshot(in context: Context,completion: @escaping (Entry)->Void) { completion(Entry(date: .now)) }
    func getTimeline(in context: Context,completion: @escaping (Timeline<Entry>)->Void) { completion(Timeline(entries: [Entry(date: .now)],policy: .never)) }
}
@main struct PhoneWidget: Widget {
    let kind = "VoiceStudioWorkspace"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind,provider: Provider()) { _ in
            HStack {
                Link(destination: URL(string: "voicestudio://workspace/speak")!) { Label("mobile.integration.shortcut_speak",systemImage: "waveform") }.frame(maxWidth: .infinity)
                Link(destination: URL(string: "voicestudio://workspace/dictate")!) { Label("mobile.integration.shortcut_dictate",systemImage: "mic") }.frame(maxWidth: .infinity)
            }.font(.headline).containerBackground(.background,for: .widget)
        }.configurationDisplayName("VoiceStudio")
            .description("mobile.integration.widget_description")
            .supportedFamilies([.systemMedium])
    }
}
