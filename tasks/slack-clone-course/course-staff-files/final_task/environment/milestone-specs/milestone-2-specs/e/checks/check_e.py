"""Exercise public REST, IRC, and browser surfaces of the trial app."""
from contextlib import ExitStack
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import signal
import socket
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from ws import WS

base = "http://127.0.0.1:"
mode = Path(__file__).with_name("lib.sh").read_text().splitlines()[0]
if mode not in ("FEEDBACK=score_only", "FEEDBACK=verbose"):
    raise ValueError("Invalid check configuration")
passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    passed += bool(ok)
    failed += not ok
    if mode == "FEEDBACK=verbose":
        print(("PASS " if ok else "FAIL ") + label + (f": {detail}" if detail and not ok else ""), flush=True)
    return bool(ok)


def api(node, method, path, token=None, body=None, status=200):
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"{base}{8000+node}/api{path}", data, headers, method=method)
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        actual, raw = response.status, response.read()
    if not check(f"{method} {path} HTTP {status}", actual == status, f"got {actual}"):
        return {}
    return json.loads(raw) if raw else {}


def fixture():
    slug = "e" + uuid.uuid4().hex[:8]
    tokens = [
        api(0, "POST", "/auth/register", body={"username": slug + name, "password": "fixturepass123"},
            status=201)["token"]
        for name in ("owner", "author", "member")
    ]
    cid = api(0, "POST", "/workspaces", tokens[0], {"slug": slug, "name": slug}, 201)["general_channel"]["id"]
    for token in tokens[1:]:
        api(0, "POST", f"/channels/{cid}/join", token, {})
    return slug, cid, tokens


def post(node, cid, token, body):
    return api(node, "POST", f"/channels/{cid}/messages", token, {"body": body}, 201)["message"]


def edits():
    slug, cid, (owner, author, member) = fixture()
    original = post(0, cid, author, slug)
    mid = original["id"]
    path = f"/messages/{mid}"
    api(1, "PATCH", path, member, {"body": "unauthorized"}, 403)
    api(1, "PATCH", path, owner, {"body": "owner is not author"}, 403)
    changed = api(1, "PATCH", path, author, {"body": "edited " + slug}).get("message", {})
    check("edit keeps message id and updates body", changed.get("id") == mid and changed.get("body") == "edited " + slug)
    edited_at = changed.get("edited_at")
    try:
        utc = datetime.fromisoformat(edited_at).utcoffset().total_seconds() == 0
    except (AttributeError, TypeError, ValueError):
        utc = False
    check("edited_at is a UTC timestamp", utc, repr(edited_at))
    rows = api(2, "GET", f"/channels/{cid}/messages", author).get("messages", [])
    check("edit persists across nodes", any(x.get("id") == mid and x.get("body") == "edited " + slug for x in rows))
    api(0, "POST", path + "/reactions", member, {"emoji": "👍"})
    api(2, "DELETE", path, member, status=403)
    check("workspace owner can delete", api(2, "DELETE", path, owner).get("deleted") is True)
    check("repeat delete is idempotent", api(0, "DELETE", path, owner).get("deleted") is True)
    api(1, "PATCH", path, author, {"body": "cannot restore"}, 409)
    rows = api(1, "GET", f"/channels/{cid}/messages", author).get("messages", [])
    visible = [x for x in rows if x.get("id") == mid]
    check("deleted message is absent or an empty tombstone",
          not visible or all(not x.get("reactions") and (x.get("deleted") or x.get("deleted_at") or not x.get("body"))
                             for x in visible))
    own = post(0, cid, author, "author may delete")["id"]
    check("author can delete own message", api(1, "DELETE", f"/messages/{own}", author).get("deleted") is True)


class IRC:
    def __init__(self, stack):
        self.sock = stack.enter_context(socket.create_connection(("127.0.0.1", 6667), timeout=3))
        self.reader = stack.enter_context(self.sock.makefile("rb"))

    def send(self, line):
        self.sock.sendall((line + "\r\n").encode())

    def next(self):
        return self.reader.readline().decode(errors="replace")

    def until(self, predicate):
        for _ in range(60):
            line = self.next()
            if not line:
                break
            if predicate(line):
                return line
        raise RuntimeError("expected IRC reply not received")

    def numeric(self, code):
        return self.until(lambda line: re.match(rf"^:\S+ {code} ", line) is not None)

    def login(self, token, nick):
        self.send("PASS " + token)
        self.send("NICK " + nick)
        self.send("USER watcher 0 * :watcher")


