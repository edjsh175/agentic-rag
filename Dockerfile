FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-base.txt .
COPY requirements-reranker.txt .

ARG INSTALL_RERANKER=false

# If pip install or document parsing fails at runtime, uncomment and adjust:
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends \
#        libglib2.0-0 libgl1 libmagic1 \
#     && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements-base.txt \
    && if [ "$INSTALL_RERANKER" = "true" ]; then \
         pip install --no-cache-dir -r requirements-reranker.txt; \
       fi

COPY rag_knowledge/ ./rag_knowledge/
COPY scripts/ ./scripts/
COPY run.py .
COPY docker_entrypoint.py .
COPY run_graph_build.py .
COPY sync_profiles_to_graph.py .
COPY sync_product_backbone_to_graph.py .

EXPOSE 10605

# 默认 run.py；若启动阶段外连卡死，可改为：python docker_entrypoint.py
CMD ["python", "run.py"]
