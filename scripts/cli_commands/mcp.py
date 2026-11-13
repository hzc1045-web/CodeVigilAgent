"""MCP commands and explicit approval before starting a local program."""
import json

from scripts.term_style import mute, ok, warn
from . import SlashContext


def mcp(ctx: SlashContext, arg: str) -> None:
    agent = ctx.agent
    from scripts.mcp_client import ensure_default_user_mcp, get_registry
    ensure_default_user_mcp()
    reg = agent.mcp_registry() if hasattr(agent, 'mcp_registry') else get_registry()
    def confirm_server(server):
        info = reg.trust_info(server)
        if info.get('error'):
            print(warn(info['error']))
            return False
        if info.get('trusted'):
            return True
        print(warn('此操作会运行本地 MCP 程序，可按当前账户权限访问文件；只读工具声明不是沙箱。'))
        print(mute('程序: ' + json.dumps([info['command'], *info['args']], ensure_ascii=False)))
        print(mute('实际路径: ' + str(info.get('executable') or '(未找到)')))
        print(mute('显式环境变量: ' + ', '.join(info.get('env_keys') or [])))
        try:
            answer = input('信任此配置并启动？输入 y 确认: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ''
        if answer not in ('y', 'yes', '是'):
            print(mute('已取消，未启动 MCP 程序。'))
            return False
        try:
            trusted = reg.trust(server, info['fingerprint'])
        except OSError:
            trusted = False
        if not trusted:
            print(warn('MCP 配置已变化或信任保存失败，请重新确认。'))
            return False
        return True

    parts = arg.split(None, 2)
    sub = (parts[0] if parts else '').lower()
    if not sub or sub in ('list', 'ls'):
        print(ok('MCP servers（内置 ascend-doc = 昇腾文档）'))
        for s in reg.list_servers():
            flag = 'disabled' if s.get('disabled') else ('trusted' if s.get('trusted') else '需确认')
            desc = s.get('description') or ''
            print(mute(f"  • {s['name']} [{flag}] {s.get('command')} {' '.join(s.get('args') or [])}"))
            if desc:
                print(mute(f'      {desc}'))
        print(mute('用法: /mcp tools <server> · /mcp call <server> <tool> [json] · /mcp reload'))
        return
    if sub == 'reload':
        reg.reload()
        print(ok('已重载 MCP 配置'))
        return
    if sub == 'tools' and len(parts) >= 2:
        if not confirm_server(parts[1]):
            return
        info = reg.list_tools(parts[1])
        if info.get('error'):
            print(warn(info['error']))
        else:
            print(ok(f"MCP tools · {info.get('server')}"))
            for t in info.get('tools') or []:
                print(mute(f"  • {t.get('name')}: {t.get('description') or ''}"))
            if not info.get('tools'):
                print(mute('（无工具或启动失败；ascend-doc 需要 Node > 22 + npx）'))
        return
    if sub == 'call' and len(parts) >= 3:
        server = parts[1]
        rest = parts[2].strip()
        # tool [json-args]
        bits = rest.split(None, 1)
        tool = bits[0]
        args_obj = {}
        if len(bits) > 1:
            try:
                args_obj = json.loads(bits[1])
            except json.JSONDecodeError:
                print(warn('arguments 必须是 JSON 对象'))
                return
        print(mute(f'MCP call {server}/{tool} …'))
        if not isinstance(args_obj, dict):
            print(warn('arguments 必须是 JSON 对象'))
            return
        if not confirm_server(server):
            return
        result = reg.call(server, tool, args_obj)
        print(json.dumps(result, ensure_ascii=False, indent=2)[:8000])
        return
    print(mute('用法: /mcp | /mcp tools ascend-doc | /mcp call ascend-doc <tool> {} | /mcp reload'))
    return


COMMANDS = {'/mcp': mcp}
