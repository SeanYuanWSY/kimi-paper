"""Select a project root independently of its LaTeX entry point."""
import json
from pathlib import Path
import socket
import sys
import yaml
from paper_tasks import safe_path, digest

def prepare(root, main):
    root=Path(root).absolute()
    if root.is_symlink() or not root.is_dir():raise ValueError('请选择实际项目文件夹。')
    root=root.resolve()
    path=safe_path(root,main)
    if path.suffix!='.tex':raise ValueError('主文件应为 .tex 文件。')
    config=root/'.tex-mcp-web.yaml'
    if config.is_symlink():raise ValueError('项目配置路径不安全。')
    data=yaml.safe_load(config.read_text()) if config.exists() else {}
    if not isinstance(data,dict):raise ValueError('现有项目配置格式无效。')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    data.update(main=main,port=port,auto_compile=False)
    data.setdefault('compiler','pdflatex')
    # This small project config contains no credential and preserves unrelated fields.
    if config.exists():
        backup=Path.home()/'Library/Application Support/Kimi Paper/projects'/digest(str(path.resolve()).encode())[:24]/'original-project-config.yaml'
        if not backup.exists():
            backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with backup.open('xb') as out:out.write(config.read_bytes())
    config.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
    return {'root':str(root),'main':main,'port':port}

if __name__=='__main__':
    try:print(json.dumps(prepare(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception:
        print(json.dumps({'error':'无法准备项目，请检查项目目录、主文件路径与配置。'},ensure_ascii=False));sys.exit(1)
