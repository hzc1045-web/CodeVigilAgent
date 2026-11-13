"""Process trust checks use temporary homes and a local Python MCP server."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mcp_client import (BUILTIN_SERVERS, McpRegistry, McpServerConfig,
                                ensure_default_user_mcp)


SERVER = r'''
import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    method, mid = message.get('method'), message.get('id')
    if method == 'notifications/initialized':
        continue
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'env'}]}
    elif method == 'tools/call':
        result = {'env': dict(os.environ)}
    else:
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': mid, 'result': result}), flush=True)
'''


class McpTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'REVIEWAGENT_HOME': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = McpServerConfig('local', sys.executable, ['-u', '-c', SERVER],
                                      env={'EXPLICIT_SERVICE_KEY': 'fake-service-key'})
        self.loader = patch('scripts.mcp_client.load_mcp_config', return_value={'local': self.config})
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.registry = McpRegistry()
        self.addCleanup(self.registry.close)

    def trust(self):
        info = self.registry.trust_info('local')
        self.assertTrue(self.registry.trust('local', info['fingerprint']))

    def test_untrusted_registry_paths_never_start_process(self):
        with patch('scripts.mcp_client.subprocess.Popen') as spawn:
            denied = self.registry.get_session('local')
            self.assertIn('尚未获信任', denied.last_error)
            self.assertIn('error', self.registry.list_tools('local'))
            self.assertIn('error', self.registry.call('local', 'env'))
        spawn.assert_not_called()
        self.assertFalse(self.registry.list_servers()[0]['trusted'])

    def test_trust_persists_exact_config_without_storing_service_secret(self):
        self.trust()
        path = self.root / 'mcp-trust.json'
        self.assertTrue(McpRegistry().is_trusted('local'))
        self.assertNotIn('fake-service-key', path.read_text(encoding='utf-8'))
        self.assertEqual(self.registry.trust_info('local')['env_keys'], ['EXPLICIT_SERVICE_KEY'])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_command_args_env_and_working_directory_changes_invalidate_trust(self):
        self.trust()
        old_command = self.config.command
        self.config.command = 'another-command'
        self.assertFalse(self.registry.is_trusted('local'))
        self.config.command = old_command
        self.config.args.append('changed')
        self.assertFalse(self.registry.is_trusted('local'))
        self.config.args.pop()
        self.config.env['EXPLICIT_SERVICE_KEY'] = 'changed'
        self.assertFalse(self.registry.is_trusted('local'))
        self.config.env['EXPLICIT_SERVICE_KEY'] = 'fake-service-key'
        with patch('scripts.mcp_client.Path.cwd', return_value=self.root):
            self.assertFalse(self.registry.is_trusted('local'))

    def test_changed_configuration_cannot_accept_an_old_confirmation(self):
        fingerprint = self.registry.trust_info('local')['fingerprint']
        self.config.args.append('changed')
        self.assertFalse(self.registry.trust('local', fingerprint))
        self.assertFalse((self.root / 'mcp-trust.json').exists())

    def test_same_command_name_resolving_to_another_executable_requires_confirmation(self):
        with patch('scripts.mcp_client.shutil.which', return_value='/trusted/python'):
            self.trust()
        with patch('scripts.mcp_client.shutil.which', return_value='/another/python'), \
                patch('scripts.mcp_client.subprocess.Popen') as spawn:
            self.assertFalse(self.registry.is_trusted('local'))
            self.assertIn('尚未获信任', self.registry.get_session('local').last_error)
        spawn.assert_not_called()

    def test_trusted_local_service_receives_only_runtime_and_explicit_environment(self):
        self.trust()
        secrets = {'OPENAI_API_KEY': 'fake-ai-secret', 'GITHUB_TOKEN': 'fake-git-secret',
                   'NODE_OPTIONS': '--fake-injection', 'ARBITRARY_SECRET': 'fake-other-secret'}
        with patch.dict(os.environ, secrets):
            session = self.registry.get_session('local')
            self.assertIsNone(session.last_error)
            result = self.registry.call('local', 'env')['result']['env']
        self.assertEqual(result['EXPLICIT_SERVICE_KEY'], 'fake-service-key')
        self.assertIn('PATH', result)
        for key in secrets:
            self.assertNotIn(key, result)
        self.assertNotIn('REVIEWAGENT_HOME', result)

    def test_reconfigured_running_service_is_closed_before_use(self):
        self.trust()
        session = self.registry.get_session('local')
        process = session.proc
        self.config.env['NEW'] = 'changed'
        denied = self.registry.get_session('local')
        self.assertIn('尚未获信任', denied.last_error)
        self.assertIsNotNone(process.poll())
        self.assertNotIn('local', self.registry._sessions)

    def test_disabled_unknown_and_corrupt_trust_cannot_start(self):
        self.assertIn('error', self.registry.trust_info('missing'))
        self.assertFalse(self.registry.trust('missing', 'anything'))
        self.trust()
        self.config.disabled = True
        self.assertIn('error', self.registry.trust_info('local'))
        self.assertFalse(self.registry.is_trusted('local'))
        self.assertFalse(self.registry.trust('local', 'anything'))
        self.config.disabled = False
        (self.root / 'mcp-trust.json').write_text('not JSON', encoding='utf-8')
        self.assertFalse(self.registry.is_trusted('local'))

    def test_builtin_and_new_user_config_pin_the_same_version(self):
        path = ensure_default_user_mcp()
        configured = json.loads(path.read_text(encoding='utf-8'))['mcpServers']['ascend-doc']
        self.assertEqual(configured['args'], ['-y', 'ascend-doc-mcp@1.0.1'])
        self.assertEqual(configured['args'], BUILTIN_SERVERS['ascend-doc']['args'])


if __name__ == '__main__':
    unittest.main()
