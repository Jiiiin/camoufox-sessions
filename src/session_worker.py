"""One named, persistent Camoufox profile per process. No account-specific logic."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
from urllib.parse import urlsplit


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def valid_url(url):
    if url == "about:blank":
        return True
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def make_options(profile, resources):
    # Redirect Camoufox's cache BEFORE importing it. Never touch another install.
    import platformdirs
    original = platformdirs.user_cache_dir
    platformdirs.user_cache_dir = lambda name=None, *a, **k: (
        str(resources / "browser-cache") if name == "camoufox" else original(name, *a, **k)
    )
    from camoufox.utils import launch_options, get_pref_env_vars
    from camoufox.addons import DefaultAddons
    from camoufox.pkgman import launch_path
    executable = Path(launch_path())
    saved = profile / "identity.json"
    if saved.exists():
        options = json.loads(saved.read_text())
    else:
        options = launch_options(
            headless=False, os="macos",
            window=(1280, 840), enable_cache=True,
            exclude_addons=list(DefaultAddons),
            firefox_user_prefs={
                "browser.privatebrowsing.autostart": False,
                "privacy.sanitize.sanitizeOnShutdown": False,
                "places.history.enabled": True,
                "browser.startup.page": 0,
                "browser.sessionstore.resume_session_once": False,
                "browser.sessionstore.resume_from_crash": False,
                "network.proxy.type": 5,
                "browser.link.open_newwindow": 3,
            },
        )
        # Persist only the generated device identity, never ambient credentials.
        options["env"] = {k: v for k, v in options["env"].items()
                          if k.startswith(("CAMOU_CONFIG", "CAMOU_PREFS"))}
        atomic_json(saved, options)
    # Routing follows the current Mac settings, even for identities saved by
    # older versions. Refresh both pref channels: startup CAMOU_PREFS and the
    # Playwright prefs. Keep the saved device identity and browser data intact.
    prefs = dict(options.get("firefox_user_prefs", {}))
    prefs["network.proxy.type"] = 5
    prefs["browser.link.open_newwindow"] = 3
    options["firefox_user_prefs"] = prefs
    options["executable_path"] = str(executable)
    environment = {**os.environ, **options["env"]}
    environment = {k: v for k, v in environment.items() if not k.startswith("CAMOU_PREFS_")}
    options["env"] = {**environment, **get_pref_env_vars(prefs)}
    options["user_data_dir"] = str(profile / "browser-data")
    options["no_viewport"] = True
    return options


async def new_tab(context):
    pages = [page for page in context.pages if not page.is_closed()]
    if not pages:
        return await context.new_page()
    # Juggler's new_page explicitly creates an OS window. A featureless
    # window.open follows Firefox's open_newwindow=3 preference instead.
    async with context.expect_page(timeout=10000) as pending:
        await pages[0].evaluate("() => { window.open('about:blank', '_blank'); }")
    return await pending.value


def browser_pid():
    rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,comm="], text=True)
    children = {}
    for row in rows.splitlines():
        parts = row.strip().split(None, 2)
        if len(parts) == 3:
            children.setdefault(int(parts[1]), []).append((int(parts[0]), parts[2]))
    pending = [os.getpid()]
    while pending:
        for pid, command in children.get(pending.pop(), []):
            if command.endswith("/MacOS/camoufox"):
                return pid
            pending.append(pid)
    return None


async def run(profile, resources, homepage):
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    lock = (profile / "worker.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("该会话已经打开。", file=sys.stderr)
        return 3
    commands = queue.Queue()
    stopping = False
    closed = asyncio.Event()
    status_path = profile / "status.json"
    phase = "starting"
    last_error = None
    pid = None
    context = None

    def publish():
        atomic_json(status_path, {"phase": phase, "pid": os.getpid(),
                    "browser_pid": pid, "error": last_error,
                    "tabs": len(context.pages) if context else 0})

    def read_commands():
        for line in sys.stdin:
            try:
                commands.put(json.loads(line))
            except ValueError:
                pass
        commands.put({"action": "close"})

    threading.Thread(target=read_commands, daemon=True).start()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: commands.put({"action": "close"}))
    publish()
    tasks = set()
    last_tabs = None

    def save_tabs():
        nonlocal last_tabs
        if context is None or tasks:
            return
        urls = [p.url for p in context.pages if valid_url(p.url)]
        # Retain the last useful snapshot if the user closes every window.
        if urls and urls != last_tabs:
            atomic_json(profile / "tabs.json", urls)
            last_tabs = urls

    async def navigate(page, url):
        nonlocal last_error
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            if not page.is_closed() and not closed.is_set():
                last_error = "部分页面加载超时，可在浏览器中刷新。"

    def schedule_navigation(page, url):
        task = asyncio.create_task(navigate(page, url))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    try:
        options = await asyncio.to_thread(make_options, profile, resources)
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(persistent_context=True, from_options=options) as context:
            context.on("close", lambda *_: closed.set())
            pid = browser_pid()
            saved_tabs = profile / "tabs.json"
            urls = json.loads(saved_tabs.read_text()) if saved_tabs.exists() else [homepage]
            urls = [url for url in urls if valid_url(url)] or ["about:blank"]
            restored_pages = list(context.pages)
            # Create the tabs while the initial page is still blank, before
            # navigation can replace its execution context.
            while len(restored_pages) < len(urls):
                restored_pages.append(await new_tab(context))
            for i, url in enumerate(urls):
                schedule_navigation(restored_pages[i], url)
            phase = "running"
            publish()
            while not stopping and not closed.is_set():
                if not context.pages and not tasks:
                    # On macOS, closing the final window leaves Firefox alive.
                    # End that named session and retain its last tab snapshot.
                    stopping = True
                    break
                while not commands.empty():
                    command = commands.get_nowait()
                    if command.get("action") == "close":
                        stopping = True
                        break
                    if command.get("action") == "focus" and context.pages:
                        await context.pages[0].bring_to_front()
                    if command.get("action") == "open" and valid_url(command.get("url", "about:blank")):
                        page = await new_tab(context)
                        schedule_navigation(page, command.get("url", "about:blank"))
                save_tabs()
                publish()
                await asyncio.sleep(0.5)
            phase = "closing"
            publish()
            if not closed.is_set():
                save_tabs()
                for task in list(tasks):
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await context.close()
        phase = "closed"
        context = None
        publish()
        return 0
    except Exception as error:
        # Playwright errors can include URLs or launch arguments; keep them out
        # of the UI and logs. The exception class is sufficient for diagnosis.
        phase = "error"
        last_error = f"启动未完成（{type(error).__name__}），请关闭残留窗口后重试。"
        context = None
        publish()
        print(type(error).__name__, file=sys.stderr)
        return 1
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--homepage", default="about:blank")
    args = parser.parse_args()
    if not valid_url(args.homepage):
        parser.error("首页需要 http:// 或 https:// 地址")
    sys.exit(asyncio.run(run(args.profile.resolve(), args.resources.resolve(), args.homepage)))


if __name__ == "__main__":
    main()
