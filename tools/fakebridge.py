import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
clients=[]; lock=threading.Lock()
SEQ=[("done",None),("working",None),("tool","bash"),("done",None),("idle",None)]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith("/events"):
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream")
        self.end_headers()
        with lock: clients.append(self)
        try:
            while True: time.sleep(3600)
        except Exception: pass
        finally:
            with lock:
                if self in clients: clients.remove(self)
    def log_message(self,*a): pass
def loop():
    i=0
    while True:
        st,tool=SEQ[i%len(SEQ)]; i+=1
        p=("data: "+json.dumps({"state":st,"tool":tool,"turn":i})+"\n\n").encode()
        with lock:
            for c in list(clients):
                try: c.wfile.write(p); c.wfile.flush()
                except Exception:
                    if c in clients: clients.remove(c)
        time.sleep(2)
threading.Thread(target=loop,daemon=True).start()
print("fake bridge on 3098")
ThreadingHTTPServer(("0.0.0.0",3098),H).serve_forever()
