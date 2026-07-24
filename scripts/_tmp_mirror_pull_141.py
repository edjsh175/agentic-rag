#!/usr/bin/env python3
import paramiko
PROXY="http://192.168.10.2:8899"
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.10.141',22,'root','123456',timeout=20,allow_agent=False,look_for_keys=False)

def run(cmd, timeout=300):
    print('=======', cmd[:120], flush=True)
    i,o,e=c.exec_command(cmd,timeout=timeout,get_pty=True)
    print(o.read().decode('utf-8','replace'), end='', flush=True)
    return o.channel.recv_exit_status()

# test registry via proxy
run(f"curl -sS -o /dev/null -w '%{{http_code}}\\n' -x {PROXY} --connect-timeout 20 https://registry-1.docker.io/v2/")
run(f"curl -sS -o /dev/null -w '%{{http_code}}\\n' -x {PROXY} --connect-timeout 20 https://docker.m.daocloud.io/v2/")
# configure mirror + keep proxy
daemon='''{
  "data-root": "/data/docker",
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://mirror.ccs.tencentyun.com"
  ],
  "log-driver": "json-file",
  "log-opts": {"max-size": "50m", "max-file": "3"}
}'''
sftp=c.open_sftp()
with sftp.file('/etc/docker/daemon.json','w') as f: f.write(daemon)
sftp.close()
run('systemctl restart docker && sleep 2 && systemctl is-active docker')
# try pull via mirror (daemon has proxy)
code=run('docker pull python:3.11-slim', timeout=600)
print('pull_exit', code, flush=True)
if code!=0:
    code=run('docker pull docker.m.daocloud.io/library/python:3.11-slim && docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim', timeout=600)
    print('mirror_pull_exit', code, flush=True)
c.close()
