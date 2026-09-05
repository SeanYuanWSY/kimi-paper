import SwiftUI
import WebKit

struct WebPane: NSViewRepresentable {
    let url: URL
    let revision: UUID
    let onFailure: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(url: url, onFailure: onFailure) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        installScripts(in: config)
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = false
        context.coordinator.lastURL = url
        context.coordinator.revision = revision
        view.load(URLRequest(url: navigationURL))
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
        localStorage.setItem('sidebarCollapsed', '1');
        if (location.pathname.startsWith('/sessions/')) {
          localStorage.setItem('kimi-web.onboarded', '1');
          localStorage.setItem('kimi-web.sidebar-collapsed', 'true');
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
        view.load(URLRequest(url: navigationURL))
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.stopLoading(); view.navigationDelegate = nil; view.uiDelegate = nil
        view.configuration.userContentController.removeAllUserScripts()
        view.configuration.websiteDataStore.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast, completionHandler: {})
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var allowedPort: Int?
        var lastURL: URL?
        var revision: UUID?
        let onFailure: (String) -> Void
        init(url: URL, onFailure: @escaping (String) -> Void) {
            allowedPort = url.port; self.onFailure = onFailure
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
