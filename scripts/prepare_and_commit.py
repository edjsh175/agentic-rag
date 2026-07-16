import subprocess
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def find_svn_bin():
    # 优先检查默认路径，再检查系统 PATH
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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if res.returncode != 0:
        print(f"Error executing command: {' '.join(args)}")
        print(f"stdout: {res.stdout}")
        print(f"stderr: {res.stderr}")
    return res

def main():
    # 1. 确保 git 不转义中文路径
    run_cmd(["git", "config", "core.quotepath", "false"])

    # 2. 运行 git status 获取修改和新增文件
    git_status = run_cmd(["git", "status", "--porcelain"])
    if git_status.returncode != 0:
        print("Failed to run git status")
        return 1

    files_to_commit = []
    untracked_files = []

    for line in git_status.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        file_path = line[3:].strip()
        # 去掉可能存在的引号
        if file_path.startswith('"') and file_path.endswith('"'):
            file_path = file_path[1:-1]
        
        # 我们要处理的文件
        files_to_commit.append(file_path)
        if "??" in status:
            untracked_files.append(file_path)

    if not files_to_commit:
        print("No changes found to commit.")
        return 0

    print(f"Found {len(files_to_commit)} files changed/added.")
    print("Files to commit:")
    for f in files_to_commit:
        print(f"  - {f}")

    # 3. 对未跟踪的文件执行 git add 和 svn add
    print("\n--- Adding untracked files to Git and SVN ---")
    for f in untracked_files:
        abs_path = os.path.abspath(ROOT / f)
        
        # Git add
        run_cmd(["git", "add", f])
        
        # SVN status check and add
        svn_stat = run_cmd([SVN_BIN, "status", abs_path])
        # 如果在 svn 中是未跟踪状态（以 ? 开头）
        if svn_stat.stdout.startswith("?") or not svn_stat.stdout.strip():
            # 执行 svn add
            run_cmd([SVN_BIN, "add", "--parents", abs_path])

    # 4. 对已修改的文件执行 git add (确保全部暂存)
    print("\n--- Staging modified files in Git ---")
    for f in files_to_commit:
        if f not in untracked_files:
            run_cmd(["git", "add", f])

    # 5. 提示用户输入提交信息或使用默认提交信息
    commit_msg = "docs & feat: update document support, add staging rebuild dryrun, and update progress documentation"
    print(f"\nUsing commit message: '{commit_msg}'")

    # 6. 执行 Git commit
    print("\n--- Committing to Git ---")
    git_commit = run_cmd(["git", "commit", "-m", commit_msg])
    if git_commit.returncode != 0:
        print("Git commit failed. Possibly no changes or check error.")

    # 7. 执行 SVN commit
    # 按照 AGENTS.md 指示：
    # "执行 svn commit 时，先 cd 到项目根目录，以 . 作为路径参数执行"
    print("\n--- Committing to SVN ---")
    svn_commit = run_cmd([SVN_BIN, "commit", "-m", commit_msg, "."], cwd=ROOT)
    if svn_commit.returncode != 0:
        print("SVN commit failed.")
        return 1

    # 8. 运行仓库卫生检查
    print("\n--- Running repo hygiene check ---")
    hygiene = run_cmd([sys.executable, "scripts/check_repo_hygiene.py"])
    if hygiene.returncode != 0:
        print("Repo hygiene check FAILED! Please review errors.")
        return 1

    print("\nAll tasks completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
