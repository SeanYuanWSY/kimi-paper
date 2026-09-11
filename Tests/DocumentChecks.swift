import Foundation

@MainActor final class FakeKimiClient: KimiSessionClient {
    var posts: [[String: Any]] = []
    var beforeMetadata: (() async throws -> Void)?
    func request(_ path: String, method: String, body: [String: Any]?) async throws -> [String: Any] {
        if method == "POST" { posts.append(body ?? [:]); return [:] }
        try await beforeMetadata?()
        return ["metadata": ["cwd": "/fictional/research"], "agent_config": ["model": "test-model", "thinking": "medium"]]
    }
    func browserURL(sessionID: String?) -> URL { URL(string: "http://127.0.0.1:19999/sessions/fixture")! }
}

@main struct DocumentChecks {
    @MainActor static func main() async throws {
        let fm = FileManager.default
        let root = URL(fileURLWithPath: fm.currentDirectoryPath).appendingPathComponent(".build/checks/documents-" + UUID().uuidString)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }
        let first = root.appendingPathComponent("draft.md"), second = root.appendingPathComponent("notes.txt")
        try Data("# Draft\n\nALPHA selected passage".utf8).write(to: first)
        try Data("BETA second document".utf8).write(to: second)
        try Data("hidden".utf8).write(to: root.appendingPathComponent(".secret"))
        try fm.createDirectory(at: root.appendingPathComponent("figures"), withIntermediateDirectories: true)
        try fm.createSymbolicLink(at: root.appendingPathComponent("shortcut.txt"), withDestinationURL: second)
        try fm.createSymbolicLink(at: root.appendingPathComponent("linked"), withDestinationURL: root.appendingPathComponent("figures"))
        try fm.createSymbolicLink(at: root.appendingPathComponent("linked.tex"), withDestinationURL: root.appendingPathComponent("missing.tex"))
        try DocumentLibrary.validateRoot(root)
        let entries = try DocumentLibrary.entries(in: root, root: root)
        precondition(entries.map(\.url.lastPathComponent) == ["figures", "draft.md", "notes.txt"])
        for path in ["shortcut.txt", ".secret", "../outside.txt", "linked/escape.txt"] {
            do { _ = try DocumentLibrary.read(root.appendingPathComponent(path), root: root); preconditionFailure("unsafe read: \(path)") }
            catch { }
        }
        let large = root.appendingPathComponent("large.txt")
        try Data(repeating: 65, count: 4 * 1024 * 1024 + 1).write(to: large)
        do { _ = try DocumentLibrary.read(large, root: root); preconditionFailure("oversize text accepted") } catch { }
        for unsafe in [fm.homeDirectoryForCurrentUser, URL(fileURLWithPath: "/"), fm.homeDirectoryForCurrentUser.appendingPathComponent(".codex")] {
            do { try DocumentLibrary.validateRoot(unsafe); preconditionFailure("unsafe root") } catch { }
        }
        try DocumentLibrary.validateLatexEntry(root.appendingPathComponent("new.tex"), root: root)
        do { try DocumentLibrary.validateLatexEntry(root.appendingPathComponent("linked.tex"), root: root); preconditionFailure("symlink latex") } catch { }
        do { try DocumentLibrary.validateLatexEntry(root.appendingPathComponent("linked/main.tex"), root: root); preconditionFailure("symlink latex parent") } catch { }

        let client = FakeKimiClient(), workspace = PaperWorkspace(client: nil)
        workspace.projectRoot = root; workspace.busy = true
        workspace.chatURL = client.browserURL(sessionID: "fixture")
        workspace.reviewURL = URL(string: "http://127.0.0.1:20000/#token=fictional")!
        let chat = workspace.chatURL, service = workspace.reviewURL
        workspace.openDocument(first); let old = workspace.documentRevision
        workspace.openDocument(second)
        precondition(workspace.selectedDocument == second && workspace.documentRevision != old)
        precondition(workspace.busy && workspace.chatURL == chat && workspace.reviewURL == service)
        workspace.documentsOnly = true
        workspace.updateKimiBusy(true); workspace.updateKimiBusy(false)
        precondition(!workspace.busy, "Agent completion must clear busy independently of compilation")
        workspace.sending = true; workspace.openDocument(first); precondition(workspace.selectedDocument == second)
        workspace.sending = false; workspace.maintenance = true; workspace.openDocument(first); precondition(workspace.selectedDocument == second)

        let active = PaperWorkspace(client: client, session: "fixture")
        active.projectRoot = root; active.selectedDocument = first; active.busy = true
        let snapshot = try DocumentLibrary.read(first, root: root)
        try await active.sendDocumentAnnotation(snapshot: snapshot, revision: active.documentRevision, quote: "ALPHA", pages: "", instruction: "Make it clearer")
        precondition(client.posts.count == 1 && !active.sending)
        let prompt = ((client.posts[0]["content"] as! [[String: String]])[0]["text"]!)
        precondition(prompt.contains(first.path) && prompt.contains("ALPHA") && !prompt.contains("main.tex"))
        precondition(client.posts[0]["model"] as? String == "test-model")
        let handler = active.scopedAgentAction()
        active.openDocument(second)
        do { _ = try await handler(["operation": "sendAnnotation"]); preconditionFailure("old pane accepted") } catch { }
        do { try await active.sendDocumentAnnotation(snapshot: snapshot, revision: old, quote: "ALPHA", pages: "", instruction: "old"); preconditionFailure("stale selected file") } catch { }
        precondition(client.posts.count == 1)

        active.openDocument(first)
        try Data("New file bytes".utf8).write(to: first)
        do { try await active.sendDocumentAnnotation(snapshot: snapshot, revision: active.documentRevision, quote: "ALPHA", pages: "", instruction: "old"); preconditionFailure("stale digest") } catch { }
        precondition(client.posts.count == 1 && !active.sending)
        let updated = try DocumentLibrary.read(first, root: root)
        client.beforeMetadata = {
            active.refreshDocument()
        }
        do { try await active.sendDocumentAnnotation(snapshot: updated, revision: active.documentRevision, quote: "New", pages: "", instruction: "race"); preconditionFailure("refresh during await") } catch { }
        precondition(client.posts.count == 1)
        client.beforeMetadata = {
            do { try await active.sendDocumentAnnotation(snapshot: updated, revision: active.documentRevision, quote: "New", pages: "", instruction: "duplicate"); preconditionFailure("concurrent send") } catch { }
        }
        try await active.sendDocumentAnnotation(snapshot: updated, revision: active.documentRevision, quote: "New", pages: "", instruction: "one send")
        precondition(client.posts.count == 2 && !active.sending)
        client.beforeMetadata = nil
        let pdf = root.appendingPathComponent("reference.pdf")
        try Data("fictional PDF bytes for transport test".utf8).write(to: pdf)
        active.openDocument(pdf)
        let pdfSnapshot = try DocumentLibrary.read(pdf, root: root)
        try await active.sendDocumentAnnotation(snapshot: pdfSnapshot, revision: active.documentRevision, quote: "two page passage", pages: "1、2", instruction: "explain")
        let pdfPrompt = (client.posts.last!["content"] as! [[String: String]])[0]["text"]!
        precondition(pdfPrompt.contains(pdf.path) && pdfPrompt.contains("PDF 页码：1、2") && pdfPrompt.contains("参考 PDF") && pdfPrompt.contains("核对"))
        print("PASS: bounded document reads, symlink/root rejection, independent busy browsing, stale view/file/refresh rejection, single-send lock, PDF source boundary and model preservation")
    }
}
