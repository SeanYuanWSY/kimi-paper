# Kimi Paper

专门适配原生 Kimi Code 的 macOS 论文工作台：左侧持续对话与写作，右侧阅读 PDF、划选翻译和批注。从空项目起草，到同一会话中反复修改，确认采纳后才更新正式论文。

[tex-mcp-web](https://github.com/MiiKiyoshi/tex-mcp-web) 提供 PDF 显示和选区能力。本仓库维护原生应用、持续 Kimi Web 会话、草稿隔离、PDF 快照、版本审核和 GitHub 协作。不是 Kimi 官方应用。

## 使用

双击构建或已安装的 Kimi Paper。点击左上角选择整个项目文件夹；空项目默认起草 `main.tex`，已有项目可以选择嵌套的 LaTeX 主文件。默认模型为 Kimi K3 256K，也可在原生 Kimi 界面选择其他模型。

批注默认发送到当前会话，也可积攒后统一发送。「修改记录」区分 AI 原始会话、冻结 PDF 预览和正式采纳；「项目文件」管理文献库、图表和研究脚本；「版本与 GitHub」提供本地自动版本与手动协作操作。详见 [使用说明](docs/使用说明.md)。

依赖本机已安装且登录有效的 Kimi Code，以及 LaTeX 和 latexmk。阅读翻译使用独立 API，支持 Qwen-MT。支持本地 LaTeX 工程，不支持 Word 编辑或仅凭 PDF 还原源稿。旧执行模块保留兼容测试，0.4 主界面统一使用持续 Kimi 会话。

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

0.4 已完成真实 Kimi K3 256K 的空项目起草、冻结 PDF、正式采纳，以及服务重启后恢复同一会话并完成第二轮修改。原生窗口验证了左右同屏、PDF 划选、保存批注、批量发送到当前会话与原生模型选择。自动化检查覆盖快照不可变、连续采纳、目录交换失败恢复、批注竞态、文件合并、Git 保护和翻译时序。

测试使用虚构论文。Git 推拉行为使用本地模拟远端验证，不自动操作真实论文仓库。翻译传输沿用已有 Qwen 适配，本轮回归使用模拟接口，不重新读取私人密钥。整机重启、其他 Mac、所有期刊模板及每项 Kimi 工具尚未逐一验证。最后补充的原生确认弹窗回调已构建安装，但现场点击复验因电脑锁定暂未完成。详细记录见 [开发计划](docs/开发计划.md)。

## 维护

目前按私有应用源码仓库整理，尚未决定对外公开发布或原生代码的开源许可。上游依赖遵守各自许可证。先在示例论文验证改动，再更新日常使用的安装版；更换 Kimi 或 tex-mcp-web 版本时应重新验证连接、批注、编译和退出清理。
