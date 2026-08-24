#!/usr/bin/env python3
"""Freeze the source-verified FR-10 v4 retrieval set from its 90-item review ledger."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
CANDIDATE = BASE / "multi_chunk_qa_gold_v4_retrieval_candidate.json"
CANDIDATE_MANIFEST = BASE / "multi_chunk_qa_gold_v4.candidate.manifest.json"
FINAL = BASE / "multi_chunk_qa_gold_v4.json"
LEDGER = BASE / "multi_chunk_qa_gold_v4.review_ledger.json"
MANIFEST = BASE / "multi_chunk_qa_gold_v4.manifest.json"

APPROVED_IDS = {
    "mq-001", "mq-009", "mq-010", "mq-017", "mq-033", "mq-034", "mq-039", "mq-040", "mq-048", "mq-050",
}

REJECTED_IDS = {
    "mq-003", "mq-020", "mq-022", "mq-023", "mq-030", "mq-032", "mq-037", "mq-038", "mq-045", "mq-046",
    "mq-047", "mq-051", "mq-056", "mq-057", "mq-058", "mq-061", "mq-062", "mq-063", "mq-064", "mq-066",
    "mq-069", "mq-071", "mq-104", "mq-107", "mq-110",
    "mq-005", "mq-006", "mq-008", "mq-015", "mq-029", "mq-081", "mq-082", "mq-083", "mq-084", "mq-085",
    "mq-086", "mq-087", "mq-088", "mq-089", "mq-090", "mq-091", "mq-092", "mq-093", "mq-094", "mq-095",
}

REJECTION_REASONS = {
    "mq-003": "题干要求完整安装流程，锚点不能逐项支持其完整性主张。",
    "mq-020": "当前锚点是运维管理网页登录，不包含自动登录配置。",
    "mq-022": "指定的 StampWebRTC 锚点不含 89:8450；题干和标准答案均不对应当前语料。",
    "mq-023": "题干为 WidgetBuilder，锚点实际是 PipelineBuilder 编译发布。",
    "mq-030": "这是证据不足行为断言，属于回答治理而非文本检索事实题。",
    "mq-032": "原文不支持“复杂密码并记录”的标准答案。",
    "mq-037": "内外网差异被概括为未在锚点中定义的 Turn/信令关系。",
    "mq-038": "与 mq-014 重复，且其“材质”锚点只支持 JPG/PNG。",
    "mq-045": "当前锚点没有 df -h 的原文证据。",
    "mq-046": "当前语料没有 systemctl status 或 active/running 的原文证据。",
    "mq-047": "题干为 WidgetBuilder，锚点实际是 PipelineBuilder。",
    "mq-051": "原文仅给出 /boot/efi 容量，未给出 FAT32。",
    "mq-056": "单一分区段落不能支持“分区创建到挂载检查”的完整流程。",
    "mq-057": "与 mq-009 重复，且原文未给出登录验证步骤。",
    "mq-058": "与 mq-017 重复。",
    "mq-061": "与 mq-025、mq-059 重叠，且锚点未给出重启步骤。",
    "mq-062": "与 mq-012 重复。",
    "mq-063": "与 mq-010 重复，且原文未给出预览步骤。",
    "mq-064": "原文未给出预览或发布前检查步骤。",
    "mq-066": "与 mq-001 重复。",
    "mq-069": "当前锚点是运维管理网页登录，不包含自动登录验证。",
    "mq-071": "当前锚点不能支持证书替换后的完整重载流程。",
    "mq-104": "与 mq-014 重复。",
    "mq-107": "当前原文没有题干所称的状态列定义。",
    "mq-110": "这是端口冲突的回答治理题，不属于 FR-10 文本检索门禁。",
}

for _item_id in {
    "mq-005", "mq-006", "mq-008", "mq-015", "mq-029", "mq-081", "mq-082", "mq-083", "mq-084", "mq-085",
    "mq-086", "mq-087", "mq-088", "mq-089", "mq-090", "mq-091", "mq-092", "mq-093", "mq-094", "mq-095",
}:
    REJECTION_REASONS[_item_id] = "跨章节关系尚未由可核验证据链或已确认图谱关系支持；不纳入当前文本检索门禁。"

REVISIONS: dict[str, dict[str, Any]] = {
    "mq-002": {
        "ground_truth": "文档分别给出 UDP 与 TCP 外网部署：两者均从 /etc/coturn/turnserver.conf 配置开始；TCP 方式额外设置 no-udp 与 no-dtls；应用侧修改 SERVERIP 后运行渲染脚本。",
        "required_facts": ["/etc/coturn/turnserver.conf", "no-udp", "no-dtls", "SERVERIP"],
    },
    "mq-007": {"ground_truth": "文档将 /boot/efi 分区大小写为 200M。", "required_facts": ["/boot/efi", "200M"]},
    "mq-011": {
        "question": "PipelineBuilder 值域映射中的用户定义值、显示值和标准值如何关联？",
        "ground_truth": "用户定义值来自字段值域；显示值默认等于用户定义值但可自定义；标准值把用户定义值映射为标准附属设施并影响生成。",
        "required_facts": ["用户定义值", "显示值", "标准值"],
    },
    "mq-012": {
        "ground_truth": "先安装 Microsoft Visual C++ 2015-2019 运行环境，再安装 GrandDogRunTimeSystemSetup.exe 授权驱动；编译发布时会执行加密锁检测。",
        "required_facts": ["Microsoft Visual C++ 2015-2019", "GrandDogRunTimeSystemSetup.exe", "加密检测"],
    },
    "mq-013": {
        "question": "ModelBuilder 文档列出的两类纹理错误分别是什么？",
        "ground_truth": "一类是缺失纹理，另一类是纹理尺寸大于设定尺寸导致的压缩问题。",
        "required_facts": ["缺失纹理", "纹理压缩", "设定尺寸"],
    },
    "mq-014": {
        "ground_truth": "纹理格式清单包含 JPG、WebP、CRN 和 DDS。",
        "required_facts": ["JPG", "WebP", "CRN", "DDS"],
        "evidence_anchors": [{"source": "StampTools用户手册.docx", "section_path_contains": "工具概述"}],
    },
    "mq-016": {
        "question": "Nginx HTTPS 配置示例的 listen 端口是多少？",
        "ground_truth": "示例配置为 listen 443 ssl。",
        "required_facts": ["listen 443 ssl"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_id": "sec_f479010826b1c073", "section_path_contains": "HTTPS配置 > 私有CA配置 > Nginx使用CA证书"}],
    },
    "mq-018": {
        "question": "WebGL 外网部署如何修改应用访问地址？",
        "ground_truth": "编辑 /data/html/StampWebGL/config/custom_config.js，将 SERVERIP 修改为外网地址。",
        "required_facts": ["custom_config.js", "SERVERIP", "外网地址"],
    },
    "mq-019": {
        "question": "WebRTC 渲染服务器启动前，文档列出的运行环境和启动方式是什么？",
        "ground_truth": "运行环境包括至少 48G 内存和 8G 显存；禁用集成显卡后，可运行 run_local_1.bat、run_local_2.bat 或 run_local_3.bat。",
        "required_facts": ["内存：48G及以上", "显卡：8G及以上显存", "run_local_1.bat"],
    },
    "mq-021": {
        "ground_truth": "在 sshd_config 中启用 GSSAPIAuthentication no、GSSAPICleanupCredentials no 和 UseDNS no，然后重启 sshd。",
        "required_facts": ["GSSAPIAuthentication no", "GSSAPICleanupCredentials no", "UseDNS no", "systemctl restart sshd.service"],
    },
    "mq-024": {
        "question": "修改 fstab 后如何重新挂载 /data？",
        "ground_truth": "先执行 systemctl daemon-reload，再执行 mount -o remount /data。",
        "required_facts": ["systemctl daemon-reload", "mount -o remount /data"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_path_contains": "fstab设置"}],
    },
    "mq-025": {
        "ground_truth": "在 turnserver.conf 中配置 user=<用户名>:<密码> 和 realm=<认证域>。",
        "required_facts": ["user=", "realm="],
    },
    "mq-028": {
        "question": "修改 httpd 服务路径后，文档要求执行哪些 systemctl 命令？",
        "ground_truth": "依次执行 systemctl daemon-reload、systemctl enable httpd 和 systemctl start httpd。",
        "required_facts": ["systemctl daemon-reload", "systemctl enable httpd", "systemctl start httpd"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_path_contains": "服务路径设置"}],
    },
    "mq-031": {"ground_truth": "文档将 /boot 分区大小写为 1024M。", "required_facts": ["/boot", "1024M"]},
    "mq-035": {
        "question": "Nginx 使用 CA 证书时，文档中的证书和私钥路径是什么？",
        "ground_truth": "证书路径为 /etc/nginx/ssl/stamp.crt，私钥路径为 /etc/nginx/ssl/stamp.key。",
        "required_facts": ["/etc/nginx/ssl/stamp.crt", "/etc/nginx/ssl/stamp.key"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_id": "sec_f479010826b1c073", "section_path_contains": "HTTPS配置 > 私有CA配置 > Nginx使用CA证书"}],
    },
    "mq-036": {"ground_truth": "UDP 外网部署示例将 Turnserver 的 listening-port 设置为 3478。", "required_facts": ["listening-port=3478"]},
    "mq-041": {
        "question": "手册中的系统基础防火墙设置如何处理 firewalld 和 SELinux？",
        "ground_truth": "文档要求停止并禁用 firewalld，并将 SELINUX=enforcing 改为 SELINUX=disabled。",
        "required_facts": ["systemctl stop firewalld", "systemctl disable firewalld", "SELINUX=disabled"],
    },
    "mq-042": {
        "question": "创建虚拟机时，手册对固态硬盘接口和顺序读写性能有什么建议？",
        "ground_truth": "建议使用支持 PCIE3.0 或 PCIE4.0 的 M.2 或 U.2 固态硬盘，顺序读写速度 3500M/S 以上，最佳 7000M/S。",
        "required_facts": ["PCIE3.0或PCIE4.0", "M.2或U.2", "3500M/S"],
    },
    "mq-043": {
        "question": "文档中 ENU、EPSG 投影的原点和偏移值在哪里设置？",
        "ground_truth": "ENU、EPSG 投影的原点经纬度坐标和平面坐标偏移值可在对应界面设置。",
        "required_facts": ["ENU", "EPSG", "原点经纬度坐标", "平面坐标偏移值"],
    },
    "mq-044": {
        "question": "WebRTC 渲染服务器的文档化运行环境要求是什么？",
        "ground_truth": "单用户访问要求至少 48G 内存、8G 显存、12 核 CPU 和 50M 带宽。",
        "required_facts": ["内存：48G及以上", "显卡：8G及以上显存", "CPU：12核及以上", "带宽：50M及以上"],
    },
    "mq-049": {
        "ground_truth": "BaseOS 和 AppStream 的 baseurl 分别指向 file:///media/cdrom/BaseOS 与 file:///media/cdrom/AppStream。",
        "required_facts": ["baseurl=file:///media/cdrom/BaseOS", "baseurl=file:///media/cdrom/AppStream"],
    },
    "mq-059": {
        "ground_truth": "UDP 外网部署先编辑 /etc/coturn/turnserver.conf，再配置 listening-port、tls-listening-port、external-ip、长期凭证、user 和 realm，随后按文档继续处理信令服务。",
        "required_facts": ["/etc/coturn/turnserver.conf", "listening-port=3478", "tls-listening-port=5349", "external-ip", "realm="],
    },
    "mq-060": {
        "question": "WebRTC TCP 外网部署中 Turnserver 的关键改动是什么？",
        "ground_truth": "在 turnserver.conf 中配置监听和 external-ip，并设置 no-udp 与 no-dtls 以强制通过 TCP 推流。",
        "required_facts": ["listening-port=3478", "external-ip", "no-udp", "no-dtls"],
    },
    "mq-065": {
        "question": "配置 httpd 服务路径后，文档给出的服务启用顺序是什么？",
        "ground_truth": "执行 systemctl daemon-reload、systemctl enable httpd、systemctl start httpd。",
        "required_facts": ["systemctl daemon-reload", "systemctl enable httpd", "systemctl start httpd"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_path_contains": "服务路径设置"}],
    },
    "mq-067": {
        "question": "启动 WebRTC 渲染服务前，文档列出的硬件条件和启动脚本是什么？",
        "ground_truth": "单用户运行环境要求至少 48G 内存和 8G 显存；启动脚本为 run_local_1.bat、run_local_2.bat 或 run_local_3.bat。",
        "required_facts": ["内存：48G及以上", "显卡：8G及以上显存", "run_local_1.bat"],
    },
    "mq-068": {
        "question": "修改 WebRTC 应用的外网地址后，文档要求如何继续启动和访问？",
        "ground_truth": "将 stamp_core_config.js 中的 SERVERIP 修改为外网 IP，运行 run_local_1.bat、run_local_2.bat 或 run_local_3.bat 后访问 StampWebRTC。",
        "required_facts": ["stamp_core_config.js", "SERVERIP", "run_local_1.bat", "访问StampWebRTC"],
    },
    "mq-070": {
        "ground_truth": "在 sshd_config 中启用 GSSAPIAuthentication no、GSSAPICleanupCredentials no 和 UseDNS no，再重启 sshd 服务。",
        "required_facts": ["GSSAPIAuthentication no", "GSSAPICleanupCredentials no", "UseDNS no", "systemctl restart sshd.service"],
    },
    "mq-072": {
        "question": "ModelBuilder 文档如何解释缺失纹理和纹理压缩两类错误？",
        "ground_truth": "缺失纹理表示纹理不存在；纹理压缩表示大于设定尺寸的纹理无法被拆分压缩到设定尺寸。",
        "required_facts": ["缺失纹理", "纹理不存在", "纹理压缩", "设定尺寸"],
    },
    "mq-101": {
        "question": "端口一览表中网络穿透服务的 TLS 端口是多少？",
        "ground_truth": "端口一览表将网络穿透服务的 TLS 端口列为 5439。",
        "required_facts": ["网络穿透服务", "TLS端口", "5439"],
    },
    "mq-102": {
        "ground_truth": "/boot 为 1024M，/var 为 102400M（100G）以上，/data 使用剩余空间并选择 ext4。",
        "required_facts": ["/boot", "1024M", "/var", "102400M", "/data", "ext4"],
    },
    "mq-103": {
        "question": "UDP 外网部署的 Turnserver 配置中列出哪些关键键？",
        "ground_truth": "关键键包括 listening-port、tls-listening-port、listening-ip、external-ip、min-port、max-port、user、realm、cert 和 pkey。",
        "required_facts": ["listening-port", "tls-listening-port", "external-ip", "user=", "realm=", "cert=", "pkey="],
    },
    "mq-105": {
        "question": "端口一览表中 443 分别对应什么服务？",
        "ground_truth": "数据服务器的推流 HTTPS 服务和 Windows 渲染服务器的 HttpsPort 均列为 443。",
        "required_facts": ["推流服务", "https服务", "HttpsPort", "443"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_id": "sec_21e6e4593247f060", "section_path_contains": "应用系统部署 > 系统网络拓扑 > 端口说明"}],
    },
    "mq-106": {
        "question": "创建虚拟机时，手册给出的内存最小值和推荐值是什么？",
        "ground_truth": "虚拟机内存最少 8G，推荐 16G 及以上。",
        "required_facts": ["最少8G", "推荐16G及以上"],
    },
    "mq-108": {
        "ground_truth": "89:8450 表示外部端口 89 映射到 HTTPS 端口 8450；文档还要求配置后重启 nginx。",
        "required_facts": ["89：8450", "service nginx restart"],
    },
    "mq-109": {
        "question": "端口一览表中 StampManager 服务端口是多少？",
        "ground_truth": "端口一览表将 StampManager 服务列为 8080。",
        "required_facts": ["StampManager服务", "8080"],
        "evidence_anchors": [{"source": "StampServer用户手册_Rocky9 .docx", "section_id": "sec_21e6e4593247f060", "section_path_contains": "应用系统部署 > 系统网络拓扑 > 端口说明"}],
    },
}


def _payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def review_candidates(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {str(item["id"]): item for item in items}
    if len(items) != 90 or len(by_id) != 90:
        raise ValueError("v4 retrieval candidate must contain 90 unique items")
    all_reviewed = APPROVED_IDS | REJECTED_IDS | set(REVISIONS)
    if all_reviewed != set(by_id):
        raise ValueError(f"review decision coverage mismatch: {sorted(set(by_id) ^ all_reviewed)}")

    ledger = []
    final_items = []
    for item_id, item in by_id.items():
        if item_id in APPROVED_IDS:
            decision, rationale, updates = "approved", "题干、标准答案、必备事实和锚点均由当前语料直接支持。", {}
        elif item_id in REVISIONS:
            decision, rationale, updates = "revised", "原题可由当前锚点支持，但需删除模板化或不受支持的表述。", REVISIONS[item_id]
        else:
            decision, rationale, updates = "rejected", REJECTION_REASONS[item_id], {}
        ledger.append({
            "id": item_id,
            "decision": decision,
            "rationale": rationale,
            "updates": updates,
            "reviewer": "Codex evidence review",
            "reviewed_at": str(date.today()),
        })
        if decision == "rejected":
            continue
        reviewed = copy.deepcopy(item)
        reviewed.update(copy.deepcopy(updates))
        reviewed["review_status"] = "approved"
        reviewed["review_basis"] = "v4 source evidence review"
        final_items.append(reviewed)
    return final_items, ledger


def main() -> int:
    candidates = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    candidate_manifest = json.loads(CANDIDATE_MANIFEST.read_text(encoding="utf-8"))
    final_items, ledger = review_candidates(candidates)
    counts = {"approved": 0, "revised": 0, "rejected": 0}
    for row in ledger:
        counts[row["decision"]] += 1
    if counts != {"approved": 10, "revised": 35, "rejected": 45} or len(final_items) != 45:
        raise SystemExit(f"unexpected review outcome: {counts}, final={len(final_items)}")

    final_payload = _payload(final_items)
    FINAL.write_text(final_payload, encoding="utf-8")
    LEDGER.write_text(_payload({
        "review_version": "fr10-v4-source-review-v1",
        "candidate_gold": CANDIDATE.name,
        "reviewer_note": "Codex evidence review; decisions are traceable to the read-only 2537-chunk audit.",
        "counts": counts,
        "items": ledger,
    }), encoding="utf-8")
    MANIFEST.write_text(_payload({
        "gold_version": "v4",
        "status": "frozen_for_fr10_retrieval",
        "parent_candidate": CANDIDATE.name,
        "candidate_manifest": CANDIDATE_MANIFEST.name,
        "corpus_snapshot_hash": candidate_manifest["corpus_snapshot_hash"],
        "gold_sha256": hashlib.sha256(final_payload.encode("utf-8")).hexdigest(),
        "review_ledger": LEDGER.name,
        "review_counts": counts,
        "retrieval_question_count": len(final_items),
        "category_counts": {
            category: sum(1 for item in final_items if item["category"] == category)
            for category in ("fact", "procedure", "cross_section", "table")
        },
        "excluded_from_v4": {
            "ocr_media": candidate_manifest["excluded_items"]["ids"],
            "cross_section": sorted(item["id"] for item in candidates if item["category"] == "cross_section"),
            "rejected_retrieval": sorted(row["id"] for row in ledger if row["decision"] == "rejected"),
        },
        "comparison_rule": "v4 has a different, source-reviewed denominator and must not be compared directly with v3.2 120-item metrics.",
    }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
