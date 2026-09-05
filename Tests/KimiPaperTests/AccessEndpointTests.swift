import XCTest
@testable import KimiPaper

final class AccessEndpointTests: XCTestCase {
    func testOnlyOwnedLoopbackEndpointCanReceiveAuthorization() {
        XCTAssertNotNil(AccessEndpoint.validated("http://127.0.0.1:12345/#token=example", port: 12345))
        for candidate in [
            "https://example.org:12345/#token=example",
            "http://127.0.0.1:23456/#token=example",
            "http://attacker@127.0.0.1:12345/#token=example",
            "http://127.0.0.1:12345/?redirect=remote#token=example",
            "http://127.0.0.1:12345/#token=",
            "http://127.0.0.1:12345/#token=example%0Aother"
        ] {
            XCTAssertNil(AccessEndpoint.validated(candidate, port: 12345), candidate)
        }
    }
}
