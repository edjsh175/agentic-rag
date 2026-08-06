"""GpuMonitor（gpu-agent 显存监控 + 显存自适应模型选择）单元测试。

不依赖真实 gpu-agent / Ollama：指标抓取用 monkeypatch urlopen，
配置用 RAG_CONFIG 临时 ini，模型选择用 TTL 缓存注入预置指标。
"""
import json
import time
import urllib.request

from rag_knowledge.config import Config
from rag_knowledge.services.gpu_monitor import GpuMonitor
from rag_knowledge.services.rag import RagChain

DEFAULT_METRICS = {
    "name": "RTX 3060",
    "total_mib": 8192,
    "used_mib": 3584,
    "free_mib": 4608,
    "utilization": 10,
    "temperature": 50,
}


def _gpu_cfg(tmp_path, monkeypatch, apply_iso, *,
             enabled="true", fallback="gemma3:4b", margin="0.5",
             poll_ttl="60", vram=None, extra_ini=None):
    """写临时 ini（含 [gpu_agent] + [gpu_agent.model_vram]，模型名含 ':'）并重建 Config。"""
    lines = [
        "[gpu_agent]",
        f"enabled = {enabled}",
        "base_url = http://localhost:11435",
        "timeout = 1",
        f"poll_ttl = {poll_ttl}",
        f"fallback_model = {fallback}",
        f"safety_margin_gib = {margin}",
        "[gpu_agent.model_vram]",
        "gemma3:4b = 4.0",
        "deepseek-r1:14b = 10.0",
    ]
    if vram:
        lines.extend(f"{k} = {v}" for k, v in vram.items())
    if extra_ini:
        lines.extend(extra_ini)
    ini = tmp_path / "gpu.ini"
    ini.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("RAG_CONFIG", str(ini))
    cfg, *_ = apply_iso()
    return cfg


def _fresh_monitor(metrics=None):
    mon = GpuMonitor()
    if metrics is not None:
        mon._metrics = metrics
        mon._fetched_at = time.monotonic()
    return mon


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

def test_gpu_agent_config_parsing(tmp_path, monkeypatch, isolated_storage):
    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    assert cfg.gpu_agent_enabled is True
    assert cfg.gpu_agent_base_url == "http://localhost:11435"
    assert cfg.gpu_agent_fallback_model == "gemma3:4b"
    assert cfg.gpu_agent_safety_margin_gib == 0.5
    # 模型名含 ":"（qwen3.5:9b 等）不应被 ConfigParser 当成分隔符拆开
    assert cfg.gpu_agent_model_vram == {"gemma3:4b": 4.0, "deepseek-r1:14b": 10.0}


def test_gpu_agent_disabled_default(tmp_path, monkeypatch, isolated_storage):
    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage, enabled="false")
    assert cfg.gpu_agent_enabled is False
    assert _fresh_monitor().get_metrics() is None


# ---------------------------------------------------------------------------
# 指标抓取与归一化
# ---------------------------------------------------------------------------

def _patch_urlopen(monkeypatch, payload):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    return _Resp


def test_get_metrics_normalizes_bytes(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    payload = json.dumps([{
        "name": "RTX 3060",
        "memoryTotal": 8 * 1024 * 1024 * 1024,
        "memoryUsed": 3 * 1024 * 1024 * 1024,
        "memoryFree": 5 * 1024 * 1024 * 1024,
        "temperature": 50,
        "utilization": 12,
        "powerDraw": 120,
    }]).encode()
    _patch_urlopen(monkeypatch, payload)
    m = _fresh_monitor().get_metrics()
    assert m is not None
    assert m["total_mib"] == 8192
    assert m["used_mib"] == 3072
    assert m["free_mib"] == 5120
    assert m["utilization"] == 12
    assert m["power_draw"] == 120


def test_get_metrics_mib_passthrough(tmp_path, monkeypatch, isolated_storage):
    # poll_ttl=0 使每次 get_metrics 都重新抓取，避免命中上一次的 TTL 缓存
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage, poll_ttl="0")
    payload = json.dumps([{
        "name": "M2",
        "memoryTotal": 4096,
        "memoryUsed": 1024,
        "memoryFree": 3072,
    }]).encode()
    _patch_urlopen(monkeypatch, payload)
    m = _fresh_monitor().get_metrics()
    assert m["total_mib"] == 4096
    assert m["used_mib"] == 1024
    # memoryFree 缺省时用 total-used 兜底
    payload = json.dumps([{"name": "x", "memoryTotal": 2048, "memoryUsed": 512}]).encode()
    _patch_urlopen(monkeypatch, payload)
    m = _fresh_monitor().get_metrics()
    assert m["free_mib"] == 1536


def test_get_metrics_unavailable(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused")))
    assert _fresh_monitor().get_metrics() is None


# ---------------------------------------------------------------------------
# 显存适配判定
# ---------------------------------------------------------------------------

def test_footprint_external_provider_zero(tmp_path, monkeypatch, isolated_storage):
    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage,
                   extra_ini=["[model.llm]", "provider = google", "model = gemini-3.5-flash"])
    mon = _fresh_monitor()
    assert mon.provider_of("gemini-3.5-flash") == "google"
    assert mon.footprint_gib("gemini-3.5-flash") == 0.0
    assert mon.footprint_gib("gemma3:4b") == 4.0
    assert mon.footprint_gib("unknown:9b") is None  # 未知本地模型，无法判断
    assert mon.fits("gemini-3.5-flash", DEFAULT_METRICS) is True


