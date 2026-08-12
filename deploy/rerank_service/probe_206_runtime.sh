#!/bin/bash
set -e
echo "=== how started ==="
docker inspect rag-service --format 'Image={{.Config.Image}} Restart={{.HostConfig.RestartPolicy.Name}}'
echo "=== mounts ==="
docker inspect rag-service --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
echo "=== cmd/entrypoint ==="
docker inspect rag-service --format 'Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}'
echo "=== compose? ==="
ls -la /opt/rag/docker-compose.yml /data/rag_python/docker-compose.yml 2>/dev/null || true
echo "=== ports ==="
docker port rag-service
echo "=== 141 ping ==="
curl -sS -m 3 http://192.168.137.141:10605/health || echo "141_unreachable_from_206"
