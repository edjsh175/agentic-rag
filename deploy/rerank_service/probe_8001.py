import socket
import urllib.request

s = socket.socket()
s.settimeout(5)
try:
    s.connect(("127.0.0.1", 8001))
    print("tcp-ok")
except Exception as e:
    print("tcp-fail", e)
finally:
    s.close()

try:
    print(urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=10).read().decode())
except Exception as e:
    print("http-fail", e)
