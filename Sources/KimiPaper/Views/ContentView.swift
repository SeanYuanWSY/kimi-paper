import SwiftUI

struct ContentView: View {
    @ObservedObject var workspace: PaperWorkspace
    @State private var showHelp = false
    @State private var leftPane = "tasks"

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
                    .disabled(workspace.connecting || workspace.busy || workspace.sending || workspace.activeCandidates > 0).keyboardShortcut("o")
                Button { leftPane = "tasks" } label: {
                    Label("修改任务\(workspace.activeCandidates > 0 ? " · \(workspace.activeCandidates)" : "")", systemImage: "sparkles")
                }
                .buttonStyle(.borderedProminent).tint(.indigo)
                .disabled(workspace.tasksURL == nil)
                .keyboardShortcut(.return, modifiers: [.command])
                Menu {
                    Button("刷新页面") { workspace.webRevision = UUID() }
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.busy || workspace.sending || workspace.connecting || workspace.activeCandidates > 0)
                    Divider()
                    Button("打开示例论文") { workspace.openExample() }.disabled(workspace.busy || workspace.sending || workspace.connecting || workspace.activeCandidates > 0)
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
                    Button("重新连接") { workspace.reconnect() }.disabled(workspace.connecting || workspace.busy || workspace.sending || workspace.activeCandidates > 0)
                    Button { workspace.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                }.padding(12).background(.orange.opacity(0.07))
                Divider()
            }
            HSplitView {
                VStack(spacing: 0) {
                    pane(title: "Kimi 修改建议", subtitle: "比较差异，确认写入", symbol: "square.stack.3d.up", url: workspace.tasksURL, isPaper: true)
                }.frame(minWidth: 400, idealWidth: 560)
                pane(title: "论文", subtitle: "划选 → 选模型 → 开始生成候选", symbol: "doc.richtext", url: workspace.reviewURL, isPaper: true)
                    .frame(minWidth: 530, idealWidth: 780)
            }
        }
        .frame(minWidth: 1000, minHeight: 660)
        .task { workspace.launch() }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 18) {
                Label("在一扇窗口里修改论文", systemImage: "book.pages").font(.title2.bold())
                Text("1. 在右侧划选文字写批注，选择 Kimi 模型和处理方式。全文要求可以用右侧 + Note 添加。\n\n2. 立即开始，或者勾选先保存、随后在左侧统一顺序处理。\n\n3. 点回论文批注，查看建议、采纳写入或继续提意见。完整差异在左侧。\n\n4. 采纳时编译检查，通过才更新正文，也可撤销。划选翻译使用独立的翻译设置。")
                Text("打开自己的论文时，请选择 main.tex 等主文件。应用会添加项目内的批注配置；你的原文由 Kimi 根据指令修改。退出应用会停止本应用启动的服务。")
                    .font(.callout).foregroundStyle(.secondary)
                HStack { Spacer(); Button("开始使用") { showHelp = false }.keyboardShortcut(.defaultAction) }
            }.padding(28).frame(width: 510)
        }
    }

    @ViewBuilder
    private func pane(title: String, subtitle: String, symbol: String, url: URL?, isPaper: Bool = false) -> some View {
        VStack(spacing: 0) {
            HStack {
                Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
                Spacer()
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 15).padding(.vertical, 9)
            Divider()
            if let url {
                WebPane(url: url, revision: workspace.webRevision, onFailure: { workspace.error = $0 },
                        translation: isPaper ? workspace.translation : nil,
                        prepareAgents: isPaper ? { try await workspace.prepareAgents() } : nil)
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
