from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from prepare_workspace import prepare


class PrepareWorkspaceTests(unittest.TestCase):
    def test_direct_workspace_rejects_home_and_private_ancestors(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve() / 'home'
            (home / '.kimi-code').mkdir(parents=True)
            (home / 'Library/Application Support/Kimi Paper').mkdir(parents=True)
            (home / '.codex/project').mkdir(parents=True)
            (home / '.aws').mkdir(parents=True)
            (home / '.gnupg').mkdir(parents=True)
            (home / 'Library/Keychains').mkdir(parents=True)
            with patch('prepare_workspace.Path.home', return_value=home):
                for unsafe in (home, home.parent, home / 'Library', home / '.codex/project',
                               home / '.aws', home / '.gnupg', home / 'Library/Keychains'):
                    with self.subTest(path=unsafe), self.assertRaises(ValueError):
                        prepare(unsafe, 'main.tex')

    def test_direct_workspace_rejects_system_tree(self):
        with self.assertRaises(ValueError):
            prepare(Path('/private/tmp'), 'main.tex')

    def test_direct_workspace_accepts_specific_project_and_nested_main(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve() / 'home'
            project = home / 'projects/paper'
            (project / 'tex').mkdir(parents=True)
            with patch('prepare_workspace.Path.home', return_value=home):
                result = prepare(project, 'tex/main.tex')
            self.assertEqual(result['root'], str(project))
            self.assertEqual(result['main'], 'tex/main.tex')
            self.assertEqual(result['port'], 0)
            self.assertFalse((project / '.tex-mcp-web.yaml').exists())

    def test_opening_project_does_not_rewrite_existing_tex_config(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve() / 'home'
            project = home / 'projects/paper'
            project.mkdir(parents=True)
            config = project / '.tex-mcp-web.yaml'
            original = b'main: old.tex\ncompiler: latexmk\n'
            config.write_bytes(original)
            with patch('prepare_workspace.Path.home', return_value=home):
                result = prepare(project, 'main.tex')
            self.assertEqual(result['main'], 'main.tex')
            self.assertEqual(config.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
