"""Complete LaTeX and bibliography passes inside the restricted working copy."""
import asyncio
import dataclasses
import json
from pathlib import Path
import re
import shutil
import sys
from tex_mcp_web import compiler

ORIGINAL_COMMAND = compiler._get_compiler_command
MODES = {'auto': '-pdf', 'latexmk': '-pdf', 'pdflatex': '-pdf',
         'xelatex': '-xelatex', 'lualatex': '-lualatex'}


def safe_command(engine, main_file, work_dir):
    # Keep upstream path/flag validation before replacing its single-pass command.
    original = ORIGINAL_COMMAND(engine, main_file, work_dir)
    if engine not in MODES or original[0] == 'pandoc':
        raise ValueError('Only LaTeX compilers are supported')
    return ['latexmk', '-norc', '-no-shell-escape', MODES[engine],
            '-interaction=nonstopmode', '-synctex=1', '-file-line-error', original[-1]]


async def build(main_file, engine, work_dir):
    if shutil.which('latexmk') is None:
        return compiler.CompileResult(success=False, errors=[compiler.CompileMessage(
            str(main_file), None, '需要安装 latexmk，以完整处理参考文献和交叉引用。', 'error')])
    previous = compiler._get_compiler_command
    compiler._get_compiler_command = safe_command
    try:
        result = await compiler.compile_tex(main_file, compiler=engine, work_dir=work_dir)
    finally:
        compiler._get_compiler_command = previous
    log_file = work_dir / (main_file.stem + '.log')
    if log_file.is_file() and not log_file.is_symlink():
        final_log = compiler._unwrap_log(log_file.read_text(errors='replace'))
        # latexmk stdout contains temporary warnings from earlier passes; use the final pass.
        result.warnings = compiler._parse_warnings(final_log, main_file.name)
        final_errors = compiler._parse_errors(final_log, main_file.name)
        if final_errors:
            result.errors = final_errors
            result.success = False
        elif result.success:
            result.errors = []
        if re.search(r'(?:There were undefined (?:references|citations)|(?:Citation|Reference) .+? undefined|Please \(re\)run (?:Biber|BibTeX))', final_log, re.IGNORECASE):
            result.success = False
            result.errors.append(compiler.CompileMessage(main_file.name, None,
                '完整编译后仍有未解析的引用，请检查文献条目或交叉引用。', 'error'))
    return result


async def main():
    result = await build(Path(sys.argv[1]), sys.argv[2], Path.cwd())
    print(json.dumps({'success': result.success,
                      'errors': [dataclasses.asdict(e) for e in result.errors],
                      'warnings': [dataclasses.asdict(e) for e in result.warnings],
                      'output': str(result.output_file) if result.output_file else None}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
