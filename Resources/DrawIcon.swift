// Encodes the approved B2 artwork in the macOS icon canvas, with transparent tile corners.
// Artwork is generated separately; this step only fits it into the platform icon mask.
import AppKit
let source = NSImage(contentsOfFile: CommandLine.arguments[1])!
let size = 1024
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSGraphicsContext.current?.imageInterpolation = .high
let tile = NSRect(x: 80, y: 80, width: 864, height: 864)
NSBezierPath(roundedRect: tile, xRadius: 180, yRadius: 180).addClip()
// Approved export: 1254 square; inner tile excludes its baked preview backdrop.
source.draw(in: tile, from: NSRect(x: 122, y: 142, width: 1010, height: 980),
    operation: .copy, fraction: 1)
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
