import Foundation
@main struct SecurityChecks {
    static func main() {
        precondition(AccessEndpoint.validated("http://127.0.0.1:12345/#token=example", port: 12345) != nil)
        let rejected = ["https://example.org:12345/#token=example", "http://127.0.0.1:23456/#token=example", "http://attacker@127.0.0.1:12345/#token=example", "http://127.0.0.1:12345/?redirect=remote#token=example", "http://127.0.0.1:12345/#token=", "http://127.0.0.1:12345/#token=example%0Aother"]
        for candidate in rejected { precondition(AccessEndpoint.validated(candidate, port: 12345) == nil) }
        print("PASS: local authentication URL boundary (7 cases)")
    }
}
