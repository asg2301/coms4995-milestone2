"""Black-box resilience checks for the local course container."""
import concurrent.futures as futures
import contextlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import Request, urlopen

from ws import WS

mode = Path(__file__).with_name("lib.sh").read_text().splitlines()[0]
if mode not in ("FEEDBACK=score_only", "FEEDBACK=verbose"):
    raise ValueError("Invalid check configuration")
passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    passed += bool(ok)
    failed += not ok
    if mode == "FEEDBACK=verbose":
        print(("PASS " if ok else "FAIL ") + label + (f": {detail}" if not ok and detail else ""), flush=True)
    return bool(ok)


def api(node, path, token=None, body=None, status=200, timeout=2):
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(f"http://127.0.0.1:{8000+node}/api{path}", data, headers), timeout=timeout) as response:
        if response.status != status:
            raise RuntimeError(f"{path}: expected HTTP {status}, got {response.status}")
        return json.load(response)


def healthy(node):
    return api(node, "/health", timeout=.6) == {"status": "ok", "node_id": node}


def fixture():
    slug = "d" + uuid.uuid4().hex[:9]
    tokens = [
        api(0, "/auth/register", body={"username": slug + name, "password": "fixturepass123"}, status=201)["token"]
        for name in ("writer", "watcher")
    ]
    cid = api(0, "/workspaces", tokens[0], {"slug": slug, "name": slug}, 201)["general_channel"]["id"]
    api(0, f"/channels/{cid}/join", tokens[1], {})
    return slug, cid, tokens


def post(node, cid, token, body, timeout=2):
    return api(node, f"/channels/{cid}/messages", token, {"body": body}, 201, timeout)["message"]["id"]


def observe(stack, node, token, cid):
    ws = WS(token, node)
    stack.callback(ws.close)
    ws.send({"type": "subscribe", "channel_id": cid})
    return ws, ws.until("subscribed", cid)["head_event_id"]


def message(ws, cid, body):
    for _ in range(40):
        frame = ws.until("message.created", cid)
        if frame.get("message", {}).get("body") == body:
            return frame["event_id"]
    raise RuntimeError("Expected message was not observed")


def listener(port):
    out = subprocess.check_output(["ss", "-H", "-ltnp", f"sport = :{port}"], text=True, timeout=2)
    pids = {int(pid) for pid in re.findall(r"pid=(\d+)", out)}
    if len(pids) != 1:
        raise RuntimeError(f"Expected one identifiable process listening on {port}, found {len(pids)}")
    return pids.pop()


