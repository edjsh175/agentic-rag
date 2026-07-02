"""Runtime compatibility checks for the persistent vector store."""

from __future__ import annotations

import sys
from importlib import metadata


REQUIRED_CHROMA_VERSIONS = {
    "chromadb": "0.6.3",
    "langchain-chroma": "0.2.3",
}


def validate_chroma_runtime() -> dict[str, str]:
    """Fail before opening Chroma when incompatible packages are installed."""
    actual: dict[str, str] = {}
    mismatches: list[str] = []

    for package, expected in REQUIRED_CHROMA_VERSIONS.items():
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            installed = "未安装"
        actual[package] = installed
        if installed != expected:
            mismatches.append(f"{package}: 需要 {expected}，当前 {installed}")

    if mismatches:
        details = "；".join(mismatches)
        raise RuntimeError(
            "Chroma 运行环境不兼容，已拒绝打开持久化向量库。"
            f"当前 Python: {sys.executable}；{details}。"
            "本地运行请使用 .\\venv\\Scripts\\python.exe，"
            "并执行 .\\venv\\Scripts\\python.exe -m pip install -r requirements.txt。"
        )

    return actual
