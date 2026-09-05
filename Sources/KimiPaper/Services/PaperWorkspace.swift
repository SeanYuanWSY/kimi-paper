import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class PaperWorkspace: ObservableObject {
    @Published var paper: URL?
    @Published var chatURL: URL?
    @Published var reviewURL: URL?
    @Published var status = "准备打开论文"
    @Published var error: String?
    @Published var connecting = false
    @Published var sending = false
    @Published var busy = false
    @Published var openComments = 0
    @Published var webRevision = UUID()
    private let paths = RuntimePaths()
    private var kimiProcess: ManagedProcess?
    private var reviewProcess: ManagedProcess?
    private var api: LocalAPI?
    private var sessionID: String?
    private var startTask: Task<Void, Never>?
    private var monitorTask: Task<Void, Never>?
    private var generation = UUID()
    private let http: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 2
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config)
    }()

    func launch() {
        guard paper == nil, !connecting else { return }
        do {
            let last = UserDefaults.standard.string(forKey: "lastPaper")
            let target = last.map { URL(fileURLWithPath: $0) }
            start(try target.flatMap { FileManager.default.fileExists(atPath: $0.path) ? $0 : nil } ?? paths.example())
        } catch { self.error = "无法创建示例论文，请检查文件夹权限。" }
    }

    func choosePaper() {
        let panel = NSOpenPanel()
        panel.title = "打开论文的 LaTeX 主文件"
        panel.prompt = "打开论文"
        panel.allowedContentTypes = [UTType(filenameExtension: "tex") ?? .plainText]
        panel.allowsMultipleSelection = false
        panel.message = "选择 main.tex 等主文件。应用会添加项目内的批注配置，正文由你向 Kimi 发出指令后修改。"
        if panel.runModal() == .OK, let url = panel.url { start(url) }
    }

    func openExample() {
        do { start(try paths.example()) } catch { self.error = "无法打开示例论文。" }
    }

    func reconnect() { if let paper { start(paper) } }

    func stop() {
        generation = UUID()
        startTask?.cancel(); startTask = nil
        monitorTask?.cancel(); monitorTask = nil
        kimiProcess?.stop(); kimiProcess = nil
        reviewProcess?.stop(); reviewProcess = nil
        api = nil; sessionID = nil
        chatURL = nil; reviewURL = nil
        connecting = false; busy = false; sending = false
        openComments = 0
    }

    func start(_ file: URL) {
        stop()
        let run = generation
        paper = file; error = nil; connecting = true; status = "正在准备论文…"
        startTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.prepare(file, run: run)
            } catch is CancellationError {
                if self.generation == run { self.stop(); self.status = "已取消打开论文" }
                return
            }
            catch {
                guard self.generation == run else { return }
                self.error = (error as? AppFailure)?.errorDescription ?? "启动未完成。请检查 Kimi 和 LaTeX 是否可用，然后重新连接。"
                self.stop()
                self.status = "需要处理启动问题"
            }
        }
    }

    private func prepare(_ file: URL, run: UUID) async throws {
        guard FileManager.default.isExecutableFile(atPath: paths.kimi.path),
              FileManager.default.isExecutableFile(atPath: paths.python.path) else {
            throw AppFailure.message("未找到 Kimi Code 或应用内的论文运行环境。请确认 Kimi 已安装。")
        }
        let python = paths.python, helper = paths.helper, env = paths.environment(localOnly: true)
        let prepared: [String: Any] = try await Task.detached {
            let p = Process(), pipe = Pipe()
            p.executableURL = python; p.arguments = [helper.path, file.path]
            p.environment = env; p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
            try p.run()
            let bytes = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            guard let obj = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                throw AppFailure.message("论文配置准备失败。")
            }
            if let message = obj["error"] as? String { throw AppFailure.message(message) }
            guard p.terminationStatus == 0 else { throw AppFailure.message("无法准备论文配置。") }
            return obj
        }.value
        try Task.checkCancellation()
        guard run == generation, let port = prepared["port"] as? Int, let root = prepared["root"] as? String else { throw CancellationError() }
        let cwd = URL(fileURLWithPath: root)
        status = "正在启动论文页面…"
        var reviewReady = false
        let review = try ManagedProcess(paths: paths, executable: paths.python,
            arguments: ["-m", "tex_mcp_web.cli", "serve"], cwd: cwd, localOnly: true)
        reviewProcess = review
        review.onLine = { line in
            if line.contains("tex-mcp-web serving on http://127.0.0.1:\(port)") { reviewReady = true }
        }
        for _ in 0..<80 {
            try Task.checkCancellation()
            if reviewReady { break }
            if !review.running { throw AppFailure.message("论文服务未启动。此论文可能已在其他窗口打开，请关闭对应服务后重试。") }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        guard reviewReady else { throw AppFailure.message("论文服务启动超时。请重新连接。") }
        let reviewAddress = URL(string: "http://127.0.0.1:\(port)")!
        let (paperData, _) = try await http.data(from: reviewAddress.appendingPathComponent("paper"))
        guard let identity = try JSONSerialization.jsonObject(with: paperData) as? [String: Any], identity["watch_dir"] as? String == root else {
            throw AppFailure.message("论文服务目录不匹配，已停止连接。")
        }
        reviewURL = reviewAddress
        status = "正在连接 Kimi…"
        let kimiPort = try RuntimePaths.freePort()
        var accessURL: URL?
        let kimi = try ManagedProcess(paths: paths, executable: paths.kimi,
            arguments: ["web", "--host", "127.0.0.1", "--port", "\(kimiPort)", "--no-open", "--web-title", "Kimi Paper"], cwd: cwd, localOnly: false)
        kimiProcess = kimi
        kimi.onLine = { line in
            guard line.contains("Local:"), let range = line.range(of: "http://127.0.0.1:") else { return }
            let candidate = String(line[range.lowerBound...]).components(separatedBy: .whitespacesAndNewlines)[0]
            guard let url = AccessEndpoint.validated(candidate, port: kimiPort) else { return }
            accessURL = url
        }
        for _ in 0..<100 {
            try Task.checkCancellation()
            if accessURL != nil { break }
            if !kimi.running { throw AppFailure.message("Kimi 服务未能启动。请先确认 Kimi Code 能正常使用。") }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        guard let accessURL, run == generation else { throw AppFailure.message("未取得 Kimi 本地连接，请重新连接。") }
        kimi.onLine = nil
        let client = try LocalAPI(accessURL: accessURL)
        let workspace = try await client.request("workspaces", method: "POST", body: ["root": root])
        guard let workspaceID = workspace["id"] as? String else { throw AppFailure.message("Kimi 未确认论文目录。") }
        let trust = try await client.request("workspaces/\(workspaceID)/trust")
        if trust["trusted"] as? Bool != true {
            if prepared["has_other_servers"] as? Bool == true {
                throw AppFailure.message("此目录还有其他项目工具。请先在 Kimi Code 中信任这个目录，再打开论文。")
            }
            if !root.hasPrefix(paths.support.path + "/") {
                let alert = NSAlert()
                alert.messageText = "允许 Kimi 处理这篇论文？"
                alert.informativeText = "将启用本应用的本地论文工具，Kimi 可按你的指令读取和修改此目录：\n\(root)"
                alert.addButton(withTitle: "允许并打开"); alert.addButton(withTitle: "取消")
                guard alert.runModal() == .alertFirstButtonReturn else { throw CancellationError() }
            }
            _ = try await client.request("workspaces/\(workspaceID)/trust", method: "POST", body: [:])
        }
        let savedID = (UserDefaults.standard.dictionary(forKey: "paperSessions") as? [String: String])?[file.path]
        var resumedID: String?
        if let savedID, savedID.range(of: "^session_[A-Za-z0-9-]+$", options: .regularExpression) != nil,
           let saved = try? await client.request("sessions/\(savedID)"),
           (saved["metadata"] as? [String: Any])?["cwd"] as? String == root {
            resumedID = savedID
        }
        let id: String
        if let resumedID { id = resumedID }
        else {
            let created = try await client.request("sessions", method: "POST", body: ["workspace_id": workspaceID, "title": "论文 · " + file.deletingPathExtension().lastPathComponent])
            guard let createdID = created["id"] as? String else { throw AppFailure.message("无法建立论文对话。") }
            id = createdID
            try await configureModel(client, sessionID: id)
        }
        try Task.checkCancellation()
        api = client; sessionID = id
        chatURL = client.browserURL(sessionID: id)
        connecting = false; status = "已就绪 · 在右侧划选文字并批注"
        UserDefaults.standard.set(file.path, forKey: "lastPaper")
        rememberSession(id, paper: file)
        review.onExit = { [weak self] in self?.serviceEnded(run) }
        kimi.onExit = { [weak self] in self?.serviceEnded(run) }
        startMonitoring(run: run)
    }

    private func serviceEnded(_ run: UUID) {
        guard generation == run else { return }
        error = "后台服务已停止，请点击重新连接。"
        stop(); status = "连接已断开"
    }

    private func configureModel(_ client: LocalAPI, sessionID: String) async throws {
        let catalog = try await client.request("models")
        let models = catalog["items"] as? [[String: Any]] ?? []
        let preferred = models.first(where: { ($0["display_name"] as? String ?? "").lowercased() == "k3-256k" })
        guard let model = preferred ?? models.first(where: { ($0["display_name"] as? String ?? "").lowercased().contains("k3") }),
              let alias = model["model"] as? String else {
            throw AppFailure.message("未找到已配置的 K3 模型，请先在 Kimi Code 中配置 K3。")
        }
        _ = try await client.request("sessions/\(sessionID)/profile", method: "POST", body: ["agent_config": ["model": alias]])
    }

    private func rememberSession(_ id: String, paper: URL) {
        var sessions = UserDefaults.standard.dictionary(forKey: "paperSessions") as? [String: String] ?? [:]
        sessions[paper.path] = id
        UserDefaults.standard.set(sessions, forKey: "paperSessions")
    }

    private func startMonitoring(run: UUID) {
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.generation == run, let reviewURL = self.reviewURL, let api = self.api, let id = self.sessionID else { return }
                do {
                    let (data, _) = try await self.http.data(from: reviewURL.appendingPathComponent("comments"))
                    let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                    let comments = body?["comments"] as? [[String: Any]] ?? []
                    guard self.generation == run else { return }
                    self.openComments = comments.filter { $0["status"] as? String == "open" }.count
                    let status = try await api.request("sessions/\(id)/status")
                    guard self.generation == run else { return }
                    guard self.sessionID == id else { continue }
                    let wasBusy = self.busy
                    self.busy = (status["busy"] as? Bool) ?? ((status["status"] as? String) == "running")
                    if wasBusy && !self.busy {
                        self.status = self.openComments == 0 ? "已完成 · 请检查右侧论文" : "Kimi 已停止 · 请查看左侧结果或提示"
                    }
                } catch { /* A transient poll failure must not replace the live web interface. */ }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func processComments() {
        guard !sending, !busy, let api, let id = sessionID else { return }
        let run = generation
        sending = true
        Task {
            defer { if generation == run { sending = false } }
            do {
                let prompt = "请通过 mcp__kimi-paper__paper 读取本论文未解决的批注。逐条按意见修改对应 LaTeX，保留未要求修改的内容；需要文献时先核实，不编造事实。修改后通过 kimi-paper 的 compile 编译，确认成功并核对修改后回复、解决已完成的批注；有问题的保持未解决并说明。"
                _ = try await api.request("sessions/\(id)/prompts", method: "POST", body: ["content": [["type": "text", "text": prompt]], "prompt_id": UUID().uuidString])
                guard generation == run, sessionID == id else { return }
                chatURL = api.browserURL(sessionID: id)
                status = "已交给 Kimi · 进度与确认会显示在左侧"
                busy = true
            } catch { if generation == run { self.error = (error as? AppFailure)?.errorDescription ?? "发送未完成，请在左侧查看连接状态。" } }
        }
    }

    func newConversation() {
        guard let api, let paper, !busy, !sending else { return }
        let run = generation
        sending = true
        Task {
            defer { if generation == run { sending = false } }
            do {
                let result = try await api.request("sessions", method: "POST", body: ["metadata": ["cwd": paper.deletingLastPathComponent().path], "title": "论文新对话"])
                guard generation == run, let id = result["id"] as? String else { return }
                try await configureModel(api, sessionID: id)
                guard generation == run else { return }
                sessionID = id; chatURL = api.browserURL(sessionID: id)
                rememberSession(id, paper: paper)
            } catch { if generation == run { self.error = "无法建立新对话，请稍后重试。" } }
        }
    }
}
