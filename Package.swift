// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "KimiPaper",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "KimiPaper", targets: ["KimiPaper"])],
    targets: [
        .executableTarget(name: "KimiPaper"),
        .testTarget(name: "KimiPaperTests", dependencies: ["KimiPaper"])
    ],
    swiftLanguageModes: [.v5]
)
