#!/usr/bin/env node
/**
 * postinstall — Hermes-style bootstrap for ReviewAgent
 */

const fs = require('fs');
const path = require('path');
const { findPython } = require('./bin/python-launcher');
const { ensureHome: prepareHome, configPath, writeConfig } = require('./bin/ui');

let chalk, boxen;
try {
  chalk = require('chalk');
  boxen = require('boxen');
} catch {
  const plain = (s) => s;
  plain.bold = plain;
  plain.cyan = plain.green = plain.yellow = plain;
  chalk = { green: plain, red: plain, yellow: plain, cyan: plain, bold: plain };
  boxen = (s) => s;
}

const TEMPLATE = path.join(__dirname, 'config', 'ai_config.json.template');

function ensureHome() {
  prepareHome();
  const target = configPath();
  if (!fs.existsSync(target) && fs.existsSync(TEMPLATE)) {
    writeConfig(JSON.parse(fs.readFileSync(TEMPLATE, 'utf8')));
    console.log(chalk.green('✓'), `已写入配置模板 ${target}`);
  }
}

function checkPython() {
  try {
    const found = findPython();
    const launcher = [found.command, ...found.args].join(' ');
    console.log(chalk.green('✓'), `Python: ${found.version}（${launcher}）`);
    return true;
  } catch {
    console.log(chalk.red('✗'), '未找到 Python 3.7+');
    return false;
  }
}

function main() {
  console.log('\n' + boxen(chalk.bold.cyan('ReviewAgent') + ' · Ascend AI Infra', {
    padding: 1, margin: 1, borderStyle: 'round', borderColor: 'cyan',
  }));

  console.log(chalk.bold('安装检查:\n'));
  const ok = checkPython();
  ensureHome();

  if (!ok) {
    console.log('\n' + boxen(
      chalk.yellow.bold('需要 Python\n\n') +
      '请安装 Python 3.7+ 后重试：https://www.python.org/downloads/',
      { padding: 1, borderStyle: 'round', borderColor: 'yellow' },
    ));
  }

  console.log('\n' + boxen(
    chalk.green.bold('唤醒 Agent（类似 hermes）\n\n') +
    chalk.cyan('  reviewagent') + '              # 对话模式\n' +
    chalk.cyan('  reviewagent review') + '       # 审查当前仓库 diff\n' +
    chalk.cyan('  reviewagent search "HCCL"') + ' # 本地案例\n' +
    chalk.cyan('  reviewagent config') + '        # 配置模型\n' +
    chalk.cyan('  reviewagent doctor') + '        # 环境自检\n' +
    chalk.cyan('  ra') + ' / ' + chalk.cyan('review') + '             # 短命令别名\n\n' +
    '模型切换（兼容 cc-switch）:\n' +
    '  导出 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL\n' +
    '  或 ANTHROPIC_API_KEY，然后直接 ' + chalk.cyan('reviewagent') + '\n' +
    '  文档: docs/CC_SWITCH.md',
    { padding: 1, margin: 1, borderStyle: 'round', borderColor: 'green' },
  ));
}

main();
