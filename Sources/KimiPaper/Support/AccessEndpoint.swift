import Foundation

enum AccessEndpoint {
    static func validated(_ raw: String, port: Int) -> URL? {
        guard let url = URL(string: raw), let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "http", parts.host == "127.0.0.1", parts.port == port,
              parts.user == nil, parts.password == nil, parts.query == nil,
              parts.path == "/", let fragment = parts.fragment,
              fragment.hasPrefix("token="), fragment.count > 6,
              !fragment.contains(where: { $0.isWhitespace }) else { return nil }
        return url
    }
}
