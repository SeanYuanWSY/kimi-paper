# Kimi Paper

把 Kimi Code 和 LaTeX 论文批注放进一个 macOS 窗口：左侧交流，右侧看论文，在 PDF 中批注后点「处理批注」，让 Kimi 修改源码、编译并更新 PDF。

这是一个 macOS 集成应用。Kimi Code 提供模型与智能体运行能力；[tex-mcp-web](https://github.com/MiiKiyoshi/tex-mcp-web) 提供论文显示、批注和 MCP 工具；本仓库维护 SwiftUI/AppKit 应用、进程管理、连接和项目配置逻辑。它不是 Kimi 官方发布的应用。

## 使用

构建后双击 `dist/Kimi Paper.app`。首次打开示例论文，也可以点「打开论文」选择自己的 LaTeX 主文件。详见 [使用说明](docs/使用说明.md)。

应用依赖本机已安装且登录有效的 Kimi Code，以及 LaTeX 编译环境。支持本地 `.tex` 工程；当前没有 Word 编辑或仅凭 PDF 修改源稿的功能。

## 从源码构建

已验证环境：Apple Silicon macOS、Swift 6.3.1、Python 3.12.13、Kimi Code 0.40.1、mcp 1.29.1。

需要 Apple Command Line Tools（或 Xcode）、[uv](https://docs.astral.sh/uv/)、Git，以及上述 Kimi / LaTeX 环境。

```bash
./scripts/build.sh
./scripts/check.sh
```

首次构建需要联网下载 Python 依赖及固定提交的 tex-mcp-web。Python 环境在仓库的 `.runtime/` 中创建；打包时将运行时复制进应用。构建不会替换个人应用程序文件夹里已安装的版本。重复构建会把旧产物保留在 `dist/previous-*`，可自行清理。

`./scripts/build.sh --run` 会在构建后打开应用。若有完整 Xcode / XCTest 环境，还可以执行 `swift test`；只有 Command Line Tools 的已验证机器使用上述独立检查及实际应用流程测试。

## 仓库内容

- `Sources/`：原生窗口、双栏网页、Kimi API 和进程生命周期。
- `Resources/`：项目准备器、进程监督器、图标、应用元数据及上游虚构示例论文。
- `scripts/`：准备运行环境、构建、检查。
- `Tests/`：本地连接地址边界检查。
- `requirements.lock`：固定的 Python 依赖版本和上游 Git 提交。
- `licenses/` 与 [第三方来源](THIRD_PARTY_NOTICES.md)：来源及许可信息。

应用本身、Python 环境、真实论文、批注记录、Kimi 私有配置和登录凭据不应提交到源码仓库。应用更新暂由本地重新构建完成，没有自动更新服务。

## 已验证范围

已在原始安装版完成：论文级批注 → Kimi 修改示例标题 → 编译成功 → PDF 更新 → 批注解决；退出后关闭应用拥有的 9 个后台进程；重开恢复原对话并继续聊天。内嵌网页的加载层退场动画也已做显示适配。

整机重启、其他 Mac、复杂期刊模板，以及原生窗口中的自动化鼠标拖选尚未全部实测。以上范围不代表每种 Kimi 工具都做过单独回归。

## 维护

目前按私有应用源码仓库整理，尚未决定对外公开发布或原生代码的开源许可。上游依赖遵守各自许可证。先在示例论文验证改动，再更新日常使用的安装版；更换 Kimi 或 tex-mcp-web 版本时应重新验证连接、批注、编译和退出清理。
