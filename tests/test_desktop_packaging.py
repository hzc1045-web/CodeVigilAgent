"""Run the real packaging script with fake compiler/signing executables."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('git'), 'POSIX shell and Git required')
class DesktopPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'desktop-app').mkdir()
        shutil.copyfile(ROOT / 'desktop-app/build-mac.sh', self.root / 'desktop-app/build-mac.sh')
        files = {
            'ascend_cli.py': '# fixture CLI\n',
            'scripts/ai_adapter.py': '# fixture adapter\n',
            'config/ai_config.json.template': '{"api_key":"YOUR_API_KEY_HERE"}',
            'package.json': '{"version":"1.8.0"}',
            'desktop-app/CodeVigilAgentApp.swift': '// fixture Swift\n',
            'data/raw/public.json': '[]',
            '.cursor/agents/advisor.md': '# fixture advisor',
            '.gitignore': '.env\nconfig/ai_config.json\ndata/raw/private.json\ndesktop-app/build/\n',
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')
        secrets = {
            '.env': 'SECRET=FAKE-ONLY',
            'config/ai_config.json': '{"api_key":"FAKE-ONLY"}',
            'data/raw/private.json': '{"secret":"FAKE-ONLY"}',
            'scripts/.env': 'SECRET=FAKE-ONLY',
            'sessions/private.json': '{"private":"FAKE-ONLY"}',
            'desktop-app/releases/old.zip': 'old artifact',
        }
        for name, content in secrets.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        # Even an ignored file accidentally tracked earlier must be excluded.
        self.git('add', '-f', 'data/raw/private.json')
        self.fakebin = self.root / 'fakebin'
        self.fakebin.mkdir()
        self.executable('swiftc', '#!/bin/sh\nif [ "$1" = --version ]; then echo "Swift fixture"; exit; fi\nwhile [ "$1" != -o ]; do shift; done\nprintf binary > "$2"\n')
        self.executable('codesign', '#!/bin/sh\nexit "${SIGN_STATUS:-0}"\n')
        self.env = {**os.environ, 'PATH': str(self.fakebin) + os.pathsep + os.environ['PATH']}

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root)

    def executable(self, name, content):
        path = self.fakebin / name
        path.write_text(content)
        path.chmod(0o755)

    def build(self, **env):
        return subprocess.run(['bash', 'desktop-app/build-mac.sh'], cwd=self.root,
                              env={**self.env, **env}, capture_output=True, text=True)

    def test_only_runtime_files_are_copied_and_manifest_matches_payload(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        app = self.root / 'desktop-app/build/CodeVigilAgent.app'
        backend = app / 'Contents/Resources/backend'
        actual = {p.relative_to(backend).as_posix() for p in backend.rglob('*') if p.is_file()}
        self.assertEqual(actual, {'ascend_cli.py', 'scripts/ai_adapter.py',
                                 'config/ai_config.json.template', 'data/raw/public.json',
                                 '.cursor/agents/advisor.md'})
        manifest = json.loads((app / 'Contents/Resources/build-manifest.json').read_text())
        self.assertEqual(manifest['source_commit'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertTrue(manifest['source_dirty'])
        self.assertEqual(manifest['version'], '1.8.0')
        self.assertEqual(set(manifest['backend_sha256']), actual)
        for name, expected in manifest['backend_sha256'].items():
            self.assertEqual(hashlib.sha256((backend / name).read_bytes()).hexdigest(), expected)
        for name, expected in manifest['source_sha256'].items():
            self.assertEqual(hashlib.sha256((self.root / name).read_bytes()).hexdigest(), expected)
        with (app / 'Contents/Info.plist').open('rb') as f:
            self.assertEqual(plistlib.load(f)['CFBundleShortVersionString'], '1.8.0')

    def test_signing_failure_is_nonzero(self):
        self.assertNotEqual(self.build(SIGN_STATUS='9').returncode, 0)

    def test_signature_verification_failure_is_nonzero(self):
        self.executable('codesign', '#!/bin/sh\nif [ "$1" = --verify ]; then exit 8; fi\n')
        self.assertNotEqual(self.build().returncode, 0)

    def test_compiler_failure_is_nonzero(self):
        self.executable('swiftc', '#!/bin/sh\nif [ "$1" = --version ]; then echo Swift; exit; fi\nexit 7\n')
        self.assertNotEqual(self.build().returncode, 0)


if __name__ == '__main__':
    unittest.main()
