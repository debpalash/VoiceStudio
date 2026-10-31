import SwiftUI
import VoiceStudioKit

private struct DesignCatalogue: Decodable {
    let categories: [DesignCategory]
}
private struct DesignCategory: Decodable, Identifiable {
    let id: String
    let label: String
    let options: [DesignOption]
}
private struct DesignOption: Decodable { let token: String; let label: String }
private struct DesignedTake {
    let audio: LocalAudio
    let text: String
    let instruction: String
    let seed: UInt64
    let model: PhoneModel
    let file: URL
}

struct VoiceDesignView: View {
    var communityRecipe: GalleryEntry? = nil
    @State private var appliedCommunityRecipe = false
    @State private var sourceAttribution: [String:String]?
    @Environment(AppModel.self) private var model
    @Environment(\.vsTheme) private var theme
    @AppStorage("design.seed") private var seed = 42
    @State private var categories: [DesignCategory] = []
    @State private var traits: [String:String] = [:]
    @State private var script = ""
    @State private var name = ""
    @State private var take: DesignedTake?
    @State private var savedVoice: PhoneVoice?
    @State private var recipeID = ""
    @State private var extraInstruction = ""
    @State private var player = AudioPlayer()
    @State private var running = false
    @State private var progress = 0.0
    @State private var work: Task<Void,Never>?
    @State private var shared: SharedFile?
    @State private var reporting: ContentReportSubject?
    private var engineModel: PhoneModel? { let chosen = model.phone.selectedModel("design",.clone); return chosen?.supportsDesign == true ? chosen : model.phone.candidates(.clone).first(where: \.supportsDesign) }
    private var instruction: String { (categories.compactMap { traits[$0.id] }.filter { !$0.isEmpty }+[extraInstruction].filter { !$0.isEmpty }).joined(separator: ", ") }
    var body: some View {
        Page {
            if let communityRecipe { Text(communityRecipe.name).font(.headline) }
            Text(model.t("mobile.local.this_phone")).font(.caption).foregroundStyle(theme.color("textMuted"))
            VStack(spacing: VSTokens.spacingGap) {
                ChoicePicker(label: model.t("mobile.books.restore_recipe"),selection: Binding(get: { recipeID },set: { id in
                    recipeID = id
                    guard let voice = model.phone.voices.first(where: { $0.id.uuidString == id }), let recipe = voice.designInstruction else { return }
                    let tokens = recipe.components(separatedBy: ", ")
                    traits = [:]
                    for category in categories { traits[category.id] = category.options.first { tokens.contains($0.token) }?.token }
                    let known = Set(categories.flatMap { $0.options.map(\.token) })
                    extraInstruction = tokens.filter { !known.contains($0) }.joined(separator: ", ")
                    sourceAttribution = voice.sourceAttribution
                    seed = Int(clamping: voice.designSeed ?? 42); script = voice.transcript; name = voice.name; take = nil; savedVoice = nil; player.clear()
                    if let source = model.phone.candidates(.clone).first(where: { $0.repo == voice.modelRepo && $0.revision == voice.modelRevision }) { model.phone.chooseModel("design",source.id) }
                }),options: [SelectionOption(id: "",title: model.t("common.not_set"))]+model.phone.voices.filter { $0.designInstruction != nil }.map { SelectionOption(id: $0.id.uuidString,title: $0.name) })
                if !extraInstruction.isEmpty { TextField(model.t("clone.describe_label"),text: $extraInstruction,axis: .vertical) }

                ForEach(categories) { category in
                    ChoicePicker(label: model.t(category.label),selection: Binding(get: { traits[category.id] ?? "" },set: { traits[category.id] = $0 }),options: category.options.map { SelectionOption(id: $0.token,title: model.t($0.label)) })
                }
                HStack {
                    Text(model.t("clone.seed_label"))
                    TextField(model.t("clone.seed_label"),value: $seed,format: .number).keyboardType(.numberPad).monospacedDigit().frame(maxWidth: .infinity,alignment: .leading)
                    Button(model.t("clone.seed_reroll"),systemImage: "shuffle") { seed = Int.random(in: 0...Int(UInt32.max)) }.studioButton(.tertiary)
                }
                TextField(model.t("clone.script"),text: $script,axis: .vertical).lineLimit(4...8).accessibilityIdentifier("design.script")
                ChoicePicker(label: model.t("mobile.speak.engine"),selection: Binding(get: { engineModel?.id ?? "" },set: { model.phone.chooseModel("design",$0) }),options: model.phone.candidates(.clone).filter(\.supportsDesign).map { SelectionOption(id: $0.id,title: $0.name,enabled: $0.fits()) })
            }.disabled(running)
            if let item = engineModel, !model.phone.ready(item) { LocalDownloadPrompt(items: [item],feature: "nav.design") }
            if running {
                ProgressView(value: progress)
                Button(model.t("common.cancel"),systemImage: "stop") { work?.cancel() }.studioButton(.tertiary)
            }
            if let take {
                Card {
                    AudioPlayerView(player: player)
                    Button(model.t("mobile.speak.share"),systemImage: "square.and.arrow.up") { shared = SharedFile(url: take.file) }.studioButton(.tertiary)
                    ReportButton(subject: .phone(.speak,id: take.file.lastPathComponent,repo: take.model.repo,revision: take.model.revision,typedText: take.text),reporting: $reporting)
                    TextField(model.t("mobile.voices.name_label"),text: $name)
                    Button(model.t(recipeID.isEmpty ? "clone.save_design_as_profile" : "mobile.books.save_recipe_copy"),systemImage: "person.crop.circle.badge.plus") { save(take) }.studioButton(.primary).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || running || savedVoice != nil || model.busy)
                    if let savedVoice { NavigationLink(savedVoice.name) { SpeakView(initialPhoneVoiceID: savedVoice.id.uuidString) } }
                }
            }
        }
        .navigationTitle(model.t("nav.design")).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom,spacing: 0) {
            Button(model.t("mobile.speak.generate"),systemImage: "waveform") { generate() }.studioButton(.primary)
                .disabled(running || script.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || script.utf16.count > 2000 || engineModel.map(model.phone.ready) != true || categories.isEmpty)
                .padding(.horizontal,Theme.compactInset).padding(.vertical,VSTokens.spacingGap/2)
        }
        .task {
            await model.phone.refresh()
            do {
                guard let file = Bundle.main.url(forResource: "design",withExtension: "json",subdirectory: "Catalogs") else { throw ClientError("mobile.local.catalogue_failed") }
                categories = try JSONDecoder().decode(DesignCatalogue.self,from: Data(contentsOf: file)).categories
                if let recipe = communityRecipe, !appliedCommunityRecipe {
                    let tokens = (recipe.instruct ?? "").components(separatedBy: ", ")
                    for category in categories { traits[category.id] = category.options.first { tokens.contains($0.token) }?.token }
                    let known = Set(categories.flatMap { $0.options.map(\.token) })
                    extraInstruction = tokens.filter { !known.contains($0) }.joined(separator: ", ")
                    sourceAttribution = recipe.attribution
                    script = recipe.sample_script ?? ""; name = recipe.name; seed = 42; appliedCommunityRecipe = true
                }
            } catch { model.error = ClientError("mobile.local.catalogue_failed") }
        }
        .sheet(item: $shared) { ShareSheet(items: [$0.url]) }
        .contentReportSheet($reporting)
        .onDisappear { work?.cancel(); player.clear() }
    }
    private func generate() {
        guard let item = engineModel, let engine = model.phone.engine else { return }
        let text = script, direction = instruction, selectedSeed = UInt64(max(0,seed))
        running = true; progress = 0; take = nil; savedVoice = nil; player.clear()
        work = Task {
            defer { running = false; work = nil }
            await model.performPhone {
                let audio = try await engine.design(model: item,text: text,instruction: direction,seed: selectedSeed,progress: { value in await MainActor.run { progress = value.fraction } })
                let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".wav")
                try audio.markSynthetic().write(to: file,options: .atomic)
                take = DesignedTake(audio: audio,text: text,instruction: direction,seed: selectedSeed,model: item,file: file)
                try await player.load(file: file); player.play()
            }
        }
    }
    private func save(_ take: DesignedTake) {
        Task { await model.performPhone {
            savedVoice = try await model.phone.voiceStore?.saveDesign(name: name,text: take.text,audio: take.audio,model: take.model,instruction: take.instruction,seed: take.seed,attribution: sourceAttribution)
            await model.phone.refresh()
        } }
    }
}
