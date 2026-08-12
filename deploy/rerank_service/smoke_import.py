import os
import traceback

os.environ.setdefault("RERANK_MODEL", r"D:\rag_rerank\models\bge-reranker-v2-m3")
os.environ.setdefault("RERANK_USE_FP16", "false")

log_path = r"D:\rag_rerank\smoke_import.log"
with open(log_path, "w", encoding="utf-8") as f:
    try:
        f.write("start\n")
        f.flush()
        import server
        f.write("imported server\n")
        f.flush()
        model = server._get_model()
        f.write(f"model-loaded type={type(model)}\n")
        scores = model.compute_score([["hello", "world"]])
        f.write(f"score={scores}\n")
        f.write("OK\n")
    except Exception:
        f.write(traceback.format_exc())
print("wrote", log_path)
