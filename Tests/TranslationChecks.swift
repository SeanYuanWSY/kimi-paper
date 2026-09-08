import Foundation

final class TranslationStub: URLProtocol {
    static let lock = NSLock()
    static var requests = 0
    static var status = 200
    static var body = ""
    static var delay: TimeInterval = 0
    static var lastPayload: [String: Any] = [:]
    private var delivery: DispatchWorkItem?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        Self.requests += 1
        var data = request.httpBody
        if data == nil, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var result = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                result.append(buffer, count: count)
            }
            data = result
        }
        Self.lastPayload = (try? JSONSerialization.jsonObject(with: data ?? Data())) as? [String: Any] ?? [:]
        let status = Self.status, body = Self.body, delay = Self.delay
        Self.lock.unlock()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(body.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
        delivery = work
        DispatchQueue.global().asyncAfter(deadline: .now() + delay, execute: work)
    }
    override func stopLoading() { delivery?.cancel() }
}

final class CredentialProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var reads = 0
    var count: Int { lock.withLock { reads } }
    func next() -> Int { lock.withLock { reads += 1; return reads } }
}

@main struct TranslationChecks {
    @MainActor static func main() async throws {
        precondition(RuntimePaths().environment()["PYTHONDONTWRITEBYTECODE"] == "1", "Bundled Python must not invalidate the app signature")
        let suite = "local.kimi-paper.tests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(suite)
        defer { try? FileManager.default.removeItem(at: folder) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TranslationStub.self]
        let credentialReads = CredentialProbe()
        let service = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("cache.json"),
                                         session: URLSession(configuration: configuration), secretReader: { _ = credentialReads.next(); return "fictional-test-only" },
                                         keyAvailable: { false })
        do {
            _ = try await service.handle(["operation": "translate", "text": "Missing configuration"])
            preconditionFailure("Missing settings must fail")
        } catch { precondition(TranslationStub.requests == 0) }
        defaults.set("https://translation.invalid/v1/chat/completions", forKey: "translation.endpoint")
        defaults.set("test-model", forKey: "translation.model")
        TranslationStub.body = """
        {"choices":[{"message":{"content":"测试译文 Eq. (1) [2]"}}]}
        """
        TranslationStub.delay = 0.1
        async let first = service.handle(["operation": "translate", "text": "Passage Eq. (1) [2]"])
        async let duplicate = service.handle(["operation": "translate", "text": "Passage Eq. (1) [2]"])
        let results = try await [first, duplicate]
        precondition(results.allSatisfy { $0["text"] as? String == "测试译文 Eq. (1) [2]" })
        precondition(TranslationStub.requests == 1, "Concurrent duplicates must share a request")
        _ = try await service.handle(["operation": "translate", "text": "Passage Eq. (1) [2]"])
        precondition(TranslationStub.requests == 1, "Repeated passages must use cache")
        defaults.set("another-model", forKey: "translation.model")
        _ = try await service.handle(["operation": "translate", "text": "Passage Eq. (1) [2]"])
        precondition(TranslationStub.requests == 2, "Model must be part of cache identity")
        TranslationStub.status = 401
        do {
            _ = try await service.handle(["operation": "translate", "text": "Denied"])
            preconditionFailure("Provider errors must be visible")
        } catch { precondition(error.localizedDescription.contains("401")) }
        defaults.set("http://translation.invalid", forKey: "translation.endpoint")
        do {
            _ = try await service.handle(["operation": "translate", "text": "Unsafe endpoint"])
            preconditionFailure("Insecure endpoint must fail before network")
        } catch { precondition(TranslationStub.requests == 3) }
        defaults.set("https://translation.invalid/v1/chat/completions", forKey: "translation.endpoint")
        TranslationStub.status = 200
        TranslationStub.delay = 0.5
        let obsolete = Task { try await service.handle(["operation": "translate", "text": "Old settings passage"]) }
        try await Task.sleep(nanoseconds: 50_000_000)
        _ = try await service.handle(["operation": "saveSettings", "endpoint": "https://translation.invalid/v1/chat/completions",
                                      "model": "new-model", "language": "简体中文", "enabled": true])
        do {
            _ = try await obsolete.value
            preconditionFailure("Settings changes must cancel an obsolete response")
        } catch { /* Expected cancellation, without accessing a real Keychain item. */ }
        defaults.set("qwen-mt-flash", forKey: "translation.model")
        TranslationStub.delay = 0
        let qwenSource = "Scientific example Eq. (1) [2]"
        _ = try await service.handle(["operation": "translate", "text": qwenSource])
        let payload = TranslationStub.lastPayload
        let messages = payload["messages"] as? [[String: String]]
        precondition(messages == [["role": "user", "content": qwenSource]], "Qwen-MT must receive exactly one unmodified user passage")
        precondition(payload["translation_options"] as? [String: String] == ["source_lang": "auto", "target_lang": "Chinese"])
        defaults.set("English", forKey: "translation.language")
        _ = try await service.handle(["operation": "translate", "text": qwenSource])
        precondition((TranslationStub.lastPayload["translation_options"] as? [String: String])?["target_lang"] == "English")
        defaults.set("generic-chat-model", forKey: "translation.model")
        _ = try await service.handle(["operation": "translate", "text": qwenSource])
        precondition(TranslationStub.lastPayload["translation_options"] == nil)
        precondition((TranslationStub.lastPayload["messages"] as? [[String: String]])?.first?["role"] == "system")
        precondition(credentialReads.count == 1, "Different passages and model/language changes must reuse the authorized credential")
        defaults.set("https://another.invalid/v1/chat/completions", forKey: "translation.endpoint")
        _ = try await service.handle(["operation": "translate", "text": "A changed endpoint"])
        precondition(credentialReads.count == 2, "An endpoint change must invalidate credential reuse")
        let reopened = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("reopened.json"),
            session: URLSession(configuration: configuration), secretReader: { _ = credentialReads.next(); return "fictional-test-only" }, keyAvailable: { false })
        _ = try await reopened.handle(["operation": "translate", "text": "A new service instance"])
        precondition(credentialReads.count == 3, "Credentials must not persist between service instances")
        let attempts = CredentialProbe()
        let denied = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("denied.json"),
            session: URLSession(configuration: configuration), secretReader: {
                let attempt = attempts.next()
                if attempt == 1 { throw NSError(domain: "fictional-denial", code: 1) }
                return "fictional-test-only"
            }, keyAvailable: { false })
        do {
            _ = try await denied.handle(["operation": "translate", "text": "Retry permission"])
            preconditionFailure("Denied credential read must fail")
        } catch { precondition(attempts.count == 1) }
        _ = try await denied.handle(["operation": "translate", "text": "Retry permission"])
        _ = try await denied.handle(["operation": "translate", "text": "Another passage after permission"])
        precondition(attempts.count == 2, "Failures must not be cached; successful retries must be reused")
        let slowReads = CredentialProbe()
        let slow = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("slow.json"),
            session: URLSession(configuration: configuration), secretReader: {
                _ = slowReads.next()
                Thread.sleep(forTimeInterval: 0.3)
                return "fictional-slow"
            }, keyAvailable: { false })
        let slowFirst = Task { try await slow.handle(["operation":"translate", "text":"Slow passage one"]) }
        let slowSecond = Task { try await slow.handle(["operation":"translate", "text":"Slow passage two"]) }
        let heartbeat = Date()
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(Date().timeIntervalSince(heartbeat) < 0.2, "Credential access must not block the UI actor")
        _ = try await slowFirst.value
        _ = try await slowSecond.value
        precondition(slowReads.count == 1, "Different passages must share an in-flight credential read")
        let changingReads = CredentialProbe()
        let changing = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("changing.json"),
            session: URLSession(configuration: configuration), secretReader: {
                _ = changingReads.next()
                Thread.sleep(forTimeInterval: 0.3)
                return "fictional-obsolete"
            }, keyAvailable: { false })
        let requestsBefore = TranslationStub.requests
        let invalidated = Task { try await changing.handle(["operation":"translate", "text":"Invalidated during credential access"]) }
        try await Task.sleep(nanoseconds: 30_000_000)
        _ = try await changing.handle(["operation":"saveSettings", "endpoint":defaults.string(forKey:"translation.endpoint")!,
                                      "model":"updated-model", "language":"English", "enabled":true])
        do { _ = try await invalidated.value; preconditionFailure("Old credential request must be cancelled") }
        catch { precondition(TranslationStub.requests == requestsBefore, "No HTTP with an invalidated credential") }
        _ = try await changing.handle(["operation":"translate", "text":"New settings after cancelled credential read"])
        precondition(changingReads.count == 2, "Old credential completion must not repopulate the cache")
        print("PASS: slow credentials keep UI responsive; reads coalesce; settings invalidate old credentials before HTTP")
        let metadata = TranslationService(defaults: defaults, cacheURL: folder.appendingPathComponent("metadata.json"),
            session: URLSession(configuration: configuration), secretReader: { "fictional-unused" },
            keyAvailable: { Thread.sleep(forTimeInterval: 0.3); return true })
        let metadataTask = Task { try await metadata.handle(["operation":"settings"]) }
        let metadataHeartbeat = Date()
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(Date().timeIntervalSince(metadataHeartbeat) < 0.2, "Key metadata queries must not block UI")
        let metadataResult = try await metadataTask.value
        precondition(metadataResult["hasKey"] as? Bool == true)
        let diskCache = try String(contentsOf: folder.appendingPathComponent("cache.json"), encoding: .utf8)
        precondition(!diskCache.contains("fictional-test-only"), "Credential must not enter the disk cache")
        print("PASS: credential reads reused per service/endpoint; failures retry; new instances reread; no credential in disk cache")
        print("PASS: translation configuration, deduplication, cache identity, provider errors and HTTPS validation (mock transport; no Keychain access)")
    }
}
