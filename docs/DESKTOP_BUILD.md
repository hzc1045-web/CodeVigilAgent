# macOS 桌面包构建与核验

在已完成测试的源码根目录执行（需要 macOS、Python 3、Git、Xcode 命令行工具）：

```sh
bash desktop-app/build-mac.sh
codesign --verify --deep --strict desktop-app/build/CodeVigilAgent.app
```

构建只复制受控运行路径中 Git 跟踪或非忽略的 Python（含 `scripts/cli_commands/` 等子包）、规则 Markdown、知识库 JSON 和配置模板。不复制真实配置、`.env`、会话、旧发布包或符号链接。修改了运行文件但尚未提交时也会复制实际内容；`Contents/Resources/build-manifest.json` 记录版本、来源提交、工作区是否有改动、每个后端文件及 Swift/构建脚本的 SHA-256。发布前核对这些值对应本次测试源码。

包内后端用于桌面的对话、审查、会话和案例检索；不包含旧 `chat.py` / `chat_v2.py` 入口、Node 命令行界面或开发测试。依赖旧入口的终端边框诊断 `scripts.frame_doctor` 应在完整源码目录运行。

从同一个构建目录生成 ZIP，避免再次复制旧 App：

```sh
ditto -c -k --sequesterRsrc --keepParent desktop-app/build/CodeVigilAgent.app desktop-app/releases/CodeVigilAgent-macOS-arm64.zip
shasum -a 256 desktop-app/releases/CodeVigilAgent-macOS-arm64.zip
```

构建脚本在编译、签名或严格签名核验失败时返回非零。当前签名是本机 ad-hoc 签名，只证明包内一致性，不是 Developer ID 身份认证或 Apple 公证；输出架构与构建机器一致，文件名中的 `arm64` 仅适用于 Apple Silicon 构建。
