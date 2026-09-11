import SwiftUI
import PDFKit
import ImageIO

// Only internal page navigation is performed; document links cannot launch
// programs, network requests, remote PDFs, or PDF JavaScript actions.
final class ReaderPDFView: PDFView {
    override func perform(_ action: PDFAction) {
        if action is PDFActionGoTo { super.perform(action) }
    }
}

@MainActor
final class ReaderInteraction: ObservableObject {
    @Published var quote = ""
    @Published var pages = ""
    @Published var translated = ""
    @Published var issue: String?
    @Published var loading = false
    @Published var editing = false
    @Published var instruction = ""
    @Published var revision = UUID()
    weak var card: NSView?

    func dismiss() {
        revision = UUID(); quote = ""; pages = ""; translated = ""
        issue = nil; loading = false; editing = false; instruction = ""
    }
    func select(_ text: String, pages: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if text == quote, pages == self.pages { return }
        dismiss()
        guard !text.isEmpty else { return }
        if text.count > 16000 { issue = "选中文字超过 16000 字，请分段划选。"; return }
        quote = text; self.pages = pages
    }
}

struct DocumentReader: View {
    @ObservedObject var workspace: PaperWorkspace
    let file: URL
    let root: URL
    let revision: UUID
    @State private var snapshot: ReaderSnapshot?
    @State private var loadError: String?
    @StateObject private var interaction = ReaderInteraction()
    @AppStorage("translation.enabled") private var autoTranslate = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Toggle("划选后自动翻译", isOn: $autoTranslate).toggleStyle(.checkbox)
                Spacer()
                Text(ReaderKind.of(file) == .pdf ? "划选批注 · 核对源稿后修改" : "划选批注 · 发送到左侧会话")
                    .foregroundStyle(.secondary).lineLimit(1)
            }.font(.caption).padding(10)
            Divider()
            ZStack(alignment: .topTrailing) {
                if let snapshot {
                    NativeDocumentPreview(snapshot: snapshot, interaction: interaction).id(snapshot.digest + file.path)
                } else if let loadError {
                    VStack(spacing: 14) {
                        Image(systemName: "doc.questionmark").font(.largeTitle)
                        Text(loadError).multilineTextAlignment(.center)
                        Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([file]) }
                    }.foregroundStyle(.secondary).padding(28).frame(maxWidth: .infinity, maxHeight: .infinity)
                } else { ProgressView("正在打开文档…").frame(maxWidth: .infinity, maxHeight: .infinity) }
                if !interaction.quote.isEmpty { selectionCard.padding(12) }
                else if let issue = interaction.issue {
                    HStack { Text(issue); Button("关闭") { interaction.dismiss() } }.padding(12).background(.regularMaterial)
                }
            }
        }
        .task(id: revision) {
            snapshot = nil; loadError = nil; interaction.dismiss()
            do {
                let reader = Task.detached { try DocumentLibrary.read(file, root: root) }
                let loaded = try await withTaskCancellationHandler(operation: { try await reader.value }, onCancel: { reader.cancel() })
                try Task.checkCancellation()
                snapshot = loaded
            } catch is CancellationError { }
            catch { loadError = error.localizedDescription }
        }
        .task(id: interaction.revision) {
            guard autoTranslate, !interaction.quote.isEmpty else { return }
            await translate()
        }
    }

    private var selectionCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(interaction.pages.isEmpty ? "选中文字" : "第 \(interaction.pages) 页", systemImage: "text.quote").font(.caption)
                Spacer()
                Button { interaction.dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).help("关闭").accessibilityLabel("关闭选区浮窗")
            }
            if interaction.loading { ProgressView("正在翻译…").controlSize(.small) }
            ScrollView {
                Text(interaction.translated.isEmpty ? interaction.quote : interaction.translated)
                    .font(.system(size: 14)).lineSpacing(4).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.frame(height: selectionHeight)
            if let issue = interaction.issue { Text(issue).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            if interaction.editing {
                TextField("希望如何修改这段？", text: $interaction.instruction, axis: .vertical)
                    .lineLimit(2...5).textFieldStyle(.roundedBorder)
                Button(workspace.sending ? "正在发送…" : "发送到左侧 Kimi") {
                    guard let snapshot else { return }
                    let quote = interaction.quote, pages = interaction.pages, text = interaction.instruction, turn = interaction.revision
                    Task {
                        do {
                            try await workspace.sendDocumentAnnotation(snapshot: snapshot, revision: revision, quote: quote, pages: pages, instruction: text)
                            if interaction.revision == turn { interaction.dismiss() }
                        } catch {
                            if interaction.revision == turn { interaction.issue = error.localizedDescription }
                        }
                    }
                }.buttonStyle(.borderedProminent)
                    .disabled(workspace.sending || workspace.maintenance || interaction.instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            } else {
                HStack {
                    Button("修改这段") { interaction.editing = true }
                        .buttonStyle(.borderedProminent)
                    Button("翻译") { Task { await translate() } }.disabled(interaction.loading)
                }
            }
        }.padding(14).frame(width: 292)
            .background(Color(red: 0.99, green: 0.98, blue: 0.95), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(.gray.opacity(0.25)))
            .shadow(color: .black.opacity(0.13), radius: 12, y: 4)
            .background(ReaderCardAnchor(interaction: interaction))
    }

    private var selectionHeight: CGFloat {
        let value = interaction.translated.isEmpty ? interaction.quote : interaction.translated
        let lines = value.components(separatedBy: "\n").reduce(0) { $0 + max(1, ($1.count + 27) / 28) }
        return min(180, max(36, CGFloat(lines) * 23))
    }

    private func translate() async {
        let turn = interaction.revision, quote = interaction.quote
        guard !quote.isEmpty, !interaction.loading else { return }
        interaction.loading = true; interaction.issue = nil
        do {
            let result = try await workspace.translation.handle(["operation": "translate", "text": quote])
            guard interaction.revision == turn else { return }
            interaction.translated = result["text"] as? String ?? ""
        } catch {
            if interaction.revision == turn { interaction.issue = error.localizedDescription }
        }
        if interaction.revision == turn { interaction.loading = false }
    }
}

