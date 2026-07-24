#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
for cmd in [
    "ping -c 2 -W 2 192.168.10.158 || true",
    "ping -c 2 -W 2 192.168.10.2 || true",
    "ip route",
    "curl -sS -m 5 -o /dev/null -w 'to_2=%{http_code}\\n' http://192.168.10.2:8899/ || true",
]:
    print("=======", cmd, flush=True)
    _i, o, e = c.exec_command(cmd, timeout=20)
    print((o.read() or e.read()).decode(errors="replace"), flush=True)
c.close()
