import SwiftUI

struct ContentView: View {
    @ObservedObject var workspace: PaperWorkspace
    @State private var panel: String?
    @State private var showHelp = false
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                Image(systemName: "book.closed.fill").foregroundStyle(.indigo)
                Button { workspace.chooseProject() } label: {
                    HStack {
                        Image(systemName: "folder")
                        Text(workspace.projectRoot?.lastPathComponent ?? "选择 Kimi 工作目录").fontWeight(.semibold)
                        Image(systemName: "chevron.down").font(.caption)
                    }
                }.buttonStyle(.plain).disabled(workspace.connecting || workspace.busy)
                Text(workspace.projectRoot?.path ?? "")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    .help(workspace.projectRoot?.path ?? "")
                Spacer()
                if workspace.connecting || workspace.busy {
                    ProgressView().controlSize(.small)
                    Text(workspace.busy ? "Kimi 正在修改" : "正在连接").font(.caption).foregroundStyle(.secondary)
                }
                Button { panel = "git" } label: { Label("GitHub", systemImage: "arrow.triangle.branch") }
                Menu {
                    Button("更换 Kimi 工作目录…") { workspace.chooseProject() }
                    Button("更换 LaTeX 主文件…") { workspace.choosePaper() }
                    Divider()
                    Button("撤销最近一次 Kimi 修改") { workspace.studioAction("undo") }
                        .disabled(workspace.busy)
                    Button("重新编译 PDF") { workspace.studioAction("freeze") }.disabled(workspace.busy)
                    Button("翻译设置") { panel = "settings" }
                    Button("刷新界面") { workspace.webRevision = UUID(); workspace.paperRevision = UUID() }
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.busy)
                    Divider()
                    Button("打开示例项目") { workspace.openExample() }
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
                        Text("Kimi").fontWeight(.semibold)
                        Spacer()
                        Text("直接修改当前项目").foregroundStyle(.secondary)
                    }.font(.caption).padding(12)
                    Divider()
                    if let url = workspace.chatURL {
                        WebPane(url: url, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, onLocation: workspace.chatNavigated).id(url.port)
                    } else { empty("Kimi 工作台", symbol: "sparkles") }
                }.frame(minWidth: 420, idealWidth: 600)
                VStack(spacing: 0) {
                    HStack {
                        Text("论文").fontWeight(.semibold)
                        Spacer()
                        Text(workspace.paper?.lastPathComponent ?? "").foregroundStyle(.secondary)
                        Button { workspace.studioAction("freeze") } label: { Image(systemName: "arrow.clockwise") }
                            .buttonStyle(.borderless).help("重新编译 PDF").disabled(workspace.busy)
                    }.font(.caption).padding(10)
                    Divider()
                    if let url = workspace.reviewURL {
                        WebPane(url: url, revision: workspace.paperRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation, prepareAgents: { try await workspace.prepareAgents() }).id(url.port)
                    } else { empty("论文预览", symbol: "doc.richtext") }
                }.frame(minWidth: 500, idealWidth: 720)
            }
        }.frame(minWidth: 1000, minHeight: 680).background(Color(nsColor: .windowBackgroundColor))
        .task { workspace.launch() }
        .sheet(isPresented: Binding(get: { panel != nil }, set: { if !$0 { panel = nil } })) {
            VStack(spacing: 0) {
                HStack { Text(panel == "git" ? "GitHub" : "翻译设置").font(.headline); Spacer(); Button("完成") { panel = nil } }.padding(16)
                if let base = workspace.tasksURL, var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) {
                    let _ = parts.queryItems = [
                        URLQueryItem(name: "tab", value: panel),
                        URLQueryItem(name: "simple", value: panel == "git" ? "1" : "0")
                    ]
                    WebPane(url: parts.url!, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation)
                }
            }.frame(width: panel == "git" ? 760 : 720, height: 680)
        }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Text("从初稿写到定稿").font(.title2.bold())
                Text("顶部选择的就是 Kimi 实际工作目录。请选择包含论文、数据、图表和相关脚本的真实项目目录。\n\n左侧用 Kimi 直接起草或做大修改；右侧划选一段写批注，会直接交给当前会话修改。Kimi 一轮结束后会自动重新编译 PDF。\n\nGitHub 按钮用于提交、拉取和推送。更多菜单中保留翻译设置和最近一次修改的撤销入口。")
                Button("明白了") { showHelp = false }
            }.padding(28).frame(width: 540)
        }
    }
    private func empty(_ title: String, symbol: String) -> some View {
        VStack(spacing: 16) { Image(systemName: symbol).font(.largeTitle); Text(workspace.connecting ? "正在连接…" : title) }.foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
