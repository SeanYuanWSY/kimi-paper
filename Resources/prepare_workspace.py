"""Validate the paper root and LaTeX entry point without changing the project."""
import json
from pathlib import Path
import sys
from paper_tasks import safe_path

def prepare(root, main):
    root=Path(root).absolute()
    if root.is_symlink() or not root.is_dir():raise ValueError('请选择实际项目文件夹。')
    root=root.resolve()
    home=Path.home().resolve()
    private=[home/'.kimi-code',home/'.codex',home/'.ssh',home/'.agents',home/'.aws',home/'.gnupg',
             home/'Library/Keychains',home/'Library/Application Support/Kimi Paper']
    system=[Path(path).resolve() for path in
            ('/System','/Library','/Applications','/usr','/bin','/sbin','/etc','/private','/var','/tmp','/opt')]
    if (root==home or root in home.parents
            or any(path==root or path.is_relative_to(root) or root.is_relative_to(path) for path in private)
            or (not root.is_relative_to(home)
                and any(root==path or root.is_relative_to(path) for path in system))):
        raise ValueError('请选择具体项目目录，不要选择用户主目录、系统目录或应用私有目录。')
    path=safe_path(root,main)
    if path.suffix!='.tex':raise ValueError('主文件应为 .tex 文件。')
    config=root/'.tex-mcp-web.yaml'
    if config.is_symlink():raise ValueError('项目配置路径不安全。')
    # The runtime reads an existing config but never creates or rewrites one merely
    # because the user opened this workspace in native Kimi Web.
    return {'root':str(root),'main':main,'port':0}

if __name__=='__main__':
    try:print(json.dumps(prepare(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception:
        print(json.dumps({'error':'无法准备项目，请检查项目目录、主文件路径与配置。'},ensure_ascii=False));sys.exit(1)
