#!/bin/bash
# 在 Mac 上把 desktop-app/CodeVigilAgentApp.swift 编译成可双击运行的
# CodeVigilAgent.app（自带后端，不需要另外装 Python 以外的依赖）。
#
# 用法：
#   chmod +x desktop-app/build-mac.sh     # 只需要做一次，给这个文件执行权限
#   ./desktop-app/build-mac.sh            # 编译，产物在 desktop-app/build/
#
# 要求：macOS 13 或更新；Python 3、Git、Xcode 命令行工具
#   （没装的话终端里执行  xcode-select --install  会弹出安装窗口）

set -euo pipefail

# 切到仓库根目录（无论从哪里调用这个脚本）
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

APP="desktop-app/build/CodeVigilAgent.app"
BIN="$APP/Contents/MacOS/CodeVigilAgent"

# ---------- 0. 环境自检 ----------
if ! command -v swiftc >/dev/null 2>&1; then
    echo "✗ 找不到 swiftc（Swift 编译器）"
    echo "  请先在终端执行：xcode-select --install"
    echo "  安装完成后重新运行本脚本。"
    exit 1
fi
for tool in python3 git codesign; do
    command -v "$tool" >/dev/null 2>&1 || { echo "✗ 找不到 $tool"; exit 1; }
done

echo "Swift 编译器：$(command -v swiftc)"
swiftc --version | head -1

if [ ! -f "$ROOT/ascend_cli.py" ]; then
    echo "✗ 找不到 ascend_cli.py，请确认在仓库根目录运行"
    exit 1
fi

# ---------- 1. 清理并建立 .app 骨架 ----------
echo
echo "[1/4] 清理旧产物并建立 .app 目录结构"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend"

# ---------- 2. 编译 Swift ----------
echo
echo "[2/4] 编译界面（CodeVigilAgentApp.swift）"
# 不指定 -target，按当前机器架构编译；Release 优化让输出区滚动更顺
swiftc -O "$ROOT/desktop-app/CodeVigilAgentApp.swift" -o "$BIN"
echo "      → $BIN"

# ---------- 3. 打包后端 ----------
echo
echo "[3/4] 拷贝 Python 后端到 Contents/Resources/backend"
python3 - "$APP" <<'PY'
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path.cwd()
app = Path(sys.argv[1])
backend = app / 'Contents/Resources/backend'
paths = subprocess.check_output([
    'git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--',
    'ascend_cli.py', 'scripts', 'config/ai_config.json.template',
    'data/raw', 'data/training', 'data/communities', '.cursor', '.agents', 'LICENSE',
]).decode('utf-8').split('\0')
ignored = subprocess.run(['git', 'check-ignore', '--no-index', '-z', '--stdin'],
                         input='\0'.join(paths).encode('utf-8'), stdout=subprocess.PIPE)
if ignored.returncode not in (0, 1):
    raise SystemExit('无法检查打包文件的 Git 忽略规则')
excluded = set(ignored.stdout.decode('utf-8').split('\0'))
hashes = {}
for name in sorted(set(paths) - excluded - {''}):
    source = root / name
    parts = Path(name).parts
    allowed = (name in ('ascend_cli.py', 'config/ai_config.json.template', 'LICENSE')
               or (parts[0] == 'scripts' and source.suffix == '.py'
                   and '__pycache__' not in parts)
               or (parts[:2] in (('data', 'raw'), ('data', 'training'), ('data', 'communities'))
                   and source.suffix == '.json')
               or (parts[0] in ('.cursor', '.agents') and len(parts) > 2
                   and parts[1] in ('agents', 'skills')
                   and source.suffix == '.md'))
    if not allowed or not source.is_file():
        continue
    if any(parent.is_symlink() for parent in (source, *source.parents) if parent != root):
        raise SystemExit('拒绝打包符号链接: ' + name)
    target = backend / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
for required in ('ascend_cli.py', 'scripts/ai_adapter.py', 'config/ai_config.json.template'):
    if required not in hashes:
        raise SystemExit('缺少必需运行文件: ' + required)
manifest = {
    'version': json.loads((root / 'package.json').read_text(encoding='utf-8'))['version'],
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'])),
    'backend_sha256': hashes,
    'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                      for name in ('desktop-app/CodeVigilAgentApp.swift', 'desktop-app/build-mac.sh')},
}
(app / 'Contents/Resources/build-manifest.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
echo "      → $(du -sh "$APP/Contents/Resources/backend" | cut -f1)"
APP_VERSION="$(python3 -c 'import json; print(json.load(open("package.json"))["version"])')"

# ---------- 4. 写 Info.plist 并签名 ----------
echo
echo "[4/4] 写入 Info.plist 并做本地签名"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
<key>CFBundleDisplayName</key><string>CodeVigil Agent</string>
<key>CFBundleExecutable</key><string>CodeVigilAgent</string>
<key>CFBundleIdentifier</key><string>com.codevigilagent.desktop</string>
<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
<key>CFBundleName</key><string>CodeVigil Agent</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
<key>CFBundleVersion</key><string>$APP_VERSION</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

# ad-hoc 签名只校验包的一致性，不提供 Developer ID 身份背书。
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo
echo "================================================"
echo "✓ 编译完成"
echo "  产物：$ROOT/$APP"
echo
echo "  双击打开："
echo "    open \"$ROOT/$APP\""
echo
echo "  如果提示「无法打开，因为无法验证开发者」："
echo "    右键点这个 .app → 打开 → 再点一次「打开」"
echo "================================================"
