"""
Real RAG Full-Pipeline LLM Benchmark Script
Usage: .\venv\Scripts\python.exe run_llm_rag_eval.py
"""
import sys
import re
import time
import json
import dataclasses
import urllib.request
import concurrent.futures
from pathlib import Path

# Ensure RAG package can be imported
if str(Path.cwd()) not in sys.path:
    sys.path.insert(0, str(Path.cwd()))

from rag_knowledge.config import Config
from rag_knowledge.services.rag import RagChain

# Benchmark Targets
# 本轮仅评测远端 119（含 deepseek-r1:8b）；本地 3060ti 保留配置但注释，避免重复耗时。
SERVERS = [
    # {
    #     "name": "3060ti (本地)",
    #     "ollama_url": "http://localhost:11434",
    #     "gpu_agent_url": "http://localhost:11435",
    #     "models": ["qwen3.5:4b", "qwen3.5:9b", "gemma4:12b"]
    # },
    {
        "name": "3070ti (远端)",
        "ollama_url": "http://192.168.10.119:11434",
        "gpu_agent_url": "http://192.168.10.119:11435",
        "models": ["qwen3.5:4b", "qwen3.5:9b", "gemma4:12b", "gemma4:e2b", "gemma4:e4b", "gemma3:4b", "deepseek-r1:8b"]
    }
]

# Max questions to test for RAG
MAX_RAG_QUESTIONS = 10

# Timeout for a single query (seconds)
QUERY_TIMEOUT_SEC = 120

def get_gpu_vram(gpu_agent_url):
    if not gpu_agent_url:
        return None
    try:
        req = urllib.request.Request(f"{gpu_agent_url}/gpu", method="GET")
        with urllib.request.urlopen(req, timeout=3) as response:
            gpus = json.loads(response.read().decode("utf-8"))
            if gpus and len(gpus) > 0:
                gpu = gpus[0]
                mem_total = gpu.get("memoryTotal", 0)
                mem_used = gpu.get("memoryUsed", 0)
                # Auto convert Bytes to MiB if needed
                if mem_total > 1000000:
                    return {
                        "used": round(mem_used / (1024 * 1024)),
                        "total": round(mem_total / (1024 * 1024))
                    }
                else:
                    return {
                        "used": round(mem_used),
                        "total": round(mem_total)
                    }
    except Exception:
        pass
    return None

