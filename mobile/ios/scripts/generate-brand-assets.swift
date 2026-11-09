// Build-time launch artwork. The App Store icon lives in the app's bundled catalog.
import CoreGraphics
import Foundation
import ImageIO

let arguments = CommandLine.arguments
guard arguments.count == 4 else { fatalError("Expected logo, tokens and asset-catalog paths") }
let output = URL(fileURLWithPath: arguments[3], isDirectory: true)
let logoURL = URL(fileURLWithPath: arguments[1])
guard let source = CGImageSourceCreateWithURL(logoURL as CFURL, nil),
      let logo = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fatalError("Cannot read repository logo") }
let tokens = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[2]))) as! [String: Any]
let palettes = tokens["palettes"] as! [[String: Any]]
let colors = palettes.first { $0["id"] as? String == "default" }!["colors"] as! [String: [String: String]]

func components(_ hex: String) -> [Double] {
    let digits = Array(hex.dropFirst())
    return stride(from: 0, to: 8, by: 2).map { Double(UInt8(String(digits[$0..<($0 + 2)]), radix: 16)!) / 255 }
}
func json(_ value: [String: Any], at directory: URL) throws {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]).write(to: directory.appendingPathComponent("Contents.json"))
}
func png(_ size: Int, at url: URL, background: String? = nil) throws {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    let alpha = background == nil ? CGImageAlphaInfo.premultipliedLast : CGImageAlphaInfo.noneSkipLast
    let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4, space: space, bitmapInfo: alpha.rawValue)!
    let bounds = CGRect(x: 0, y: 0, width: size, height: size)
    if let background {
        let c = components(background)
        context.setFillColor(CGColor(colorSpace: space, components: [c[0], c[1], c[2], 1])!)
        context.fill(bounds)
    }
    context.interpolationQuality = .high
    context.draw(logo, in: bounds)
    let image = context.makeImage()!
    // Catch empty/flat renders before they can become a launcher icon.
    let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
    guard stride(from: 0, to: size * size * 4, by: 4).contains(where: { offset in
        pixels[offset] != pixels[0] || pixels[offset + 1] != pixels[1] || pixels[offset + 2] != pixels[2]
    }) else { fatalError("Logo render is blank") }
    let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw CocoaError(.fileWriteUnknown) }
}
let info: [String: Any] = ["author": "xcode", "version": 1]
try json(["info": info], at: output)
let icons = output.appendingPathComponent("AppIcon.appiconset")
// Remove the former generated icon too, so incremental builds do not retain two sets.
if FileManager.default.fileExists(atPath: icons.path) { try FileManager.default.removeItem(at: icons) }
let launch = output.appendingPathComponent("LaunchLogo.imageset")
try json(["info": info, "images": (1...3).map { ["filename": "logo-\($0)x.png", "idiom": "universal", "scale": "\($0)x"] }], at: launch)
for scale in 1...3 { try png(128 * scale, at: launch.appendingPathComponent("logo-\(scale)x.png")) }
let canvas: [[String: Any]] = ["light", "dark"].map { appearance in
    let c = components(colors[appearance]!["canvas"]!)
    var value: [String: Any] = ["idiom": "universal", "color": ["color-space": "srgb", "components": ["red": String(c[0]), "green": String(c[1]), "blue": String(c[2]), "alpha": String(c[3])]]]
    if appearance == "dark" { value["appearances"] = [["appearance": "luminosity", "value": "dark"]] }
    return value
}
try json(["info": info, "colors": canvas], at: output.appendingPathComponent("LaunchCanvas.colorset"))
