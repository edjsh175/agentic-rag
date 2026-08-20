# FR-10 v4 检索黄金集人工签核清单

- **状态**：`human_signoff_pending`
- **生成日期**：2026-07-21
- **题量**：45
- **技术冻结**：`multi_chunk_qa_gold_v4.json` + Codex evidence review
- **基线报告**：`fr10_live_2537_v4_production_path_post_conflict_planner/fr10_baseline_report.json`

签核人请在 `multi_chunk_qa_gold_v4.human_signoff_ledger.json` 填写 `decision` / `signer` / `signed_at`。

| ID | 分类 | Codex 决定 | FR-10 通过 | 题干摘要 |
|---|---|---|---|---|
| mq-001 | fact | approved | 是 | 当执行 xfs_repair 修复分区时，如果提示文件系统已挂载，需要先执行什么命令？ |
| mq-007 | fact | revised | 是 | /boot/efi 分区大小一般如何规划？ |
| mq-014 | fact | revised | 是 | 文档是否支持 JPG/WebP/CRN/DDS 纹理格式？ |
| mq-016 | fact | revised | 是 | Nginx HTTPS 配置示例的 listen 端口是多少？ |
| mq-018 | fact | revised | 是 | WebGL 外网部署如何修改应用访问地址？ |
| mq-021 | fact | revised | 是 | 上传速度优化在安装流程中的位置和作用是什么？ |
| mq-024 | fact | revised | 是 | 修改 fstab 后如何重新挂载 /data？ |
| mq-031 | fact | revised | 是 | StampServer 分区表中 /boot 的建议大小是多少？ |
| mq-033 | fact | approved | 是 | sshd_config 中 PermitRootLogin 应如何设置？ |
| mq-034 | fact | approved | 是 | sysctl 中需放开的内核参数示例有哪些？ |
| mq-035 | fact | revised | 否 | Nginx 使用 CA 证书时，文档中的证书和私钥路径是什么？ |
| mq-036 | fact | revised | 是 | Turnserver listening-port 默认值是多少？ |
| mq-039 | fact | approved | 是 | PipelineBuilder 导出字段映射文件的扩展名或用途是什么？ |
| mq-040 | fact | approved | 是 | 加密锁检测失败时应先检查什么？ |
| mq-041 | fact | revised | 是 | 手册中的系统基础防火墙设置如何处理 firewalld 和 SELinux？ |
| mq-042 | fact | revised | 是 | 创建虚拟机时，手册对固态硬盘接口和顺序读写性能有什么建议？ |
| mq-043 | fact | revised | 是 | 文档中 ENU、EPSG 投影的原点和偏移值在哪里设置？ |
| mq-044 | fact | revised | 否 | WebRTC 渲染服务器的文档化运行环境要求是什么？ |
| mq-048 | fact | approved | 是 | Turnserver realm 配置项的作用是什么？ |
| mq-049 | fact | revised | 否 | Rocky 安装介质 baseurl 示例指向哪里？ |
| mq-050 | fact | approved | 否 | StampServer 数据目录通常挂载在哪个路径？ |
| mq-002 | procedure | revised | 是 | WebRTC 外网部署在文档中包含哪些具体方式？请分别说明关键配置步骤。 |
| mq-009 | procedure | approved | 是 | 如何修改 SSH 以允许 root 密码登录？ |
| mq-010 | procedure | approved | 是 | StampTools 管线字段映射如何导出？ |
| mq-011 | procedure | revised | 是 | PipelineBuilder 值域映射中的用户定义值、显示值和标准值如何关联？ |
| mq-012 | procedure | revised | 是 | StampTools 加密锁驱动安装需要哪些前置步骤？ |
| mq-013 | procedure | revised | 是 | ModelBuilder 文档列出的两类纹理错误分别是什么？ |
| mq-017 | procedure | approved | 是 | 如何用 vim 修改 sysctl 并生效？ |
| mq-019 | procedure | revised | 否 | WebRTC 渲染服务器启动前，文档列出的运行环境和启动方式是什么？ |
| mq-025 | procedure | revised | 是 | Turnserver 用户凭证如何配置？ |
| mq-028 | procedure | revised | 是 | 修改 httpd 服务路径后，文档要求执行哪些 systemctl 命令？ |
| mq-059 | procedure | revised | 是 | WebRTC UDP 外网：从 Turnserver 到信令修改的步骤。 |
| mq-060 | procedure | revised | 是 | WebRTC TCP 外网部署中 Turnserver 的关键改动是什么？ |
| mq-065 | procedure | revised | 是 | 配置 httpd 服务路径后，文档给出的服务启用顺序是什么？ |
| mq-067 | procedure | revised | 否 | 启动 WebRTC 渲染服务前，文档列出的硬件条件和启动脚本是什么？ |
| mq-068 | procedure | revised | 是 | 修改 WebRTC 应用的外网地址后，文档要求如何继续启动和访问？ |
| mq-070 | procedure | revised | 是 | 上传速度优化的配置与验证步骤。 |
| mq-072 | procedure | revised | 是 | ModelBuilder 文档如何解释缺失纹理和纹理压缩两类错误？ |
| mq-101 | table | revised | 是 | 端口一览表中网络穿透服务的 TLS 端口是多少？ |
| mq-102 | table | revised | 是 | 分区容量表中 /boot、/var、/data 的推荐值？ |
| mq-103 | table | revised | 是 | UDP 外网部署的 Turnserver 配置中列出哪些关键键？ |
| mq-105 | table | revised | 是 | 端口一览表中 443 分别对应什么服务？ |
| mq-106 | table | revised | 是 | 创建虚拟机时，手册给出的内存最小值和推荐值是什么？ |
| mq-108 | table | revised | 是 | Nginx HTTPS 配置中的端口映射示例 89:8450 表示什么？ |
| mq-109 | table | revised | 是 | 端口一览表中 StampManager 服务端口是多少？ |
