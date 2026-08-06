"""
GPU 显存监控与显存自适应 LLM 模型选择。

客户端对接 gpu-agent sidecar（FastAPI，默认 11435 端口）：
  GET {base_url}/gpu → JSON 数组（每个 GPU 一个对象）
    字段：memoryTotal / memoryUsed / memoryFree（字节；部分实现返回 MiB，用
    memoryTotal > 1000000 自动换算）、temperature、utilization、powerDraw（仅 NVIDIA）
  503 = 无 GPU 后端；500 = 查询失败；两者均按“指标不可用”降级处理。

模型显存占用表来自 config.ini [gpu_agent.model_vram]（单位 GiB，基于评测/实测）；
外部 provider（google/openai）模型不占用本地显存，视为始终可加载。
gpu-agent 不可用或未启用时，推荐/降级一律保持原模型（不破坏现有问答）。
"""
import json
import logging
import time
import urllib.request

from rag_knowledge.config import Config

logger = logging.getLogger(__name__)


class GpuMonitor:
    """GPU 显存指标采集 + 显存自适应模型选择（单例，带短 TTL 缓存）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._metrics: dict | None = None
        self._fetched_at = 0.0
        self._initialized = True

    @property
    def _cfg(self) -> Config:
        return Config()

    @property
    def enabled(self) -> bool:
        """GPU 监控服务是否在配置中启用。"""
        return self._cfg.gpu_agent_enabled


    # ------------------------------------------------------------------
    # 指标采集
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict | None:
        """返回归一化 GPU 指标（显存 MiB），gpu-agent 不可用时返回 None。带 TTL 缓存。"""
        cfg = self._cfg
        if not cfg.gpu_agent_enabled:
            return None
        now = time.monotonic()
        if self._metrics is not None and now - self._fetched_at < cfg.gpu_agent_poll_ttl:
            return self._metrics
        try:
            req = urllib.request.Request(f"{cfg.gpu_agent_base_url}/gpu", method="GET")
            with urllib.request.urlopen(req, timeout=cfg.gpu_agent_timeout) as response:
                gpus = json.loads(response.read().decode("utf-8"))
            if not gpus:
                self._metrics = None
                return None
            gpu = gpus[0]
            mem_total = gpu.get("memoryTotal") or 0
            mem_used = gpu.get("memoryUsed") or 0
            mem_free = gpu.get("memoryFree") or 0
            # 字节→MiB 自动换算（与 run_llm_rag_eval.get_gpu_vram 一致）
            if mem_total > 1000000:
                mem_total, mem_used, mem_free = mem_total / (1024 * 1024), mem_used / (1024 * 1024), mem_free / (1024 * 1024)
            self._metrics = {
                "name": gpu.get("name", ""),
                "total_mib": round(mem_total),
                "used_mib": round(mem_used),
                "free_mib": round(mem_free if mem_free else max(0, mem_total - mem_used)),
                "utilization": gpu.get("utilization"),
                "temperature": gpu.get("temperature"),
                "power_draw": gpu.get("powerDraw"),
            }
            self._fetched_at = now
            return self._metrics
        except Exception as e:
            logger.warning("获取 GPU 指标失败: %s", e)
            self._metrics = None
            return None

    # ------------------------------------------------------------------
    # 模型显存适配
    # ------------------------------------------------------------------

    def provider_of(self, model: str) -> str:
        """推断模型 provider：命中角色配置→其 provider，否则视为 ollama（本地）。"""
        cfg = self._cfg
        for role in ["llm", "helper_llm", "vision", "compression", "graph_extraction"]:
            try:
                ep = cfg.endpoint_for(role)
            except Exception:
                continue
            if ep.model == model:
                return ep.normalized_provider()
        return "ollama"

    def footprint_gib(self, model: str) -> float | None:
        """模型预估显存占用(GiB)。不在配置表且为本地模型→None（未知，不降级）。"""
        vram = self._cfg.gpu_agent_model_vram
        if model in vram:
            return vram[model]
        if self.provider_of(model) != "ollama":
            return 0.0  # 外部模型不占用本地显存
        return None

    def fits(self, model: str, metrics: dict) -> bool | None:
        """当前显存下模型能否加载。None=无法判断（指标不可用或占用未知）。"""
        foot = self.footprint_gib(model)
        if foot is None or metrics is None:
            return None
        available = metrics["free_mib"] / 1024.0 - self._cfg.gpu_agent_safety_margin_gib
        return foot <= available

    def recommend_model(self, candidates: list[str], preferred: str) -> str:
        """推荐当前显存下最合适的模型：preferred 可装下→用它；否则候选里占用最大且可装下者。"""
        metrics = self.get_metrics()
        if metrics is None:
            return preferred
        candidates = [c for c in candidates if c]
        if preferred and self.fits(preferred, metrics) is not False:
            return preferred
        fitting = [c for c in candidates if self.fits(c, metrics) is True]
        if not fitting:
            return preferred
        fitting.sort(key=lambda m: self.footprint_gib(m) or 0.0, reverse=True)
        return fitting[0]

    def resolve_model(self, requested: str) -> tuple[str, bool]:
        """自动降级兜底：requested 超显存且 fallback 可装下→降级。返回（最终模型，是否降级）。"""
        metrics = self.get_metrics()
        if metrics is None or self.fits(requested, metrics) is not False:
            return requested, False
        fallback = self._cfg.gpu_agent_fallback_model
        if fallback and fallback != requested and self.fits(fallback, metrics) is True:
            return fallback, True
        return requested, False
