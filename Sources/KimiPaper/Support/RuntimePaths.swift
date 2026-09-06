import Foundation
import Darwin
import CFNetwork

enum AppFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }
}

struct RuntimePaths {
    let resources = Bundle.main.resourceURL!
    var python: URL { resources.appendingPathComponent("python/bin/python3.12") }
    var helper: URL { resources.appendingPathComponent("prepare_project.py") }
    var supervisor: URL { resources.appendingPathComponent("supervise.py") }
    var paperService: URL { resources.appendingPathComponent("paper_service.py") }
    var kimi: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".kimi-code/bin/kimi") }
    var support: URL { FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Kimi Paper") }

    // Keep local app requests direct; only the trusted Codex gateway receives this route.
    var agentProxyEnvironment: [String: String] {
        let inherited = ProcessInfo.processInfo.environment
        for name in ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"] {
            if let value = inherited[name], !value.isEmpty {
                return ["KIMI_PAPER_UPSTREAM_PROXY": value]
            }
        }
        guard let settings = CFNetworkCopySystemProxySettings()?.takeRetainedValue() as? [String: Any] else { return [:] }
        for prefix in ["HTTPS", "HTTP"] {
            if (settings[prefix + "Enable"] as? NSNumber)?.boolValue == true,
               let host = settings[prefix + "Proxy"] as? String,
               let port = settings[prefix + "Port"] as? Int, (1...65535).contains(port) {
                var address = URLComponents()
                address.scheme = "http"; address.host = host; address.port = port
                if let value = address.string { return ["KIMI_PAPER_UPSTREAM_PROXY": value] }
            }
        }
        return [:]
    }

    func environment(localOnly: Bool = false) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = "/Library/TeX/texbin:/opt/homebrew/bin:/usr/local/bin:\(home)/.local/bin:\(home)/.npm-global/bin:\(home)/.kimi-code/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUNBUFFERED"] = "1"
        // The bundled interpreter owns its imports; never inherit another Python environment.
        env.removeValue(forKey: "PYTHONHOME")
        env.removeValue(forKey: "PYTHONPATH")
        if localOnly {
            for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"] { env.removeValue(forKey: key) }
            env["NO_PROXY"] = "localhost,127.0.0.1"
            env["no_proxy"] = "localhost,127.0.0.1"
        }
        return env
    }

    func example() throws -> URL {
        let dir = support.appendingPathComponent("示例论文")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let main = dir.appendingPathComponent("main.tex")
        if !FileManager.default.fileExists(atPath: main.path) {
            try FileManager.default.copyItem(at: resources.appendingPathComponent("example.tex"), to: main)
        }
        return main
    }

    static func freePort() throws -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw AppFailure.message("无法分配本地端口。") }
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else { throw AppFailure.message("本地端口分配失败。") }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &address) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
        }
        guard result == 0 else { throw AppFailure.message("无法读取本地端口。") }
        return Int(UInt16(bigEndian: address.sin_port))
    }
}