private struct ReaderCardAnchor: NSViewRepresentable {
    let interaction: ReaderInteraction
    func makeNSView(context: Context) -> NSView { let view = NSView(); interaction.card = view; return view }
    func updateNSView(_ view: NSView, context: Context) { interaction.card = view }
}

struct NativeDocumentPreview: NSViewRepresentable {
    let snapshot: ReaderSnapshot
    let interaction: ReaderInteraction
    func makeCoordinator() -> Coordinator { Coordinator(interaction: interaction) }
    func makeNSView(context: Context) -> NSView {
        let container = NSView()
        let content: NSView
        switch snapshot.kind {
        case .pdf:
            let pdf = ReaderPDFView()
            pdf.document = PDFDocument(data: snapshot.bytes)
            pdf.autoScales = true; pdf.displayMode = .singlePageContinuous; pdf.displaysPageBreaks = true
            pdf.backgroundColor = NSColor(red: 0.95, green: 0.94, blue: 0.91, alpha: 1)
            if pdf.document == nil { content = message("这个 PDF 无法读取或需要密码，请使用外部应用打开。") }
            else { content = pdf; context.coordinator.pdf = pdf }
        case .image:
            if let source = CGImageSourceCreateWithData(snapshot.bytes as CFData, nil),
               let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
               let width = properties[kCGImagePropertyPixelWidth] as? Int,
               let height = properties[kCGImagePropertyPixelHeight] as? Int,
               width > 0, height > 0, width <= 30000, height <= 30000, width * height <= 80_000_000,
               let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceThumbnailMaxPixelSize: 2400,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceShouldCacheImmediately: false
               ] as CFDictionary) {
                let view = NSImageView(); view.image = NSImage(cgImage: image, size: .zero)
                view.imageScaling = .scaleProportionallyUpOrDown; content = view
            } else { content = message("图片无法读取或像素超过预览限制，请使用外部应用打开。") }
        default:
            let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
            let text = NSTextView()
            text.frame = NSRect(x: 0, y: 0, width: 500, height: 1)
            text.minSize = .zero; text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
            text.isEditable = false; text.isSelectable = true; text.isRichText = true
            text.isAutomaticLinkDetectionEnabled = false
            text.textContainerInset = NSSize(width: 28, height: 24)
            text.isVerticallyResizable = true; text.isHorizontallyResizable = false
            text.autoresizingMask = [.width]; text.textContainer?.widthTracksTextView = true
            text.textContainer?.containerSize = NSSize(width: 500, height: CGFloat.greatestFiniteMagnitude)
            text.backgroundColor = NSColor(red: 0.99, green: 0.98, blue: 0.96, alpha: 1)
            if let source = String(data: snapshot.bytes, encoding: .utf8) {
                // Very large Markdown stays selectable as source instead of blocking
                // the main thread with hundreds of thousands of attributed runs.
                text.textStorage?.setAttributedString(Self.formatted(source, markdown: snapshot.kind == .markdown && source.utf8.count <= 500_000))
            } else { text.string = "该文本不是 UTF-8 编码，请使用外部编辑器转换后打开。"; text.isSelectable = false }
            scroll.documentView = text; content = scroll; context.coordinator.text = text
        }
        container.addSubview(content); content.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([content.leadingAnchor.constraint(equalTo: container.leadingAnchor), content.trailingAnchor.constraint(equalTo: container.trailingAnchor), content.topAnchor.constraint(equalTo: container.topAnchor), content.bottomAnchor.constraint(equalTo: container.bottomAnchor)])
        context.coordinator.install(container)
        return container
    }
    func updateNSView(_ view: NSView, context: Context) { }
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) { coordinator.stop() }

    private func message(_ value: String) -> NSView {
        let text = NSTextField(wrappingLabelWithString: value); text.alignment = .center; return text
    }

    static func formatted(_ source: String, markdown: Bool) -> NSAttributedString {
        let output = NSMutableAttributedString(string: "")
        var code = false
        for line in source.components(separatedBy: "\n") {
            var value = line, size: CGFloat = 15
            var font = NSFont.systemFont(ofSize: size)
            if markdown {
                if line.hasPrefix("```") { code.toggle(); continue }
                if !code {
                    let level = line.prefix(while: { $0 == "#" }).count
                    if (1...6).contains(level), line.dropFirst(level).hasPrefix(" ") {
                        value = String(line.dropFirst(level + 1)); size = max(16, 29 - CGFloat(level) * 2)
                        font = NSFont.systemFont(ofSize: size, weight: .semibold)
                    } else if line.hasPrefix("- ") || line.hasPrefix("* ") { value = "• " + line.dropFirst(2) }
                }
            }
            if code || !markdown { font = NSFont.monospacedSystemFont(ofSize: 14, weight: .regular) }
            let paragraph = NSMutableParagraphStyle(); paragraph.lineSpacing = 5; paragraph.paragraphSpacing = markdown ? 7 : 1
            let row: NSMutableAttributedString
            if markdown, !code, let inline = try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                row = NSMutableAttributedString(attributedString: NSAttributedString(inline))
                // Links are text only. The reader never follows document-defined URLs.
                row.removeAttribute(.link, range: NSRange(location: 0, length: row.length))
            } else { row = NSMutableAttributedString(string: value) }
            row.append(NSAttributedString(string: "\n"))
            row.addAttributes([.font: font, .foregroundColor: NSColor.labelColor, .paragraphStyle: paragraph], range: NSRange(location: 0, length: row.length))
            output.append(row)
        }
        return output
    }

    @MainActor final class Coordinator {
        let interaction: ReaderInteraction
        weak var pdf: PDFView?
        weak var text: NSTextView?
        weak var container: NSView?
        private var monitor: Any?
        private var selecting = false
        private var observation: NSObjectProtocol?
        private var releaseTimer: Timer?
        private var captureGeneration = UUID()
        init(interaction: ReaderInteraction) { self.interaction = interaction }
        func install(_ view: NSView) {
            container = view
            // NSTextView/PDFKit may consume mouseUp inside their own tracking
            // loop. Observe native selection and wait for physical release too.
            if let object = (pdf as NSView?) ?? text {
                let name = pdf != nil ? Notification.Name.PDFViewSelectionChanged : NSTextView.didChangeSelectionNotification
                observation = NotificationCenter.default.addObserver(forName: name, object: object, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.awaitRelease() }
                }
            }
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .leftMouseUp, .keyUp]) { [weak self] event in
                guard let self, let container = self.container, event.window === container.window else { return event }
                let inside = container.bounds.contains(container.convert(event.locationInWindow, from: nil))
                let inCard = self.interaction.card.map { $0.bounds.contains($0.convert(event.locationInWindow, from: nil)) } ?? false
                if event.type == .leftMouseDown {
                    self.cancelCapture()
                    self.selecting = inside && !inCard
                    if !inside && !inCard { self.interaction.dismiss() }
                } else if event.type == .leftMouseUp && self.selecting {
                    self.selecting = false
                    self.scheduleCapture()
                } else if event.type == .keyUp && event.keyCode == 53 {
                    self.cancelCapture(); self.interaction.dismiss()
                }
                else if event.type == .keyUp && event.modifierFlags.contains(.shift) && inside {
                    self.scheduleCapture()
                }
                return event
            }
        }
        private func awaitRelease() {
            guard container != nil else { return }
            releaseTimer?.invalidate()
            let generation = captureGeneration, revision = interaction.revision
            let timer = Timer(timeInterval: 0.08, repeats: true) { [weak self] timer in
                MainActor.assumeIsolated {
                    guard let self, self.container != nil, self.captureGeneration == generation,
                          self.interaction.revision == revision else { timer.invalidate(); return }
                    guard NSEvent.pressedMouseButtons & 1 == 0 else { return }
                    timer.invalidate(); self.capture(generation: generation, revision: revision)
                }
            }
            releaseTimer = timer
            RunLoop.main.add(timer, forMode: .common)
        }
        private func scheduleCapture() {
            let generation = captureGeneration, revision = interaction.revision
            DispatchQueue.main.async { [weak self] in self?.capture(generation: generation, revision: revision) }
        }
        private func cancelCapture() {
            captureGeneration = UUID(); selecting = false
            releaseTimer?.invalidate(); releaseTimer = nil
        }
        private func capture(generation: UUID, revision: UUID) {
            guard container != nil, captureGeneration == generation, interaction.revision == revision else { return }
            if let pdf, let selection = pdf.currentSelection {
                let pages = selection.pages.compactMap { page in pdf.document.map { String($0.index(for: page) + 1) } }.joined(separator: "、")
                interaction.select(selection.string ?? "", pages: pages)
            } else if let text {
                let range = text.selectedRange()
                let source = text.string as NSString
                interaction.select(NSMaxRange(range) <= source.length ? source.substring(with: range) : "", pages: "")
            } else { interaction.dismiss() }
        }
        func stop() {
            cancelCapture(); container = nil
            if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil
            if let observation { NotificationCenter.default.removeObserver(observation) }; observation = nil
            pdf?.document = nil
        }
    }
}
