"""VAIDER-mask local app. Serves the page on 127.0.0.1 only; no network calls, no telemetry.
Run:  python server.py     (opens your browser)   Stop: Ctrl+C or the Quit button."""
import json, os, secrets, sys, threading, webbrowser
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import core

HERE = Path(__file__).parent
TOKEN = secrets.token_urlsafe(24)
STATE = dict(phase="idle", done=0, total=0, msg="Ready", result=None, error=None, scan=None)
CANCEL = threading.Event(); LOCK = threading.Lock(); HTTPD = None

def opts(d):
    names = {f.name for f in fields(core.Options)}
    return core.Options(**{k: v for k, v in d.items() if k in names})

def progress(phase, done, total, msg):
    with LOCK: STATE.update(phase=phase, done=done, total=total, msg=msg)

def job(kind, o):
    try:
        if kind == "scan":
            _, s = core.scan(o, progress, CANCEL)
            with LOCK: STATE.update(phase="scanned", scan=s, msg="Scan complete. Nothing has been written.", done=1, total=1)
        else:
            r = core.run(o, progress, CANCEL)
            with LOCK: STATE.update(phase="done", result=r, msg="Finished" if r["ok"] else "Finished with problems: see report", done=1, total=1)
    except core.Cancelled:
        with LOCK: STATE.update(phase="cancelled", msg="Cancelled. Files already written are kept; the crosswalk was saved.")
    except Exception as e:
        with LOCK: STATE.update(phase="error", error=f"{type(e).__name__}: {e}", msg="Stopped with an error")

def start(kind, d):
    with LOCK:
        if STATE["phase"] in ("scan", "write", "verify"): return False
        STATE.update(phase="scan", done=0, total=1, msg="Starting", result=None, error=None, scan=None if kind == "run" else STATE["scan"])
    CANCEL.clear(); threading.Thread(target=job, args=(kind, opts(d)), daemon=True).start(); return True

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def ok_host(self):
        h = self.headers.get("Host", "")
        return h.split(":")[0] in ("127.0.0.1", "localhost")
    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if not self.ok_host(): return self.send(403, {"error": "bad host"})
        if self.path.split("?")[0] == "/":
            html = (HERE / "index.html").read_text().replace("__TOKEN__", TOKEN).replace("__VERSION__", core.VERSION)
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if self.path == "/api/status":
            with LOCK: return self.send(200, dict(STATE))
        self.send(404, {"error": "not found"})
    def do_POST(self):
        if not self.ok_host() or self.headers.get("X-Token") != TOKEN: return self.send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length", 0)); d = json.loads(self.rfile.read(n) or b"{}")
        p = self.path
        if p == "/api/ls":
            base = Path(d.get("path") or Path.home()).expanduser()
            try:
                base = base.resolve()
                if base.is_file(): base = base.parent
                dirs = sorted(x.name for x in base.iterdir() if x.is_dir() and not x.name.startswith("."))
                nf = sum(1 for x in base.iterdir() if x.is_file())
                return self.send(200, dict(path=str(base), parent=str(base.parent), dirs=dirs, files=nf))
            except Exception as e: return self.send(200, dict(error=str(e), path=str(base), parent=str(base.parent), dirs=[], files=0))
        if p == "/api/mkdir":
            try: Path(d["path"]).expanduser().mkdir(parents=True, exist_ok=True); return self.send(200, {"ok": True})
            except Exception as e: return self.send(200, {"error": str(e)})
        if p in ("/api/scan", "/api/run"):
            try: core.validate(opts(d))
            except Exception as e: return self.send(200, {"error": str(e)})
            return self.send(200, {"started": start(p[5:], d)})
        if p == "/api/cancel": CANCEL.set(); return self.send(200, {"ok": True})
        if p == "/api/quit":
            self.send(200, {"ok": True}); threading.Thread(target=HTTPD.shutdown, daemon=True).start(); return
        self.send(404, {"error": "not found"})

def main():
    global HTTPD
    HTTPD = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("DEID_PORT", 0))), H)
    url = f"http://127.0.0.1:{HTTPD.server_address[1]}/"
    print(f"VAIDER-mask {core.VERSION} running at {url}  (this computer only). Ctrl+C to stop.")
    if not os.environ.get("DEID_NOBROWSER"): webbrowser.open(url)
    try: HTTPD.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__": main()
