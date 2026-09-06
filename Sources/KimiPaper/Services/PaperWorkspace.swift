import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class PaperWorkspace: ObservableObject {
    @Published var paper: URL?
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
        panel.message = "选择 main.tex 等主文件。Kimi 生成修改建议，由你确认采纳后才写入正文。"
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
        chatURL = nil; reviewURL = nil; tasksURL = nil; activeCandidates = 0
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
        var reviewAccess: URL?
        let review = try ManagedProcess(paths: paths, executable: paths.python,
            arguments: [paths.paperService.path], cwd: cwd, localOnly: true,
            extraEnvironment: paths.agentProxyEnvironment)
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
        taskAddress.path = "/workbench"
        tasksURL = taskAddress.url
        status = "正在连接 Kimi…"
        connecting = false; status = "已就绪 · 在右侧划选文字并批注"
        UserDefaults.standard.set(file.path, forKey: "lastPaper")
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
                guard let self, self.generation == run, let reviewURL = self.reviewURL else { return }
                do {
                    let (data, _) = try await self.http.data(from: reviewURL.appendingPathComponent("comments"))
                    let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                    let comments = body?["comments"] as? [[String: Any]] ?? []
                    guard self.generation == run else { return }
                    self.openComments = comments.filter { $0["status"] as? String == "open" }.count
                    var parts = URLComponents(url: reviewURL, resolvingAgainstBaseURL: false)!
                    let reviewToken = parts.fragment?.dropFirst(6)
                    parts.fragment = nil; parts.path = "/kp/tasks"
                    var request = URLRequest(url: parts.url!)
                    request.setValue("Bearer " + String(reviewToken ?? ""), forHTTPHeaderField: "Authorization")
                    let (tasks, _) = try await self.http.data(for: request)
                    self.activeCandidates = (try JSONSerialization.jsonObject(with: tasks) as? [String: Any])?["running"] as? Int ?? 0
                } catch { /* A transient poll failure must not replace the live web interface. */ }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func prepareAgents() async throws {
        guard reviewURL != nil else { throw CancellationError() }
    }
}
