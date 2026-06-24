#!/usr/bin/env python3
"""
push-note.py
============
将单个 Obsidian .md 文件中的 callout 语法转换为 GitHub 兼容的 <details> 格式，
推送到远程仓库，然后本地自动还原为原始 Obsidian 版本。

用法：
    python push-note.py <文件路径> [--message "提交信息"]

示例：
    python push-note.py "论文阅读/my-notes.md"
    python push-note.py "论文阅读/my-notes.md" --message "发布新笔记"

效果：
    GitHub 上  →  <details> 折叠块（所有平台可靠渲染）
    本地文件   →  原始 Obsidian callout（[!success]- 等语法保持不变）

提示：
    如果要一次处理整个文件夹，用 push-folder.py
"""

import re
import sys
import subprocess
import os
from pathlib import Path

# ── 核心转换逻辑 ──────────────────────────────────────────────

def convert_callouts_to_details(content: str) -> str:
    """
    将 Obsidian blockquote callout 转换为 HTML <details> 折叠块。

    支持的 callout 类型（自动识别）：
        > [!success]- 标题   →  <details><summary><b>标题</b></summary>
        > [!info]- 标题      →  同上
        > [!warning]- 标题   →  同上
        > [!danger]- 标题    →  同上
        > [!note]- 标题      →  同上
        > [!question]- 标题  →  同上
        （实际上任何 [!xxx] 都会被识别）

    处理细节：
        - 移除每行 > 前缀，保留内部 Markdown 格式
        - 背靠背 callout（无空行分隔）自动断开
        - <summary> 后插入空行，确保 GitHub 正确渲染内部 Markdown
        - 不添加 open 属性 = 默认折叠状态（还原 Obsidian "-" 的语义）
    """
    lines = content.splitlines(keepends=True)
    result = []
    in_callout = False

    # 匹配 > [!type] 或 > [!type]- 或 > [!type]- 标题
    callout_pattern = re.compile(r'^> \[!(\w+)\]-?\s*(.*)$')

    for line in lines:
        match = callout_pattern.match(line)

        # ── 情况 1：遇到新 callout ──
        if match:
            if in_callout:
                # 先关闭前一个 callout（处理背靠背场景）
                result.append('</details>\n')
                result.append('\n')
            title = match.group(2).strip() or match.group(1)
            result.append('<details>\n')
            result.append(f'<summary><b>{title}</b></summary>\n')
            result.append('\n')
            in_callout = True
            continue

        # ── 情况 2：当前在 callout 内部 ──
        if in_callout:
            # 完全空行（无 >）= callout 结束
            if line.strip() == '' and not line.startswith('>'):
                result.append('</details>\n')
                result.append('\n')
                in_callout = False
                result.append('\n')
                continue

            # 移除块引用前缀
            if line.startswith('> '):
                result.append(line[2:])          # "> content" → "content"
            elif line == '>\n' or line == '>\r\n':
                result.append('\n')               # ">" 空行 → 空行
            elif line.startswith('>'):
                result.append(line[1:])           # ">content" → "content"
            else:
                result.append(line)               # 无前缀行，保持原样
            continue

        # ── 情况 3：callout 外的普通文本 ──
        result.append(line)

    # 文件末尾未闭合的 callout
    if in_callout:
        result.append('</details>\n')

    return ''.join(result)


# ── Git 操作 ──────────────────────────────────────────────────

def run_git(args: list[str], cwd: str = '.') -> subprocess.CompletedProcess:
    """运行 git 命令，出错时打印并退出。"""
    result = subprocess.run(
        ['git'] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[错误] git {' '.join(args)} 失败：")
        print(result.stderr.strip())
        sys.exit(1)
    return result


def is_git_repo() -> bool:
    """检查当前目录是否在 git 仓库中。"""
    result = subprocess.run(
        ['git', 'rev-parse', '--git-dir'],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def file_is_tracked(filepath: str) -> bool:
    """检查文件是否已被 git 跟踪。"""
    result = subprocess.run(
        ['git', 'ls-files', '--error-unmatch', filepath],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ── 主流程 ────────────────────────────────────────────────────

def main():
    # ── 解析参数 ──
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    commit_message = None

    # 支持 --message / -m 自定义提交信息
    args = sys.argv[2:]
    for i, arg in enumerate(args):
        if arg in ('--message', '-m') and i + 1 < len(args):
            commit_message = args[i + 1]
            break

    # ── 前置检查 ──
    if not os.path.isfile(filepath):
        print(f"[错误] 文件不存在：{filepath}")
        sys.exit(1)

    if not is_git_repo():
        print("[错误] 当前目录不是 git 仓库")
        sys.exit(1)

    repo_root = run_git(['rev-parse', '--show-toplevel']).stdout.strip()
    os.chdir(repo_root)

    basename = os.path.basename(filepath)
    tracked = file_is_tracked(filepath)

    print(f"📄 文件：{basename}")
    print(f"📦 仓库：{repo_root}")
    print()

    # ── 步骤 1：保存原始版本 ──
    print("[1/5] 保存原始 Obsidian 版本...")

    if tracked:
        # 已跟踪文件：用 git stash 暂存
        run_git(['stash', 'push', '--include-untracked', '-m',
                 f'[obsidian-convert] stash: {filepath}', '--', filepath])
        stashed = True
        print("       ✅ 已用 git stash 暂存")
    else:
        # 新文件：复制到临时文件
        import tempfile
        import shutil
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.md')
        shutil.copy2(filepath, tmp.name)
        tmp_path = tmp.name
        stashed = False
        print("       ✅ 新文件，已复制到临时位置")

    # ── 步骤 2：转换 ──
    print("[2/5] 转换 Obsidian callout → HTML <details>...")

    with open(filepath, 'r', encoding='utf-8') as f:
        original = f.read()

    converted = convert_callouts_to_details(original)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(converted)

    # 统计转换情况
    callout_count = len(re.findall(r'<details>', converted))
    print(f"       ✅ 转换了 {callout_count} 个 callout")

    # ── 步骤 3：提交 ──
    print("[3/5] 提交到本地仓库...")

    if commit_message is None:
        commit_message = f"publish: convert Obsidian callouts to GitHub details — {basename}"

    run_git(['add', filepath])
    run_git(['commit', '-m', commit_message])
    print(f"       ✅ 已提交")

    # ── 步骤 4：推送 ──
    print("[4/5] 推送到远程仓库...")
    run_git(['push'])
    print("       ✅ 已推送")

    # ── 步骤 5：还原本地版本 ──
    print("[5/5] 还原本地 Obsidian 版本...")

    if stashed:
        # 从 stash 恢复
        run_git(['stash', 'pop'])
    else:
        # 从临时文件恢复
        import shutil
        shutil.copy2(tmp_path, filepath)
        os.unlink(tmp_path)

    # 确保不在暂存区（防止误提交）
    if tracked:
        subprocess.run(['git', 'restore', '--staged', filepath],
                       capture_output=True)
    else:
        subprocess.run(['git', 'rm', '--cached', '-f', filepath],
                       capture_output=True)

    print("       ✅ 已还原，本地文件保持 Obsidian 格式")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎉 完成！")
    print(f"   GitHub：<details> 折叠块（跨平台兼容）")
    print(f"   本地：  [!xxx] callout（Obsidian 原生渲染）")
    print(f"   git status 不会显示此文件有修改")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == '__main__':
    main()
