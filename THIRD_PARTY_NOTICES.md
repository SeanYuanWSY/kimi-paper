# 第三方来源

## tex-mcp-web

- 项目：https://github.com/MiiKiyoshi/tex-mcp-web
- 固定提交：`fd7e337469445ce35bf8b2013ce1b0835e74430b`
- 版本：0.7.0
- 用途：PDF 页面、批注、MCP 工具及编译工作流。
- MIT 许可证副本：[licenses/tex-mcp-web-MIT.txt](licenses/tex-mcp-web-MIT.txt)。
- `Resources/example.tex` 来自该提交的 `examples/demo-paper/main.tex`，是一篇虚构示例论文。

上游说明 tex-mcp-web 源自 queelius/scholia 的 `e6c745400d2ad70fb43eca053e31183d48765f89` 提交。其 PDF 页面使用 EmbedPDF 和 PDFium WebAssembly；相应声明随安装包保留在 `tex_mcp_web/static/embedpdf/LICENSE` 与 `LICENSE.pdfium`。

## Kimi Code

通过本机已安装的 Kimi Code 启动官方 Web 服务，未将其二进制或用户配置纳入本仓库。使用遵守 Kimi Code 自身的许可及服务条款。

官方 Web 文档：https://moonshotai.github.io/kimi-code/en/guides/web.html

## 其他运行依赖

Python 及 Python 包在构建时下载并随本机应用打包，各自的许可文件保留在运行时和包元数据中；版本见 `requirements.lock`。本应用对第三方依赖的引用不改变其许可证。