def wait_until(fn, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if fn():
                return True
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pass
        time.sleep(.2)
    return False


def kill(pid):
    if not Path("/.dockerenv").is_file() or not Path("/app/start.sh").is_file() or pid in (0, 1, os.getpid()):
        raise RuntimeError("Refusing to signal a process outside the course app container")
    os.kill(pid, signal.SIGKILL)


def irc_bridge(slug, cid, tokens, ws, live_node):
    chan = f"#{slug}/general"
    with socket.create_connection(("127.0.0.1", 6667), timeout=3) as sock, sock.makefile("rb") as stream:
        def send(line):
            sock.sendall((line + "\r\n").encode())

        def until(predicate):
            for _ in range(45):
                line = stream.readline().decode(errors="replace")
                if not line:
                    break
                if predicate(line):
                    return
            raise RuntimeError("Expected IRC reply was not received")

        send("PASS " + tokens[1])
        send("NICK " + slug + "watcher")
        send("USER watcher 0 * :observer")
        until(lambda line: " 001 " in line)
        send("JOIN " + chan)
        until(lambda line: " 366 " in line)
        web = slug + "-web-to-irc"
        post(live_node, cid, tokens[0], web)
        until(lambda line: f" PRIVMSG {chan} :{web}" in line)
        check("web write bridges to IRC while node is down", True)
        body = slug + "-irc-to-web"
        send(f"PRIVMSG {chan} :{body}")
        check("IRC write bridges to WebSocket on surviving node", isinstance(message(ws, cid, body), int))


def node_outage():
    slug, cid, (writer, watcher) = fixture()
    nodes = [listener(8000 + n) for n in range(3)]
    if not check("three independently killable HTTP nodes", len(set(nodes)) == 3):
        return
    with contextlib.ExitStack() as stack:
        first, head = observe(stack, 0, watcher, cid)
        other, _ = observe(stack, 2, watcher, cid)
        kill(nodes[1])
        killed_at = time.monotonic()
        try:
            expected_ids = []
            for idx, node in enumerate((0, 2)):
                body = f"{slug}-http-{idx}"
                msg = post(node, cid, writer, body)
                expected_ids.append(msg)
                check(f"surviving node {node} broadcasts", message(first, cid, body) == head + idx + 1)
                check(f"other survivor observes write {idx}", message(other, cid, body) == head + idx + 1)
                rows = api(2 if node == 0 else 0, f"/channels/{cid}/messages", watcher)["messages"]
                check(f"cross-node read after write {idx}", any(row["id"] == msg for row in rows))
            replay = WS(watcher, 2)
            stack.callback(replay.close)
            replay.send({"type": "resume", "channel_id": cid, "since_event_id": head})
            resumed = []
            while True:
                frame = replay.receive()
                if frame.get("channel_id") != cid:
                    continue
                if frame["type"] == "resumed":
                    break
                if frame["type"] == "message.created":
                    resumed.append((frame["event_id"], frame["message"]["id"]))
            check("resume returns ordered missing writes", resumed == list(zip((head+1, head+2), expected_ids)))
            irc_bridge(slug, cid, (writer, watcher), first, 2)
        except Exception as exc:
            check("survivor or IRC exercise completed", False, f"{type(exc).__name__}: {exc}")
        finally:
            remaining = max(.1, 60 - (time.monotonic() - killed_at))
            check("HTTP listener was replaced within 60 seconds",
                  wait_until(lambda: healthy(1) and listener(8001) != nodes[1], remaining))
            check("all nodes answer health after recovery", all(healthy(n) for n in range(3)))


def redis_outage():
    slug, cid, (writer, watcher) = fixture()
    redis = listener(6379)
    cmd = Path(f"/proc/{redis}/cmdline").read_bytes()
    if not check("port 6379 belongs to redis-server", b"redis-server" in cmd):
        return
    with contextlib.ExitStack() as stack:
        ws0, head = observe(stack, 0, watcher, cid)
        ws2, _ = observe(stack, 2, watcher, cid)
        kill(redis)
        t0 = time.monotonic()
        try:
            for idx, node in enumerate((0, 1, 2)):
                body = f"{slug}-redis-{node}"
                post(node, cid, writer, body, timeout=1.5)
                eid = head + idx + 1
                check(f"Redis outage write {node} dense on node 0", message(ws0, cid, body) == eid)
                check(f"Redis outage write {node} dense on node 2", message(ws2, cid, body) == eid)
            check("all Redis outage writes delivered within 5 seconds", time.monotonic() - t0 <= 5)
        except Exception as exc:
            check("writes and fan-out continued during Redis outage", False, f"{type(exc).__name__}: {exc}")
        def replacement():
            pid = listener(6379)
            if pid == redis:
                return False
            with socket.create_connection(("127.0.0.1", 6379), timeout=.6) as conn:
                conn.sendall(b"*1\r\n$4\r\nPING\r\n")
                return conn.recv(32).startswith(b"+PONG")
        if check("Redis respawned and responds to PING", wait_until(replacement, 45)):
            body = slug + "-redis-restored"
            post(1, cid, writer, body)
            check("fan-out after Redis restart", isinstance(message(ws2, cid, body), int))


def load():
    slug, cid, (writer, watcher) = fixture()
    count = 120
    sent = {}
    seen = [{}, {}, {}]
    stop = threading.Event()

    def reader(ws, node):
        while not stop.is_set() and len(seen[node]) < count:
            try:
                frame = ws.receive()
            except (OSError, ValueError, RuntimeError):
                return
            if frame.get("type") == "message.created" and frame.get("channel_id") == cid:
                body = frame.get("message", {}).get("body", "")
                if body.startswith(slug + "-load-"):
                    seen[node].setdefault(body, time.monotonic())

    def write(idx):
        body = f"{slug}-load-{idx}"
        sent[body] = time.monotonic()
        return post(idx % 3, cid, writer, body, timeout=5)

    with contextlib.ExitStack() as stack:
        threads = []
        for node in range(3):
            ws, _ = observe(stack, node, watcher, cid)
            ws.sock.settimeout(8)
            thread = threading.Thread(target=reader, args=(ws, node), daemon=True)
            thread.start()
            threads.append(thread)
        errors = 0
        with futures.ThreadPoolExecutor(max_workers=12) as pool:
            for result in [pool.submit(write, i) for i in range(count)]:
                try:
                    result.result()
                except Exception:
                    errors += 1
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(len(row) < count for row in seen):
            time.sleep(.05)
        stop.set()
        check("all concurrent HTTP writes committed", errors == 0, f"{errors} failed")
        for node, arrivals in enumerate(seen):
            samples = sorted((t - sent[body]) * 1000 for body, t in arrivals.copy().items() if body in sent)
            check(f"observer {node} misses at most 1%", len(samples) >= count * .99, f"{len(samples)}/{count}")
            median = statistics.median(samples) if samples else math.inf
            p95 = samples[math.ceil(.95 * len(samples))-1] if samples else math.inf
            check(f"observer {node} median <= 300ms", median <= 300, f"{median:.1f} ms")
            check(f"observer {node} p95 <= 800ms", p95 <= 800, f"{p95:.1f} ms")


def deadline(*_):
    raise TimeoutError("overall check deadline")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(75)
    try:
        {"1": node_outage, "2": redis_outage, "3": load}[sys.argv[1]]()
    except Exception as exc:
        check("stage completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        signal.alarm(0)
    print(f"RESULT: {passed} passed, {failed} failed")
    sys.exit(bool(failed))
