#!/usr/bin/env python3
"""Point 141 config at Windows Ollama forwarder and restart rag-service."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

# rewrite base_url only
cmd = r"""
python3 - <<'PY'
from pathlib import Path
p = Path('/data/rag_python/config.ini')
text = p.read_text(encoding='utf-8')
old = 'base_url = http://192.168.10.158:11434'
new = 'base_url = http://192.168.10.2:11435'
if old not in text and '192.168.10.2:11435' not in text:
    # also handle already-changed or alternate spacing
    import re
    text2, n = re.subn(r'(?m)^base_url\s*=\s*.*$', new, text, count=1)
    if n != 1:
        raise SystemExit('base_url line not found')
    text = text2
else:
    text = text.replace(old, new)
p.write_text(text, encoding='utf-8')
print('config_ok')
for line in p.read_text(encoding='utf-8').splitlines():
    if 'base_url' in line and not line.strip().startswith(';'):
        print(line)
PY
cd /opt/rag && docker compose up -d --force-recreate
sleep 3
curl -sS -m 8 -o /dev/null -w 'host_via_fwd=%{http_code}\n' http://192.168.10.2:11435/api/tags
docker exec rag-service python -c "import httpx; r=httpx.get('http://192.168.10.2:11435/api/tags', timeout=10); print('container', r.status_code, 'models', len(r.json().get('models',[])))"
for i in $(seq 1 24); do
  st=$(docker inspect --format='{{.State.Health.Status}}' rag-service 2>/dev/null || echo missing)
  echo wait_$i=$st
  [ "$st" = healthy ] && break
  [ "$st" = unhealthy ] && break
  sleep 5
done
curl -sS -m 5 http://127.0.0.1:10605/health
"""
print(cmd[:80], flush=True)
_i, o, e = c.exec_command(cmd, timeout=180, get_pty=True)
print(o.read().decode(errors="replace").encode("ascii", "replace").decode(), flush=True)
c.close()