def irc():
    slug, cid, (owner, author, member) = fixture()
    chan = f"#{slug}/general"
    with ExitStack() as stack:
        bad = IRC(stack)
        bad.login("invalid-token", "bad" + slug)
        check("invalid PASS rejected", bool(bad.numeric("464")))
        client = IRC(stack)
        nick = "watch" + slug
        client.login(author, nick)
        for code in ("001", "002", "003", "004", "005", "422"):
            check("registration numeric " + code, bool(client.numeric(code)))
        other = IRC(stack)
        other.login(member, nick)
        check("duplicate nick rejected", bool(other.numeric("433")))
        other.send("NICK alt" + slug)
        check("nick retry on same connection", bool(other.numeric("001")))
        client.send(f"JOIN #{slug}-general")
        check("hyphenated channel rejected", bool(client.numeric("403")))
        client.send("JOIN " + chan)
        joined = client.until(lambda line: bool(re.search(rf"^:{re.escape(nick)}![^ ]+ JOIN :?{re.escape(chan)}\s*$", line)))
        check("join echoes client prefix", bool(joined))
        check("join returns names with nick", nick in client.numeric("353"))
        check("join ends names", bool(client.numeric("366")))
        client.send("NOTACOMMAND")
        check("unknown command rejected", bool(client.numeric("421")))
        client.send("PING :fixture-" + slug)
        check("PING echoes payload", bool(client.until(lambda line: " PONG " in line and slug in line)))
        ws = WS(member, 2)
        stack.callback(ws.close)
        ws.send({"type": "subscribe", "channel_id": cid})
        head = ws.until("subscribed", cid)["head_event_id"]
        first = post(0, cid, owner, "web " + slug)
        check("web message bridges with IRC prefix",
              bool(client.until(lambda line: bool(re.match(rf"^:\S+!\S+@\S+ PRIVMSG {re.escape(chan)} :web {slug}\s*$", line)))))
        frame = ws.until("message.created", cid)
        check("web event advances channel sequence", frame.get("event_id") == head + 1
              and frame.get("message", {}).get("id") == first["id"])
        client.send(f"PRIVMSG {chan} :irc {slug}")
        frame = ws.until("message.created", cid)
        check("IRC event advances same sequence", frame.get("event_id") == head + 2
              and frame.get("message", {}).get("body") == "irc " + slug)
        rows = api(1, "GET", f"/channels/{cid}/messages", member).get("messages", [])
        check("IRC write appears in web history", any(row.get("body") == "irc " + slug for row in rows))
        client.send("QUIT :bye")
        lines = [client.next()]
        for _ in range(8):
            if not lines[-1]:
                break
            lines.append(client.next())
        check("QUIT closes without error", not lines[-1] and not any(line.startswith("ERROR") for line in lines))


roles = {
    "primary": ("rgb(0, 122, 90)", "auth-submit send-btn thread-send create-channel-submit join-workspace-submit workspace-general-submit create-invitation-submit channel-topic-submit channel-add-member-submit"),
    "danger": ("rgb(224, 30, 90)", "archive-channel-btn logout-btn"),
    "secondary": ("rgb(18, 100, 163)", "new-channel-btn create-invitation-btn unarchive-channel-btn create-channel-cancel close-thread channel-settings-close workspace-settings-close channel-settings-btn workspace-settings-btn"),
}
static = """
auth-form auth-submit auth-toggle workspace-create-form empty-state-create-workspace
join-workspace-form join-workspace-submit channel-list dms-list new-channel-btn current-user
logout-btn workspace-header workspace-settings-btn workspace-settings-close workspace-name-input
workspace-join-mode workspace-general-submit channel-title channel-topic channel-settings-btn
channel-settings-title channel-settings-close channel-members-list channel-add-member-form
channel-add-member-input channel-add-member-submit channel-topic-input channel-topic-submit
archive-channel-btn unarchive-channel-btn create-channel-form create-channel-private
create-channel-submit create-channel-cancel message-list message-input send-btn thread-panel
thread-input thread-send close-thread settings-tab-members settings-tab-invitations
settings-tab-general settings-pane-members settings-pane-invitations settings-pane-general
create-invitation-btn create-invitation-form create-invitation-submit invitations-list
""".split()


