# Kimi Paper

专门适配原生 Kimi Code 的 macOS 论文工作台：左侧 Kimi 直接在你选择的原始项目目录中工作，右侧阅读 PDF、划选翻译或直接发起细节修改。

[tex-mcp-web](https://github.com/MiiKiyoshi/tex-mcp-web) 提供 PDF 显示和选区能力。本仓库维护原生应用、持续 Kimi Web 会话、PDF 自动重新编译、最近修改恢复和 GitHub 协作。不是 Kimi 官方应用。

## 使用

双击已安装的 Kimi Paper。点击左上角选择 Kimi 的研究工作区；它不必是 LaTeX 文件夹，可以是同时包含 `paper/`、`data/`、`figures/` 等目录的上级项目目录。论文主文件单独选择，例如 `paper/main.tex`。

左侧适合起草和大修改；右侧划选文字、写下要求后，会直接发送到当前 Kimi 会话修改项目文件。每轮结束后 PDF 自动重新编译。顶部只保留 GitHub 主入口；翻译设置、手动编译和最近修改撤销收在更多菜单中。详见 [使用说明](docs/使用说明.md)。

依赖本机已安装且登录有效的 Kimi Code，以及 LaTeX 和 latexmk。阅读翻译使用独立 API，支持 Qwen-MT。支持本地 LaTeX 工程，不支持 Word 编辑或仅凭 PDF 还原源稿。旧的隔离草稿和候选记录保留在应用私有状态中，0.5 不再将它们暴露在默认界面。

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

0.5 将默认主流程改为原项目直接编辑。Kimi 会话与用户选择的绝对项目目录绑定；可读取同一项目中的数据、图表和脚本，但外层边界禁止它写入项目 `.git`、应用状态与 Kimi 全局凭据。应用保留上一次编译成功的 PDF，并在私有本地历史中保存可恢复的最近修改。

自动检查使用虚构论文，Git 推拉使用本地模拟远端；不自动操作真实论文仓库。翻译回归使用模拟接口，不重新读取私人密钥。详细记录见 [开发计划](docs/开发计划.md)。

## 维护

目前按私有应用源码仓库整理，尚未决定对外公开发布或原生代码的开源许可。上游依赖遵守各自许可证。先在示例论文验证改动，再更新日常使用的安装版；更换 Kimi 或 tex-mcp-web 版本时应重新验证连接、批注、编译和退出清理。
