import SwiftUI
import AppKit

@main
struct KimiPaperApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        Window("Kimi Paper", id: "main") {
            ContentView(workspace: delegate.workspace)
        }
        .defaultSize(width: 1420, height: 860)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("打开论文…") { delegate.workspace.choosePaper() }
                    .keyboardShortcut("o")
                    .disabled(delegate.workspace.busy || delegate.workspace.connecting
                              || delegate.workspace.sending || delegate.workspace.maintenance
                              || delegate.workspace.activeCandidates > 0)
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let workspace = PaperWorkspace()
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if workspace.busy || workspace.sending || workspace.maintenance || workspace.activeCandidates > 0 {
            let alert = NSAlert()
            alert.messageText = workspace.maintenance ? "Git 操作仍在进行" : "仍有论文任务在处理"
            alert.informativeText = workspace.maintenance
                ? "现在退出可能中断正在进行的 Git 操作。建议继续等待操作完成。"
                : "退出会停止本应用启动的服务。已保存的论文和对话会保留。"
            alert.addButton(withTitle: "继续等待"); alert.addButton(withTitle: "停止并退出")
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        }
        workspace.stop()
        return .terminateNow
    }
}
