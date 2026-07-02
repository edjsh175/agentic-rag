"""直接通过 hf-mirror.com HTTP 下载 BGE reranker 模型"""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from urllib.request import urlretrieve, urlopen
from urllib.error import URLError

MIRROR = "https://hf-mirror.com"
MODEL = "BAAI/bge-reranker-v2-m3"
LOCAL = "models/bge-reranker-v2-m3"

# 需要下载的文件列表
FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "model.safetensors",
    "pytorch_model.bin",  # fallback if safetensors not available
]

os.makedirs(LOCAL, exist_ok=True)

for fname in FILES:
    url = f"{MIRROR}/{MODEL}/resolve/main/{fname}"
    local_path = os.path.join(LOCAL, fname)

    if os.path.exists(local_path):
        size = os.path.getsize(local_path)
        print(f"  [SKIP] {fname} ({size/1024/1024:.1f} MB) — 已存在")
        continue

    print(f"  下载 {fname} ...", end=" ", flush=True)
    try:
        urlretrieve(url, local_path)
        size = os.path.getsize(local_path)
        print(f"OK ({size/1024/1024:.1f} MB)")
    except URLError as e:
        print(f"FAIL: {e}")
        # model.safetensors vs pytorch_model.bin 只需一个
        if fname == "model.safetensors":
            print("  (将尝试 pytorch_model.bin)")
            continue
        elif fname == "pytorch_model.bin":
            print("  (两个权重文件都下载失败)")

print(f"\n下载到: {os.path.abspath(LOCAL)}")
for f in os.listdir(LOCAL):
    size = os.path.getsize(os.path.join(LOCAL, f))
    print(f"  {f}: {size/1024/1024:.1f} MB")
