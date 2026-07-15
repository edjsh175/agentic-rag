# Chunk 健康审计报告（Round 0B）

- 生成时间：`2026-07-14T08:51:58.096702+00:00`
- 只读：`True`
- 语料快照哈希：`19421c7f8fb3124f`
- 代码指纹：`380b4b7bc2d1048f`

## 总览

| 指标 | 值 |
|---|---:|
| Chunk 总量 | 767 |
| 长度中位数 | 101.0 |
| `<100` 字符 | 49.28% |
| `<200` 字符 | 73.92% |
| `300-800` 字符 | 16.95% |
| `>1200` 字符 | 0.52% |
| 空 section_path | 22.16% |
| approved | 767 / 767 |

## 按文档分布

| 文档 | Chunk | 中位数 | <100% | <200% | 空 section_path% |
|---|---:|---:|---:|---:|---:|
| `StampServer用户手册_Rocky9 .docx` | 362 | 83.0 | 65.47 | 92.82 | 0.0 |
| `2ca727efa70847b49f0f67528544d210.pdf` | 155 | 334.0 | 9.03 | 21.29 | 100.0 |
| `StampTools用户手册.docx` | 153 | 86.0 | 56.86 | 86.27 | 0.0 |
| `StampWebRTC用户手册.docx` | 53 | 116.0 | 47.17 | 73.58 | 0.0 |
| `b8aa16233693-MySQL数据库_数据类型与表约束.md` | 15 | 149.0 | 26.67 | 66.67 | 6.67 |
| `实景三维新功能.txt` | 9 | 74.0 | 66.67 | 88.89 | 0.0 |
| `矢量瓦片接口说明.doc` | 8 | 375.0 | 12.5 | 25.0 | 100.0 |
| `0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md` | 5 | 208.0 | 20.0 | 40.0 | 40.0 |
| `东方通用户手册_Rocky9.doc` | 4 | 279.0 | 25.0 | 50.0 | 100.0 |
| `2026陕西耕地保护系统问题收集.docx` | 3 | 70.0 | 66.67 | 100.0 | 0.0 |

## 疑似错误标题（样本）

- `command_like` | `StampServer用户手册_Rocky9 .docx` | `操作系统安装 > 系统基础配置 > vim /etc/selinux/config`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `操作系统安装 > 系统基础配置 > vim  /etc/fstab`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `操作系统安装 > 系统基础配置 > vim ~/.bashrc`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `操作系统安装 > 系统基础配置 > net.ipv4.tcp_tw_reuse = 1`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 软件安装准备 > name= BaseOS`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 软件安装准备 > name= AppStream`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 软件安装准备 > enabled=1`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 软件安装准备 > vim /etc/samba/smb.conf`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > yum install -y redis`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > vim /etc/redis/redis.conf`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > yum install -y httpd`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > cd /data/setup`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > Description=Tomcat Server`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > Type= oneshot`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > cd /data/setup/node-v22`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > reboot`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `基础软件安装 > 基础软件安装 > yum install -y *.rpm`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `2）USB授权锁 > cd /usr/lib64`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `Stamp服务部署 > 服务部署准备 > chmod -R 777 /data`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `Stamp服务部署 > StampNodeServer部署 > cd /data/StampNodeServer/`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `Stamp服务部署 > StampNodeServer部署 > pm2 save`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `Stamp服务部署 > matchmaker部署 > cd /data/matchmaker/`
- `port_like` | `StampServer用户手册_Rocky9 .docx` | `5349：TLS/TCP，TLS服务`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `3Dtiles数据服务发布 > GB28181服务配置 > Type=simple`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `MINIO部署 > 挂载磁盘 > mount -a`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `MINIO部署 > 安装MinIO > cd /data/setup/minio/`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `MINIO部署 > 安装MinIO > MINIO_ROOT_USER=minio`
- `command_like` | `StampServer用户手册_Rocky9 .docx` | `HTTPS配置 > 私有CA配置 > vim /etc/ca/openssl.cnf`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `HTTPS配置 > 私有CA配置 > default_days      = 3650`
- `config_like` | `StampServer用户手册_Rocky9 .docx` | `HTTPS配置 > 私有CA配置 > prompt             = no`

## 重解析过滤对比

解析文档数：8；过滤前块数：706；过滤后块数：685；过滤比例：2.97%

| 文档 | 过滤前 | 过滤后 | 过滤数 | 比例 | 主因 |
|---|---:|---:|---:|---:|---|
| `41bab783fa8840fd8c24a34840a4484d.pdf` | 227 | 227 | 0 | 0.0% | `` |
| `2026陕西耕地保护系统问题收集.docx` | 4 | 3 | 1 | 25.0% | `single_short_line` |
| `实景三维新功能.txt` | 12 | 6 | 6 | 50.0% | `compact_too_short` |
| `StampServer用户手册_Rocky9 .docx` | 224 | 213 | 11 | 4.91% | `single_short_line` |
| `StampTools用户手册.docx` | 105 | 104 | 1 | 0.95% | `compact_too_short` |
| `StampWebRTC用户手册.docx` | 113 | 112 | 1 | 0.88% | `compact_too_short` |
| `东方通用户手册_Rocky9.doc` | None | None | None | None% | `` |
| `矢量瓦片接口说明.doc` | None | None | None | None% | `` |
| `0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md` | 5 | 5 | 0 | 0.0% | `` |
| `b8aa16233693-MySQL数据库_数据类型与表约束.md` | 16 | 15 | 1 | 6.25% | `single_short_line` |

## 一致性

- consistent: `True`
- index_chunks: `767`
- chroma_chunks: `767`
- missing_indexed: `0`
- unexpected_chroma: `0`

## 媒体覆盖

- DOCX 媒体合计：`515`
- extract_embedded_images：`False`
- `StampWebRTC用户手册.docx`: media=201 path_exists=True
- `StampServer用户手册_Rocky9 .docx`: media=191 path_exists=True
- `StampTools用户手册.docx`: media=123 path_exists=True
- `0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md`: media=0 path_exists=True
- `2026陕西耕地保护系统问题收集.docx`: media=0 path_exists=True
- `41bab783fa8840fd8c24a34840a4484d.pdf`: media=0 path_exists=True
- `b8aa16233693-MySQL数据库_数据类型与表约束.md`: media=0 path_exists=True
- `东方通用户手册_Rocky9.doc`: media=0 path_exists=True
- `实景三维新功能.txt`: media=0 path_exists=True
- `矢量瓦片接口说明.doc`: media=0 path_exists=True

## 同键多值冲突候选

- `localhost` values=['1947，在打开页面的', '3003/', '8080/StampManager2', '8080/sqliteQuery', '8082'] sources=['2ca727efa70847b49f0f67528544d210.pdf', 'StampServer用户手册_Rocky9 .docx']
- `baseurl` values=['file:///media/cdrom/AppStream', 'file:///media/cdrom/BaseOS'] sources=['2ca727efa70847b49f0f67528544d210.pdf', 'StampServer用户手册_Rocky9 .docx']
