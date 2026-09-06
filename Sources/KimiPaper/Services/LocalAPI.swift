import Foundation

private final class RejectRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

@MainActor
final class LocalAPI {
    private let base: URL
    private let token: String
    private let session: URLSession

    init(accessURL: URL) throws {
        guard var parts = URLComponents(url: accessURL, resolvingAgainstBaseURL: false),
              parts.scheme == "http", parts.host == "127.0.0.1", parts.user == nil, parts.password == nil,
              let fragment = parts.fragment, fragment.hasPrefix("token="), fragment.count > 6 else {
            throw AppFailure.message("Kimi 未返回有效的本地连接信息。")
        }
        token = String(fragment.dropFirst(6))
        parts.fragment = nil; parts.path = "/"; parts.query = nil
        base = parts.url!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.connectionProxyDictionary = [:]
        session = URLSession(configuration: configuration, delegate: RejectRedirects(), delegateQueue: nil)
    }

    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> [String: Any] {
        let pieces = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        var parts = URLComponents(url: base.appendingPathComponent("api/v1/" + pieces[0]), resolvingAgainstBaseURL: false)!
        if pieces.count == 2 { parts.percentEncodedQuery = String(pieces[1]) }
        guard let url = parts.url else { throw AppFailure.message("Kimi 请求地址无效。") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200 || response.statusCode == 201,
              let envelope = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AppFailure.message("无法连接 Kimi。请尝试重新连接。")
        }
        guard (envelope["code"] as? Int) == 0 else {
            // Server diagnostics may contain sensitive paths or credentials. Show only a code.
            throw AppFailure.message("Kimi 请求未完成（\(envelope["code"] as? Int ?? -1)）。请查看左侧提示。")
        }
        return envelope["data"] as? [String: Any] ?? [:]
    }

    func browserURL(sessionID: String? = nil) -> URL {
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.path = sessionID.map { "/sessions/" + $0 } ?? "/"
        parts.fragment = "token=" + token
        return parts.url!
    }
}
