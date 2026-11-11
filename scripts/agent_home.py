"""ReviewAgent user home (Hermes-style REVIEWAGENT_HOME)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from scripts.knowledge_search import PROJECT_ROOT

DEFAULT_HOME_NAME = '.reviewagent'
CONFIG_NAME = 'ai_config.json'
TEMPLATE = PROJECT_ROOT / 'config' / 'ai_config.json.template'


def home_dir() -> Path:
    override = os.environ.get('REVIEWAGENT_HOME', '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_HOME_NAME


def config_path() -> Path:
    return home_dir() / CONFIG_NAME


def project_config_path() -> Path:
    return PROJECT_ROOT / 'config' / CONFIG_NAME


def ensure_home() -> Path:
    root = home_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    cfg = config_path()
    if not cfg.exists() and TEMPLATE.exists():
        try:
            fd = os.open(str(cfg), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # Another CLI process created the first-run config.
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(TEMPLATE.read_bytes())
    if cfg.exists():
        cfg.chmod(0o600)
    return root


def resolve_config_file(explicit: Optional[str] = None) -> str:
    """Prefer explicit path, then env-injected empty (caller merges env), then home, then project."""
    if explicit:
        return str(Path(explicit).expanduser())
    env_cfg = os.environ.get('REVIEWAGENT_CONFIG', '').strip()
    if env_cfg:
        return str(Path(env_cfg).expanduser())
    home_cfg = config_path()
    if home_cfg.exists():
        return str(home_cfg)
    proj = project_config_path()
    if proj.exists():
        return str(proj)
    # Ensure home template exists for first-run UX
    ensure_home()
    return str(config_path())


def read_config(path: Optional[str] = None) -> dict:
    target = Path(resolve_config_file(path) if path is None else path)
    if not target.exists():
        return {}
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)


def write_config(data: dict, path: Optional[str] = None) -> Path:
    ensure_home()
    target = Path(path).expanduser() if path else config_path()
    # Explicit configs may live in a shared directory; only protect the file there.
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.exists():
        target.chmod(0o600)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return target
