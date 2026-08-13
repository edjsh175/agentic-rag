#!/usr/bin/env python3
"""Start FastAPI without initial/scheduled directory scan (chroma import bootstrap)."""
from __future__ import annotations

import logging
import socket

socket.setdefaulttimeout(5)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from rag_knowledge.__main__ import _setup_logging
from rag_knowledge.config import Config
from rag_knowledge.api.routes import init_components, router
from rag_knowledge.api.middleware import RequestLogMiddleware
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.loader import FileLoader
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.scanner import DirectoryScanner


def main() -> None:
    cfg = Config()
    _setup_logging(cfg.log_dir)
    logger = logging.getLogger("rag")
    logger.info("BOOTSTRAP: start API without directory scan")
    scanner = DirectoryScanner()
    rag = RagChain()
    loader = FileLoader()
    store = VectorStore()
    RelationalDB()
    init_components(scanner, rag, loader, store, cfg)
    app = FastAPI(title="RAG Knowledge (bootstrap no-scan)", version="2.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RequestLogMiddleware)
    app.include_router(router)
    logger.info("VectorStore count=%s", store.count())
    uvicorn.run(app, host=cfg.server_host, port=cfg.server_port, log_config=None)


if __name__ == "__main__":
    main()
