# Kimi Paper

专门适配原生 Kimi Code 的 macOS 论文应用：右侧阅读并批注，后台生成修改建议；点回批注查看建议、继续反馈，确认采纳后才写入正文。支持即时并行、批量顺序处理、本地自动版本和手动 GitHub 协作。阅读翻译使用单独配置的 API。

这是一个 macOS 集成应用。[tex-mcp-web](https://github.com/MiiKiyoshi/tex-mcp-web) 提供论文显示和批注；本仓库维护 SwiftUI/AppKit 应用、Kimi 调用、建议审核、版本和协作逻辑。普通改写使用原生 Kimi 的无工具建议模式；需要搜索或工具时可选深入处理，并保留具体权限确认。它不是 Kimi 官方发布的应用。

## 使用

构建后双击 `dist/Kimi Paper.app`。首次打开示例论文，也可以点「打开论文」选择自己的 LaTeX 主文件。详见 [使用说明](docs/使用说明.md)。

应用依赖本机已安装且登录有效的 Kimi Code，以及 LaTeX 编译环境与 latexmk。默认 Kimi K3 256K，可选择当前 Kimi 提供的其他模型。支持本地 `.tex` 工程；当前没有 Word 编辑或仅凭 PDF 修改源稿的功能。旧 Codex 任务仅保留历史，新任务只使用 Kimi。

## 从源码构建

已验证环境：Apple Silicon macOS、Swift 6.3.1、Python 3.12.13、Kimi Code 0.41.0、mcp 1.29.1。

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
- `Tests/`：任务并发、隔离、取消、事务恢复与翻译请求的检查。
- `requirements.lock`：固定的 Python 依赖版本和上游 Git 提交。
- `licenses/` 与 [第三方来源](THIRD_PARTY_NOTICES.md)：来源及许可信息。

应用本身、Python 环境、真实论文、批注记录、Kimi 私有配置和登录凭据不应提交到源码仓库。应用更新暂由本地重新构建完成，没有自动更新服务。

## 当前验收状态

Kimi 专用版正在进行安装后的界面验收。80 项 Python 检查、Swift 安全与翻译检查、网页脚本检查通过；虚构论文上的真实 K3 普通建议约 5.6 秒，确认前源稿不变，采纳与撤销验证通过。Git 协作使用本地模拟远端验证快进、分叉保护和手动推送，不在真实论文仓库自动执行网络操作。

完整范围和未完成项见 [开发计划与验收记录](docs/开发计划.md)。耗时受模型服务和论文长度影响，不是速度保证；尚未验证整机重启、其他 Mac、扫描 PDF OCR 或全部期刊模板，也没有逐一回归每项 Kimi 工具。

## 维护

目前按私有应用源码仓库整理，尚未决定对外公开发布或原生代码的开源许可。上游依赖遵守各自许可证。先在示例论文验证改动，再更新日常使用的安装版；更换 Kimi 或 tex-mcp-web 版本时应重新验证连接、批注、编译和退出清理。
