# v5 覆盖黄金集审核清单

审核要点：锚点 chunk 是否存在；`required_facts` 是否可从原文直接推出；题干是否过宽。

- 候选题数：105

| id | category | answerability | question | required_facts |
|---|---|---|---|---|
| cv5-001 | table | full | 「矢量数据入库 > EPSG编码」中关于字段/表项有哪些关键取值说明？ | CGCS2000；degree；Gauss-Kruger |
| cv5-002 | table | full | 「工具概述 > EPSG编码」中关于字段/表项有哪些关键取值说明？ | CGCS2000；degree；Gauss-Kruger |
| cv5-003 | fact | full | 关于「分析」，文档中与 StampWebGL用户手册 相关的关键配置/事实是什么？ | 开始管段；选择分析的开始管段；结束管段；选择分析的结束管段 |
| cv5-004 | fact | full | 关于「分析」，文档中与 StampWebRTC用户手册 相关的关键配置/事实是什么？ | 分析日期；法定标准时间为大寒日或冬至，也可以选择自定义时间。；自定义；自定义日照分析日期 |
| cv5-005 | table | full | 「(no_section)」中关于字段/表项有哪些关键取值说明？ | varchar；projectid；imgpath；creater |
| cv5-006 | fact | full | 关于「分析」，文档中与 PipelineSystem用户手册 相关的关键配置/事实是什么？ | 管段；选择管段；半径；分析的缓冲半径 |
| cv5-007 | fact | full | 关于「GB+28181国家标准《安全防范视频监控联网系统信息传输、交换、控制技术要求》+（征求意见稿） > 信息传输、交换、控制技术要求」，文档中与 GB+28181国家标准《安全防范视频监控联网系统信息传输、交换、控制技术要求》+（征求意见稿） 相关的关键配置/事实是什么？ | 全局类型规定如下；a)；resultType；MPAlarmRecordType |
| cv5-008 | procedure | full | 在「2ca727efa70847b49f0f67528544d210 > 2024 年6 月」中，执行或配置时需要用到哪条关键命令/步骤？ | 运维基础配置；ca727efa70847b49f0f67528544d210；aksusbd；StampServer |
| cv5-009 | procedure | full | 在「200 OK > 修改安装配置文件」中，执行或配置时需要用到哪条关键命令/步骤？ | 参照如下conf示例修改配置文件。；p_kafka_password；p_kafka_security；scram.ScramLoginModule |
| cv5-010 | procedure | full | 在「编译安装POSTGIS > 安装postgis扩展」中，执行或配置时需要用到哪条关键命令/步骤？ | /home/stampserver/se_query.so；POSTGIS；llvm-toolset-7.0；openssl |
| cv5-011 | fact | full | 关于「分析」，文档中与 StampExplorer用户手册 相关的关键配置/事实是什么？ | 水平箭头；选中呈黄色，进行水平剖切；水平面；选中呈红色，沿水平箭头方向移动则向前或向后进行连续剖切。在剖切状态或非剖切状态时可以左右移动剖切操作符位置。 |
| cv5-012 | table | full | 「(no_section)」中关于字段/表项有哪些关键取值说明？ | http://192.168.10.224/egis/base/v1/wtts/publish?；http://192.168.10.224/egis/base/v1/wtts/publish/add?；http://192.168.10.224/egis/base/v1/wtts/publish/getProgress?；http://192.168.10.224/egis/base/v1/wtts/publish/createLayer? |
| cv5-013 | fact | full | 关于「(no_section)」，文档中与 EGIS三维数据服务接口规范0902 相关的关键配置/事实是什么？ | Layers；wops_beijing；/name；/srid |
| cv5-014 | procedure | full | 根据文档「9.安装httpd > 建立软链接」，相关操作应执行什么命令？ | tar -xzvf boost_1_74_0.tar.gz；yum install gcc-c++；tar -xzvf cmake-3.30.2.tar.gz；/data/setup/postgresql-15.8/contrib/fuzzystrmatch |
| cv5-015 | table | full | 「(no_section)」中关于字段/表项有哪些关键取值说明？ | http://192.168.10.224/wtts/publish?；http://192.168.10.224/wtts/publish/add?；http://192.168.10.224/wtts/publish/getProgress?；http://192.168.10.224/wtts/publish/createLayer? |
| cv5-016 | procedure | full | 在「安装aksusbd-8.23.1」中，执行或配置时需要用到哪条关键命令/步骤？ | /data/pgdata/；/usr/local/pgsql-15/bin/postgresql-15-check-db-dir；Environment；PGDATA=/data/pgdata/ |
| cv5-017 | table | full | 「Sheet1」中关于字段/表项有哪些关键取值说明？ | Sheet1；FillPower2；WaitCount；WaitTime |
| cv5-018 | fact | full | 关于「查询统计 > 管线查询」，文档中与 PipelineWebGL用户手册 相关的关键配置/事实是什么？ | 道路名称；输入需要查询的道路名称关键字进行模糊查询，查询结果显示在道路列表框中。如果不输入关键字则查询全部道路。；道路；选择需要查询的道路名称，与之相交的道路显示在交叉路列表框中 |
| cv5-018-oral | fact | full | 管线工具这一节主要讲什么？ | 道路名称；输入需要查询的道路名称关键字进行模糊查询，查询结果显示在道路列表框中。如果不输入关键字则查询全部道路。；道路；选择需要查询的道路名称，与之相交的道路显示在交叉路列表框中 |
| cv5-019 | fact | full | 关于「空间数据」，文档中与 三维基本概念 相关的关键配置/事实是什么？ | 地形数据；表示地表起伏；DEM；Digital Elevation Model，数字高程模型，是DTM的分支，用于对地貌形态的虚拟表示，可派生出等高线、 |
| cv5-020 | fact | full | 文档「四、使用命令行 > 4. 旧库导出」提到的关键路径是什么？ | /home/stamp_tmp/db_bak/bin2/191_scdrelation_2.bin；/home/stamp_tmp/db_bak/bin2/191_sctexture_2.bin；/home/stamp_tmp/db_bak/bin2/191_smeshtexture_2.bin；/home/stamp_tmp/db_bak/bin2/191_spatial_2.bin |
| cv5-021 | table | full | 「(no_section)」中关于字段/表项有哪些关键取值说明？ | 输出参数；见表38。；search；matchmodel |
| cv5-022 | fact | full | 关于「(no_section)」，文档中与 VSCode+SVN配置 相关的关键配置/事实是什么？ | position；svn.layout.trunkRegexName；Number；commit |
| cv5-023 | procedure | full | 根据文档「VMwareESXI创建虚拟机 > linux系统安装显卡驱动」，相关操作应执行什么命令？ | systemctl set-default graphical.target；VMwareESXI；NVIDIA；nvidia-smi |
| cv5-024 | procedure | full | 根据文档「HTTPS配置 > 私有CA配置」，相关操作应执行什么命令？ | mkdir -p /etc/nginx/ssl；chmod 400 /etc/nginx/ssl/stamp.key；/etc/nginx/ssl；/etc/nginx/ssl/stamp.key |
| cv5-025 | fact | full | 关于「产品概述 > 产品技术特点」，文档中与 StampGIS三维产品白皮书 相关的关键配置/事实是什么？ | StampGIS |
| cv5-026 | procedure | full | 在「FUNCTION public.stamp_shortest_road」中，执行或配置时需要用到哪条关键命令/步骤？ | -离起点最近的线；-离终点最近的线；-距离起点最近线的终点；-距离终点最近线的起点 |
| cv5-027 | procedure | full | 在「2、Apache配置https」中，执行或配置时需要用到哪条关键命令/步骤？ | 将下载的压缩包放在/home/目录；/usr/local/openssl；可以在线安装；yum install gcc |
| cv5-028 | fact | full | 文档「(no_section)」提到的关键路径是什么？ | /usr/lib/systemd/system/minio.service；/usr/local/bin/minio；/usr/local；地址 |
| cv5-029 | procedure | full | 在「11.陕西耕地保护」中，执行或配置时需要用到哪条关键命令/步骤？ | 线程池 & 异步；降低资源消耗、提高响应速度、统一管理。；同步；你等结果回来再干别的（打电话等对方说完）。 |
| cv5-030 | procedure | full | 在「1.MySQL 数据类型 > 1.1 数值类型」中，执行或配置时需要用到哪条关键命令/步骤？ | - 无符号 tinyint（0~255） create table tt2(num tinyint unsigned)；TINYINT、INT、BIGINT、FLOAT、DECIMAL；TINYINT；1字节，-128~127 |
| cv5-031 | fact | full | 文档「示例」中给出的访问地址是什么？ | http://192.168.10.224/geoserver?；http；//192.168.10.224/geoserver?；//192.168.10.224/geoserver |
| cv5-032 | fact | full | 关于「(no_section)」，文档中与 坡度分析-坡度连片度分析--相关接口 相关的关键配置/事实是什么？ | type；slope_connected&layer=400ded9c-5b11-ec11-f93c-0000f93d0080&a；slope_connected；ded9c-5b11-ec11-f93c-0000f93d0080 |
| cv5-033 | procedure | full | 根据文档「(no_section)」，相关操作应执行什么命令？ | df -h；umount /home；查看分区情况；Security |
| cv5-034 | fact | full | 关于「服务发布」，文档中与 ArcGIS配图及发布切片服务手册 相关的关键配置/事实是什么？ | 设置切片缓存路径；默认是使用root根路径，即安装arcgis server时设置的切片缓存路径，也可自定义缓存路径，设置完点击“Cont；松散瓦片模式设置；arcgis切片模式有松散型（Exploded）和紧凑型（Compact）,选择松散瓦片模式；点击“Cathing（缓存 |
| cv5-035 | procedure | full | 在「SQL 6」中，执行或配置时需要用到哪条关键命令/步骤？ | -6.取到坐标点的包围圈；SELECT；st_concavehull；st_collect |
| cv5-036 | procedure | full | 在「(no_section)」中，执行或配置时需要用到哪条关键命令/步骤？ | 如果用IE打开场景不能显示基础球，则需要生成基础shader，具体方法为：将DerivedDataCache下的所有文件删除，将data.data中除全球DEM；UE场景shader生成；将UE场景中的粒子图层设置为初始不可见；SVN目录下\Engine\DerivedDataCacheCustom下默认会有DynamicWeatherParticlesMat和Water两个目录 |
| cv5-037 | procedure | full | 根据文档「一、SSL模块检查与安装」，相关操作应执行什么命令？ | nginx -V | grep -- --with-http_ssl_module；mkdir /usr/local/nginx/cert；chmod 700 /usr/local/nginx/cert；/usr/sbin |
| cv5-038 | fact | full | 关于「(no_section)」，文档中与 readme 相关的关键配置/事实是什么？ | startLon；117.181283&startLat=34.275755&endLon=117.161432&endLat=34.24；起点经度【117.181283】；startLat |
| cv5-039 | fact | full | 文档「(no_section)」提到的关键路径是什么？ | /data/stamp_data；设置图层名称；xigang；设置路径 |
| cv5-040 | fact | full | 关于「六、NSSM 注册系统服务（核心步骤） > 2. 新建 NSSM 服务」，文档中与 信令以及节点管理程序Windows自启动 相关的关键配置/事实是什么？ | 取消勾选；Local System account；选择；This account |
| cv5-041 | fact | full | 关于「VMwareESXI创建虚拟机 > 配置虚拟机规格」，文档中与 VMware ESXi虚拟机GPU直通教程 相关的关键配置/事实是什么？ | VMwareESXI；windows10 |
| cv5-042 | fact | full | 文档「(no_section)」提到的关键路径是什么？ | /data/cache；原始数据；shp矢量数据 或者 tiff影像文件；切片思路 |
| cv5-043 | fact | full | 关于「IIS配置代理转发到Apache或其他端口监听服务」，文档中与 IIS配置代理转发到Apache或其他端口监听服务 - love_coder - 博客园 相关的关键配置/事实是什么？ | Apache；Server；Settings；Enable |
| cv5-044 | procedure | full | 在「(no_section)」中，执行或配置时需要用到哪条关键命令/步骤？ | -# 日志最多保存天数 -->；/data/stampserver/gb28181_server；Description；gb28181_server |
| cv5-045 | fact | full | 关于「(no_section)」，文档中与 web设置允许跨域 相关的关键配置/事实是什么？ | tomcat；geowebcache；cors-filter-1.7.jar；java-property-utils-1.9.jar |
| cv5-046 | procedure | full | 在「一、安装rockyLinux9镜像 > 7.选择“x86-64/”」中，执行或配置时需要用到哪条关键命令/步骤？ | 1.打开“VMware Workstation Pro” -- 创建虚拟机--典型--下一步；2.稍后安装操作系统（S）--下一步；3.Linux（L）--Rocky Linux 64 位--下一步；4.虚拟机名称--位置--下一步 |
| cv5-047 | procedure | full | 在「(no_section)」中，执行或配置时需要用到哪条关键命令/步骤？ | 创建自定义配置文件；default_bits；2048；prompt |
| cv5-048 | fact | full | 关于「(no_section)」，文档中与 335b1bd4ff7e4141a97a1d8c2479ff6a 相关的关键配置/事实是什么？ | CPU；主频越高、线程数越多越好，推荐I9 13900K及以上；显卡；Nvidia独立显卡，8G显存及以上，推荐Nvidia3060Ti及以上 |
| cv5-049 | fact | full | 关于「(no_section)」，文档中与 gb28181端口映射 相关的关键配置/事实是什么？ | 如上图所示；第一行为gb28181服务器配置端口映射，用来跟外网接入平台或者摄像头做SIP信令交互使用；其中；外部端口：指路由器端口 |
| cv5-050 | fact | full | 文档「(no_section)」提到的关键路径是什么？ | /dataConfigAction.do；http://192.168.100.133:8080/StampManager2/dataConfigAction.do?；示例；http://192.168.100.133:8080/StampManager2/dataConfigAction.d |
| cv5-051 | procedure | full | 根据文档「(no_section)」，相关操作应执行什么命令？ | df -h；注意；进行释放空间这个操作，虚拟机所在磁盘剩余空间必须要大于当前虚拟机（压缩前）所占空间，比如释放空间前的当前虚拟机有960G；VMWare |
| cv5-052 | fact | full | 关于「(no_section)」，文档中与 VMWare虚拟机自动识别USB加密锁方法 相关的关键配置/事实是什么？ | 注意；需要将<vid>和<pid>替换为上面获取的VID和PID，替换后的完整代码为：；我们使用的USB加密锁的VID和PID是固定的。；Sentinel |
| cv5-053 | fact | full | 文档「虚拟机服务器操作文档」中给出的访问地址是什么？ | 192.168.10.73；设置网络适配器；单电脑使用仅主机 ——局域网使用桥接模式；之后点击开启此虚拟机 |
| cv5-054 | fact | full | 关于「普通索引」，文档中与 大数据量矢量查询效率提升方法 相关的关键配置/事实是什么？ | pgadmin；navicat；jtfw_fzz2；CREATE |
| cv5-055 | procedure | full | 在「虚拟测试设备配置：」中，执行或配置时需要用到哪条关键命令/步骤？ | 需要在启动虚拟机的服务器上运行ipc3文件夹中的exe程序：；1.测试视频文件（test.264）在exe程序中已固定写死，只能命名为test.264；测试视频文件（test.264）在exe程序中已固定写死，只能命名为test.264；如果想做多个测试文件，可以将文件夹拷贝多份，修改里面的端口和摄像头唯一标识 |
| cv5-056 | fact | full | 关于「权限管理」，文档中与 2026陕西耕地保护系统问题收集 相关的关键配置/事实是什么？ | ArcGIS；Geoserver |
| cv5-057 | fact | full | 文档「(no_section)」提到的关键路径是什么？ | /data/html/matchmaker/；matchmaker；matchmaker.js |
| cv5-058 | procedure | full | 在「(no_section)」中，执行或配置时需要用到哪条关键命令/步骤？ | 启动VS2019下的命令行工具：x64 Native Tools Command Prompt for VS 2019；执行命令生成接口库；VS2019；Native |
| cv5-059 | fact | full | 关于「req」，文档中与 openssl-ip 相关的关键配置/事实是什么？ | default_bits；2048；prompt；no |
| cv5-060 | fact | full | 关于「运维管理配置 > 数据配置」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 样式文件；为MVT服务图层配置已经存在的样式文件（*.prj2d）；渲染类型；贴地形或贴模型 |
| cv5-061 | procedure | full | 根据文档「基础软件安装 > 基础软件安装」，相关操作应执行什么命令？ | yum install -y *.rpm；systemctl enable postgresql-16；systemctl start postgresql-16；/data/setup/postgresql-16/ |
| cv5-062 | procedure | full | 在「应用系统部署 > WebRTC部署」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/pki/coturn/public/turn_server_cert.pem；/etc/pki/coturn/private/turn_server_pkey.pem；/etc/coturn/certs/fullchain.pem；/etc/coturn/certs/privkey.pem |
| cv5-063 | fact | full | 文档「HTTPS配置 > 私有CA配置」提到的关键路径是什么？ | /etc/nginx/conf.d/default.conf；/etc/nginx/ssl/stamp.crt；/etc/nginx/ssl/stamp.key；listen |
| cv5-064 | procedure | full | 在「Stamp服务部署 > Nginx代理设置」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/nginx/conf.d/default.conf；/usr/share/nginx/html；http://127.0.0.1:8082；server |
| cv5-065 | fact | full | 文档「IPV6配置」提到的关键路径是什么？ | /data/html/StampNodeServer/stamp/config.js；StampServer；"http://[fd00:1234:5678::100]"；http |
| cv5-066 | procedure | full | 在「Stamp服务部署」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_dom.so；/data/stampmanager/server.xml；/data/stampserver/se_grid.so |
| cv5-067 | procedure | full | 在「操作系统安装 > 系统基础配置」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/sysctl.conf；设置完成后执行；sysctl -p；注意公式 |
| cv5-068 | table | full | 「应用系统部署 > 系统网络拓扑」中关于字段/表项有哪些关键取值说明？ | Apache；WebRTC；StampManager；GB28181 |
| cv5-069 | procedure | full | 在「Stamp服务部署 > SignallingWebServer部署」中，执行或配置时需要用到哪条关键命令/步骤？ | 渲染服务管理；SignallingWebServer；platform_scripts；run_local_1.bat |
| cv5-070 | fact | full | 关于「运维管理配置」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 数据库路径；选择第一步创建的目录作为数据库安装路径；日志是否开启；开启日志记录 |
| cv5-071 | fact | full | 关于「矢量数据入库」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 连接数据库；打开QGIS连接PostgreSQL数据库。；数据入库；在PostgreSQL节点下面选择上面建立的数据库连接进行GDB数据导入（选择到gdb目录）。入库时必须勾选红色选项，如 |
| cv5-072 | fact | full | 关于「运维管理配置 > GB28181配置」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 是否鉴权；默认为否；下图以大华摄像头直连模式为例说明；ID编码说明 |
| cv5-073 | procedure | full | 在「应用系统部署 > 多显卡大屏部署」中，执行或配置时需要用到哪条关键命令/步骤？ | ConfigFile；NDC_WallCurved4x2.ndisplay；Node；Node_0 |
| cv5-074 | fact | full | 关于「运维管理配置 > 服务设置」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 数据处理缓存；设置数据处理的内存缓存大小，默认为4G，具体可根据数据量和内存大小设置。；数据处理线程数；设置数据处理的线程数量，默认为4个，可根据CPU的核数和线程数设置。 |
| cv5-075 | procedure | full | 根据文档「基础软件安装 > 软件安装准备」，相关操作应执行什么命令？ | mount /dev/cdrom /media/cdrom/；mkdir /etc/yum.repos.d/tmp；mv /etc/yum.repos.d/*.repo /etc/yum.repos.d/tmp/；yum clean all |
| cv5-076 | procedure | full | 在「Stamp服务部署 > 3DTiles服务配置」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_w3ts.so；/data/stampmanager/server.xml；/data/stamp_data/jl_3dt |
| cv5-077 | procedure | full | 根据文档「Stamp服务部署 > Trunserver部署」，相关操作应执行什么命令？ | yum install -y epel-release；yum install -y turnserver；mkdir -p /etc/coturn/certs；/etc/coturn/certs |
| cv5-078 | procedure | full | 在「Stamp服务部署 > GB28181服务配置」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_gb28181_module.so；/data/stampmanager/server.xml；/usr/lib/systemd/system |
| cv5-079 | procedure | full | 在「应用系统部署」中，执行或配置时需要用到哪条关键命令/步骤？ | 运行渲染服务器；运行渲染服务脚本：run_local_1.bat、run_local_2.bat、 run_local_3.bat；第二步；访问StampWebRTC |
| cv5-080 | procedure | full | 根据文档「MINIO部署」，相关操作应执行什么命令？ | mount -a；/data/disk3；/data/setup；/data/setup/minio/ |
| cv5-081 | procedure | full | 在「Stamp服务部署 > I3S服务配置」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_i3s.so；/data/stampmanager/server.xml；/data/stamp_data/js_l |
| cv5-082 | procedure | full | 在「HTTPS配置 > 私有域名配置」中，执行或配置时需要用到哪条关键命令/步骤？ | 登录管理界面；获取Mac地址；AX1800；//192.168.10.1 |
| cv5-083 | procedure | full | 在「Stamp服务部署 > 三维数据库服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_db_query_pg.so；/data/stampmanager/server.xml；Oracle |
| cv5-084 | fact | full | 文档「运维管理配置 > 运动物体配置」提到的关键路径是什么？ | /data/dynamic；骨骼动画；添加一个带骨骼动画的动画人（*.dynamic）；路径 |
| cv5-085 | procedure | full | 在「操作系统安装」中，执行或配置时需要用到哪条关键命令/步骤？ | VMNet1；Install |
| cv5-086 | procedure | full | 在「Stamp服务部署 > 管线更新服务」中，执行或配置时需要用到哪条关键命令/步骤？ | 管线更新输出目录下必须要要有管线配置的resource目录，如果工程上配置的管线配置文件所在目录与更新数据输出目录不一致则需要将resource目录拷贝到管线更；/data/stampserver/se_pipeline_publish_tool.so；/data/stampmanager/server.xml；Oracle |
| cv5-086-oral | procedure | full | 在「Stamp服务部署 > 管线更新服务」中，执行或配置时需要用到哪条关键命令/步骤？ | 管线更新输出目录下必须要要有管线配置的resource目录，如果工程上配置的管线配置文件所在目录与更新数据输出目录不一致则需要将resource目录拷贝到管线更；/data/stampserver/se_pipeline_publish_tool.so；/data/stampmanager/server.xml；Oracle |
| cv5-087 | procedure | full | 根据文档「Stamp服务部署 > StampNodeServer部署」，相关操作应执行什么命令？ | mv /data/setup/StampNodeServer/ /data/；chmod -R 777 /data/StampNodeServer；/data/setup/StampNodeServer/；/data/StampNodeServer |
| cv5-088 | procedure | full | 在「Stamp服务部署 > Apache数据服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_sde.so；/data/stampmanager/server.xml；Apache |
| cv5-089 | procedure | full | 根据文档「Stamp服务部署 > 倾斜发布服务」，相关操作应执行什么命令？ | chmod -R 777 /data/stampmanager/pcs.csv；/data/stampmanager/pcs.csv；/etc/httpd/conf/httpd.conf；/data/stampserver/se_oblique_publish.so |
| cv5-090 | procedure | full | 根据文档「Stamp服务部署 > 服务部署准备」，相关操作应执行什么命令？ | chmod -R 777 /data；/data/stamp_data；/data/stamp_tmp；/data/stamp_pub |
| cv5-091 | procedure | full | 在「Stamp服务部署 > 矢量编辑服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_；/data/stampmanager/server.xml；Oracle |
| cv5-092 | procedure | full | 在「Stamp服务部署 > 工程地质服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_geologic.so；/data/stampmanager/server.xml；StampManager- |
| cv5-093 | table | full | 「操作系统安装 > Rocky安装」中关于字段/表项有哪些关键取值说明？ | Swap；交换分区（虚拟内存），分区大小一般是内存的2倍，如果内存时8G，则设置16384M（16G）；注意；由于小文件读写性能方面ext4比xfs优越，因此文件系统格式不能选择默认的xfs。 |
| cv5-094 | procedure | full | 在「Stamp服务部署 > 矢量瓦片服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_geo_server.so；/data/stampmanager/server.xml；Oracle |
| cv5-095 | procedure | full | 在「Stamp服务部署 > 三维数据库更新服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_model_tool_publish.so；/data/stampmanager/server.xml；ModelLayer |
| cv5-096 | procedure | full | 在「Stamp服务部署 > 管线分析服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_pipeline.so；/data/stampmanager/server.xml；Oracle |
| cv5-096-oral | procedure | full | 在「Stamp服务部署 > 管线分析服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_pipeline.so；/data/stampmanager/server.xml；Oracle |
| cv5-097 | procedure | full | 在「Stamp服务部署 > 三维搜索服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_search.so；/data/stampmanager/server.xml；StampManager |
| cv5-098 | procedure | full | 在「Stamp服务部署 > 管线查询服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_query.so；/dataquery；/data/stampmanager/server.xml |
| cv5-098-oral | procedure | full | 在「Stamp服务部署 > 管线查询服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_query.so；/dataquery；/data/stampmanager/server.xml |
| cv5-099 | procedure | full | 在「Stamp服务部署 > 管线发布服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_pipeline_publish.so；/data/stampmanager/server.xml；Apache |
| cv5-099-oral | procedure | full | 在「Stamp服务部署 > 管线发布服务」中，执行或配置时需要用到哪条关键命令/步骤？ | /etc/httpd/conf/httpd.conf；/data/stampserver/se_pipeline_publish.so；/data/stampmanager/server.xml；Apache |
| cv5-100 | fact | full | 关于「矢量数据入库 > PostGIS Bundle」，文档中与 StampServer用户手册_Rocky9  相关的关键配置/事实是什么？ | 注意；经纬度数据入库时必须要填写SRID：4490；字符编码；根据SHP数据的字符类型设置导入数据的字符类型为：UTF-8（默认）或GBK，设置不正确会导入不成功。 |
