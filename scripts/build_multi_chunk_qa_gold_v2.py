"""Build FR-10 multi-evidence gold v2 from Round 0A seed (offline asset only)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "docs/3_待办清单/chunk-foundation-round0a/multi_chunk_qa_gold.json"
AUDIT = ROOT / "docs/3_待办清单/chunk-foundation-round0a/chunk_health_audit.json"
OUT = ROOT / "docs/3_待办清单/chunk-foundation-parallel-prep/multi_chunk_qa_gold_v2.json"

SNAPSHOT = "19421c7f8fb3124f"
SERVER = "StampServer用户手册_Rocky9 .docx"
WEBRTC = "StampWebRTC用户手册.docx"
TOOLS = "StampTools用户手册.docx"
PDF = "2ca727efa70847b49f0f67528544d210.pdf"
PDF_ALT = "41bab783fa8840fd8c24a34840a4484d.pdf"

# category quotas from PRD FR-10
QUOTAS = {
    "fact": 30,
    "procedure": 30,
    "cross_section": 20,
    "table": 10,
    "ocr": 10,
    "conflict": 10,
    "none": 10,
}

SEED_CATEGORY = {
    "mq-001": "fact",
    "mq-002": "procedure",
    "mq-003": "procedure",
    "mq-004": "conflict",
    "mq-005": "cross_section",
    "mq-006": "cross_section",
    "mq-007": "fact",
    "mq-008": "cross_section",
    "mq-009": "procedure",
    "mq-010": "procedure",
    "mq-011": "procedure",
    "mq-012": "procedure",
    "mq-013": "procedure",
    "mq-014": "fact",
    "mq-015": "cross_section",
    "mq-016": "fact",
    "mq-017": "procedure",
    "mq-018": "fact",
    "mq-019": "procedure",
    "mq-020": "fact",
    "mq-021": "fact",
    "mq-022": "fact",
    "mq-023": "procedure",
    "mq-024": "fact",
    "mq-025": "procedure",
    "mq-026": "ocr",
    "mq-027": "none",
    "mq-028": "procedure",
    "mq-029": "cross_section",
    "mq-030": "procedure",
}


def _item(
    id_: str,
    category: str,
    question: str,
    answerability: str,
    ground_truth: str,
    required_facts: list[str],
    source: str,
    section_path_contains: str = "",
    forbidden_claims: list[str] | None = None,
    notes: str = "",
    pending_media: bool = False,
) -> dict:
    row = {
        "id": id_,
        "category": category,
        "question": question,
        "answerability": answerability,
        "ground_truth": ground_truth,
        "required_facts": required_facts,
        "evidence_anchors": [{"source": source, "section_path_contains": section_path_contains}],
        "required_section_ids": [],
        "forbidden_claims": forbidden_claims or [],
        "source_snapshot_hash": SNAPSHOT,
        "notes": notes,
    }
    if pending_media:
        row["pending_media"] = True
    return row


def _extra_items() -> list[dict]:
    """Fill remaining quotas after seed remapping."""
    items: list[dict] = []
    n = 31

    def nid() -> str:
        nonlocal n
        out = f"mq-{n:03d}"
        n += 1
        return out

    # ---- fact fillers ----
    fact_bank = [
        ("StampServer 分区表中 /boot 的建议大小是多少？", "full", "约 1G 或手册给出的 /boot 容量", ["/boot", "容量"], SERVER, "分区"),
        ("Rocky9 安装时 root 密码设置要求是什么？", "full", "按手册设置复杂 root 密码并记录。", ["root", "密码"], SERVER, "root"),
        ("sshd_config 中 PermitRootLogin 应如何设置？", "full", "允许 root 密码登录的相关项按手册开启。", ["PermitRootLogin", "sshd_config"], SERVER, "SSH"),
        ("sysctl 中需放开的内核参数示例有哪些？", "full", "如 fs.file-max、net.core 等手册列出的项。", ["sysctl", "file-max 或 net.core"], SERVER, "sysctl"),
        ("StampServer HTTPS 证书默认放置路径在哪里？", "full", "按手册给出的证书目录配置。", ["证书", "路径"], SERVER, "HTTPS"),
        ("Turnserver listening-port 默认值是多少？", "full", "按手册正文/表格给出的 listening-port。", ["listening-port"], WEBRTC, "Turnserver"),
        ("WebRTC 内网部署与外网部署的主要区分点是什么？", "full", "是否需要公网 Turn/信令与外网地址。", ["内网", "外网"], WEBRTC, "部署"),
        ("StampTools 材质压缩支持哪些常见格式？", "full", "包含 JPG/WebP/CRN/DDS 等。", ["JPG", "WebP"], TOOLS, "材质"),
        ("PipelineBuilder 导出字段映射文件的扩展名或用途是什么？", "full", "导出为字段映射文件供管线复用。", ["字段映射", "导出"], TOOLS, "字段映射"),
        ("加密锁检测失败时应先检查什么？", "full", "驱动安装与授权状态。", ["加密锁", "驱动或授权"], TOOLS, "加密锁"),
        ("StampServer 防火墙放行的关键端口类型有哪些？", "full", "HTTP/HTTPS 及业务端口按手册清单。", ["防火墙", "端口"], SERVER, "防火墙"),
        ("PCIE/M.2 与 GPU 配置说明在手册哪一类要求中？", "full", "硬件/服务器配置要求章节。", ["PCIE 或 M.2", "GPU"], SERVER, "硬件"),
        ("Cesium 相关配置关键字 EPSG 代表什么？", "full", "空间参考/坐标系代码。", ["EPSG", "空间参考或坐标系"], TOOLS, ""),
        ("WebRTC 渲染服务依赖的关键硬件是什么？", "full", "GPU/显卡与驱动。", ["GPU 或显卡", "渲染"], WEBRTC, "渲染"),
        ("df -h 用于检查什么？", "full", "磁盘挂载与使用率。", ["df -h", "挂载或磁盘"], SERVER, ""),
        ("systemctl status 用于确认什么？", "full", "服务是否处于 active/running。", ["systemctl status"], SERVER, ""),
        ("StampTools WidgetBuilder 发布目标是什么？", "full", "发布到配置的目标环境/服务。", ["发布", "WidgetBuilder"], TOOLS, "发布"),
        ("Turnserver realm 配置项的作用是什么？", "full", "设定认证域/realm。", ["realm"], WEBRTC, "Turnserver"),
        ("Rocky 安装介质 baseurl 示例指向哪里？", "full", "file:///media/cdrom/... 类本地介质路径。", ["baseurl", "cdrom"], SERVER, ""),
        ("StampServer 数据目录通常挂载在哪个路径？", "full", "/data", ["/data"], SERVER, "分区"),
        ("/boot/efi 文件系统类型一般是什么？", "full", "FAT32", ["FAT32", "/boot/efi"], SERVER, "boot"),
        ("WebRTC 信令服务 TLS 相关配置关键字有哪些？", "full", "证书/密钥路径与监听端口。", ["TLS", "证书或密钥"], WEBRTC, "信令"),
        ("StampTools 管线预览在导出前的作用？", "full", "预览映射结果再导出。", ["预览", "导出"], TOOLS, "映射"),
        ("chmod 在安装脚本场景中通常用于什么？", "full", "赋予脚本可执行权限。", ["chmod"], SERVER, ""),
        ("pm2 或类似进程管理在文档中用于什么？", "full", "保活/管理前端或业务进程（以手册为准）。", ["进程", "启动或保活"], WEBRTC, ""),
    ]
    for q, _ab, gt, facts, src, sec in fact_bank:
        items.append(_item(nid(), "fact", q, "full", gt, facts, src, sec, notes="v2 fact filler"))

    # ---- procedure fillers ----
    proc_bank = [
        ("请按顺序说明 Rocky9 分区创建到挂载检查的步骤。", ["分区创建", "文件系统", "挂载检查"], SERVER, "分区", "完整分区流程"),
        ("说明从修改 sshd_config 到验证 SSH 登录的步骤。", ["sshd_config", "restart sshd", "登录验证"], SERVER, "SSH", ""),
        ("说明 sysctl 修改并生效的完整命令序列。", ["vim /etc/sysctl.conf", "sysctl -p"], SERVER, "sysctl", ""),
        ("WebRTC UDP 外网：从 Turnserver 到信令修改的步骤。", ["Turnserver", "信令", "UDP"], WEBRTC, "UDP", ""),
        ("WebRTC TCP 外网：列出关键配置修改顺序。", ["TCP 外网", "信令", "应用地址"], WEBRTC, "TCP", "完整流程如下"),
        ("Turnserver 从编辑配置到重启服务的步骤。", ["编辑配置", "user=", "重启"], WEBRTC, "Turnserver", ""),
        ("StampTools 加密锁驱动安装到授权检测通过的步骤。", ["安装驱动", "运行环境", "授权检测"], TOOLS, "加密锁", ""),
        ("PipelineBuilder 字段映射配置到导出的步骤。", ["配置映射", "预览", "导出"], TOOLS, "字段映射", ""),
        ("值域/状态映射配置到发布前检查的步骤。", ["状态映射", "预览", "发布前检查"], TOOLS, "值域", ""),
        ("StampServer 基础软件安装后的服务启动顺序。", ["安装", "systemctl start 或 enable"], SERVER, "服务", ""),
        ("从 umount 到执行 xfs_repair 的安全步骤。", ["umount", "xfs_repair"], SERVER, "xfs_repair", ""),
        ("外网部署后渲染服务器启动前检查清单。", ["GPU或驱动", "端口或日志", "启动"], WEBRTC, "渲染", ""),
        ("应用外网地址修改后需要验证什么。", ["外网地址", "访问验证"], WEBRTC, "应用外网", ""),
        ("自动登录配置后的验证步骤。", ["自动登录", "重启验证"], SERVER, "登录", ""),
        ("上传速度优化的配置与验证步骤。", ["上传速度优化", "验证"], SERVER, "上传", ""),
        ("HTTPS 证书替换后的服务重载步骤。", ["证书", "重载或重启"], SERVER, "HTTPS", ""),
        ("StampTools 纹理错误排查的有序检查步骤。", ["材质参数", "压缩格式", "资源路径"], TOOLS, "纹理", ""),
        ("WidgetBuilder 发布前确认与发布步骤。", ["确认配置", "发布"], TOOLS, "发布", ""),
        ("防火墙开放端口后的验证步骤。", ["firewall", "端口", "验证"], SERVER, "防火墙", ""),
        ("内网 WebRTC 部署关键步骤概览。", ["内网部署", "关键服务"], WEBRTC, "内网", ""),
        ("Rocky 安装介质挂载与 repo 配置步骤。", ["挂载", "baseurl 或 repo"], SERVER, "安装", ""),
        ("StampServer 目录权限与属主设置步骤。", ["chown 或 chmod", "目录"], SERVER, "", ""),
        ("信令服务证书与密钥配置步骤。", ["证书", "密钥", "信令"], WEBRTC, "信令", ""),
        ("从创建 /data 到写入业务数据目录的步骤。", ["/data", "挂载或权限"], SERVER, "data", ""),
        ("Turnserver 用户凭证轮换步骤。", ["user=", "重启"], WEBRTC, "Turnserver", ""),
    ]
    for q, facts, src, sec, forbid in proc_bank:
        forbidden = [forbid] if forbid else []
        items.append(
            _item(
                nid(),
                "procedure",
                q,
                "full",
                "按手册顺序完成所列关键步骤，证据不足时不得宣称完整。",
                facts,
                src,
                sec,
                forbidden_claims=forbidden,
                notes="v2 procedure filler",
            )
        )

    # ---- cross_section ----
    cross_bank = [
        ("对比 UDP 与 TCP 外网部署所需修改的服务集合差异。", ["UDP", "TCP", "Turnserver 或信令"], WEBRTC, "外网"),
        ("安装流程中分区规划与 SSH  hardening 如何衔接？", ["分区", "SSH"], SERVER, "安装"),
        ("Turnserver 配置与信令 TLS 配置如何配合？", ["Turnserver", "信令", "TLS"], WEBRTC, ""),
        ("字段映射与值域映射在管线中的前后关系？", ["字段映射", "值域映射"], TOOLS, "映射"),
        ("硬件要求、系统安装与服务启动三段如何串联？", ["硬件", "安装", "systemctl"], SERVER, ""),
        ("外网地址修改与渲染启动的依赖关系？", ["外网地址", "渲染"], WEBRTC, ""),
        ("Redis/Cesium 配置与发布管线是否同属一章？如何串联？", ["Redis", "Cesium 或发布"], TOOLS, ""),
        ("sysctl 调优与上传速度优化是否属于同一配置阶段？", ["sysctl", "上传"], SERVER, ""),
        ("防火墙规则与 HTTPS 端口开放如何一起说明？", ["防火墙", "443 或 HTTPS"], SERVER, ""),
        ("加密锁、运行环境与 PipelineBuilder 启动顺序？", ["加密锁", "PipelineBuilder"], TOOLS, ""),
        ("内网部署完成后切到外网部署时要额外改哪些？", ["内网", "外网", "Turnserver"], WEBRTC, ""),
        ("/var 与 /data 用途差异及对后续服务的影响？", ["/var", "/data"], SERVER, "分区"),
        ("材质格式选择对纹理错误排查的影响？", ["格式", "纹理错误"], TOOLS, ""),
        ("自动登录与 SSH root 登录分别解决什么问题？", ["自动登录", "SSH"], SERVER, "登录"),
        ("端口表与正文配置项应如何交叉核对？", ["端口表", "配置项"], WEBRTC, "端口"),
        ("WidgetBuilder 发布与服务端口配置的关联？", ["发布", "端口"], TOOLS, "发布"),
        ("xfs_repair 场景与日常挂载检查的关系？", ["xfs_repair", "mount 或 df"], SERVER, ""),
        ("GPU 检查与 WebRTC 渲染服务章节如何对应？", ["GPU", "渲染"], WEBRTC, "渲染"),
        ("EPSG 空间参考与 Cesium/WebGL 渲染配置的关联？", ["EPSG", "Cesium 或 WebGL"], TOOLS, ""),
        ("基础软件安装与 systemctl enable 的先后？", ["基础软件", "systemctl enable"], SERVER, "服务"),
    ]
    for q, facts, src, sec in cross_bank:
        items.append(
            _item(nid(), "cross_section", q, "full", "需综合至少两个相关章节回答。", facts, src, sec, notes="v2 cross_section")
        )

    # ---- table ----
    table_bank = [
        ("端口表中列出的 TLS/TCP 相关端口有哪些？", ["端口", "TLS 或 TCP"], WEBRTC, "端口"),
        ("分区容量表中 /boot、/var、/data 的推荐值？", ["/boot", "/var", "/data"], SERVER, "分区"),
        ("Turnserver 配置表或列表中的关键键有哪些？", ["listening-port 或 realm", "user"], WEBRTC, "Turnserver"),
        ("StampTools 支持格式表是否包含 CRN/DDS？", ["CRN", "DDS"], TOOLS, ""),
        ("防火墙或端口对照表中 443 的含义？", ["443", "HTTPS"], SERVER, ""),
        ("硬件配置表中内存/GPU 最低要求？", ["内存或 GPU", "要求"], SERVER, "硬件"),
        ("值域映射表状态列如何理解？", ["状态", "映射"], TOOLS, "值域"),
        ("WebRTC 端口映射示例 89:8450 在表中表示什么？", ["89", "8450"], WEBRTC, ""),
        ("服务端口一览中 StampManager 相关端口？", ["8080 或 StampManager", "端口"], SERVER, ""),
        ("表格中 tls-listening-port 与正文是否一致？", ["tls-listening-port", "表格"], WEBRTC, "Turnserver"),
    ]
    for q, facts, src, sec in table_bank:
        items.append(
            _item(
                nid(),
                "table",
                q,
                "full",
                "以表格内容为主回答，并保留父级章节上下文。",
                facts,
                src,
                sec,
                notes="v2 table",
            )
        )

    # ---- ocr / pending_media ----
    ocr_bank = [
        ("截图中“点击发布”按钮在界面何处？缺少图片时应如何回答？", TOOLS, "发布"),
        ("安装向导截图中下一步按钮文案是什么？无 OCR 时如何处理？", SERVER, "安装"),
        ("Turnserver 配置界面截图展示了哪些字段？", WEBRTC, "Turnserver"),
        ("PipelineBuilder 字段映射界面有哪些列？无图时能否编造？", TOOLS, "字段映射"),
        ("自动登录设置截图中的选项名称是什么？", SERVER, "登录"),
        ("加密锁检测成功截图的判定标志是什么？", TOOLS, "加密锁"),
        ("渲染服务启动成功截图应显示什么状态？", WEBRTC, "渲染"),
        ("防火墙放行截图中的端口列表是什么？", SERVER, "防火墙"),
        ("WidgetBuilder 发布成功提示文案是什么？", TOOLS, "发布"),
        ("UDP 外网配置相关截图包含哪些输入框？", WEBRTC, "UDP"),
    ]
    for q, src, sec in ocr_bank:
        items.append(
            _item(
                nid(),
                "ocr",
                q,
                "partial",
                "Round 0D 前截图/OCR 证据可能缺失；应声明证据不足，不得编造界面细节。",
                ["证据不足或需截图/OCR"],
                src,
                sec,
                forbidden_claims=["完整界面操作细节（无图时）"],
                notes="v2 ocr pending_media",
                pending_media=True,
            )
        )

    # ---- conflict ----
    conflict_bank = [
        (
            "Turnserver TLS 端口在端口表与正文是否一致？出现 5439 与 5349 时应如何回答？",
            ["5439", "5349", "冲突提示"],
            WEBRTC,
            "Turnserver",
            ["仅存在唯一端口"],
        ),
        (
            "文档中 localhost 相关端口出现多个取值时如何处理？",
            ["多个端口值", "冲突提示"],
            SERVER,
            "",
            ["只能使用其中一个端口"],
        ),
        (
            "baseurl 同时出现 AppStream 与 BaseOS 路径时是否算冲突？如何表述？",
            ["AppStream", "BaseOS", "同时说明"],
            SERVER,
            "",
            ["只能选一个 baseurl 且不说明来源"],
        ),
        (
            "若表格写 5439、配置示例写 5349，能否只答其中一个？",
            ["5439", "5349", "不得静默二选一"],
            WEBRTC,
            "端口",
            ["确定端口就是"],
        ),
        (
            "同一配置键在 PDF 与 DOCX 中取值不同时应如何回答？",
            ["多来源", "冲突或核对"],
            PDF,
            "",
            ["以某一文档为准且不提示冲突"],
        ),
        (
            "listening-port 与 tls-listening-port 数值接近时如何避免混淆？",
            ["listening-port", "tls-listening-port", "分别引用"],
            WEBRTC,
            "Turnserver",
            ["这两个端口相同"],
        ),
        (
            "防火墙开放列表与正文示例端口不一致时怎么答？",
            ["防火墙", "正文端口", "冲突提示"],
            SERVER,
            "防火墙",
            ["随便选一个端口"],
        ),
        (
            "HttpsPort=443 与其他端口示例并存时如何说明适用范围？",
            ["443", "适用范围或来源"],
            SERVER,
            "HTTPS",
            ["所有环境都必须 443 且无依据"],
        ),
        (
            "Turnserver user 凭证示例出现多组时如何处理？",
            ["多组凭证示例", "标明示例"],
            WEBRTC,
            "Turnserver",
            ["真实生产密码就是手册示例"],
        ),
        (
            "StampManager 相关 8080 路径有多条时如何并列说明？",
            ["8080", "多路径", "并列"],
            SERVER,
            "",
            ["只有唯一路径"],
        ),
    ]
    for q, facts, src, sec, forbid in conflict_bank:
        items.append(
            _item(
                nid(),
                "conflict",
                q,
                "conflict",
                "必须同时展示冲突取值与来源，禁止静默二选一。",
                facts,
                src,
                sec,
                forbidden_claims=forbid,
                notes="v2 conflict",
            )
        )

    # ---- none / insufficient ----
    none_bank = [
        ("StampGIS 火星坐标系私有秘钥算法细节是什么？", "知识库未覆盖该机密细节时应拒答。", ["未查询到或无法提供"], TOOLS, "", ["给出具体密钥算法实现"]),
        ("当前 PDF 手册完整 section_path 树是什么？", "PDF 结构不足时应承认无法稳定恢复。", ["section_path 缺失或无法稳定恢复"], PDF, "", ["伪造的详细 section_path"]),
        ("未收录产品 FooBarX 的安装端口是多少？", "知识库无该产品时应拒答。", ["未查询到"], SERVER, "", ["编造端口"]),
        ("2027 年版本的 WebRTC 默认端口规划是什么？", "超出语料时间范围应拒答。", ["未查询到"], WEBRTC, "", ["给出未来版本端口"]),
        ("手册未附图时，界面像素级点击坐标是什么？", "无图证据应拒答细节。", ["证据不足"], TOOLS, "发布", ["给出像素坐标"]),
        ("内部未公开的授权服务器私钥路径？", "机密未入库应拒答。", ["未查询到"], SERVER, "", ["编造私钥路径"]),
        ("与本知识库无关的 Kubernetes Operator CRD 字段？", "主体无关应拒答。", ["未查询到"], SERVER, "", ["编造 CRD 字段"]),
        ("仅有页眉页脚的 PDF 封面页的业务配置？", "无业务内容应说明不足。", ["无有效业务内容或未查询到"], PDF_ALT, "", ["编造配置"]),
        ("文档未提到的第三方 CDN 密钥？", "未收录应拒答。", ["未查询到"], WEBRTC, "", ["编造 CDN 密钥"]),
        ("知识库快照之外的口头传达安装步骤？", "无来源应拒答。", ["未查询到"], SERVER, "", ["当作手册正文回答"]),
    ]
    for q, gt, facts, src, sec, forbid in none_bank:
        items.append(
            _item(
                nid(),
                "none",
                q,
                "none",
                gt,
                facts,
                src,
                sec,
                forbidden_claims=forbid,
                notes="v2 none/insufficient",
            )
        )

    return items


def main() -> None:
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    if AUDIT.exists():
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        snapshot = str(audit.get("corpus_snapshot_hash") or SNAPSHOT)
    else:
        snapshot = SNAPSHOT

    upgraded: list[dict] = []
    for row in seed:
        item = dict(row)
        item["category"] = SEED_CATEGORY.get(item["id"], "fact")
        item["source_snapshot_hash"] = snapshot
        if item["id"] == "mq-026":
            item["pending_media"] = True
        upgraded.append(item)

    extras = _extra_items()
    for item in extras:
        item["source_snapshot_hash"] = snapshot

    all_items = upgraded + extras

    # Trim each category to exact quota (seed first, then extras in order)
    by_cat: dict[str, list[dict]] = {k: [] for k in QUOTAS}
    for item in all_items:
        cat = item["category"]
        if cat not in by_cat:
            continue
        if len(by_cat[cat]) < QUOTAS[cat]:
            by_cat[cat].append(item)

    # If short, fail loudly
    missing = {k: QUOTAS[k] - len(v) for k, v in by_cat.items() if len(v) < QUOTAS[k]}
    if missing:
        raise SystemExit(f"quota shortfall: {missing}")

    final: list[dict] = []
    for cat in ("fact", "procedure", "cross_section", "table", "ocr", "conflict", "none"):
        final.extend(by_cat[cat])

    # Stable renumber while keeping original seed ids
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {k: len(v) for k, v in by_cat.items()}
    print(f"wrote {OUT} total={len(final)} counts={counts}")


if __name__ == "__main__":
    main()