def unload_model(ollama_url, model):
    try:
        url = f"{ollama_url}/api/chat"
        data = {
            "model": model,
            "messages": [{"role": "user", "content": "unload"}],
            "keep_alive": 0,
            "options": {"num_predict": 1}
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    except Exception:
        pass

# 定义需要排除的格式噪音与高频无意义通用词
IGNORED_KEYWORDS = {
    "---", "...", "....................................................................................................................................",
    "可以", "以下", "允许", "依次", "代表", "的", "了", "和", "是", "在", "等", "进行", "需要", "以及", "通过",
    "on", "so", "in", "at", "by", "for", "with", "to", "of", "and", "or", "the", "a", "an", "is", "are"
}

def calculate_keywords_score(answer: str, expected_targets: list) -> float:
    if not expected_targets:
        return 1.0

    # Collect all unique keywords from targets
    all_keywords = set()
    for target in expected_targets:
        keywords = target.get("keywords", [])
        for kw in keywords:
            kw_clean = kw.strip().lower()
            # 过滤掉长度小于等于1的字符，以及停用词
            if len(kw_clean) > 1 and kw_clean not in IGNORED_KEYWORDS:
                all_keywords.add(kw_clean)

    if not all_keywords:
        return 1.0

    answer_lower = answer.lower()
    hit_count = 0
    for kw in all_keywords:
        # 如果是纯英文字母/数字，使用前后非字母边界保护，防止 matches 诸如 these/also
        if kw.isalnum() and kw.isascii():
            pattern = rf"(?<![a-zA-Z]){re.escape(kw)}(?![a-zA-Z])"
            if re.search(pattern, answer_lower):
                hit_count += 1
        else:
            if kw in answer_lower:
                hit_count += 1

    return hit_count / len(all_keywords)

def _merge_thinking_output(raw_answer: str) -> tuple[str, str]:
    pattern = r'(?is)\s*<think>(.*?)</think>\s*(.*)'
    m = re.match(pattern, raw_answer)
    if m:
        body = m.group(2).strip()
        merged = (m.group(1) + m.group(2)).strip()
        return body, merged
    else:
        stripped = raw_answer.strip()
        return stripped, stripped

def _enforce_model_binding(m: str, server: dict):
    # 1. 锁定向量服务地址（在改写 ollama_base_url 之前），防止 embedding 跟随被测服务器漂移。
    #    本地 chroma_db 是按 158 的 qwen3-embedding:latest（4096 维）构建的；
    #    若 embedding 漂移到如 119 的 qwen3-embedding:4b（2560 维），检索会因维度不匹配返回空 context。
    #    仅在未显式配置时锁定为当前解析地址（config.ini 默认 158）。
    embed_original = Config().embedding_endpoint.resolved_base_url(Config().ollama_base_url)
    if not Config().embedding_endpoint.base_url:
        Config().embedding_endpoint = dataclasses.replace(
            Config().embedding_endpoint,
            base_url=embed_original,
        )
    # 2. 锁定 helper_llm（路由/改写/摘要）到绑定前的模型与地址，保证各被测模型走同一检索路径。
    #    若 helper 跟随被测模型，会系统性改变召回，导致“更守规矩的大模型拒答更多→准确率更低”。
    helper_ep = Config().helper_llm_endpoint
    helper_original_url = helper_ep.resolved_base_url(Config().ollama_base_url)
    helper_original_model = helper_ep.model
    Config().helper_llm_endpoint = dataclasses.replace(
        helper_ep,
        base_url=helper_original_url,
    )
    Config().helper_llm_model = helper_original_model
    # 3. 仅绑定生成用 LLM 到当前被测服务器与模型
    Config().ollama_base_url = server["ollama_url"]
    Config().llm_endpoint = dataclasses.replace(
        Config().llm_endpoint,
        provider="ollama",
        model=m,
        base_url=server["ollama_url"]
    )
    assert Config().llm_endpoint.model == m
    assert Config().llm_endpoint.base_url == server["ollama_url"]
    assert Config().helper_llm_endpoint.model == helper_original_model
    assert Config().helper_llm_endpoint.resolved_base_url(Config().ollama_base_url) == helper_original_url
    # 4. embedding 端点保持绑定前解析地址，不随 ollama_base_url 漂移
    assert Config().embedding_endpoint.resolved_base_url(Config().ollama_base_url) == embed_original

def _check_ollama_available(ollama_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

def _run_query_with_timeout(chain, question: str, model: str, thinking: bool, ollama_url: str, timeout_sec: int = QUERY_TIMEOUT_SEC) -> tuple[dict | None, str]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(chain.query, question, llm_model=model, thinking=thinking)
        try:
            result = future.result(timeout=timeout_sec)
            return result, "OK"
        except concurrent.futures.TimeoutError:
            return None, "TIMEOUT"
        except Exception:
            return None, "ERROR"
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)

def _preflight_servers(servers: list) -> list:
    enriched = []
    for server in servers:
        server_entry = {
            "name": server["name"],
            "ollama_url": server["ollama_url"],
            "gpu_agent_url": server["gpu_agent_url"],
            "models": []
        }
        available_models_on_server = set()
        server_reachable = False
        try:
            req = urllib.request.Request(f"{server['ollama_url']}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    server_reachable = True
                    data = json.loads(resp.read().decode("utf-8"))
                    for mdl in data.get("models", []):
                        available_models_on_server.add(mdl.get("name", ""))
        except Exception:
            server_reachable = False

        if not server_reachable:
            for m in server["models"]:
                server_entry["models"].append({"name": m, "available": False})
        else:
            for m in server["models"]:
                server_entry["models"].append({"name": m, "available": m in available_models_on_server})

        enriched.append(server_entry)
    return enriched

def run_benchmark():
    print("=== STARTING FULL-PIPELINE RAG LLM BENCHMARK ===")

    # Load Evaluation Dataset
    dataset_path = Path("data/eval_dataset.json")
    if not dataset_path.exists():
        print(f"ERROR: Dataset not found at {dataset_path}")
        return

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    test_subset = dataset[:MAX_RAG_QUESTIONS]
    print(f"Loaded {len(dataset)} items. Selected top {len(test_subset)} questions for RAG evaluation.")

    # Initialize RagChain
    print("Initializing RagChain...")
    chain = RagChain()

    all_results = []

    # Preflight servers: check connectivity & model availability
    print("\n--- Preflight: 检查各服务器与模型可用性 ---")
    enriched_servers = _preflight_servers(SERVERS)

    for server_idx, server in enumerate(enriched_servers):
        original_server = SERVERS[server_idx]
        print(f"\n==========================================")
        print(f"测试服务器: {server['name']} ({server['ollama_url']})")
        print(f"==========================================")

        for model_info in server["models"]:
            m = model_info["name"]
            model_available = model_info["available"]

            if not model_available:
                print(f"\n------ 模型: {m}  [UNAVAILABLE] ------")
                all_results.append({
                    "server": server["name"],
                    "model": m,
                    "status": "UNAVAILABLE",
                    "available": False,
                    "error": "服务器不可达或模型未安装",
                    "rag_score": 0.0,
                    "avg_speed": 0.0,
                    "avg_latency": 0.0,
                    "non_empty_rate": 0.0,
                    "has_sources_rate": 0.0,
                    "avg_answer_chars": 0.0
                })
                continue

            print(f"\n------ 模型: {m} ------")

            # Enforce model binding via helper
            _enforce_model_binding(m, server)

            # 0. Health check Ollama before starting
            if not _check_ollama_available(server["ollama_url"]):
                print(f"  Ollama 不可达，跳过模型 {m}")
                all_results.append({
                    "server": server["name"],
                    "model": m,
                    "status": "ERROR",
                    "available": False,
                    "error": "Ollama 服务不可达",
                    "rag_score": 0.0,
                    "avg_speed": 0.0,
                    "avg_latency": 0.0,
                    "non_empty_rate": 0.0,
                    "has_sources_rate": 0.0,
                    "avg_answer_chars": 0.0
                })
                continue

            # 1. Unload all models to measure Idle VRAM
            print("正在卸载其他模型...")
            for other_model in original_server["models"]:
                unload_model(server["ollama_url"], other_model)
            # 额外卸载本机常见常驻模型，避免显存残留污染净增读数
            for extra in ("qwen3.5:4b", "qwen3.5:9b", "gemma4:12b", "gemma4:e4b", "gemma3:4b", "deepseek-r1:8b", "qwen3-embedding:4b"):
                if extra not in original_server["models"]:
                    unload_model(server["ollama_url"], extra)
            time.sleep(3.0)

            idle_stats = get_gpu_vram(server["gpu_agent_url"])
            idle_vram = idle_stats["used"] if idle_stats else None

            print(f"Idle VRAM: {idle_vram if idle_vram is not None else 'n/a'} MiB")

            # 2. Warm up / Load model
            print("正在加载并预热模型...")
            warmup_ok = True
            try:
                _, warmup_status = _run_query_with_timeout(
                    chain, "你好", m, thinking=False, ollama_url=server["ollama_url"], timeout_sec=60
                )
                if warmup_status != "OK":
                    raise RuntimeError(f"Warmup status: {warmup_status}")
                time.sleep(1.5)
                loaded_stats = get_gpu_vram(server["gpu_agent_url"])
                loaded_vram = loaded_stats["used"] if loaded_stats else None
                delta_vram = loaded_vram - idle_vram if (loaded_vram is not None and idle_vram is not None) else None
                print(f"Loaded VRAM: {loaded_vram if loaded_vram is not None else 'n/a'} MiB (净增: {delta_vram if delta_vram is not None else 'n/a'} MiB)")
            except Exception as e:
                print(f"预热模型失败: {e}")
                warmup_ok = False
                unload_model(server["ollama_url"], m)
                all_results.append({
                    "server": server["name"],
                    "model": m,
                    "status": "ERROR",
                    "available": False,
                    "error": f"模型加载/预热失败: {e}",
                    "rag_score": 0.0,
                    "avg_speed": 0.0,
                    "avg_latency": 0.0,
                    "non_empty_rate": 0.0,
                    "has_sources_rate": 0.0,
                    "avg_answer_chars": 0.0
                })
                continue

            if not warmup_ok:
                continue

            # 3. Run RAG test subset
            per_question = []
            peak_vram = loaded_vram or 0

            print(f"开始运行 {len(test_subset)} 个真实 RAG 问题测试...")
            for idx, item in enumerate(test_subset):
                q = item["question"]
                targets = item.get("expected_targets", [])

                # Fetch VRAM before request
                before_stats = get_gpu_vram(server["gpu_agent_url"])

                t0 = time.time()
                res, q_status = _run_query_with_timeout(
                    chain, q, m, thinking=False, ollama_url=server["ollama_url"]
                )
                t1 = time.time()

                # Fetch VRAM after request
                after_stats = get_gpu_vram(server["gpu_agent_url"])

                # Compute VRAM peak
                cur_peak = max(
                    before_stats["used"] if before_stats else 0,
                    after_stats["used"] if after_stats else 0,
                    loaded_vram or 0
                )
                if cur_peak > peak_vram:
                    peak_vram = cur_peak

                duration = t1 - t0

                if q_status != "OK":
                    print(f"  [{q_status}] 问题 {idx+1}")
                    if q_status in ("TIMEOUT", "ERROR"):
                        unload_model(server["ollama_url"], m)
                    per_question.append({
                        "status": q_status,
                        "body": "",
                        "kw_score": 0.0,
                        "speed": 0.0,
                        "latency": duration * 1000,
                        "has_sources": False
                    })
                    continue

                ans = res.get("answer", "") if isinstance(res, dict) else ""
                has_sources = bool(res and isinstance(res, dict) and res.get("source_documents"))

                # Merge thinking output
                body, merged = _merge_thinking_output(ans)
                ans_len = len(merged)
                estimated_tokens = ans_len * 1.3
                speed = estimated_tokens / duration if duration > 0 else 0.0

                kw_score = calculate_keywords_score(merged, targets)

                per_question.append({
                    "status": "OK",
                    "body": body,
                    "kw_score": kw_score,
                    "speed": speed,
                    "latency": duration * 1000,
                    "has_sources": has_sources
                })

                print(f"  问题 {idx+1}: 关键词匹配 {kw_score*100:.1f}% | 耗时 {duration:.2f}s | 估算速度 {speed:.1f} tok/s | body长度 {len(body)} 字")

            # Clean up/unload
            unload_model(server["ollama_url"], m)

            # Compute aggregates from OK results only
            ok_results = [x for x in per_question if x["status"] == "OK"]
            non_empty_rate = sum(1 for r in ok_results if r["body"].strip()) / len(ok_results) if ok_results else 0.0
            has_sources_rate = sum(1 for r in ok_results if r.get("has_sources")) / len(ok_results) if ok_results else 0.0
            avg_answer_chars = sum(len(r["body"]) for r in ok_results) / len(ok_results) if ok_results else 0.0
            avg_score = sum(r["kw_score"] for r in ok_results) / len(ok_results) if ok_results else 0.0
            avg_speed = sum(r["speed"] for r in ok_results) / len(ok_results) if ok_results else 0.0
            avg_latency = sum(r["latency"] for r in ok_results) / len(ok_results) if ok_results else 0.0

            # Determine overall status
            status_counts = {}
            for pq in per_question:
                status_counts[pq["status"]] = status_counts.get(pq["status"], 0) + 1
            overall_status = "OK" if len(ok_results) == len(per_question) and len(ok_results) > 0 else "PARTIAL"
            if len(ok_results) == 0 and per_question:
                first_status = per_question[0]["status"]
                overall_status = first_status if first_status in ("TIMEOUT", "ERROR") else "ERROR"

            all_results.append({
                "server": server["name"],
                "model": m,
                "status": overall_status,
                "available": True,
                "error": None,
                "idle_vram": idle_vram,
                "loaded_vram": loaded_vram,
                "delta_vram": delta_vram,
                "peak_vram": peak_vram,
                "rag_score": avg_score,
                "avg_speed": avg_speed,
                "avg_latency": avg_latency,
                "non_empty_rate": non_empty_rate,
                "has_sources_rate": has_sources_rate,
                "avg_answer_chars": avg_answer_chars,
                "question_status_counts": status_counts
            })
            print(f"完成 RAG 测评: 平均关键词准确率 {avg_score*100:.1f}% | 平均速度 {avg_speed:.1f} tok/s | 平均延迟 {avg_latency:.0f}ms | 非空率 {non_empty_rate*100:.0f}% | 平均字数 {avg_answer_chars:.0f}")

    # Generate Markdown Report
    md = build_report_md(all_results)

    docs_dir = Path("docs")
    docs_dir.mkdir(parents=True, exist_ok=True)

    with open(docs_dir / "model-eval-report.md", "w", encoding="utf-8") as f:
        f.write(md)

    print("\n===== BENCHMARK REPORT COMPLETED =====")
    print(f"Saved to: docs/model-eval-report.md")

def min_max_norm(values: list) -> list:
    if not values:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        return [1.0 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]

def build_report_md(results) -> str:
    lines = []
    lines.append("# RAG 系统真实全链路 LLM 生成性能与效果评测报告")
    lines.append("")
    lines.append(f"- 测评生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("- 数据集说明：data/eval_dataset.json（StampGIS 接口说明书 + 车流穿梭附件）")
    lines.append("- 测评说明：真实 RAG 全链路（Chroma 检索 → Context 拼装 → LLM 生成）")
    lines.append(f"- 统一测试参数：测试集前 {MAX_RAG_QUESTIONS} 题；评分依据：核心关键词 Keywords 匹配覆盖率")
    lines.append("- 已知免责说明：本地 3060ti (8GB) 下 gemma4:12b 因显存溢出预期退化，若出现 PARTIAL/TIMEOUT 属正常")
    lines.append("")
    lines.append("## 1. 真实 RAG 评测汇总对比表")
    lines.append("")
    lines.append(
        "| 运行服务器 | 模型名称 | 状态 | 关键词匹配准确率 | 非空率 | Sources 命中率 | 平均字数 | tok/s | 延迟 ms | 空闲显存 | 净增显存 | 峰值显存 |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    for r in results:
        status_str = r.get("status", "OK" if r["available"] else "UNAVAILABLE")
        if status_str in ("UNAVAILABLE", "ERROR") or not r["available"]:
            lines.append(
                f"| {r['server']} | {r['model']} | {status_str} | - | - | - | - | - | - | - | - | - |"
            )
            continue

        score_pct = f"{r['rag_score']*100:.0f}%"
        non_empty_pct = f"{r.get('non_empty_rate', 0.0)*100:.0f}%"
        has_src_pct = f"{r.get('has_sources_rate', 0.0)*100:.0f}%"
        avg_chars_str = f"{r.get('avg_answer_chars', 0.0):.0f}"
        speed_str = f"{r['avg_speed']:.1f}"
        latency_str = f"{r['avg_latency']:.0f}"
        idle_str = f"{r['idle_vram']}" if r.get('idle_vram') is not None else "-"
        delta_str = f"{r['delta_vram']}" if r.get('delta_vram') is not None else "-"
        peak_str = f"{r['peak_vram']}" if r.get('peak_vram') is not None else "-"

        lines.append(
            f"| {r['server']} | {r['model']} | {status_str} | {score_pct} | {non_empty_pct} | {has_src_pct} | {avg_chars_str} | {speed_str} | {latency_str} | {idle_str} | {delta_str} | {peak_str} |"
        )

    lines.append("")
    lines.append("## 2. 每台机器 Top3 推荐")
    lines.append("")

    # 从实际结果动态提取服务器列表，避免依赖当前 SERVERS 注释状态
    server_keys = list(dict.fromkeys(r["server"] for r in results))
    top_selections = {}

    for server_name in server_keys:
        rows = [r for r in results if r["server"] == server_name and r["available"] and r.get("status") not in ("UNAVAILABLE", "ERROR")]
        if not rows:
            lines.append(f"### {server_name} Top3 推荐")
            lines.append("")
            lines.append("_无可用模型_")
            lines.append("")
            top_selections[server_name] = []
            continue

        rag_scores = [r["rag_score"] for r in rows]
        avg_speeds = [r["avg_speed"] for r in rows]
        peak_vrams = [1.0 / max(r.get("peak_vram") or 1.0, 1.0) for r in rows]

        score_norms = min_max_norm(rag_scores)
        speed_norms = min_max_norm(avg_speeds)
        vram_norms = min_max_norm(peak_vrams)

        scored_rows = []
        for i, r in enumerate(rows):
            composite = 0.4 * score_norms[i] + 0.3 * speed_norms[i] + 0.3 * vram_norms[i]
            scored_rows.append((composite, r))

        scored_rows.sort(key=lambda x: x[0], reverse=True)
        top3 = scored_rows[:3]
        top_selections[server_name] = top3

        lines.append(f"### {server_name} Top3 推荐")
        lines.append("")
        for rank, (comp_score, r) in enumerate(top3, 1):
            score_pct = f"{r['rag_score']*100:.0f}%"
            speed_str = f"{r['avg_speed']:.1f}"
            peak_str = f"{r.get('peak_vram') or '-'}"
            lines.append(f"{rank}. **{r['model']}** — 综合分 {comp_score:.3f} | RAG Score: {score_pct} | tok/s: {speed_str} | 峰值显存: {peak_str} MiB")
        lines.append("")

    lines.append("## 3. 综合选型结论")
    lines.append("")

    local_server_name = "3060ti (本地)"
    remote_server_name = "3070ti (远端)"

    local_top = top_selections.get(local_server_name, [])
    if local_top:
        comp, r = local_top[0]
        lines.append(f"1) **本地 3060ti 场景首选：`{r['model']}`**。综合分 {comp:.3f}，在 RAG 关键词准确率（{r['rag_score']*100:.0f}%）、生成速度（{r['avg_speed']:.1f} tok/s）与显存占用（峰值 {r.get('peak_vram') or '-'} MiB）三者间取得了最佳平衡，适合作为本地常驻模型。")
    else:
        lines.append("1) **本地 3060ti 场景**：当前无可用模型，建议先安装 4B-9B 级轻量模型。")
    lines.append("")

    remote_top = top_selections.get(remote_server_name, [])
    if remote_top:
        comp, r = remote_top[0]
        lines.append(f"2) **远端 3070ti 场景首选：`{r['model']}`**。综合分 {comp:.3f}，RAG Score {r['rag_score']*100:.0f}% + 速度 {r['avg_speed']:.1f} tok/s + 峰值显存 {r.get('peak_vram') or '-'} MiB，精度与效率俱佳，适合远端常驻处理高精度请求。")
    else:
        lines.append("2) **远端 3070ti 场景**：当前无可用模型。")
    lines.append("")

    lines.append("3) **跨机器协同场景建议**：远端 3070ti 常驻首选高精度模型（如 Gemma 系列 4B-9B 级），承担需要精确关键词匹配的复杂问答；本地 3060ti 则部署 4B 级轻量模型负责低延迟快速响应与日常交互。**明确不建议本地 3060ti 上 gemma4:12b**，该模型在 8GB 显存下极易发生显存溢出导致 PARTIAL/TIMEOUT，严重拖慢响应效率。")
    lines.append("")

    return "\n".join(lines)

def _selftest():
    print("[selftest] TR-1.1 测试 _merge_thinking_output ...")
    # Case 1: No think tags
    body1, merged1 = _merge_thinking_output("  hello world  ")
    assert body1 == "hello world", f"body1 failed: {body1!r}"
    assert merged1 == "hello world", f"merged1 failed: {merged1!r}"

    # Case 2: With think tags
    raw2 = "  <think> step1 step2 </think>  final answer  "
    body2, merged2 = _merge_thinking_output(raw2)
    assert body2 == "final answer", f"body2 failed: {body2!r}"
    assert "step1 step2" in merged2 and "final answer" in merged2, f"merged2 failed: {merged2!r}"

    # Case 3: Multiline think tags
    raw3 = """<think>
Line 1 reasoning
Line 2 reasoning
</think>

The real body text.
"""
    body3, merged3 = _merge_thinking_output(raw3)
    assert body3 == "The real body text.", f"body3 failed: {body3!r}"
    assert "Line 1 reasoning" in merged3 and "The real body text." in merged3, f"merged3 failed: {merged3!r}"
    print("[selftest] TR-1.1 passed.")

    print("[selftest] TR-1.2 测试 _enforce_model_binding ...")
    from rag_knowledge.llm_http import ModelEndpoint
    saved_instance = Config._instance
    try:
        Config._instance = None
        fresh_cfg = Config()
        # Force endpoints to something known
        fresh_cfg.llm_endpoint = ModelEndpoint(role="llm", provider="ollama", model="old:1b", base_url="")
        fresh_cfg.helper_llm_endpoint = ModelEndpoint(role="helper_llm", provider="ollama", model="oldhelper:1b", base_url="")
        fresh_cfg.embedding_endpoint = ModelEndpoint(role="embedding", provider="ollama", model="oldembed:1b", base_url="")
        fresh_cfg.ollama_base_url = "http://old:11434"

        test_server = {"ollama_url": "http://testhost:11434", "name": "t", "gpu_agent_url": ""}
        test_model = "qwen-test:42b"
        _enforce_model_binding(test_model, test_server)

        assert Config().ollama_base_url == "http://testhost:11434"
        assert Config().llm_endpoint.model == test_model
        assert Config().llm_endpoint.base_url == "http://testhost:11434"
        assert Config().llm_endpoint.provider == "ollama"
        # helper_llm 应保持绑定前模型与地址，不跟随被测模型
        assert Config().helper_llm_endpoint.model == "oldhelper:1b"
        assert Config().helper_llm_endpoint.provider == "ollama"
        assert Config().helper_llm_endpoint.resolved_base_url(Config().ollama_base_url) == "http://old:11434"
        assert Config().helper_llm_model == "oldhelper:1b"
        # embedding 端点应锁定为绑定前的解析地址，不随 ollama_base_url 漂移到被测服务器
        assert Config().embedding_endpoint.base_url == "http://old:11434"
        assert Config().embedding_endpoint.resolved_base_url(Config().ollama_base_url) == "http://old:11434"
        print("[selftest] TR-1.2 passed.")
    finally:
        Config._instance = saved_instance

    print("[selftest] TR-1.3 测试 _run_query_with_timeout TIMEOUT ...")
    import time as _time

    class _SlowChain:
        def query(self, question, llm_model=None, thinking=False):
            _time.sleep(1.0)
            return {"answer": "slow"}

    slow_chain = _SlowChain()
    res, status = _run_query_with_timeout(
        slow_chain, "q", "slowm", thinking=False, ollama_url="http://dummy:11434", timeout_sec=0.1
    )
    assert res is None, f"Expected None result but got {res}"
    assert status == "TIMEOUT", f"Expected TIMEOUT but got {status}"
    print("[selftest] TR-1.3 passed.")

    # Extra: test OK path
    class _FastChain:
        def query(self, question, llm_model=None, thinking=False):
            return {"answer": "ok", "sources": ["doc1"]}

    fast_chain = _FastChain()
    res2, status2 = _run_query_with_timeout(
        fast_chain, "q", "fastm", thinking=False, ollama_url="http://dummy:11434", timeout_sec=10
    )
    assert status2 == "OK", f"Expected OK but got {status2}"
    assert res2 and res2.get("answer") == "ok"
    print("[selftest] OK-path smoke-test passed.")

    # Extra: test ERROR path
    class _BadChain:
        def query(self, question, llm_model=None, thinking=False):
            raise RuntimeError("boom")

    bad_chain = _BadChain()
    res3, status3 = _run_query_with_timeout(
        bad_chain, "q", "badm", thinking=False, ollama_url="http://dummy:11434", timeout_sec=10
    )
    assert status3 == "ERROR", f"Expected ERROR but got {status3}"
    assert res3 is None
    print("[selftest] ERROR-path smoke-test passed.")

    print("[selftest] TR-2.1 测试 build_report_md 动态生成 ...")
    mock_results = [
        {
            "server": "3060ti (本地)",
            "model": "model-a",
            "status": "OK",
            "available": True,
            "idle_vram": 1200,
            "delta_vram": 1800,
            "peak_vram": 3000,
            "rag_score": 0.9,
            "avg_speed": 50.0,
            "avg_latency": 2000.0,
            "non_empty_rate": 1.0,
            "has_sources_rate": 0.9,
            "avg_answer_chars": 250.0
        },
        {
            "server": "3060ti (本地)",
            "model": "model-b",
            "status": "OK",
            "available": True,
            "idle_vram": 1200,
            "delta_vram": 3800,
            "peak_vram": 5000,
            "rag_score": 0.7,
            "avg_speed": 80.0,
            "avg_latency": 1500.0,
            "non_empty_rate": 1.0,
            "has_sources_rate": 0.8,
            "avg_answer_chars": 180.0
        },
        {
            "server": "3060ti (本地)",
            "model": "model-c",
            "status": "PARTIAL",
            "available": True,
            "idle_vram": 1200,
            "delta_vram": 2800,
            "peak_vram": 4000,
            "rag_score": 0.5,
            "avg_speed": 30.0,
            "avg_latency": 3500.0,
            "non_empty_rate": 0.7,
            "has_sources_rate": 0.6,
            "avg_answer_chars": 150.0
        },
        {
            "server": "3070ti (远端)",
            "model": "remote-x",
            "status": "OK",
            "available": True,
            "idle_vram": 900,
            "delta_vram": 3100,
            "peak_vram": 4000,
            "rag_score": 0.85,
            "avg_speed": 100.0,
            "avg_latency": 1200.0,
            "non_empty_rate": 1.0,
            "has_sources_rate": 0.95,
            "avg_answer_chars": 220.0
        },
        {
            "server": "3070ti (远端)",
            "model": "remote-y",
            "status": "OK",
            "available": True,
            "idle_vram": 900,
            "delta_vram": 2600,
            "peak_vram": 3500,
            "rag_score": 0.8,
            "avg_speed": 90.0,
            "avg_latency": 1400.0,
            "non_empty_rate": 0.95,
            "has_sources_rate": 0.9,
            "avg_answer_chars": 200.0
        }
    ]
    md = build_report_md(mock_results)
    assert "## 1. 真实 RAG 评测汇总对比表" in md, "Missing 汇总对比表 section"
    assert "3060ti (本地) Top3 推荐" in md, "Missing 本地 3060ti Top3 推荐 section"
    assert "3070ti (远端) Top3 推荐" in md, "Missing 远端 3070ti Top3 推荐 section"
    assert "## 3. 综合选型结论" in md, "Missing 综合选型结论 section"
    assert "已知免责说明" in md, "Missing 已知免责说明"

    local_3060ti_rows = [r for r in mock_results if r["server"] == "3060ti (本地)" and r["available"] and r.get("status") not in ("UNAVAILABLE", "ERROR")]
    rag_scores = [r["rag_score"] for r in local_3060ti_rows]
    avg_speeds = [r["avg_speed"] for r in local_3060ti_rows]
    peak_vrams = [1.0 / max(r.get("peak_vram") or 1.0, 1.0) for r in local_3060ti_rows]
    score_norms = min_max_norm(rag_scores)
    speed_norms = min_max_norm(avg_speeds)
    vram_norms = min_max_norm(peak_vrams)
    scored = []
    for i, r in enumerate(local_3060ti_rows):
        comp = 0.4 * score_norms[i] + 0.3 * speed_norms[i] + 0.3 * vram_norms[i]
        scored.append((comp, r["model"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    expected_top1_model = scored[0][1]

    local_section_start = md.find("3060ti (本地) Top3 推荐")
    local_section_end = md.find("3070ti (远端) Top3 推荐", local_section_start)
    local_section = md[local_section_start:local_section_end]
    first_bullet = [ln for ln in local_section.split("\n") if ln.strip().startswith("1.")][0]
    assert f"**{expected_top1_model}**" in first_bullet, f"Expected Top1 {expected_top1_model} but got line: {first_bullet}"
    print(f"[selftest] TR-2.1 passed (3060ti Top1={expected_top1_model}).")

    print("\n[selftest] ALL TESTS PASSED.")

if __name__ == "__main__":
    # _selftest()
    run_benchmark()
