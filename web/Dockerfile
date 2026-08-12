# 生产前端：本机构建 dist 后打入镜像（避免目标机拉 nginx:alpine / 跑 npm）
# 构建前请在 web/ 执行：npm ci && npm run build
FROM python:3.11-slim

WORKDIR /app

COPY dist /app/dist
COPY static_server.py /app/static_server.py

EXPOSE 80

CMD ["python", "/app/static_server.py"]
