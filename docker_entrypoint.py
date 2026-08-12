"""Docker 启动包装：限制默认 socket 超时，避免启动阶段外连卡死。

生产镜像可选 CMD：python docker_entrypoint.py
"""
import socket

socket.setdefaulttimeout(5)

from rag_knowledge.__main__ import main

if __name__ == "__main__":
    main()
