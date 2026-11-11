"""Minimal MCP stdio client (tools only) for ReviewAgent.

The registry requires user trust before starting a server. This is a process
permission boundary, not a filesystem sandbox.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.agent_home import ensure_home, home_dir
from scripts.knowledge_search import PROJECT_ROOT

BUILTIN_SERVERS = {
    'ascend-doc': {
        'command': 'npx',
        'args': ['-y', 'ascend-doc-mcp@1.0.1'],
        'description': '昇腾官方只读文档 MCP（ascend-doc-mcp）',
    },
}

# Only launcher/runtime essentials. Service credentials must be explicit in env.
PROCESS_ENV_KEYS = ('PATH', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
                    'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
                    'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE')


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    description: str = ''

    def as_dict(self) -> Dict:
        return {
            'name': self.name,
            'command': self.command,
            'args': self.args,
            'disabled': self.disabled,
            'description': self.description,
            'builtin': self.name in BUILTIN_SERVERS,
        }


def _executable(config: McpServerConfig) -> str:
    path = config.env.get('PATH', os.environ.get('PATH', os.defpath))
    return shutil.which(config.command, path=path) or config.command


def _fingerprint(config: McpServerConfig) -> str:
    payload = {'command': config.command, 'executable': _executable(config), 'cwd': str(Path.cwd()),
               'args': config.args, 'env': config.env}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _parse_servers_block(raw: Dict) -> Dict[str, Dict]:
    if not isinstance(raw, dict):
        return {}
    block = raw.get('mcpServers') or raw.get('mcp') or raw.get('servers') or {}
    return block if isinstance(block, dict) else {}


def _load_json_file(path: Path) -> Dict:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return {}
    # Allow JSONC-ish line comments
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('//'):
            continue
        lines.append(line)
    try:
        data = json.loads('\n'.join(lines))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def config_paths() -> List[Path]:
    ensure_home()
    return [
        PROJECT_ROOT / '.mcp.json',
        PROJECT_ROOT / 'mcp.json',
        Path(home_dir()) / 'mcp.json',
    ]


def load_mcp_config(extra: Optional[Dict] = None) -> Dict[str, McpServerConfig]:
    """Merge project → user → builtin defaults (project/user override builtin)."""
    merged: Dict[str, Dict] = {k: dict(v) for k, v in BUILTIN_SERVERS.items()}
    for path in reversed(config_paths()):
        block = _parse_servers_block(_load_json_file(path))
        for name, cfg in block.items():
            if not isinstance(cfg, dict):
                continue
            merged[name] = {**merged.get(name, {}), **cfg}
    if extra:
        for name, cfg in _parse_servers_block(extra).items():
            if isinstance(cfg, dict):
                merged[name] = {**merged.get(name, {}), **cfg}

    out: Dict[str, McpServerConfig] = {}
    for name, cfg in merged.items():
        command = cfg.get('command')
        args = cfg.get('args') or []
        if not isinstance(args, list):
            args = [str(args)]
        # OpenCode-style: command as list
        if isinstance(command, list) and command:
            args = list(command[1:]) + list(args)
            command = command[0]
        if not command:
            continue
        env = cfg.get('env') if isinstance(cfg.get('env'), dict) else {}
        out[name] = McpServerConfig(
            name=name,
            command=str(command),
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()},
            disabled=bool(cfg.get('disabled') or cfg.get('enabled') is False),
            description=str(cfg.get('description') or BUILTIN_SERVERS.get(name, {}).get('description') or ''),
        )
    return out


def ensure_default_user_mcp() -> Path:
    """Write ~/.reviewagent/mcp.json with ascend-doc if missing."""
    ensure_home()
    path = Path(home_dir()) / 'mcp.json'
    if path.is_file():
        return path
    payload = {
        'mcpServers': {
            'ascend-doc': {
                'command': 'npx',
                'args': list(BUILTIN_SERVERS['ascend-doc']['args']),
                'description': '昇腾官方只读文档 MCP',
            }
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


class McpSession:
    """One stdio MCP server process using newline-delimited JSON-RPC 2.0."""

    def __init__(self, config: McpServerConfig):
        self.config = config
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self._reader_buf = b''
        self._stdout_queue = queue.Queue(maxsize=64)
        self._reader_stop = threading.Event()
        self._reader_threads = []
        self._stderr_tail = bytearray()
        self.tools: List[Dict] = []
        self.last_error: Optional[str] = None

    def start(self, timeout: float = 45.0) -> bool:
        self.close()
        deadline = time.monotonic() + timeout
        if self.config.disabled:
            self.last_error = 'server disabled'
            return False
        executable = _executable(self.config)
        if self.config.command == 'npx' and executable == 'npx':
            self.last_error = '未找到 npx（需 Node.js ≥ 22 以运行 ascend-doc-mcp）'
            return False
        cmd = [executable or self.config.command, *self.config.args]
        env = {key: value for key, value in os.environ.items() if key.upper() in PROCESS_ENV_KEYS}
        env.update(self.config.env)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            self.last_error = f'启动失败: {exc}'
            return False
        self._reader_buf = b''
        self.tools = []
        self._stdout_queue = queue.Queue(maxsize=64)
        self._reader_stop = threading.Event()
        self._stderr_tail = bytearray()
        self._start_readers()
        try:
            self._request('initialize', {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'reviewagent', 'version': '1.8.0'},
            }, timeout=max(0, deadline - time.monotonic()))
            # notifications/initialized (no id)
            self._notify('notifications/initialized', {})
            listed = self._request('tools/list', {}, timeout=max(0, deadline - time.monotonic()))
            tools = (listed or {}).get('tools') if isinstance(listed, dict) else None
            self.tools = tools if isinstance(tools, list) else []
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            return False

    def close(self) -> None:
        self._reader_stop.set()
        self.tools = []
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            if self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=3)
                except Exception:
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=3)
                    except Exception:
                        pass
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
        finally:
            self.proc = None
            for thread in self._reader_threads:
                thread.join(timeout=0.2)
            self._reader_threads = []

    def call_tool(self, name: str, arguments: Optional[Dict] = None,
                  timeout: float = 60.0) -> Dict[str, Any]:
        if not self.proc:
            return {'error': self.last_error or 'MCP 未启动'}
        try:
            result = self._request('tools/call', {
                'name': name,
                'arguments': arguments or {},
            }, timeout=timeout)
            if isinstance(result, dict) and result.get('isError'):
                return {'error': 'MCP 工具执行失败', 'result': result}
            return {'ok': True, 'result': result}
        except (TimeoutError, ValueError, OSError) as exc:
            self.last_error = str(exc)
            self.close()
            return {'error': self.last_error}
        except Exception as exc:
            return {'error': str(exc)}

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _notify(self, method: str, params: Dict) -> None:
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params}
        self._write(msg)

    def _request(self, method: str, params: Dict, timeout: float = 30.0) -> Any:
        with self._lock:
            req_id = self._next_id()
            msg = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params}
            self._write(msg)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                resp = self._read_message(timeout=max(0, deadline - time.monotonic()))
                if resp is None:
                    continue
                if 'method' in resp:
                    if 'id' in resp:
                        reply = {'jsonrpc': '2.0', 'id': resp['id']}
                        if resp['method'] == 'ping':
                            reply['result'] = {}
                        else:
                            reply['error'] = {'code': -32601, 'message': 'Method not supported'}
                        self._write(reply)
                    continue
                if resp.get('id') != req_id:
                    # ignore unrelated notifications
                    continue
                if 'error' in resp:
                    err = resp['error']
                    raise RuntimeError(err.get('message') if isinstance(err, dict) else str(err))
                return resp.get('result')
            raise TimeoutError(f'MCP {method} 超时')

    def _write(self, msg: Dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError('MCP stdin 不可用')
        body = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        self.proc.stdin.write(body + b'\n')
        self.proc.stdin.flush()

    def _start_readers(self) -> None:
        stop, output, stderr_tail = self._reader_stop, self._stdout_queue, self._stderr_tail

        def read_stream(stream, is_stdout):
            try:
                while not stop.is_set():
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    if is_stdout:
                        while not stop.is_set():
                            try:
                                output.put(chunk, timeout=0.1)
                                break
                            except queue.Full:
                                pass
                    else:
                        stderr_tail.extend(chunk)
                        del stderr_tail[:-500]
            except (OSError, ValueError):
                pass
            finally:
                if is_stdout and not stop.is_set():
                    while not stop.is_set():
                        try:
                            output.put(None, timeout=0.1)
                            break
                        except queue.Full:
                            pass

        self._reader_threads = [
            threading.Thread(target=read_stream, args=(stream, is_stdout), daemon=True)
            for stream, is_stdout in ((self.proc.stdout, True), (self.proc.stderr, False))
        ]
        for thread in self._reader_threads:
            thread.start()

    def _read_message(self, timeout: float = 5.0) -> Optional[Dict]:
        if not self.proc or not self.proc.stdout:
            return None
        deadline = time.monotonic() + timeout
        while True:
            if b'\n' in self._reader_buf:
                line, self._reader_buf = self._reader_buf.split(b'\n', 1)
                message = json.loads(line.decode('utf-8'))
                if not isinstance(message, dict):
                    raise ValueError('MCP 消息必须是 JSON 对象')
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                chunk = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if chunk is None:
                error = self._stderr_tail.decode('utf-8', errors='replace')
                raise RuntimeError(f'MCP 进程退出 code={self.proc.poll()} {error}')
            self._reader_buf += chunk
            if len(self._reader_buf) > 4 * 1024 * 1024:
                raise ValueError('MCP 单条消息超过 4 MiB 限制')


class McpRegistry:
    """Lazy cache of MCP sessions keyed by server name."""

    def __init__(self):
        self.configs = load_mcp_config()
        self._sessions: Dict[str, McpSession] = {}

    def reload(self) -> None:
        self.close()
        self.configs = load_mcp_config()

    def close(self) -> None:
        for s in self._sessions.values():
            s.close()
        self._sessions.clear()

    def list_servers(self) -> List[Dict]:
        return [{**c.as_dict(), 'trusted': self.is_trusted(c.name)} for c in self.configs.values()]

    def is_trusted(self, name: str) -> bool:
        cfg = self.configs.get(name)
        if not cfg or cfg.disabled:
            return False
        trusted = _load_json_file(Path(home_dir()) / 'mcp-trust.json')
        return trusted.get(name) == _fingerprint(cfg)

    def trust_info(self, name: str) -> Dict:
        cfg = self.configs.get(name)
        if not cfg:
            return {'error': f'未知 MCP server: {name}'}
        if cfg.disabled:
            return {'error': f'MCP server 已禁用: {name}'}
        return {**cfg.as_dict(), 'executable': _executable(cfg),
                'env_keys': sorted(cfg.env), 'fingerprint': _fingerprint(cfg),
                'trusted': self.is_trusted(name)}

    def trust(self, name: str, fingerprint: str) -> bool:
        """Persist only the exact configuration the user just confirmed."""
        cfg = self.configs.get(name)
        if not cfg or cfg.disabled or _fingerprint(cfg) != fingerprint:
            return False
        root = Path(ensure_home())
        path = root / 'mcp-trust.json'
        trusted = _load_json_file(path)
        trusted[name] = fingerprint
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root,
                                             prefix='.mcp-trust-', delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump(trusted, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return True

    def get_session(self, name: str, start: bool = True) -> Optional[McpSession]:
        cfg = self.configs.get(name)
        if not cfg:
            return None
        sess = self._sessions.get(name)
        if not self.is_trusted(name):
            if sess:
                sess.close()
                self._sessions.pop(name, None)
            denied = McpSession(cfg)
            denied.last_error = ('MCP server 已禁用' if cfg.disabled else
                                 f'MCP 进程尚未获信任；请先用 /mcp tools {name} 确认命令权限')
            return denied if start else None
        if sess and sess.proc and sess.proc.poll() is None:
            return sess
        if not start:
            return None
        if sess:
            sess.close()
        sess = McpSession(cfg)
        if sess.start():
            self._sessions[name] = sess
            return sess
        return sess  # caller reads last_error

    def list_tools(self, name: str) -> Dict[str, Any]:
        sess = self.get_session(name)
        if not sess:
            return {'error': f'未知 MCP server: {name}'}
        if sess.last_error and not sess.tools:
            return {'error': sess.last_error, 'server': name}
        return {
            'server': name,
            'tools': [
                {'name': t.get('name'), 'description': str(t.get('description') or '')[:200]}
                for t in sess.tools if isinstance(t, dict)
            ],
        }

    def call(self, server: str, tool: str, arguments: Optional[Dict] = None) -> Dict[str, Any]:
        sess = self.get_session(server)
        if not sess:
            return {'error': f'未知 MCP server: {server}'}
        if sess.last_error and (not sess.proc or sess.proc.poll() is not None):
            return {'error': sess.last_error, 'server': server}
        return sess.call_tool(tool, arguments or {})


_REGISTRY: Optional[McpRegistry] = None


def get_registry() -> McpRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        ensure_default_user_mcp()
        _REGISTRY = McpRegistry()
        atexit.register(_REGISTRY.close)
    return _REGISTRY
