#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
for cmd in [
    "head -80 /etc/nginx/conf.d/default.conf",
    "echo '===='",
    "head -80 /etc/nginx/conf.d/default0527.conf",
    "echo '==== nginx.conf server includes ===='",
    "grep -n include /etc/nginx/nginx.conf | head",
]:
    _i, o, e = c.exec_command(cmd, timeout=20)
    print(o.read().decode(errors="replace").encode("ascii", "replace").decode(), end="")
c.close()
