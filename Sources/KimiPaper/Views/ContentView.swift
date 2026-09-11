import SwiftUI

struct ContentView: View {
    @ObservedObject var workspace: PaperWorkspace
    @State private var panel: String?
    @State private var showHelp = false
    @State private var showFiles = false
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 28, height: 28)
                    .accessibilityHidden(true)
                Text("Kimi Paper").font(.system(size: 14, weight: .semibold))
                Rectangle().fill(Atelier.line).frame(width: 1, height: 18).padding(.horizontal, 2)
                Button { workspace.choosePaper() } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "folder").foregroundStyle(Atelier.olive)
                        Text(workspace.projectRoot?.lastPathComponent ?? "打开文件夹").lineLimit(1).truncationMode(.middle)
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                }
                .buttonStyle(.plain)
                .help(workspace.projectRoot?.path ?? "打开文档文件夹")
                .accessibilityLabel("更换文档文件夹")
                .disabled(workspace.connecting || workspace.sending || workspace.maintenance)
                Spacer(minLength: 16)
                if workspace.connecting || workspace.busy {
                    ProgressView().controlSize(.small)
                    Text(workspace.busy ? "Kimi 正在工作" : "正在连接").font(.caption).foregroundStyle(.secondary)
                }
                Button { panel = "git" } label: { Label("GitHub", systemImage: "arrow.triangle.branch") }
                    .disabled(workspace.sending || workspace.maintenance || workspace.connecting || workspace.tasksURL == nil)
                Menu {
                    Button("更换文档文件夹…") { workspace.choosePaper() }
                        .disabled(workspace.connecting || workspace.sending || workspace.maintenance)
                    Divider()
                    Button("重新编译 PDF") { workspace.studioAction("freeze") }
                        .disabled(!workspace.canCompile || workspace.busy || workspace.sending || workspace.maintenance)
                    Button("翻译设置") { panel = "settings" }
                    Button("刷新右侧文档") { workspace.paperRevision = UUID(); workspace.refreshDocument() }
                        .disabled(workspace.sending || workspace.maintenance)
                    Button("重新连接") { workspace.reconnect() }
                        .disabled(workspace.connecting || workspace.busy || workspace.sending || workspace.maintenance)
                    Divider()
                    Button("打开示例项目") { workspace.openExample() }
                        .disabled(workspace.busy || workspace.sending || workspace.maintenance)
                    Button("使用说明") { showHelp = true }
                } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).fixedSize()
            }.padding(.horizontal, 18).padding(.vertical, 9).background(Atelier.ivory)
            Divider()
            if let issue = workspace.error {
                HStack { Text(issue).font(.callout); Spacer(); Button("关闭") { workspace.error = nil } }
                    .padding(10).background(.orange.opacity(0.08))
            }
            HSplitView {
                VStack(spacing: 0) {
                    if let url = workspace.chatURL {
                        ZStack {
                            WebPane(url: url, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, onLocation: workspace.chatNavigated).id(url.port)
                                .allowsHitTesting(!workspace.maintenance)
                            if workspace.maintenance {
                                Color(nsColor: .windowBackgroundColor).opacity(0.88)
                                VStack(spacing: 12) { ProgressView(); Text("正在安全处理 Git 操作…") }
                            }
                        }
                    } else { empty("Kimi 工作台", symbol: "sparkles") }
                }.frame(minWidth: 360, idealWidth: 540)
                VStack(spacing: 0) {
                    HStack {
                        Button { showFiles.toggle(); if let directory = workspace.browseDirectory { workspace.browse(directory) } } label: {
                            Label("文件", systemImage: "sidebar.left")
                        }.buttonStyle(.borderless).help("浏览文件夹中的文档")
                            .popover(isPresented: $showFiles, arrowEdge: .bottom) { fileBrowser }
                        Spacer()
                        Text(paperLocation).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle).help(workspace.selectedDocument?.path ?? "")
                        Button {
                            if workspace.selectedDocument?.pathExtension.lowercased() == "tex" { workspace.studioAction("freeze") }
                            else { workspace.refreshDocument() }
                        } label: { Image(systemName: "arrow.clockwise") }
                            .buttonStyle(.borderless).help("刷新当前文档")
                            .disabled(workspace.sending || workspace.maintenance || workspace.selectedDocument == nil)
                    }.font(.caption).padding(.horizontal, 12).padding(.vertical, 8).background(Atelier.ivory)
                    Divider()
                    if let file = workspace.selectedDocument, let root = workspace.projectRoot {
                        if ReaderKind.of(file) == .latex, !workspace.documentsOnly {
                            if let url = workspace.reviewURL {
                                WebPane(url: url, revision: workspace.paperRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation, agentAction: workspace.scopedAgentAction())
                                    .id(String(url.port ?? 0) + workspace.documentRevision.uuidString)
                            } else { empty("正在准备论文预览…", symbol: "doc.richtext") }
                        } else {
                            DocumentReader(workspace: workspace, file: file, root: root, revision: workspace.documentRevision)
                                .id(file.path)
                        }
                    } else {
                        VStack(spacing: 16) {
                            Image(systemName: "folder").font(.system(size: 36, weight: .light))
                            Text("打开文件，边读边修改").font(.headline)
                            Text("PDF · Markdown · 文本 · 图片 · LaTeX").font(.caption)
                            Button("浏览文件") { showFiles = true }.buttonStyle(.bordered)
                        }.foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }.frame(minWidth: 500, idealWidth: 780)
            }
        }.frame(minWidth: 940, minHeight: 640).background(Atelier.ivory)
        .tint(Atelier.olive)
        .preferredColorScheme(.light)
        .task { workspace.launch() }
        .sheet(isPresented: Binding(get: { panel != nil }, set: { if !$0 { panel = nil } })) {
            VStack(spacing: 0) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(panel == "git" ? "GitHub" : "翻译设置").font(.headline)
                        if panel == "git" { Text(workspace.projectRoot?.path ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }
                    }
                    Spacer()
                    Button("完成") { panel = nil }.disabled(workspace.maintenance)
                }.padding(16)
                if let base = workspace.tasksURL, var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) {
                    let _ = parts.queryItems = [
                        URLQueryItem(name: "tab", value: panel),
                        URLQueryItem(name: "simple", value: panel == "git" ? "1" : "0")
                    ]
                    WebPane(url: parts.url!, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation, agentAction: workspace.scopedAgentAction())
                }
            }
            .frame(width: panel == "git" ? 760 : 720, height: 680)
            .interactiveDismissDisabled(workspace.maintenance)
        }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Text("从初稿写到定稿").font(.title2.bold())
                Text("左侧使用 Kimi 自己的工作区与会话，适合起草和整体修改。\n\n顶部打开右侧文档文件夹，点击“文件”浏览子目录。Kimi 工作时也能切换 PDF、Markdown、文本与图片；打开 LaTeX 主文件会显示编译后的论文。\n\n划选文字后可翻译或发送批注。文本批注明确绑定源文件；独立 PDF 的批注会要求 Kimi 先核对可编辑源稿。图片支持预览，扫描 PDF 暂不提供 OCR。\n\nGitHub 操作范围是顶部所选文件夹。Git 写入前仍要求所有 Kimi 任务空闲，完成后恢复会话。")
                Button("明白了") { showHelp = false }
            }.padding(28).frame(width: 540)
        }
    }
    private func empty(_ title: String, symbol: String) -> some View {
        VStack(spacing: 16) { Image(systemName: symbol).font(.largeTitle); Text(workspace.connecting ? "正在连接…" : title) }.foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    private var paperLocation: String {
        guard let paper = workspace.selectedDocument else { return "尚未打开文档" }
        guard let root = workspace.projectRoot, paper.path.hasPrefix(root.path + "/") else {
            return paper.lastPathComponent
        }
        return String(paper.path.dropFirst(root.path.count + 1))
    }

    private var fileBrowser: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button {
                    if let directory = workspace.browseDirectory { workspace.browse(directory.deletingLastPathComponent()) }
                } label: { Image(systemName: "chevron.left") }
                    .disabled(workspace.browseDirectory == workspace.projectRoot)
                Text(workspace.browseDirectory?.lastPathComponent ?? "文件").font(.headline).lineLimit(1)
                Spacer()
                Button { if let directory = workspace.browseDirectory { workspace.browse(directory) } } label: { Image(systemName: "arrow.clockwise") }.help("刷新文件列表")
            }.buttonStyle(.borderless).padding(14)
            Divider()
            if workspace.listing { ProgressView().frame(maxWidth: .infinity).padding() }
            if let issue = workspace.listingError { Text(issue).foregroundStyle(.secondary).padding(14) }
            ScrollView {
                LazyVStack(spacing: 2) {
                    ForEach(workspace.entries) { entry in
                        Button {
                            if entry.directory { workspace.browse(entry.url) }
                            else { workspace.openDocument(entry.url); showFiles = false }
                        } label: {
                            HStack(spacing: 10) {
                                Image(systemName: entry.directory ? "folder" : (entry.kind == .image ? "photo" : "doc.text")).foregroundStyle(Atelier.olive)
                                Text(entry.url.lastPathComponent).lineLimit(1).truncationMode(.middle)
                                Spacer(minLength: 4)
                                if entry.url == workspace.selectedDocument { Image(systemName: "checkmark").foregroundStyle(Atelier.olive) }
                                else if entry.directory { Image(systemName: "chevron.right").foregroundStyle(.tertiary) }
                            }.padding(.horizontal, 12).padding(.vertical, 9).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                            .disabled(!entry.directory && (workspace.sending || workspace.maintenance || (workspace.connecting && entry.kind == .latex)))
                    }
                    if !workspace.listing, workspace.entries.isEmpty, workspace.listingError == nil { Text("这个文件夹是空的").foregroundStyle(.secondary).padding(20) }
                }.padding(6)
            }.frame(height: 350)
            Divider()
            Button("打开其他文件夹…") { showFiles = false; workspace.choosePaper() }
                .buttonStyle(.borderless).padding(14).disabled(workspace.connecting || workspace.sending || workspace.maintenance)
        }.frame(width: 340).background(Atelier.ivory)
    }
}

private enum Atelier {
    static let ivory = Color(red: 0.973, green: 0.961, blue: 0.933)
    static let olive = Color(red: 0.275, green: 0.329, blue: 0.231)
    static let line = Color(red: 0.886, green: 0.871, blue: 0.827)
}
