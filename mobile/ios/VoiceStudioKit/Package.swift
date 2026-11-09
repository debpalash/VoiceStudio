// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VoiceStudioKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "VoiceStudioKit", targets: ["VoiceStudioKit"])],
    targets: [
        .target(name: "VSLame",path: "Sources/VSNative/Lame",
                exclude: ["COPYING","README","ChangeLog","config.h.in","source.json","libmp3lame/Makefile.am"],
                publicHeadersPath: "include",cSettings: [.headerSearchPath("."),.define("HAVE_CONFIG_H")],linkerSettings: [.linkedLibrary("m")]),
        .target(name: "VSNative",dependencies: ["VSLame"],exclude: ["Lame"],publicHeadersPath: "include",linkerSettings: [.linkedLibrary("z")]),
        .target(name: "VoiceStudioKit", dependencies: ["VSNative"]),
        .testTarget(name: "VoiceStudioKitTests", dependencies: ["VoiceStudioKit"])
    ],
    swiftLanguageModes: [.v6]
)
