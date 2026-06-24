#!/usr/bin/env python3
"""
push-folder.py
==============
将整个文件夹中的 Obsidian .md 文件（含子目录）批量转换为 GitHub 兼容格式，
一次性提交推送，然后本地全部还原为原始 Obsidian 版本。

用法：
    python push-folder.py <文件夹路径> [--message "提交信息"]

示例：
    python push-folder.py "论文阅读/"
    python push-folder.py "系统程序设计基础复习/" --message "发布复习笔记"

与 push-note.py 的区别：
    push-note.py  → 一次处理一个 .md 文件
    push-folder.py → 一次处理整个文件夹（递归查找所有 .md）
"""

import re
import sys
import subprocess
import os
import shutil
import tempfile
from pathlib import Path

# ── 核心转换逻辑（与 push-note.py 共用）──────────────────────

def convert_callouts_to_details(content: str) -> str:
    """将 Obsidian blockquote callout 转换为 HTML <details> 折叠块。"""
    lines = content.splitlines(keepends=True)
    result = []
    in_callout = False
    callout_pattern = re.compile(r'^> \[!(\w+)\]-?\s*(.*)$')

    for line in lines:
        match = callout_pattern.match(line)
        if match:
            if in_callout:
                result.append('</details>\n')
                result.append('\n')
            title = match.group(2).strip() or match.group(1)
            result.append('<details>\n')
            result.append(f'<summary><b>{title}</b></summary>\n')
            result.append('\n')
            in_callout = True
            continue

        if in_callout:
            if line.strip() == '' and not line.startswith('>'):
                result.append('</details>\n')
                result.append('\n')
                in_callout = False
                result.append('\n')
                continue
            if line.startswith('> '):
                result.append(line[2:])
            elif line == '>\n' or line == '>\r\n':
                result.append('\n')
            elif line.startswith('>'):
                result.append(line[1:])
            else:
                result.append(line)
            continue

        result.append(line)

    if in_callout:
        result.append('</details>\n')

    return ''.join(result)


# ── 文件扫描 ──────────────────────────────────────────────────

def find_md_files(folder: str, skip_ignored: bool = True) -> list[str]:
    """
    递归查找文件夹中所有 .md 文件。

    参数：
        folder: 文件夹路径
        skip_ignored: 是否跳过 .gitignore 中的文件（默认 True）

    返回：
        相对于仓库根目录的文件路径列表
    """
    md_files = []

    # 读取 .gitignore 规则
    ignored_patterns = []
    gitignore_path = os.path.join(folder, '.gitignore')
    if os.path.isfile(gitignore_path) and skip_ignored:
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            ignored_patterns = [line.strip() for line in f
                               if line.strip() and not line.startswith('#')]
    # 也读取仓库根目录的 .gitignore
    root_gitignore = '.gitignore'
    if os.path.isfile(root_gitignore) and skip_ignored:
        with open(root_gitignore, 'r', encoding='utf-8') as f:
            ignored_patterns += [line.strip() for line in f
                                if line.strip() and not line.startswith('#')]

    for root, dirs, files in os.walk(folder):
        # 跳过 .git 目录
        dirs[:] = [d for d in dirs if d != '.git']

        for file in files:
            if not file.endswith('.md'):
                continue

            full_path = os.path.normpath(os.path.join(root, file))

            # 检查是否被 gitignore
            rel_path = os.path.relpath(full_path, '.')
            if skip_ignored and any(rel_path.startswith(p.rstrip('/')) or
                                    file == p for p in ignored_patterns):
                continue

            md_files.append(full_path)

    return md_files


# ── Git 操作 ──────────────────────────────────────────────────

