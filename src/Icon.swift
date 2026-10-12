import AppKit

let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let backdrop = NSBezierPath(roundedRect: NSRect(x: 45, y: 45, width: 934, height: 934), xRadius: 210, yRadius: 210)
NSGradient(starting: NSColor(calibratedRed: 0.13, green: 0.37, blue: 0.35, alpha: 1),
           ending: NSColor(calibratedRed: 0.27, green: 0.59, blue: 0.50, alpha: 1))!.draw(in: backdrop, angle: 65)
for (x, y, alpha) in [(240.0, 310.0, 0.32), (200.0, 370.0, 0.60), (160.0, 430.0, 1.0)] {
    let shadow = NSShadow(); shadow.shadowColor = .black.withAlphaComponent(0.12)
    shadow.shadowBlurRadius = 20; shadow.shadowOffset = NSSize(width: 5, height: -10)
    NSGraphicsContext.saveGraphicsState(); shadow.set()
    let card = NSBezierPath(roundedRect: NSRect(x: x, y: y, width: 550, height: 330), xRadius: 44, yRadius: 44)
    NSColor.white.withAlphaComponent(alpha).setFill(); card.fill()
    NSGraphicsContext.restoreGraphicsState()
}
NSColor(calibratedRed: 0.95, green: 0.64, blue: 0.37, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 215, y: 675, width: 28, height: 28)).fill()
NSColor(calibratedRed: 0.23, green: 0.49, blue: 0.42, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 215, y: 610, width: 440, height: 11), xRadius: 5, yRadius: 5).fill()
let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 132, weight: .semibold),
    .foregroundColor: NSColor(calibratedRed: 0.18, green: 0.43, blue: 0.38, alpha: 1)]
("C" as NSString).draw(at: NSPoint(x: 380, y: 445), withAttributes: attrs)
image.unlockFocus()
let target = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
            bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: representation)
        image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
        NSGraphicsContext.restoreGraphicsState()
        let filename = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try representation.representation(using: .png, properties: [:])!.write(to: target.appendingPathComponent(filename))
    }
}
