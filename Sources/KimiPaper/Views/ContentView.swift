import SwiftUI

struct ContentView: View {
    @ObservedObject var workspace: PaperWorkspace
    @State private var showHelp = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: "book.closed.fill").font(.title3).foregroundStyle(.indigo)
                VStack(alignment: .leading, spacing: 2) {
                    Text(workspace.paper?.deletingLastPathComponent().lastPathComponent ?? "Kimi Paper").font(.headline)
                    Text(workspace.status).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                if workspace.connecting { ProgressView().controlSize(.small) }
                Button { workspace.choosePaper() } label: { Label("打开论文", systemImage: "folder") }
                    .disabled(workspace.connecting || workspace.busy || workspace.sending).keyboardShortcut("o")
                Button { workspace.processComments() } label: {
                    Label(workspace.sending ? "正在发送…" : "处理批注\(workspace.openComments > 0 ? " · \(workspace.openComments)" : "")", systemImage: "sparkles")
                }
                .buttonStyle(.borderedProminent).tint(.indigo)
                .disabled(workspace.chatURL == nil || workspace.sending || workspace.busy || workspace.openComments == 0)
                .keyboardShortcut(.return, modifiers: [.command])
                Menu {
                    Button("新对话") { workspace.newConversation() }.disabled(workspace.busy || workspace.sending || workspace.connecting)
                    Button("刷新页面") { workspace.webRevision = UUID() }
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.busy || workspace.sending || workspace.connecting)
                    Divider()
                    Button("打开示例论文") { workspace.openExample() }.disabled(workspace.busy || workspace.sending || workspace.connecting)
                    Button("在 Finder 中查看论文") {
                        if let paper = workspace.paper { NSWorkspace.shared.activateFileViewerSelecting([paper]) }
                    }
                    Button("怎么使用") { showHelp = true }
                } label: { Image(systemName: "ellipsis.circle").font(.title3) }
                .menuStyle(.borderlessButton).fixedSize().help("更多操作")
            }
            .padding(.horizontal, 18).padding(.vertical, 12)
            Divider()
            if let message = workspace.error {
                HStack {
                    Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                    Text(message).font(.callout).textSelection(.enabled)
                    Spacer()
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.connecting || workspace.busy || workspace.sending)
                    Button { workspace.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                }.padding(12).background(.orange.opacity(0.07))
                Divider()
            }
            HSplitView {
                pane(title: "Kimi", subtitle: "交流、修改与确认", symbol: "bubble.left.and.bubble.right", url: workspace.chatURL)
                    .frame(minWidth: 370, idealWidth: 500)
                pane(title: "论文", subtitle: "划选正文 → Comment → 处理批注", symbol: "doc.richtext", url: workspace.reviewURL)
                    .frame(minWidth: 530, idealWidth: 780)
            }
        }
        .frame(minWidth: 1000, minHeight: 660)
        .task { workspace.launch() }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Label("在一扇窗口里修改论文", systemImage: "book.pages").font(.title2.bold())
                Text("1. 在右侧拖动选中一句话，点 Comment，写下意见并提交。\n\n2. 点顶部的「处理批注」。Kimi 会在左侧执行，你也可以直接和它交流。\n\n3. 检查右侧更新后的论文；需要调整时继续批注。")
                Text("打开自己的论文时，请选择 main.tex 等主文件。应用会添加项目内的批注配置；你的原文由 Kimi 根据指令修改。退出应用会停止本应用启动的服务。")
                    .font(.callout).foregroundStyle(.secondary)
                HStack { Spacer(); Button("开始使用") { showHelp = false }.keyboardShortcut(.defaultAction) }
            }.padding(28).frame(width: 510)
        }
    }

    @ViewBuilder
    private func pane(title: String, subtitle: String, symbol: String, url: URL?) -> some View {
        VStack(spacing: 0) {
            HStack {
                Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
                Spacer()
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 15).padding(.vertical, 9)
            Divider()
            if let url {
                WebPane(url: url, revision: workspace.webRevision, onFailure: { workspace.error = $0 })
                    .id(url.port)
            } else {
                VStack(spacing: 14) {
                    Image(systemName: symbol).font(.system(size: 34)).foregroundStyle(.tertiary)
                    Text(workspace.connecting ? "正在打开\(title)…" : "\(title)将在这里打开").foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}
