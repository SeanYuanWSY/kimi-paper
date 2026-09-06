import SwiftUI

struct ContentView: View {
    @ObservedObject var workspace: PaperWorkspace
    @State private var panel: String?
    @State private var showHelp = false
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                Image(systemName: "book.closed.fill").foregroundStyle(.indigo)
                Text("论文").foregroundStyle(.secondary)
                Text(paperLocation).fontWeight(.semibold)
                Text(workspace.paper?.path ?? "")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    .help(workspace.paper?.path ?? "")
                Spacer()
                if workspace.connecting || workspace.busy {
                    ProgressView().controlSize(.small)
                    Text(workspace.busy ? "Kimi 正在修改" : "正在连接").font(.caption).foregroundStyle(.secondary)
                }
                Button { panel = "git" } label: { Label("GitHub", systemImage: "arrow.triangle.branch") }
                    .disabled(workspace.sending || workspace.maintenance)
                Menu {
                    Button("更换右侧论文…") { workspace.choosePaper() }
                        .disabled(workspace.connecting || workspace.busy || workspace.maintenance)
                    Divider()
                    Button("重新编译 PDF") { workspace.studioAction("freeze") }
                        .disabled(workspace.busy || workspace.sending || workspace.maintenance)
                    Button("翻译设置") { panel = "settings" }
                    Button("刷新界面") { workspace.webRevision = UUID(); workspace.paperRevision = UUID() }
                    Button("重新连接") { workspace.reconnect() }
                        .disabled(workspace.busy || workspace.sending || workspace.maintenance)
                    Divider()
                    Button("打开示例项目") { workspace.openExample() }
                        .disabled(workspace.busy || workspace.sending || workspace.maintenance)
                    Button("使用说明") { showHelp = true }
                } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).fixedSize()
            }.padding(.horizontal, 20).padding(.vertical, 14)
            Divider()
            if let issue = workspace.error {
                HStack { Text(issue).font(.callout); Spacer(); Button("关闭") { workspace.error = nil } }
                    .padding(10).background(.orange.opacity(0.08))
            }
            HSplitView {
                VStack(spacing: 0) {
                    HStack {
                        Text("Kimi Code").fontWeight(.semibold)
                        Spacer()
                        Text("在左侧栏切换工作区与会话").foregroundStyle(.secondary)
                    }.font(.caption).padding(12)
                    Divider()
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
                }.frame(minWidth: 420, idealWidth: 600)
                VStack(spacing: 0) {
                    HStack {
                        Text("论文").fontWeight(.semibold)
                        Spacer()
                        Text(paperLocation).foregroundStyle(.secondary)
                        Button("更换…") { workspace.choosePaper() }
                            .buttonStyle(.borderless).disabled(workspace.connecting || workspace.busy || workspace.maintenance)
                        Button { workspace.studioAction("freeze") } label: { Image(systemName: "arrow.clockwise") }
                            .buttonStyle(.borderless).help("重新编译 PDF")
                            .disabled(workspace.busy || workspace.sending || workspace.maintenance)
                    }.font(.caption).padding(10)
                    Divider()
                    if let url = workspace.reviewURL {
                        WebPane(url: url, revision: workspace.paperRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation, agentAction: workspace.handleAgentAction).id(url.port)
                    } else { empty("论文预览", symbol: "doc.richtext") }
                }.frame(minWidth: 500, idealWidth: 720)
            }
        }.frame(minWidth: 1000, minHeight: 680).background(Color(nsColor: .windowBackgroundColor))
        .task { workspace.launch() }
        .sheet(isPresented: Binding(get: { panel != nil }, set: { if !$0 { panel = nil } })) {
            VStack(spacing: 0) {
                HStack {
                    Text(panel == "git" ? "GitHub" : "翻译设置").font(.headline)
                    Spacer()
                    Button("完成") { panel = nil }.disabled(workspace.maintenance)
                }.padding(16)
                if let base = workspace.tasksURL, var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) {
                    let _ = parts.queryItems = [
                        URLQueryItem(name: "tab", value: panel),
                        URLQueryItem(name: "simple", value: panel == "git" ? "1" : "0")
                    ]
                    WebPane(url: parts.url!, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation, agentAction: workspace.handleAgentAction)
                }
            }
            .frame(width: panel == "git" ? 760 : 720, height: 680)
            .interactiveDismissDisabled(workspace.maintenance)
        }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Text("从初稿写到定稿").font(.title2.bold())
                Text("Kimi 的工作区和会话全部在左侧原生侧栏中管理。可以在同一目录新建多个会话，也可以直接切换到其他目录；应用不再要求先选择第二套工作区。\n\n右侧论文独立绑定。划选一段写批注时，会发送给当时打开的 Kimi 会话，并注明这篇论文的位置。\n\nGitHub 和编译只作用于右侧绑定的论文。Git 操作期间会短暂暂停左侧 Kimi，完成后恢复当前会话。")
                Button("明白了") { showHelp = false }
            }.padding(28).frame(width: 540)
        }
    }
    private func empty(_ title: String, symbol: String) -> some View {
        VStack(spacing: 16) { Image(systemName: symbol).font(.largeTitle); Text(workspace.connecting ? "正在连接…" : title) }.foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    private var paperLocation: String {
        guard let paper = workspace.paper else { return "尚未选择论文" }
        guard let root = workspace.projectRoot, paper.path.hasPrefix(root.path + "/") else {
            return paper.lastPathComponent
        }
        return String(paper.path.dropFirst(root.path.count + 1))
    }
}
