"""CLI and planner process permissions, with no files or external processes."""
import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from ascend_cli import handle_chat_slash
from scripts.conversation_agent import ConversationAgent


class McpConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.registry = MagicMock()
        self.registry.trust_info.return_value = {
            'command': 'local-python', 'args': ['server.py'], 'executable': '/local/python',
            'env_keys': ['SERVICE_KEY'], 'fingerprint': 'current-config', 'trusted': False,
        }
        self.registry.trust.return_value = True
        self.registry.list_tools.return_value = {'server': 'local', 'tools': []}
        self.registry.call.return_value = {'ok': True}
        self.agent = SimpleNamespace(mcp_registry=lambda: self.registry)

    def slash(self, command, answer='y'):
        output = io.StringIO()
        with patch('scripts.mcp_client.ensure_default_user_mcp'), \
                patch('builtins.input', side_effect=answer if isinstance(answer, BaseException) else None,
                      return_value=answer) as prompt, redirect_stdout(output):
            self.assertEqual(handle_chat_slash(command, self.agent), 'handled')
        return output.getvalue(), prompt

    def test_refusal_and_interrupted_input_never_save_trust_or_start(self):
        for answer in ('n', EOFError(), KeyboardInterrupt()):
            with self.subTest(answer=type(answer).__name__):
                self.registry.reset_mock()
                output, _ = self.slash('/mcp tools local', answer)
                self.registry.trust.assert_not_called()
                self.registry.list_tools.assert_not_called()
                self.registry.call.assert_not_called()
                self.assertIn('未启动', output)

    def test_tools_and_call_confirm_current_configuration_before_use(self):
        for command, expected in (('/mcp tools local', call.list_tools('local')),
                                  ('/mcp call local echo {"x":1}', call.call('local', 'echo', {'x': 1}))):
            with self.subTest(command=command):
                self.registry.reset_mock()
                output, prompt = self.slash(command)
                prompt.assert_called_once()
                self.registry.assert_has_calls([call.trust('local', 'current-config'), expected])
                self.assertIn('/local/python', output)
                self.assertIn('SERVICE_KEY', output)

    def test_invalid_call_arguments_do_not_request_process_trust(self):
        for arguments in ('[]', 'null', 'invalid JSON'):
            with self.subTest(arguments=arguments):
                output, prompt = self.slash('/mcp call local echo ' + arguments)
                prompt.assert_not_called()
                self.registry.trust_info.assert_not_called()
                self.registry.trust.assert_not_called()
                self.registry.call.assert_not_called()
                self.assertIn('JSON 对象', output)

    def test_trust_write_failure_is_reported_without_starting(self):
        self.registry.trust.side_effect = OSError('disk full')
        output, _ = self.slash('/mcp tools local')
        self.assertIn('信任保存失败', output)
        self.registry.list_tools.assert_not_called()
        self.registry.call.assert_not_called()

    def test_automatic_investigation_never_starts_untrusted_server(self):
        client = MagicMock()
        client.chat.side_effect = [json.dumps({'tool': 'mcp_call', 'server': 'local',
                                               'name': 'echo', 'arguments': {}}),
                                   '{"tool":"finish"}']
        self.registry.list_servers.return_value = [{'name': 'local', 'trusted': False}]
        self.registry.is_trusted.return_value = False
        agent = ConversationAgent(client, MagicMock(), MagicMock(), searcher=MagicMock())
        agent._mcp = self.registry
        results = agent.investigate('check NPU API', {'historical_references': []})
        self.registry.is_trusted.assert_called_once_with('local')
        self.registry.get_session.assert_not_called()
        self.registry.call.assert_not_called()
        self.registry.trust.assert_not_called()
        self.assertIn('尚未获信任', results[0]['error'])


if __name__ == '__main__':
    unittest.main()
