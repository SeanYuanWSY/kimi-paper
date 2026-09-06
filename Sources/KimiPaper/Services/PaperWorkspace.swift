import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class PaperWorkspace: ObservableObject {
    @Published var paper: URL?
    @Published var projectRoot: URL?
    @Published var paperRevision = UUID()
    @Published var chatURL: URL?
    @Published var reviewURL: URL?
    @Published var tasksURL: URL?
    @Published var status = "准备打开论文"
    @Published var error: String?
    @Published var connecting = false
    @Published var sending = false
    @Published var busy = false
    @Published var maintenance = false
    @Published var webRevision = UUID()
    @Published var activeCandidates = 0
    @Published var openComments = 0
    @Published var previewID: String?
    @Published var projectFiles: [String] = []

    let translation = TranslationService()
    private let paths = RuntimePaths()
    private var kimiProcess: ManagedProcess?
    private var reviewProcess: ManagedProcess?
    private var api: LocalAPI?
    private var currentSession: String?
    private var paperControlToken: String?
    private var selectionEpoch: UInt64 = 0
    private var startTask: Task<Void, Never>?
    private var monitorTask: Task<Void, Never>?
    private var paperGeneration = UUID()
    private var kimiGeneration = UUID()
    private var lastBusy = false
    private let http: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config)
    }()

    func launch() {
        guard paper == nil, !connecting else { return }
        let args = ProcessInfo.processInfo.arguments
        if let index = args.firstIndex(of: "--project"), args.indices.contains(index + 1) {
            let root = URL(fileURLWithPath: args[index + 1])
            start(root.appendingPathComponent("main.tex"), root: root)
            return
        }
        do {
            let last = UserDefaults.standard.string(forKey: "lastPaper")
            let target = last.map { URL(fileURLWithPath: $0) }
            let savedRoot = UserDefaults.standard.string(forKey: "lastPaperRoot")
                ?? UserDefaults.standard.string(forKey: "lastProject")
            let file = try target.flatMap {
                FileManager.default.fileExists(atPath: $0.deletingLastPathComponent().path) ? $0 : nil
            } ?? paths.example()
            start(file, root: savedRoot.map { URL(fileURLWithPath: $0) })
        } catch { self.error = "无法创建示例论文，请检查文件夹权限。" }
    }

    func choosePaper() {
        guard !connecting, !busy, !sending, !maintenance else { return }
        let panel = NSOpenPanel()
        panel.title = "打开论文的 LaTeX 主文件"
        panel.prompt = "打开论文"
        panel.allowedContentTypes = [UTType(filenameExtension: "tex") ?? .plainText]
        panel.allowsMultipleSelection = false
        panel.message = "只更换右侧论文。左侧 Kimi 的工作区与会话保持不变。"
        if panel.runModal() == .OK, let url = panel.url {
            start(url, root: url.deletingLastPathComponent())
        }
    }

    func openExample() {
        guard !connecting, !busy, !sending, !maintenance else { return }
        do { start(try paths.example()) } catch { self.error = "无法打开示例论文。" }
    }

    func reconnect() {
        guard !connecting, !busy, !sending, !maintenance else { return }
        if let paper { start(paper, root: projectRoot) }
    }

    func stop() {
        stopPaper()
        monitorTask?.cancel(); monitorTask = nil
        kimiProcess?.stop(); kimiProcess = nil
        api = nil; chatURL = nil; currentSession = nil
        busy = false; sending = false; maintenance = false
    }

    private func stopPaper() {
        paperGeneration = UUID()
        startTask?.cancel(); startTask = nil
        reviewProcess?.stop(); reviewProcess = nil
        paperControlToken = nil
        reviewURL = nil; tasksURL = nil
        connecting = false; openComments = 0; previewID = nil; projectFiles = []
    }

    func start(_ file: URL, root: URL? = nil) {
        stopPaper()
        let run = paperGeneration
        let paperRoot = root.flatMap { file.path.hasPrefix($0.path + "/") ? $0 : nil }
            ?? file.deletingLastPathComponent()
        paper = file; projectRoot = paperRoot; error = nil
        connecting = true; status = "正在准备论文…"
        startTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.startKimiIfNeeded(cwd: paperRoot)
                try await self.preparePaper(file, run: run)
                self.startMonitoringIfNeeded()
            } catch is CancellationError {
                if self.paperGeneration == run { self.stopPaper(); self.status = "已取消打开论文" }
            } catch {
                guard self.paperGeneration == run else { return }
                self.error = (error as? AppFailure)?.errorDescription
                    ?? "启动未完成。请检查 Kimi 和 LaTeX 是否可用，然后重新连接。"
                self.stopPaper(); self.status = "需要处理启动问题"
            }
        }
    }

    private func startKimiIfNeeded(cwd: URL) async throws {
        if api != nil, kimiProcess?.running == true { return }
        guard FileManager.default.isExecutableFile(atPath: paths.kimi.path) else {
            throw AppFailure.message("未找到 Kimi Code。请确认 Kimi 已安装。")
        }
        let run = UUID(); kimiGeneration = run
        var access: URL?
        let process = try ManagedProcess(paths: paths, executable: paths.kimi,
            arguments: ["web", "--port", "0", "--host", "127.0.0.1", "--no-open"],
            cwd: cwd, localOnly: false)
        kimiProcess = process
        process.onLine = { line in
            if let range = line.range(of: "http://127.0.0.1:"), line.contains("#token=") {
                let suffix = line[range.lowerBound...]
                let candidate = suffix.prefix { !$0.isWhitespace && $0 != "\u{001B}" }
                access = AccessEndpoint.validated(String(candidate))
            }
        }
        for _ in 0..<400 {
            try Task.checkCancellation()
            if access != nil { break }
            if !process.running { throw AppFailure.message("Kimi Web 未能启动，请检查 Kimi 登录状态。") }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let access else { throw AppFailure.message("Kimi Web 启动超时，请重新连接。") }
        process.onLine = nil
        let nextAPI = try LocalAPI(accessURL: access)
        if let currentSession {
            do {
                _ = try await nextAPI.request("sessions/" + currentSession)
            } catch {
                self.currentSession = nil
            }
        }
        api = nextAPI
        chatURL = nextAPI.browserURL(sessionID: currentSession)
        process.onExit = { [weak self] in
            guard let self, self.kimiGeneration == run else { return }
            self.api = nil; self.kimiProcess = nil; self.chatURL = nil
            if !self.maintenance { self.error = "Kimi Web 已停止，请点击重新连接。" }
        }
    }

    private func preparePaper(_ file: URL, run: UUID) async throws {
        guard FileManager.default.isExecutableFile(atPath: paths.python.path) else {
            throw AppFailure.message("未找到应用内的论文运行环境。")
        }
        let project = projectRoot ?? file.deletingLastPathComponent()
        let mainRelative = String(file.path.dropFirst(project.path.count + 1))
        let python = paths.python, helper = paths.helper, env = paths.environment(localOnly: true)
        let prepared: [String: Any] = try await Task.detached {
            let process = Process(), pipe = Pipe()
            process.executableURL = python; process.arguments = [helper.path, project.path, mainRelative]
            process.environment = env; process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
            try process.run()
            let bytes = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                throw AppFailure.message("论文配置准备失败。")
            }
            if let message = object["error"] as? String { throw AppFailure.message(message) }
            guard process.terminationStatus == 0 else { throw AppFailure.message("无法准备论文配置。") }
            return object
        }.value
        try Task.checkCancellation()
        guard run == paperGeneration, let root = prepared["root"] as? String else { throw CancellationError() }
        let cwd = URL(fileURLWithPath: root)
        let controlToken = UUID().uuidString.lowercased()
        paperControlToken = controlToken
        status = "正在启动论文页面…"
        var reviewAccess: URL?
        let review = try ManagedProcess(paths: paths, executable: paths.python,
            arguments: [paths.paperService.path], cwd: cwd, localOnly: false,
            extraEnvironment: ["KIMI_PAPER_VIEWER":"1", "KIMI_PAPER_ROOT":root,
                               "KIMI_PAPER_MAIN":mainRelative, "KIMI_PAPER_CONTROL_TOKEN":controlToken])
        reviewProcess = review
        review.onLine = { line in
            if line.hasPrefix("Kimi Paper service: ") {
                reviewAccess = AccessEndpoint.validated(String(line.dropFirst("Kimi Paper service: ".count)))
            }
        }
        for _ in 0..<800 {
            try Task.checkCancellation()
            if reviewAccess != nil { break }
            if !review.running { throw AppFailure.message("论文服务未启动，请检查这篇论文的配置。") }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let reviewAccess else { throw AppFailure.message("论文服务启动超时，请重新连接。") }
        review.onLine = nil
        var address = URLComponents(url: reviewAccess, resolvingAgainstBaseURL: false)!
        address.fragment = nil
        guard let base = address.url else { throw AppFailure.message("论文服务地址无效。") }
        let (paperData, _) = try await http.data(from: base.appendingPathComponent("paper"))
        guard let identity = try JSONSerialization.jsonObject(with: paperData) as? [String: Any],
              identity["watch_dir"] as? String == root else {
            throw AppFailure.message("论文服务目录不匹配，已停止连接。")
        }
        reviewURL = reviewAccess
        var taskAddress = URLComponents(url: reviewAccess, resolvingAgainstBaseURL: false)!
        taskAddress.path = "/studio-panel"
        tasksURL = taskAddress.url
        connecting = false; status = "左侧 Kimi 与右侧论文已独立连接"
        if !ProcessInfo.processInfo.arguments.contains("--project") {
            UserDefaults.standard.set(file.path, forKey: "lastPaper")
            UserDefaults.standard.set(project.path, forKey: "lastPaperRoot")
        }
        review.onExit = { [weak self] in
            guard let self, self.paperGeneration == run else { return }
            self.reviewProcess = nil; self.reviewURL = nil; self.tasksURL = nil
            self.error = "论文页面已停止，请点击重新连接。"
        }
    }

    private func startMonitoringIfNeeded() {
        guard monitorTask == nil else { return }
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let nextBusy = try await self.kimiBusy()
                    if self.lastBusy && !nextBusy && self.reviewURL != nil && !self.maintenance && !self.sending {
                        _ = try await self.serviceRequest("/kp/recompile", body: [:])
                    }
                    self.lastBusy = nextBusy; self.busy = nextBusy
                } catch { /* Keep the visible pages during a transient local poll failure. */ }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    private func kimiBusy() async throws -> Bool {
        guard let api else { return false }
        var before: String?
        var seen = Set<String>()
        while true {
            var path = "sessions?include_archive=true&page_size=100"
            if let before { path += "&before_id=" + before }
            let data = try await api.request(path)
            guard let rows = (data["items"] ?? data["sessions"]) as? [[String: Any]],
                  let more = data["has_more"] as? Bool else {
                throw AppFailure.message("Kimi 会话状态格式不兼容。")
            }
            for row in rows {
                guard let id = row["id"] as? String,
                      row["busy"] is Bool || row["main_turn_active"] is Bool else {
                    throw AppFailure.message("Kimi 会话状态格式不兼容。")
                }
                if row["busy"] as? Bool == true || row["main_turn_active"] as? Bool == true { return true }
                if id.isEmpty { throw AppFailure.message("Kimi 会话状态格式不兼容。") }
            }
            if !more { return false }
            guard let cursor = rows.last?["id"] as? String, !seen.contains(cursor) else {
                throw AppFailure.message("无法完整确认 Kimi 会话状态。")
            }
            seen.insert(cursor); before = cursor
        }
    }

    func serviceRequest(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        guard let reviewURL else { throw CancellationError() }
        var parts = URLComponents(url: reviewURL, resolvingAgainstBaseURL: false)!
        let token = String(parts.fragment?.dropFirst(6) ?? "")
        parts.fragment = nil; parts.path = path
        var request = URLRequest(url: parts.url!); request.timeoutInterval = 240
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if let paperControlToken { request.setValue(paperControlToken, forHTTPHeaderField: "X-Kimi-Paper-Control") }
        if let body {
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await http.data(for: request)
        let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw AppFailure.message(result["error"] as? String ?? "操作未完成。")
        }
        return result
    }

    func chatNavigated(_ url: URL) {
        guard url.scheme == "http", url.host == "127.0.0.1", url.port == chatURL?.port else { return }
        let pieces = url.pathComponents
        let next: String?
        if url.path == "/" { next = nil }
        else if pieces.count == 3, pieces[1] == "sessions",
                pieces[2].range(of: #"^[A-Za-z0-9_-]{1,150}$"#, options: .regularExpression) != nil {
            next = pieces[2]
        } else { return }
        guard next != currentSession else { return }
        selectionEpoch &+= 1
        currentSession = next
    }

    func handleAgentAction(_ body: [String: Any]) async throws -> [String: Any] {
        switch body["operation"] as? String {
        case "sendAnnotation":
            return try await sendAnnotation(body)
        case "executeGit":
            guard let token = body["token"] as? String, !token.isEmpty else {
                throw AppFailure.message("Git 操作已经过期，请重新预览。")
            }
            return try await executeGit(token)
        default:
            throw AppFailure.message("无法识别论文操作。")
        }
    }

    private func sendAnnotation(_ body: [String: Any]) async throws -> [String: Any] {
        guard !maintenance, let api, let session = currentSession,
              let root = projectRoot?.path, let mainFile = paper else {
            throw AppFailure.message("请先在左侧打开一个 Kimi 会话。")
        }
        let epoch = selectionEpoch
        let quote = String((body["quote"] as? String ?? "").prefix(16000))
        let instruction = String((body["text"] as? String ?? "").prefix(16000))
        let digest = body["digest"] as? String
        guard !instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AppFailure.message("请先填写修改要求。")
        }
        sending = true
        defer { sending = false }
        let paperState = try await serviceRequest("/paper")
        guard digest != nil, digest == paperState["pdf_digest"] as? String else {
            throw AppFailure.message("PDF 已更新，请重新划选后发送。")
        }
        let selected = try await api.request("sessions/" + session)
        guard let cwd = (selected["metadata"] as? [String: Any])?["cwd"] as? String,
              cwd.hasPrefix("/") else { throw AppFailure.message("当前 Kimi 会话没有有效工作目录。") }
        guard epoch == selectionEpoch, session == currentSession, !maintenance else {
            throw AppFailure.message("Kimi 会话已经切换，批注尚未发送；请确认后重试。")
        }
        let finalPaperState = try await serviceRequest("/paper")
        guard digest == finalPaperState["pdf_digest"] as? String else {
            throw AppFailure.message("PDF 已更新，请重新划选后发送。")
        }
        guard epoch == selectionEpoch, session == currentSession, !maintenance else {
            throw AppFailure.message("Kimi 会话已经切换，批注尚未发送；请确认后重试。")
        }
        let profile = selected["agent_config"] as? [String: Any] ?? [:]
        let model = profile["model"] as? String ?? "kimi-for-coding/k3-256k"
        let relativeMain = mainFile.path.hasPrefix(root + "/")
            ? String(mainFile.path.dropFirst(root.count + 1)) : mainFile.lastPathComponent
        var prompt = "论文批注。绑定论文根目录：\(root)\n主文件：\(relativeMain)\n请定位原文、修改这篇论文的源文件并保存：\n"
        prompt += "选中的原文：\n> " + quote.replacingOccurrences(of: "\n", with: "\n> ")
        prompt += "\n\n我的要求：" + instruction
        var payload: [String: Any] = [
            "content": [["type":"text", "text":prompt]],
            "prompt_id": UUID().uuidString.lowercased(), "model": model
        ]
        if let thinking = profile["thinking"] as? String { payload["thinking"] = thinking }
        else if model.hasSuffix("k3-256k") { payload["thinking"] = "high" }
        _ = try await api.request("sessions/" + session + "/prompts", method: "POST", body: payload)
        return ["ok": true]
    }

    private func executeGit(_ token: String) async throws -> [String: Any] {
        let lock = try await beginMaintenance()
        guard let ticket = lock["ticket"] as? String, !ticket.isEmpty else {
            await endMaintenance()
            throw AppFailure.message("无法安全锁定论文，请重新操作。")
        }
        do {
            let result = try await serviceRequest(
                "/kp/git/execute", body: ["token": token, "maintenance": ticket])
            await endMaintenance()
            return result
        } catch {
            await endMaintenance()
            throw error
        }
    }

    private func beginMaintenance() async throws -> [String: Any] {
        guard !maintenance else { throw AppFailure.message("另一项版本操作正在进行。") }
        guard !sending else { throw AppFailure.message("批注正在发送，请稍后再执行 Git 操作。") }
        maintenance = true
        selectionEpoch &+= 1
        do {
            try await Task.sleep(nanoseconds: 300_000_000)
            if try await kimiBusy() { throw AppFailure.message("请先等待所有 Kimi Agent 完成或停止。") }
            let process = kimiProcess
            kimiGeneration = UUID(); kimiProcess = nil; api = nil; chatURL = nil
            if let process { try await process.stopAndWait() }
            let ticket = try await serviceRequest("/kp/maintenance", body: [:])
            return ["ticket": ticket["ticket"] as? String ?? ""]
        } catch {
            await endMaintenance()
            throw error
        }
    }

    private func endMaintenance() async {
        defer { maintenance = false }
        guard let cwd = projectRoot ?? paper?.deletingLastPathComponent() else { return }
        do { try await startKimiIfNeeded(cwd: cwd) }
        catch { self.error = (error as? AppFailure)?.errorDescription ?? "Kimi 未能恢复，请点击重新连接。" }
    }

    func studioAction(_ action: String, body: [String: Any] = [:]) {
        guard action == "freeze" else { return }
        Task {
            do { _ = try await serviceRequest("/kp/recompile", body: [:]) }
            catch { self.error = (error as? AppFailure)?.errorDescription ?? "重新编译未完成。" }
        }
    }
}
