import AppKit
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSGradient(starting: NSColor(calibratedRed: 0.34, green: 0.27, blue: 0.84, alpha: 1), ending: NSColor(calibratedRed: 0.14, green: 0.13, blue: 0.44, alpha: 1))!.draw(in: NSBezierPath(roundedRect: NSRect(x: 45, y: 45, width: 934, height: 934), xRadius: 210, yRadius: 210), angle: -65)
NSColor.white.setFill()
NSBezierPath(roundedRect: NSRect(x: 260, y: 228, width: 492, height: 610), xRadius: 40, yRadius: 40).fill()
NSColor(calibratedRed: 0.80, green: 0.80, blue: 0.94, alpha: 1).setFill()
for (width, y) in [(310.0, 695.0), (310.0, 600.0), (212.0, 505.0)] {
    NSBezierPath(roundedRect: NSRect(x: 350, y: y, width: width, height: 24), xRadius: 12, yRadius: 12).fill()
}
NSColor(calibratedRed: 0.33, green: 0.80, blue: 0.76, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 531, y: 154, width: 315, height: 242), xRadius: 58, yRadius: 58).fill()
let tail = NSBezierPath(); tail.move(to: NSPoint(x: 600, y: 200)); tail.line(to: NSPoint(x: 580, y: 112)); tail.line(to: NSPoint(x: 680, y: 176)); tail.close(); tail.fill()
NSColor(calibratedRed: 0.13, green: 0.32, blue: 0.39, alpha: 1).setStroke()
let check = NSBezierPath(); check.lineWidth = 20; check.lineCapStyle = .round; check.lineJoinStyle = .round
check.move(to: NSPoint(x: 615, y: 279)); check.line(to: NSPoint(x: 668, y: 231)); check.line(to: NSPoint(x: 759, y: 321)); check.stroke()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