class Hooks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hooks, self.ids = {}, set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-testid" in attrs:
            self.hooks[attrs["data-testid"]] = attrs
        if "id" in attrs:
            self.ids.add(attrs["id"])


def browser():
    for node in range(3):
        with urlopen(f"{base}{8000+node}/", timeout=3) as response:
            html = response.read().decode()
            check(f"node {node} serves HTML", response.status == 200 and "html" in response.headers.get_content_type())
        hooks = Hooks()
        hooks.feed(html)
        missing = set(static) - hooks.hooks.keys()
        check(f"node {node} exposes initial UI hooks", not missing, ", ".join(sorted(missing)))
        ids = {"auth-modal", "create-channel-modal", "channel-settings-modal", "workspace-settings-modal",
               "create-invitation-form"} - hooks.ids
        check(f"node {node} exposes pinned DOM ids", not ids, ", ".join(sorted(ids)))
        for role, (_, names) in roles.items():
            wrong = [name for name in names.split() if hooks.hooks.get(name, {}).get("data-button-role") != role]
            check(f"node {node} sets {role} button roles", not wrong, ", ".join(wrong))

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    slug, cid, (owner, _, _) = fixture()
    with sync_playwright() as p:
        chromium = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        try:
            page = chromium.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(3500)
            page.goto(f"{base}8000/?{urlencode({'token': owner})}", wait_until="domcontentloaded", timeout=6000)
            for role, (background, names) in roles.items():
                wrong = []
                for name in names.split():
                    item = page.get_by_test_id(name)
                    if not item.count() or item.first.evaluate(
                        "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]"
                    ) != [background, "rgb(255, 255, 255)"]:
                        wrong.append(name)
                check(f"computed {role} button colors", not wrong, ", ".join(wrong))
            entry = page.locator(f'[data-channel-id="{cid}"][data-channel-name="general"]').first
            try:
                entry.wait_for(state="visible")
            except PWTimeout:
                # A valid app may use a supported localStorage key instead of the query parameter.
                page.evaluate(
                    """token => { for (const key of ["huddle.token", "token", "auth_token"])
                        localStorage.setItem(key, token); }""", owner)
                page.reload(wait_until="domcontentloaded", timeout=6000)
                entry.wait_for(state="visible")
            check("workspace hydration renders channel entry", entry.is_visible())
            entry.click()
            check("channel header appears", page.get_by_test_id("channel-title").is_visible())
            text = "browser fixture " + slug
            page.get_by_test_id("message-input").fill(text)
            page.get_by_test_id("send-btn").click()
            row = page.get_by_test_id("message").filter(has_text=text).first
            row.wait_for(state="visible")
            check("composing a message renders it", row.get_by_test_id("message-body").is_visible())
            page.reload(wait_until="domcontentloaded", timeout=6000)
            entry.click()
            row.wait_for(state="visible")
            check("message survives reload", row.is_visible())
            row.hover()
            row.get_by_test_id("open-thread-btn").click()
            page.get_by_test_id("thread-panel").wait_for(state="visible")
            check("dedicated thread opens", page.get_by_test_id("thread-panel").is_visible())
            page.get_by_test_id("thread-input").fill("reply " + slug)
            page.get_by_test_id("thread-send").click()
            page.get_by_test_id("thread-panel").get_by_text("reply " + slug, exact=True).wait_for(state="visible")
            check("thread reply renders", True)
            page.get_by_test_id("close-thread").click()
            row.hover()
            row.get_by_test_id("reaction-button").click()
            picker = page.get_by_test_id("emoji-picker")
            picker.wait_for(state="visible")
            picker.get_by_test_id("emoji-option").first.click()
            row.get_by_test_id("reaction-chip").first.wait_for(state="visible")
            check("emoji picker adds a reaction badge", True)
        finally:
            chromium.close()


def deadline(*_):
    raise TimeoutError("overall check deadline")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(65)
    try:
        {"1": edits, "2": irc, "3": browser}[sys.argv[1]]()
    except Exception as exc:
        check("stage completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        signal.alarm(0)
    print(f"RESULT: {passed} passed, {failed} failed")
    sys.exit(bool(failed))
