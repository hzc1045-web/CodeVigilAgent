"""提问功能：底部输入框提交。"""
from __future__ import annotations

import os
import uuid

from .. import ROOT
from .base import Feature


def send(app, text: str) -> None:
    text = (text or '').strip()
    if not text:
        return
    if app.runner.busy:
        app.append('\n上一个任务还没结束，请等待或取消后再发送。\n', 'err')
        return
    repo = app.current_repo()
    repo = os.path.realpath(repo) if repo and os.path.isdir(repo) else ''
    if getattr(app, 'chat_repository', None) != repo or not getattr(app, 'chat_session', None):
        app.chat_repository = repo
        app.chat_session = 'desktop-win-' + uuid.uuid4().hex
    args = [
        app.runner.python(), os.path.join(ROOT, 'ascend_cli.py'),
        'chat', '--message', text, '--network', 'auto',
        '--session', app.chat_session, '--repository', repo,
    ]
    app.run_cli(args, cwd=repo or ROOT, label='提问')


FEATURES = [
    Feature(
        id='chat.send', group='会话', label='发送', handler=send,
        placement='input', order=10,
        hint='把输入框里的问题发给 Agent（可带仓库上下文）',
    ),
]
