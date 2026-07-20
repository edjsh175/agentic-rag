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
        if stdout_str.strip():
            print(f"stdout: {stdout_str.strip()}")
        if stderr_str.strip():
            print(f"stderr: {stderr_str.strip()}")

    class CommandResult:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return CommandResult(res.returncode, stdout_str, stderr_str)

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # 1. 确保 git 不转义中文路径
    run_cmd(["git", "config", "core.quotepath", "false"])

    # 2. 运行 git status 获取所有变更
    git_status = run_cmd(["git", "status", "--porcelain"])
    if git_status.returncode != 0:
        print("Failed to run git status")
        return 1

    files_to_commit = []
    untracked_files = []
    deleted_files = []

    for line in git_status.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        file_path = line[3:].strip()
        if file_path.startswith('"') and file_path.endswith('"'):
            file_path = file_path[1:-1]

        if file_path.startswith("scratch/"):
            continue

        files_to_commit.append((status, file_path))
        if "??" in status:
            untracked_files.append(file_path)
        elif "D" in status:
            deleted_files.append(file_path)

    if not files_to_commit:
        print("No changes found to commit.")
        return 0

    print(f"Found {len(files_to_commit)} files changed/added/deleted.")
    print("Files to commit:")
    for st, f in files_to_commit:
        print(f"  [{st}] {f}")

    # 3. 处理删除的文件与 SVN 缺失 (Status !)
    print("\n--- Processing Deleted / Missing files in SVN & Git ---")
    for f in deleted_files:
        run_cmd(["git", "rm", "--ignore-unmatch", f])

    svn_status_all = run_cmd([SVN_BIN, "status", "."])
    for line in svn_status_all.stdout.splitlines():
        if line.startswith("!"):
            missing_path = line[8:].strip()
            print(f"Cleaning up missing SVN file: {missing_path}")
            run_cmd([SVN_BIN, "delete", os.path.abspath(ROOT / missing_path)])

    # 4. 对未跟踪的文件执行 git add 和 svn add
    print("\n--- Adding untracked files to Git and SVN ---")
    for f in untracked_files:
        abs_path = os.path.abspath(ROOT / f)
        if os.path.exists(abs_path):
            run_cmd(["git", "add", f])
            svn_stat = run_cmd([SVN_BIN, "status", abs_path])
            if svn_stat.stdout.startswith("?") or not svn_stat.stdout.strip():
                run_cmd([SVN_BIN, "add", "--parents", abs_path])

    # 5. 对已修改的文件执行 git add
    print("\n--- Staging modified files in Git ---")
    for st, f in files_to_commit:
        if "??" not in st and "D" not in st:
            run_cmd(["git", "add", f])

    # 6. 提交信息
    commit_msg = "feat: implement FR-10 gold v4 dataset, evidence pack governance, query planner improvements and architecture docs"
    print(f"\nUsing commit message: '{commit_msg}'")

    # 7. 执行 Git commit
    print("\n--- Committing to Git ---")
    git_commit = run_cmd(["git", "commit", "-m", commit_msg])
    if git_commit.returncode != 0:
        print("Git commit failed or no changes to commit.")

    # 8. 执行 Git push
    print("\n--- Pushing to Git Remote ---")
    git_push = run_cmd(["git", "push", "origin", "main"])
    if git_push.returncode != 0:
        print("Git push failed!")

    # 9. 执行 SVN commit
    print("\n--- Committing to SVN ---")
    svn_commit = run_cmd([SVN_BIN, "commit", "-m", commit_msg, "."], cwd=ROOT)
    if svn_commit.returncode != 0:
        print("SVN commit failed.")
        return 1

    # 10. 运行仓库卫生检查
    print("\n--- Running repo hygiene check ---")
    hygiene = run_cmd([sys.executable, "scripts/check_repo_hygiene.py"])
    if hygiene.returncode != 0:
        print("Repo hygiene check FAILED! Please review errors.")
        return 1

    print("\nAll tasks completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
