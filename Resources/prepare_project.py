"""Add only Kimi Paper's project-local configuration; never edit manuscript text."""
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import uuid
import yaml


def save_json(path, data):
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temp, path)


def main():
    main_file = Path(sys.argv[1]).absolute()
    python = Path(sys.executable).resolve()
    if main_file.is_symlink() or not main_file.is_file() or main_file.suffix.lower() != ".tex":
        raise ValueError("请选择实际存在的 LaTeX 主文件（.tex）。")
    root = main_file.parent.resolve()
    config = root / ".tex-mcp-web.yaml"
    kimi_dir = root / ".kimi-code"
    mcp = kimi_dir / "mcp.json"
    marker = root / ".kimi-paper.json"
    for path in (config, kimi_dir, mcp, marker):
        if path.is_symlink():
            raise ValueError("项目配置为符号链接；为保留原配置，本次没有启用。")
    ownership = json.loads(marker.read_text()) if marker.exists() else {}
    current = json.loads(mcp.read_text()) if mcp.exists() else {"mcpServers": {}}
    if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
        raise ValueError("现有 MCP 配置格式无法识别，已保留原文件。")
    servers = current.setdefault("mcpServers", {})
    name = "kimi-paper"
    if name in servers and ownership.get("server") != name:
        raise ValueError("项目中已有同名 kimi-paper 工具，已保留原配置。")
    if config.exists():
        cfg = yaml.safe_load(config.read_text())
        if not isinstance(cfg, dict) or (root / str(cfg.get("main", ""))).resolve() != main_file.resolve():
            raise ValueError("这个目录已配置了另一篇论文，请打开其中已配置的 LaTeX 主文件。")
        port = cfg.get("port", 8765)
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise ValueError("现有论文服务端口无效，请先修正配置。")
    else:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cfg = {"main": main_file.name, "watch": ["*.tex", "*.bib"], "ignore": [],
               "compiler": "pdflatex", "auto_compile": False, "port": port}
    env_args = [key + "=" for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "PYTHONHOME", "PYTHONPATH")]
    env_args += ["NO_PROXY=localhost,127.0.0.1", "no_proxy=localhost,127.0.0.1"]
    servers[name] = {"command": "/usr/bin/env", "args": env_args + [str(python), str(Path(__file__).with_name("paper_mcp.py"))],
                     "cwd": str(root), "toolTimeoutMs": 300000}
    kimi_dir.mkdir(exist_ok=True)
    if mcp.exists():
        backup = mcp.with_name("mcp.json.before-kimi-paper-" + uuid.uuid4().hex[:12])
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as destination, mcp.open("rb") as source:
            shutil.copyfileobj(source, destination)
    if not config.exists():
        with config.open("x") as stream:
            yaml.safe_dump(cfg, stream, sort_keys=False, allow_unicode=True)
    save_json(mcp, current)
    save_json(marker, {"server": name, "main": main_file.name})
    print(json.dumps({"root": str(root), "main": main_file.name, "port": port,
                      "has_other_servers": any(key != name for key in servers)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        # Parse failures may embed sensitive configuration excerpts. Never echo them.
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(json.dumps({"error": "无法准备项目配置；原有正文未改动。请检查目录权限和配置格式。"}, ensure_ascii=False))
        raise SystemExit(1)
