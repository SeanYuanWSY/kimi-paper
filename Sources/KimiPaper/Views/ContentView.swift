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
                    HStack { Text(workspace.projectRoot?.lastPathComponent ?? "选择项目").fontWeight(.semibold); Image(systemName: "chevron.down").font(.caption) }
                }.buttonStyle(.plain).disabled(workspace.connecting || workspace.busy)
                Text(workspace.paper?.lastPathComponent ?? "").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if workspace.connecting || workspace.busy { ProgressView().controlSize(.small) }
                Button("项目文件") { panel = "files" }
                Button("修改记录") { panel = "changes" }
                Button("版本与 GitHub") { panel = "git" }
                Menu {
                    Button("选择 LaTeX 主文件") { workspace.choosePaper() }
                    Button("翻译设置") { panel = "settings" }
                    Button("刷新页面") { workspace.webRevision = UUID(); workspace.paperRevision = UUID() }
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.busy)
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
                    HStack { Text("Kimi 工作台").fontWeight(.semibold); Spacer(); Text("持续会话 · 草稿写作").foregroundStyle(.secondary) }.font(.caption).padding(12)
                    Divider()
                    if let url = workspace.chatURL {
                        WebPane(url: url, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, onLocation: workspace.chatNavigated).id(url.port)
                    } else { empty("Kimi 工作台", symbol: "sparkles") }
                }.frame(minWidth: 420, idealWidth: 600)
                VStack(spacing: 0) {
                    HStack {
                        Text(workspace.previewID == nil ? "正式论文" : "草稿预览").fontWeight(.semibold)
                        Spacer()
                        Button("正式稿") { workspace.studioAction("view", body: ["id": NSNull()]) }
                        Button("生成预览") { workspace.studioAction("freeze") }.disabled(workspace.busy)
                        if let id = workspace.previewID {
                            Button("查看并采纳") { panel = "changes" }.tint(.indigo).help("版本 " + String(id.prefix(6)))
                        }
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
                HStack { Text("项目工作区").font(.headline); Spacer(); Button("完成") { panel = nil } }.padding(16)
                if let base = workspace.tasksURL, var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) {
                    let _ = parts.queryItems = [URLQueryItem(name: "tab", value: panel)]
                    WebPane(url: parts.url!, revision: workspace.webRevision, onFailure: { workspace.error = $0 }, translation: workspace.translation)
                }
            }.frame(width: 950, height: 710)
        }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Text("从初稿写到定稿").font(.title2.bold())
                Text("选择整个论文项目，在左侧 Kimi 中起草或讨论。Kimi 使用持久草稿目录，右侧生成 PDF 预览。\n\n划选文字即可翻译或写批注，批注发送到当前会话；也可以积攒后一起发送。模型和会话在 Kimi 原生界面选择。\n\n在修改记录中查看预览，再确认采纳到正式项目。正文、文献库、图表一同留存本地版本；GitHub 同步由你手动发起。")
                Button("明白了") { showHelp = false }
            }.padding(28).frame(width: 540)
        }
    }
    private func empty(_ title: String, symbol: String) -> some View {
        VStack(spacing: 16) { Image(systemName: symbol).font(.largeTitle); Text(workspace.connecting ? "正在连接…" : title) }.foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
