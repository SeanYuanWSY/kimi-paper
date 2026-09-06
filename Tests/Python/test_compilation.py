import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_compile import safe_command
from paper_engine import compile_copy

TEX_PATH = '/Library/TeX/texbin:' + os.environ.get('PATH', '')


class CompilerCommandTests(unittest.TestCase):
    def test_engine_mapping_preserves_safety_and_path_validation(self):
        for engine, mode in [('pdflatex', '-pdf'), ('xelatex', '-xelatex'), ('lualatex', '-lualatex')]:
            args = safe_command(engine, Path('main.tex'), Path.cwd())
            self.assertIn(mode, args)
            self.assertIn('-norc', args)
            self.assertIn('-no-shell-escape', args)
        for name in ('-shell-escape.tex', '../outside.tex'):
            with self.assertRaises(ValueError):
                safe_command('pdflatex', Path(name), Path.cwd())


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('latexmk', path=TEX_PATH), 'requires macOS and latexmk')
class CleanCompilationTests(unittest.IsolatedAsyncioTestCase):
    async def compile(self, citation):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = root / 'work'; work.mkdir()
            (work / 'main.tex').write_text(r'''\documentclass{article}
\begin{document}
See Section~\ref{sec:method} and~\cite{''' + citation + r'''}.
\section{Method}\label{sec:method}
A fictional test only.
\bibliographystyle{plain}\bibliography{references}
\end{document}
''')
            (work / 'references.bib').write_text('@misc{example,author={Example, A.},title={Fictional Test Reference},year={2026}}')
            with patch.dict(os.environ, {'PATH': TEX_PATH}):
                result = await compile_copy(work, 'main.tex', 'pdflatex', root / 'forbidden')
            return result, (work / 'main.log').read_text(errors='replace'), (work / 'main.bbl').is_file()

    async def test_clean_copy_resolves_bibliography_and_cross_references(self):
        result, log, bibliography = await self.compile('example')
        self.assertTrue(result['success'], result['errors'])
        self.assertTrue(bibliography)
        self.assertNotIn('undefined', log)

    async def test_unresolved_citation_cannot_publish_success(self):
        result, _, _ = await self.compile('missing-entry')
        self.assertFalse(result['success'])
        self.assertTrue(result['errors'])
