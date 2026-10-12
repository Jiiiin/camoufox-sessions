"""Real-browser regression: isolation, restart persistence, tabs, identity, locking."""
import argparse
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs

parser = argparse.ArgumentParser()
parser.add_argument("--resources", type=Path, required=True)
parser.add_argument("--scratch", type=Path, required=True)
args = parser.parse_args()
resources = args.resources.resolve()
args.scratch.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(dir=args.scratch)
root = Path(temporary.name)
reports = []
seeding = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        if parts.path == "/report":
            reports.append(json.loads(parse_qs(parts.query)["data"][0]))
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            return
        name = parts.path.rsplit("-", 1)[-1]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if seeding: self.send_header("Set-Cookie", f"session={name}; Max-Age=86400; Path=/; SameSite=Lax")
        self.end_headers()
        setup = f"localStorage.setItem('marker', '{name}');" if seeding else ""
        cookie = self.headers.get("Cookie", "")
        html = f"""<!doctype html><title>Session test {name}</title><h1>Session {name}</h1>
        <script>{setup}
        const report = {{name:'{name}',path:location.pathname,marker:localStorage.getItem('marker'),
            cookie:{json.dumps(cookie)},ua:navigator.userAgent,cores:navigator.hardwareConcurrency}};
        fetch('/report?data='+encodeURIComponent(JSON.stringify(report)));
        </script>"""
        self.wfile.write(html.encode())


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = f"http://127.0.0.1:{server.server_port}"
workers = []


def native_window_count(pid):
    """Count onscreen macOS document windows without reading window titles."""
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    owner_key = ctypes.c_void_p.in_dll(cg, 'kCGWindowOwnerPID').value
    layer_key = ctypes.c_void_p.in_dll(cg, 'kCGWindowLayer').value
    # All-windows includes Firefox's hidden menu/popup backing windows.
    # Our test browsers are not minimized; onscreen still includes occluded
    # windows, so overlapping browser windows cannot evade this assertion.
    windows = cg.CGWindowListCopyWindowInfo(1, 0)
    assert windows, 'macOS window inventory unavailable'
    count = 0
    try:
        for index in range(cf.CFArrayGetCount(windows)):
            row = cf.CFArrayGetValueAtIndex(windows, index)
            owner = ctypes.c_int(); layer = ctypes.c_int()
            cf.CFNumberGetValue(cf.CFDictionaryGetValue(row, owner_key), 9, ctypes.byref(owner))
            cf.CFNumberGetValue(cf.CFDictionaryGetValue(row, layer_key), 9, ctypes.byref(layer))
            if owner.value == pid and layer.value == 0:
                count += 1
        return count
    finally:
        cf.CFRelease(windows)


def assert_one_window(name):
    status = json.loads((root / name / 'status.json').read_text())
    count = native_window_count(status['browser_pid'])
    assert count == 1, f'Session {name}: expected one window, got {count}'


def start(name):
    log = (root / f"{name}.log").open("a")
    process = subprocess.Popen([str(resources / "runtime/bin/python"), str(resources / "session_worker.py"),
        "--resources", str(resources), "--profile", str(root / name), "--homepage", f"{origin}/seed-{name}"],
        stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
    workers.append(process)
    return process


def wait_for(predicate, description, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        for name in ("a", "b"):
            status = root / name / "status.json"
            if status.exists() and json.loads(status.read_text())["phase"] == "error":
                raise AssertionError(f"Worker failed: {status.read_text()} {(root / (name+'.log')).read_text()}")
        time.sleep(0.2)
    raise AssertionError(f"Timed out: {description}")


def close(process):
    process.stdin.write('{"action":"close"}\n'); process.stdin.flush()
    assert process.wait(timeout=20) == 0


try:
    a = start("a"); b = start("b")
    wait_for(lambda: len(reports) >= 2, "two concurrent sessions")
    for name, process in (("a", a), ("b", b)):
        for prefix in ('child', 'third'):
            process.stdin.write(json.dumps({"action": "open", "url": f"{origin}/{prefix}-{name}"}) + "\n")
        process.stdin.flush()
    wait_for(lambda: len(reports) >= 6, "three tabs per session")
    for name in ("a", "b"):
        assert_one_window(name)
        child = next(r for r in reports if r["path"] == f"/child-{name}")
        assert child["marker"] == name and f"session={name}" in child["cookie"], child
    duplicate = start("a")
    assert duplicate.wait(timeout=15) == 3, "Duplicate launch must reject profile lock"
    wait_for(lambda: all((root / n / "tabs.json").exists() and
        len(json.loads((root / n / "tabs.json").read_text())) == 3 for n in ("a", "b")), "tab snapshots")
    identity_a = (root / "a/identity.json").read_bytes()
    first_identity = {r["name"]: (r["ua"], r["cores"]) for r in reports}
    close(a); close(b)
    assert all(json.loads((root / n / "status.json").read_text())["phase"] == "closed" for n in ("a", "b"))
    seeding = False
    reports.clear()
    a = start("a"); b = start("b")
    wait_for(lambda: len(reports) >= 6, "restored tabs on restart")
    for name in ('a', 'b'):
        assert_one_window(name)
    for report in reports:
        assert report["marker"] == report["name"], report
        assert f"session={report['name']}" in report["cookie"], report
        assert (report["ua"], report["cores"]) == first_identity[report["name"]], report
    assert (root / "a/identity.json").read_bytes() == identity_a
    close(a); close(b)
    print(json.dumps({"concurrent_sessions": "passed", "cookie_isolation": "passed",
        "local_storage_isolation": "passed", "restart_persistence": "passed", "tab_restore": "passed",
        "single_window_three_tabs": "passed", "restart_single_window": "passed",
        "stable_identity": "passed", "duplicate_lock": "passed", "graceful_close": "passed"}, indent=2))
finally:
    for process in workers:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    server.shutdown()
    temporary.cleanup()
