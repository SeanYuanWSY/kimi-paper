import SwiftUI
import WebKit

// Starting navigation before SwiftUI attaches a replacement pane can leave WebKit blank.
final class AttachedWebView: WKWebView {
    private var pendingRequest: URLRequest?

    func navigate(_ request: URLRequest) {
        guard window != nil else { pendingRequest = request; return }
        pendingRequest = nil
        load(request)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window != nil, let request = pendingRequest { navigate(request) }
    }
}

struct WebPane: NSViewRepresentable {
    let url: URL
    let revision: UUID
    let onFailure: (String) -> Void
    var onLocation: ((URL) -> Void)? = nil
    var translation: TranslationService? = nil
    var agentAction: (([String: Any]) async throws -> [String: Any])? = nil

    func makeCoordinator() -> Coordinator { Coordinator(url: url, onFailure: onFailure, translation: translation, agentAction: agentAction) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        installScripts(in: config)
        if translation != nil { config.userContentController.addScriptMessageHandler(context.coordinator, contentWorld: .page, name: "paper") }
        let view = AttachedWebView(frame: .zero, configuration: config)
        context.coordinator.locationObservation = view.observe(\.url, options: [.new]) { _, change in
            if let address = change.newValue ?? nil { DispatchQueue.main.async { onLocation?(address) } }
        }
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = false
        context.coordinator.lastURL = url
        context.coordinator.revision = revision
        view.navigate(URLRequest(url: navigationURL))
        return view
    }

    private var navigationURL: URL {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.fragment = nil
        return parts.url!
    }

    private func installScripts(in config: WKWebViewConfiguration) {
        config.userContentController.removeAllUserScripts()
        let bootstrap = """
        localStorage.setItem('kimi-web.onboarded', '1');
        if (location.pathname.startsWith('/sessions/')) {
          document.addEventListener('DOMContentLoaded', () => {
            const style = document.createElement('style');
            // WKWebView can pause the animation frame that finishes Vue's ready-state fade.
            // Only the leaving state is hidden; active loading and error messages remain visible.
            style.textContent = '.gload.gload-fade-leave-active { visibility: hidden !important; pointer-events: none !important; transition: none !important; }';
            document.head.appendChild(style);
          });
        }
        """
        config.userContentController.addUserScript(WKUserScript(source: bootstrap, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        // Use the official in-page credential store without exposing a credential in the page URL.
        // This WKWebsiteDataStore is nonpersistent and is cleared when the pane is dismantled.
        if url.scheme == "http", url.host == "127.0.0.1", let port = url.port,
           let fragment = url.fragment, fragment.hasPrefix("token="), fragment.count > 6,
           let values = try? JSONSerialization.data(withJSONObject: ["http://127.0.0.1:\(port)", String(fragment.dropFirst(6))]),
           let encoded = String(data: values, encoding: .utf8) {
            let authentication = """
            (() => {
              const [origin, credential] = \(encoded);
              if (location.origin === origin) {
                localStorage.setItem('kimi-web.server-credential', JSON.stringify({version: 1, credential, expiresAt: Date.now() + 604800000}));
                \(translation != nil ? """
                sessionStorage.setItem('kimi-paper.token', credential);
                const originalFetch = window.fetch.bind(window);
                window.fetch = (input, options = {}) => {
                  const target = new URL(typeof input === 'string' ? input : input.url, location.href);
                  if (target.origin === origin) {
                    const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
                    headers.set('Authorization', 'Bearer ' + credential);
                    options = {...options, headers};
                  }
                  return originalFetch(input, options);
                };
                """ : "")
              }
            })();
            """
            config.userContentController.addUserScript(WKUserScript(source: authentication, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard context.coordinator.lastURL != url || context.coordinator.revision != revision else { return }
        context.coordinator.allowedPort = url.port
        context.coordinator.lastURL = url
        context.coordinator.revision = revision
        installScripts(in: view.configuration)
        (view as? AttachedWebView)?.navigate(URLRequest(url: navigationURL))
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.stopLoading(); view.navigationDelegate = nil; view.uiDelegate = nil
        view.configuration.userContentController.removeAllUserScripts()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "paper", contentWorld: .page)
        view.configuration.websiteDataStore.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast, completionHandler: {})
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
        var locationObservation: NSKeyValueObservation?
        var allowedPort: Int?
        var lastURL: URL?
        var revision: UUID?
        let onFailure: (String) -> Void
        let translation: TranslationService?
        let agentAction: (([String: Any]) async throws -> [String: Any])?
        init(url: URL, onFailure: @escaping (String) -> Void, translation: TranslationService?, agentAction: (([String: Any]) async throws -> [String: Any])?) {
            allowedPort = url.port; self.onFailure = onFailure
            self.translation = translation; self.agentAction = agentAction
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                                   replyHandler: @escaping (Any?, String?) -> Void) {
            guard message.frameInfo.isMainFrame, let source = message.frameInfo.request.url, permitted(source),
                  let body = message.body as? [String: Any], let translation else {
                replyHandler(nil, "不允许的页面请求。"); return
            }
            Task { @MainActor in
                do {
                    if ["sendAnnotation", "executeGit"].contains(body["operation"] as? String ?? "") {
                        replyHandler(try await agentAction?(body) ?? [:], nil)
                    } else { replyHandler(try await translation.handle(body), nil) }
                } catch { replyHandler(nil, (error as? AppFailure)?.errorDescription ?? "请求未完成，请检查连接或设置。") }
            }
        }

        func permitted(_ url: URL) -> Bool {
            url.scheme == "http" && url.host == "127.0.0.1" && url.port == allowedPort && url.user == nil && url.password == nil
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { decisionHandler(.cancel); return }
            if permitted(url) || (action.targetFrame?.isMainFrame == false && url.scheme == "about") {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
                // External links never inherit the local authorization fragment or referrer.
                if action.navigationType == .linkActivated, url.scheme == "https", url.fragment == nil, url.user == nil, url.password == nil {
                    NSWorkspace.shared.open(url)
                }
            }
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            guard frame.isMainFrame, let source = frame.request.url, permitted(source) else { completionHandler(false); return }
            let alert = NSAlert(); alert.messageText = "确认操作"
            alert.informativeText = String(message.prefix(2000))
            alert.addButton(withTitle: "确认"); alert.addButton(withTitle: "取消")
            completionHandler(alert.runModal() == .alertFirstButtonReturn)
        }

        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            if frame.isMainFrame, let source = frame.request.url, permitted(source) { onFailure(String(message.prefix(2000))) }
            completionHandler()
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url, permitted(url) { webView.load(URLRequest(url: url)) }
            return nil
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { onFailure("页面暂时无法打开，请重新连接。") }
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            onFailure("页面进程已停止，请点击刷新页面。")
        }
    }
}
