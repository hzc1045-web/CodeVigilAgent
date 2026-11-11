"""Config writers protect keys without changing an explicit shared parent."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import agent_home

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'POSIX file permission checks')
class ConfigPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'agent-home'
        self.env = {'REVIEWAGENT_HOME': str(self.home)}
        self.old_umask = os.umask(0)
        self.addCleanup(os.umask, self.old_umask)

    def assert_mode(self, path, mode):
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_python_creates_private_files_and_repairs_existing_permissions(self):
        with patch.dict(os.environ, self.env):
            config = agent_home.write_config({'api_key': 'fake-only'})
            self.assert_mode(self.home, 0o700)
            self.assert_mode(config, 0o600)
            self.home.chmod(0o755)
            config.chmod(0o666)
            agent_home.ensure_home()
            self.assert_mode(self.home, 0o700)
            self.assert_mode(config, 0o600)

    def test_python_explicit_file_keeps_existing_shared_parent_mode(self):
        shared = self.root / 'shared'
        shared.mkdir(mode=0o755)
        config = shared / 'custom.json'
        config.write_text('{}')
        config.chmod(0o666)
        with patch.dict(os.environ, self.env):
            agent_home.write_config({'api_key': 'fake-only'}, str(config))
        self.assert_mode(shared, 0o755)
        self.assert_mode(config, 0o600)
        self.assertEqual(json.loads(config.read_text())['api_key'], 'fake-only')

    def test_concurrent_first_run_keeps_the_other_process_config(self):
        config = self.home / 'ai_config.json'
        def created_by_another_process(*args):
            config.write_text('{"model":"other-process"}')
            raise FileExistsError('already created')
        with patch.dict(os.environ, self.env), patch('scripts.agent_home.os.open',
                                                    side_effect=created_by_another_process):
            agent_home.ensure_home()
        self.assertEqual(json.loads(config.read_text())['model'], 'other-process')
        self.assert_mode(config, 0o600)

    def test_legacy_config_init_and_update_use_private_writer(self):
        import review_cli
        config = self.root / 'legacy.json'
        args = argparse.Namespace(config=str(config), show=False, init=True, force=False, set=None)
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review_cli.cmd_config(args), 0)
            self.assert_mode(config, 0o600)
            config.chmod(0o666)
            args.init = False
            args.set = 'api_key=fake-only'
            self.assertEqual(review_cli.cmd_config(args), 0)
        self.assert_mode(config, 0o600)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_node_wizard_and_install_protect_config_and_honor_explicit_path(self):
        shared = self.root / 'shared'
        shared.mkdir(mode=0o755)
        target = shared / 'custom.json'
        env = {**os.environ, **self.env, 'REVIEWAGENT_CONFIG': str(target)}
        code = """
const fs = require('fs');
const ui = require('./bin/ui');
const onboard = require('./bin/onboard');
onboard.writeConfig({api_key: 'fake-only', model: 'example'});
fs.chmodSync(ui.homeDir(), 0o755);
fs.chmodSync(ui.configPath(), 0o666);
onboard.writeConfig({model: 'updated'});
"""
        subprocess.run(['node', '-e', code], cwd=ROOT, env=env, check=True, capture_output=True)
        self.assert_mode(self.home, 0o700)
        self.assert_mode(shared, 0o755)
        self.assert_mode(target, 0o600)
        self.assertEqual(json.loads(target.read_text())['api_key'], 'fake-only')
        target.chmod(0o666)
        subprocess.run(['node', 'install.js'], cwd=ROOT, env=env, check=True, capture_output=True)
        self.assert_mode(target, 0o600)
        target.unlink()
        subprocess.run(['node', 'install.js'], cwd=ROOT, env=env, check=True, capture_output=True)
        self.assert_mode(target, 0o600)
        self.assert_mode(shared, 0o755)


if __name__ == '__main__':
    unittest.main()