def test_fits_margin(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage, margin="0.5")
    mon = _fresh_monitor()
    # free 4.5 GiB - 0.5 余量 = 4.0 GiB 可用
    assert mon.fits("gemma3:4b", DEFAULT_METRICS) is True     # 4.0 <= 4.0
    assert mon.fits("deepseek-r1:14b", DEFAULT_METRICS) is False  # 10 > 4.0


# ---------------------------------------------------------------------------
# 推荐 + 自动降级
# ---------------------------------------------------------------------------

def test_resolve_model_downshift_to_fallback(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage, fallback="gemma3:4b")
    mon = _fresh_monitor(DEFAULT_METRICS)  # free 4.5 GiB
    final, downshifted = mon.resolve_model("deepseek-r1:14b")
    assert final == "gemma3:4b"
    assert downshifted is True


def test_resolve_model_keeps_fitting_model(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    mon = _fresh_monitor(DEFAULT_METRICS)
    final, downshifted = mon.resolve_model("gemma3:4b")
    assert final == "gemma3:4b"
    assert downshifted is False


def test_resolve_model_unknown_not_downshifted(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    mon = _fresh_monitor(DEFAULT_METRICS)
    final, downshifted = mon.resolve_model("unknown:9b")
    assert final == "unknown:9b"
    assert downshifted is False


def test_resolve_model_metrics_unavailable_no_downshift(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused")))
    mon = _fresh_monitor()
    final, downshifted = mon.resolve_model("deepseek-r1:14b")
    assert final == "deepseek-r1:14b"
    assert downshifted is False


def test_recommend_model_prefers_preferred_if_fits(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    mon = _fresh_monitor(DEFAULT_METRICS)
    assert mon.recommend_model(["gemma3:4b", "deepseek-r1:14b"], "gemma3:4b") == "gemma3:4b"


def test_recommend_model_picks_largest_fitting(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    mon = _fresh_monitor(DEFAULT_METRICS)
    # 首选 deepseek 不装下 → 选占用最大的可装下者 gemma3:4b
    assert mon.recommend_model(["gemma3:4b", "deepseek-r1:14b"], "deepseek-r1:14b") == "gemma3:4b"


def test_recommend_model_metrics_unavailable_keeps_preferred(tmp_path, monkeypatch, isolated_storage):
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused")))
    mon = _fresh_monitor()
    assert mon.recommend_model(["gemma3:4b", "deepseek-r1:14b"], "deepseek-r1:14b") == "deepseek-r1:14b"


# ---------------------------------------------------------------------------
# RagChain 显存守卫
# ---------------------------------------------------------------------------

def test_apply_vram_guard_downshifts(tmp_path, monkeypatch, isolated_storage):
    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage, fallback="gemma3:4b")
    mon = _fresh_monitor(DEFAULT_METRICS)
    chain = object.__new__(RagChain)
    chain._llm_model = cfg.llm_model
    final, downshifted = chain._apply_vram_guard("deepseek-r1:14b")
    assert final == "gemma3:4b"
    assert downshifted is True
    fields = chain._downshift_fields(downshifted, final)
    assert fields["used_model"] == "gemma3:4b"
    assert "降级" in fields["downshift_notice"]


def test_apply_vram_guard_noop_when_ok(tmp_path, monkeypatch, isolated_storage):
    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    _fresh_monitor(DEFAULT_METRICS)
    chain = object.__new__(RagChain)
    chain._llm_model = cfg.llm_model
    final, downshifted = chain._apply_vram_guard("gemma3:4b")
    assert final == "gemma3:4b"
    assert downshifted is False
    assert chain._downshift_fields(downshifted, final) == {}


def test_apply_vram_guard_stub_without_llm_model(tmp_path, monkeypatch, isolated_storage):
    """object.__new__(RagChain) 测试桩未运行 __init__（无 _llm_model），guard 不应崩溃。"""
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused")))
    chain = object.__new__(RagChain)
    final, downshifted = chain._apply_vram_guard(None)
    assert final is None
    assert downshifted is False
    assert chain._downshift_fields(downshifted, final) == {}


# ---------------------------------------------------------------------------
# /gpu 路由
# ---------------------------------------------------------------------------

def test_gpu_route(tmp_path, monkeypatch, isolated_storage):
    from rag_knowledge.api import routes

    cfg = _gpu_cfg(tmp_path, monkeypatch, isolated_storage)
    monkeypatch.setattr(routes, "_cfg", cfg)

    class _FakeMonitor:
        enabled = True

        def __init__(self):
            pass

        def get_metrics(self):
            return dict(DEFAULT_METRICS)

        def footprint_gib(self, name):
            return 4.0

        def fits(self, name, metrics):
            return True

        def recommend_model(self, candidates, preferred):
            return preferred

    monkeypatch.setattr(routes, "GpuMonitor", _FakeMonitor)

    data = routes.gpu_status()
    assert data["enabled"] is True
    assert data["gpu"]["total_mib"] == 8192
    assert data["current_model"] == cfg.llm_model
    assert data["recommended_model"] == cfg.llm_model
    assert data["fallback_model"] == "gemma3:4b"
    names = [m["name"] for m in data["models"]]
    assert "gemma3:4b" in names and "deepseek-r1:14b" in names
    assert all(m["fits"] is True for m in data["models"])


def test_gpu_monitor_enabled_property_dynamic(tmp_path, monkeypatch, isolated_storage):
    # 测试启用状态下
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage, enabled="true")
    mon = GpuMonitor()
    assert mon.enabled is True

    # 测试动态重载配置文件后，单例属性能够自动同步更新
    _gpu_cfg(tmp_path, monkeypatch, isolated_storage, enabled="false")
    assert mon.enabled is False

