# 使用轻量级的 Python 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 先拷贝依赖文件，利用 Docker 缓存优化构建速度
COPY requirements.txt .

# 安装依赖（建议使用国内源加速）
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目所有代码到镜像中
COPY . .

# 暴露你的项目端口
EXPOSE 10605

# 运行启动脚本
CMD ["python", "run.py"]