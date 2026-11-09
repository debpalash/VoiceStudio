import Foundation

/// Literal pronunciation replacements at Unicode word boundaries; replacement text is never regex syntax.
enum NarrationText {
    static func rules(_ values: [String:String]) throws -> [(NSRegularExpression,String)] {
        try values.sorted { $0.key.count == $1.key.count ? $0.key < $1.key : $0.key.count > $1.key.count }.map { word,spoken in
            guard !word.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, word.count <= 100, !spoken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, spoken.count <= 500 else { throw ClientError("mobile.books.invalid_controls") }
            return (try NSRegularExpression(pattern: "(?<![\\p{L}\\p{N}_])"+NSRegularExpression.escapedPattern(for: word)+"(?![\\p{L}\\p{N}_])"),spoken)
        }
    }
    static func apply(_ line: String,rules: [(NSRegularExpression,String)]) throws -> String {
        // Match the original only: replacements do not recursively rewrite other pronunciation entries.
        var matches: [(NSRange,String)] = []
        for (regex,spoken) in rules {
            for match in regex.matches(in: line,range: NSRange(line.startIndex...,in: line)) where !matches.contains(where: { NSIntersectionRange($0.0,match.range).length > 0 }) { matches.append((match.range,spoken)) }
        }
        var result = "", cursor = line.startIndex, count = 0
        for (range,spoken) in matches.sorted(by: { $0.0.location < $1.0.location }) {
            guard let range = Range(range,in: line) else { throw ClientError("mobile.books.invalid_controls") }
            let prefix = line[cursor..<range.lowerBound]
            count += prefix.utf16.count+spoken.utf16.count
            guard count <= BookImport.textLimit else { throw ClientError("mobile.simple.audio_long") }
            result += prefix+spoken; cursor = range.upperBound
        }
        guard count+line[cursor...].utf16.count <= BookImport.textLimit else { throw ClientError("mobile.simple.audio_long") }
        result += line[cursor...]; return result
    }
}

/// Bounded overlap-add time stretching, shared with Android; changing speed keeps speech pitch.
enum NarrationAudio {
    static func speed(_ input: [Float],rate: Float) throws -> [Float] {
        guard rate.isFinite, (0.5...2).contains(rate), !input.isEmpty, input.count <= 14_400_000 else { throw ClientError("mobile.errors.audio") }
        if abs(rate-1) < 0.0001 { return input }
        let count = max(1,Int((Double(input.count)/Double(rate)).rounded())), window = min(960,input.count), hop = max(1,min(240,window/4))
        var output = [Float](repeating: 0,count: count+window), weights = [Float](repeating: 0,count: count+window)
        let envelope = (0..<window).map { Float(0.5-0.5*cos(2*Double.pi*(Double($0)+0.5)/Double(window))) }
        var destination = 0
        while destination < count {
            let expected = min(max(0,Int(Double(destination)*Double(rate))),input.count-window)
            var source = expected
            if destination > 0, window >= 240 {
                var best = -Double.infinity
                for candidate in stride(from: max(0,expected-120),through: min(input.count-window,expected+120),by: 8) {
                    var correlation = 0.0, energy = 0.000001
                    for offset in stride(from: 0,to: min(window-hop,count-destination),by: 8) {
                        let prior = weights[destination+offset] > 0 ? output[destination+offset]/weights[destination+offset] : 0
                        let sample = input[candidate+offset]
                        correlation += Double(prior*sample); energy += Double(sample*sample)
                    }
                    let score = correlation/sqrt(energy)
                    if score > best { best = score; source = candidate }
                }
            }
            for offset in 0..<min(window,count-destination) { output[destination+offset] += input[source+offset]*envelope[offset]; weights[destination+offset] += envelope[offset] }
            destination += hop
        }
        return (0..<count).map { weights[$0] > 0 ? output[$0]/weights[$0] : 0 }
    }
}
