import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class PaperWorkspace: ObservableObject {
    @Published var paper: URL?
    @Published var projectRoot: URL?
    @Published var previewID: String?
    @Published var paperRevision = UUID()
    @Published var projectFiles: [String] = []
    private var currentSession: String?
    @Published var chatURL: URL?
    @Published var reviewURL: URL?
    @Published var tasksURL: URL?
    @Published var activeCandidates = 0
    let translation = TranslationService()
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
        let args = ProcessInfo.processInfo.arguments
        if let index = args.firstIndex(of: "--project"), args.indices.contains(index + 1) {
            let root = URL(fileURLWithPath: args[index + 1])
            start(root.appendingPathComponent("main.tex"), root: root); return
        }
        do {
            let last = UserDefaults.standard.string(forKey: "lastPaper")
            let target = last.map { URL(fileURLWithPath: $0) }
            start(try target.flatMap { FileManager.default.fileExists(atPath: $0.deletingLastPathComponent().path) ? $0 : nil } ?? paths.example(), root: UserDefaults.standard.string(forKey: "lastProject").map { URL(fileURLWithPath: $0) })
        } catch { self.error = "无法创建示例论文，请检查文件夹权限。" }
    }

    func chooseProject() {
        let panel = NSOpenPanel()
        panel.title = "选择研究工作区"; panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.canCreateDirectories = true; panel.prompt = "设为工作区"
        panel.message = "这里是 Kimi 的工作位置，不必是 LaTeX 文件夹。可以选择同时包含 paper、data、figures 等目录的上级项目文件夹。"
        guard panel.runModal() == .OK, let root = panel.url else { return }
        if let current = paper, current.path.hasPrefix(root.path + "/") {
            start(current, root: root); return
        }
        let main = root.appendingPathComponent("main.tex")
        if FileManager.default.fileExists(atPath: main.path) { start(main, root: root); return }
        let picker = NSOpenPanel(); picker.directoryURL = root
        picker.title = "在工作区中选择论文主文件"
        picker.message = "main.tex 可以位于 paper、manuscript 等任意子目录。新项目可以取消，稍后由 Kimi 创建工作区根目录下的 main.tex。"
        picker.allowedContentTypes = [UTType(filenameExtension: "tex") ?? .plainText]
        if picker.runModal() == .OK, let file = picker.url, file.path.hasPrefix(root.path + "/") { start(file, root: root) }
        else { start(main, root: root) }
    }

    func choosePaper() {
        let panel = NSOpenPanel()
        panel.title = "打开论文的 LaTeX 主文件"
        panel.prompt = "打开论文"
        panel.allowedContentTypes = [UTType(filenameExtension: "tex") ?? .plainText]
        panel.allowsMultipleSelection = false
        panel.message = "选择 main.tex 等主文件。主文件位于当前研究工作区内时，Kimi 的工作区不会变化。"
        if panel.runModal() == .OK, let url = panel.url {
            let root = projectRoot.flatMap { url.path.hasPrefix($0.path + "/") ? $0 : nil }
            start(url, root: root)
        }
    }

    func openExample() {
        do { start(try paths.example()) } catch { self.error = "无法打开示例论文。" }
    }

    func reconnect() { if let paper { start(paper, root: projectRoot) } }

    func stop() {
        generation = UUID()
        startTask?.cancel(); startTask = nil
        monitorTask?.cancel(); monitorTask = nil
        kimiProcess?.stop(); kimiProcess = nil
        reviewProcess?.stop(); reviewProcess = nil
        api = nil; sessionID = nil
        chatURL = nil; reviewURL = nil; tasksURL = nil; activeCandidates = 0
        connecting = false; busy = false; sending = false
        openComments = 0
    }

    func start(_ file: URL, root: URL? = nil) {
        stop()
        let run = generation
        paper = file; projectRoot = root ?? file.deletingLastPathComponent(); error = nil; connecting = true; status = "正在准备论文…"
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
        let project = projectRoot ?? file.deletingLastPathComponent()
        let mainRelative = String(file.path.dropFirst(project.path.count + 1))
        let prepared: [String: Any] = try await Task.detached {
            let p = Process(), pipe = Pipe()
            p.executableURL = python; p.arguments = [helper.path, project.path, mainRelative]
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
        var reviewAccess: URL?
        let review = try ManagedProcess(paths: paths, executable: paths.python,
            arguments: [paths.paperService.path], cwd: cwd, localOnly: true,
            extraEnvironment: paths.agentProxyEnvironment.merging(["KIMI_PAPER_STUDIO":"direct"]) { _, new in new })
        reviewProcess = review
        review.onLine = { line in
            if line.hasPrefix("Kimi Paper service: ") {
                reviewAccess = AccessEndpoint.validated(String(line.dropFirst("Kimi Paper service: ".count)), port: port)
            }
        }
        for _ in 0..<800 {
            try Task.checkCancellation()
            if reviewAccess != nil { break }
            if !review.running { throw AppFailure.message("论文服务未启动。此论文可能已在其他窗口打开，请关闭对应服务后重试。") }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        guard let reviewAccess, reviewAccess.fragment?.hasPrefix("token=") == true else { throw AppFailure.message("论文服务启动超时。请重新连接。") }
        review.onLine = nil
        let reviewAddress = URL(string: "http://127.0.0.1:\(port)")!
        let (paperData, _) = try await http.data(from: reviewAddress.appendingPathComponent("paper"))
        guard let identity = try JSONSerialization.jsonObject(with: paperData) as? [String: Any], identity["watch_dir"] as? String == root else {
            throw AppFailure.message("论文服务目录不匹配，已停止连接。")
        }
        reviewURL = reviewAccess
        var taskAddress = URLComponents(url: reviewAccess, resolvingAgainstBaseURL: false)!
        taskAddress.path = "/studio-panel"
        tasksURL = taskAddress.url
        status = "正在连接原项目中的 Kimi…"
        try await refreshStudio()
        connecting = false; status = "Kimi 正在原项目目录中工作"
        if !ProcessInfo.processInfo.arguments.contains("--project") {
            UserDefaults.standard.set(file.path, forKey: "lastPaper")
            UserDefaults.standard.set(project.path, forKey: "lastProject")
        }
        review.onExit = { [weak self] in self?.serviceEnded(run) }
        startMonitoring(run: run)
    }

    private func serviceEnded(_ run: UUID) {
        guard generation == run else { return }
        error = "后台服务已停止，请点击重新连接。"
        stop(); status = "连接已断开"
    }

    private func startMonitoring(run: UUID) {
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.generation == run, self.reviewURL != nil else { return }
                do {
                    try await self.refreshStudio()
                } catch { /* A transient poll failure must not replace the live web interface. */ }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func serviceRequest(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        guard let reviewURL else { throw CancellationError() }
        var parts = URLComponents(url: reviewURL, resolvingAgainstBaseURL: false)!
        let token = String(parts.fragment?.dropFirst(6) ?? "")
        parts.fragment = nil; parts.path = path
        var request = URLRequest(url: parts.url!); request.timeoutInterval = 240
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if let body { request.httpMethod = "POST"; request.httpBody = try JSONSerialization.data(withJSONObject: body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data,response) = try await http.data(for: request)
        let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw AppFailure.message(result["error"] as? String ?? "操作未完成。") }
        return result
    }

    func refreshStudio() async throws {
        let state = try await serviceRequest("/kp/studio/connection")
        if let address = state["chatURL"] as? String, let nextURL = URL(string: address), chatURL != nextURL { chatURL = nextURL }
        currentSession = state["session"] as? String
        let next = state["preview"] as? String
        if previewID != next { previewID = next }
        busy = state["busy"] as? Bool ?? false
        openComments = (state["pending"] as? [Any])?.count ?? 0
        projectFiles = (state["files"] as? [[String: Any]] ?? []).compactMap { $0["path"] as? String }
        if let issue = state["error"] as? String { error = issue }
    }

    func studioAction(_ action: String, body: [String: Any] = [:]) {
        Task { do { _ = try await serviceRequest("/kp/studio/" + action, body: body); try await refreshStudio() }
            catch { self.error = (error as? AppFailure)?.errorDescription ?? "操作未完成，请重试。" } }
    }

    func chatNavigated(_ url: URL) {
        guard url.scheme == "http", url.host == "127.0.0.1", url.port == chatURL?.port else { return }
        if url.path == "/" {
            guard currentSession != "creating" else { return }
            currentSession = "creating"; studioAction("new"); return
        }
        let pieces = url.pathComponents
        guard pieces.count == 3, pieces[1] == "sessions", pieces[2] != currentSession else { return }
        studioAction("select", body: ["session": pieces[2]])
    }

    func prepareAgents() async throws {
        guard reviewURL != nil else { throw CancellationError() }
    }
}