def run_git(args: list[str]) -> subprocess.CompletedProcess:
    """运行 git 命令。"""
    result = subprocess.run(
        ['git'] + args, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [错误] git {' '.join(args)} 失败：")
        print(f"  {result.stderr.strip()}")
        sys.exit(1)
    return result


def is_git_repo() -> bool:
    result = subprocess.run(
        ['git', 'rev-parse', '--git-dir'], capture_output=True, text=True,
    )
    return result.returncode == 0


# ── 备份与还原 ────────────────────────────────────────────────

def backup_files(filepaths: list[str]) -> dict:
    """
    备份所有文件。返回备份信息字典。
    已跟踪文件 → git stash
    新文件     → 复制到临时目录
    """
    backup = {'stashed': [], 'tmpdir': None, 'tmpfiles': {}}

    # 分离已跟踪和新文件
    tracked = []
    untracked = []
    for fp in filepaths:
        result = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', fp],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            tracked.append(fp)
        else:
            untracked.append(fp)

    # 已跟踪文件：一起 stash
    if tracked:
        run_git(['stash', 'push', '--include-untracked', '-m',
                 '[push-folder] stash original files'] + tracked)
        backup['stashed'] = tracked

    # 新文件：复制到临时目录
    if untracked:
        tmpdir = tempfile.mkdtemp(prefix='push-folder-backup-')
        backup['tmpdir'] = tmpdir
        for fp in untracked:
            # 保持目录结构
            rel = os.path.relpath(fp, '.')
            dest = os.path.join(tmpdir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(fp, dest)
            backup['tmpfiles'][fp] = dest

    return backup


def restore_files(backup: dict) -> None:
    """从备份还原所有文件。"""
    # 还原 stash
    if backup['stashed']:
        subprocess.run(['git', 'stash', 'pop'], capture_output=True)
        # 取消暂存
        for fp in backup['stashed']:
            subprocess.run(['git', 'restore', '--staged', fp],
                          capture_output=True)

    # 还原新文件
    if backup['tmpdir']:
        for fp, src in backup['tmpfiles'].items():
            shutil.copy2(src, fp)
        shutil.rmtree(backup['tmpdir'])
        # 从索引移除（如果被 add 了）
        for fp in backup['tmpfiles']:
            subprocess.run(['git', 'rm', '--cached', '-f', fp],
                          capture_output=True)


# ── 主流程 ────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    folder = sys.argv[1]
    commit_message = None

    args = sys.argv[2:]
    for i, arg in enumerate(args):
        if arg in ('--message', '-m') and i + 1 < len(args):
            commit_message = args[i + 1]
            break

    # ── 前置检查 ──
    if not os.path.isdir(folder):
        print(f"[错误] 文件夹不存在：{folder}")
        sys.exit(1)

    if not is_git_repo():
        print("[错误] 当前目录不是 git 仓库")
        sys.exit(1)

    repo_root = run_git(['rev-parse', '--show-toplevel']).stdout.strip()
    os.chdir(repo_root)

    print(f"📁 文件夹：{folder}")
    print(f"📦 仓库：  {repo_root}")
    print()

    # ── 步骤 1：扫描 .md 文件 ──
    print("[1/5] 扫描文件夹中的 .md 文件...")
    md_files = find_md_files(folder)

    if not md_files:
        print("       ⚠️  没有找到 .md 文件（可能都被 .gitignore 忽略了）")
        sys.exit(0)

    # 检查哪些文件包含 callout
    files_with_callouts = []
    files_without_callouts = []
    for fp in md_files:
        with open(fp, 'r', encoding='utf-8') as f:
            content = f.read()
        if re.search(r'>\s*\[!', content):
            files_with_callouts.append(fp)
        else:
            files_without_callouts.append(fp)

    print(f"       找到 {len(md_files)} 个 .md 文件")
    print(f"       其中 {len(files_with_callouts)} 个包含 callout（需要转换）")

    if files_without_callouts:
        names = [os.path.basename(f) for f in files_without_callouts]
        print(f"       {len(files_without_callouts)} 个无 callout（跳过）：{', '.join(names)}")

    if not files_with_callouts:
        print()
        print("没有需要转换的文件，无需操作。")
        sys.exit(0)

    print()
    for f in files_with_callouts:
        print(f"       → {os.path.basename(f)}")

    print()

    # ── 步骤 2：备份 ──
    print("[2/5] 备份原始文件...")
    backup = backup_files(files_with_callouts)
    stashed = len(backup['stashed'])
    copied = len(backup['tmpfiles'])
    print(f"       ✅ {stashed} 个已跟踪文件 → git stash")
    print(f"       ✅ {copied} 个新文件 → 临时备份")

    # ── 步骤 3：转换 ──
    print("[3/5] 转换 Obsidian callout → HTML <details>...")
    total_callouts = 0

    for fp in files_with_callouts:
        with open(fp, 'r', encoding='utf-8') as f:
            original = f.read()

        converted = convert_callouts_to_details(original)

        with open(fp, 'w', encoding='utf-8') as f:
            f.write(converted)

        count = len(re.findall(r'<details>', converted))
        total_callouts += count
        print(f"       {count:3d} callout → {os.path.basename(fp)}")

    print(f"       ─────────────────────")
    print(f"       {total_callouts:3d} callout 合计")

    # ── 步骤 4：提交并推送 ──
    print("[4/5] 提交并推送...")

    if commit_message is None:
        folder_name = os.path.basename(os.path.normpath(folder))
        commit_message = f"publish: convert Obsidian callouts in {folder_name}/ to GitHub details"

    for fp in files_with_callouts:
        run_git(['add', fp])
    run_git(['commit', '-m', commit_message])
    print("       ✅ 已提交")

    run_git(['push'])
    print("       ✅ 已推送")

    # ── 步骤 5：还原 ──
    print("[5/5] 还原本地 Obsidian 版本...")
    restore_files(backup)
    print("       ✅ 已还原，本地文件保持 Obsidian 格式")

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎉 完成！")
    print(f"   处理文件：{len(files_with_callouts)} 个")
    print(f"   转换 callout：{total_callouts} 个")
    print(f"   GitHub：<details> 折叠块（跨平台兼容）")
    print(f"   本地：  [!xxx] callout（Obsidian 原生渲染）")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == '__main__':
    main()
