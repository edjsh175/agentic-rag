import subprocess
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def find_svn_bin():
    default_path = r"C:\Program Files\SlikSvn\bin\svn.exe"
    if os.path.exists(default_path):
        return default_path
    default_path_tortoise = r"C:\Program Files\TortoiseSVN\bin\svn.exe"
    if os.path.exists(default_path_tortoise):
        return default_path_tortoise
    return "svn"

SVN_BIN = find_svn_bin()

def run_cmd(args, cwd=ROOT):
    print(f"Running: {' '.join(args)}")
    res = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True
    )
    # 用 utf-8 解码，如果出错用 gbk
    stdout_str = ""
    stderr_str = ""
    if res.stdout:
        try:
            stdout_str = res.stdout.decode("utf-8")
        except UnicodeDecodeError:
            stdout_str = res.stdout.decode("gbk", errors="ignore")
    if res.stderr:
        try:
            stderr_str = res.stderr.decode("utf-8")
        except UnicodeDecodeError:
            stderr_str = res.stderr.decode("gbk", errors="ignore")

    if res.returncode != 0:
        print(f"Error executing command: {' '.join(args)}")
        # 使用安全的 print 避免 GBK 编码错
        print(f"stdout: {stdout_str.strip()}")
        print(f"stderr: {stderr_str.strip()}")

    # 包装一个简单的结果类以便外面读取
    class CommandResult:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return CommandResult(res.returncode, stdout_str, stderr_str)

def main():
    # 强制将 sys.stdout 和 sys.stderr 重新配置为 utf-8，解决 windows print unicode 报错
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # 1. 检查是否存在带有 § 符号的非规范文件名，并在 Git 中重命名它
    old_file = "docs/3_待办清单/知识图谱语义抽取/待执行-第4轮-GraphRAG实效验收/GraphRAG_§13成功标准勾选表.md"
    new_file = "docs/3_待办清单/知识图谱语义抽取/待执行-第4轮-GraphRAG实效验收/GraphRAG_13成功标准勾选表.md"

    if os.path.exists(ROOT / old_file):
        print(f"\n--- Renaming database-nonconform file: {old_file} ---")
        run_cmd(["git", "mv", old_file, new_file])
        run_cmd(["git", "commit", "-m", "docs: rename database-nonconform filename for SVN compatibility"])
    elif os.path.exists(ROOT / new_file):
        print(f"\nFile {new_file} already exists and renamed.")
    else:
        print(f"\nWarning: Neither {old_file} nor {new_file} found in workspace.")

    # 2. 提取最近 2 个 git 提交中修改和新增的文件，准备同步到 SVN
    print("\n--- Collecting files from recent 2 Git commits ---")
    git_show = run_cmd(["git", "diff", "--name-status", "HEAD~2", "HEAD"])
    if git_show.returncode != 0:
        # 如果最近没有 2 个 commit (比如新库)，可以 fallback 读最近 1 个
        git_show = run_cmd(["git", "diff", "--name-status", "HEAD~1", "HEAD"])

    files_to_sync = []
    added_files = []

    for line in git_show.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        status = parts[0]
        file_path = parts[1].strip()
        if file_path.startswith('"') and file_path.endswith('"'):
            file_path = file_path[1:-1]

        # 排除 prepare_and_commit.py 脚本本身（它如果被 commit 了也需要加到 SVN）
        files_to_sync.append((status, file_path))
        if "A" in status:
            added_files.append(file_path)

    print(f"Collected {len(files_to_sync)} files to sync with SVN.")
    for status, f in files_to_sync:
        print(f"  [{status}] {f}")

    # 3. 对在 Git 中是新增的（A）但在 SVN 中是未跟踪（?）的文件，执行 svn add
    print("\n--- Synchronizing SVN Status (add new files) ---")
    for f in added_files:
        abs_path = os.path.abspath(ROOT / f)
        if not os.path.exists(abs_path):
            # 可能在更近的 commit 里被删除了
            continue
        svn_stat = run_cmd([SVN_BIN, "status", abs_path])
        if svn_stat.stdout.startswith("?") or not svn_stat.stdout.strip():
            run_cmd([SVN_BIN, "add", "--parents", abs_path])

    # 4. 检查是否有缺失文件 (Status !) 需用 svn delete
    print("\n--- Checking for missing files (Status !) in SVN ---")
    svn_status_all = run_cmd([SVN_BIN, "status", "."])
    for line in svn_status_all.stdout.splitlines():
        if line.startswith("!"):
            missing_path = line[8:].strip()
            print(f"Cleaning up missing SVN file: {missing_path}")
            run_cmd([SVN_BIN, "delete", os.path.abspath(ROOT / missing_path)])

    # 5. SVN commit
    commit_msg = "docs and feat: update document support, add staging rebuild dryrun, and update progress documentation"
    print(f"\nUsing commit message: '{commit_msg}'")
    print("\n--- Committing to SVN ---")
    svn_commit = run_cmd([SVN_BIN, "commit", "-m", commit_msg, "."], cwd=ROOT)
    if svn_commit.returncode != 0:
        print("SVN commit failed.")
        return 1

    # 6. 运行仓库卫生检查
    print("\n--- Running repo hygiene check ---")
    hygiene = run_cmd([sys.executable, "scripts/check_repo_hygiene.py"])
    if hygiene.returncode != 0:
        print("Repo hygiene check FAILED! Please review errors.")
        return 1

    print("\nAll tasks completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
