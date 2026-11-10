#!/usr/bin/env python3
"""Exercise Component C over real HTTP and WebSocket connections."""
import json
from pathlib import Path
import re
import signal
import sys
import uuid
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ws import WS

passed = failed = 0
mode = Path(__file__).with_name("lib.sh").read_text().splitlines()[0]
if mode not in ("FEEDBACK=score_only", "FEEDBACK=verbose"):
    raise ValueError("Invalid check configuration")
verbose = mode == "FEEDBACK=verbose"


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    if verbose:
        print(("PASS " if ok else "FAIL ") + label + (f": {detail}" if detail and not ok else ""), flush=True)
    return ok


def call(method, path, token=None, body=None, *, raw=None, content_type="application/json", node=0):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = Request(f"http://127.0.0.1:{8000+node}/api{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=2) as response:
            return response.status, response.headers, response.read()
    except HTTPError as e:
        return e.code, e.headers, e.read()


def request(label, method, path, token=None, body=None, *, status=200, **kwargs):
    actual, headers, data = call(method, path, token, body, **kwargs)
    check(label, actual == status, f"expected HTTP {status}, got {actual}; {data[:160]!r}")
    if actual != status or not data:
        return {}
    try:
        value = json.loads(data)
    except ValueError:
        check(label + " JSON response", False, "not JSON")
        return {}
    return value if isinstance(value, dict) else {}


def fixtures():
    prefix = "c" + uuid.uuid4().hex[:9]
    users = {}
    for role in ("owner", "member", "outside", "offline"):
        name = f"{prefix}_{role}"
        r = request("register " + role, "POST", "/auth/register",
                    body={"username": name, "password": "fixturepass123"}, status=201)
        users[role] = (r["user"]["id"], r["token"], name)
    w = request("create fixture workspace", "POST", "/workspaces", users["owner"][1],
                {"slug": prefix, "name": "Component C fixture"}, status=201)
    cid = w["general_channel"]["id"]
    for role in ("member", "offline"):
        request("join fixture " + role, "POST", f"/channels/{cid}/join", users[role][1])
    return prefix, users, cid


def post(cid, token, body, label="post fixture message", **extra):
    return request(label, "POST", f"/channels/{cid}/messages", token,
                   {"body": body, **extra}, status=201)["message"]


def reactions_dm():
    _, u, cid = fixtures()
    owner, member, outsider = u["owner"], u["member"], u["outside"]
    mid = post(cid, owner[1], "reaction target")["id"]
    path = f"/messages/{mid}/reactions"
    request("reaction requires auth", "POST", path, body={"emoji": "👍"}, status=401)
    for label, actor, ids in [("first", owner, [owner[0]]), ("repeat", owner, [owner[0]]),
                              ("second user", member, [owner[0], member[0]])]:
        r = request("reaction "+label, "POST", path, actor[1], {"emoji":"👍"}, node=1 if actor==member else 0)
        rows = r.get("reactions", [])
        check("reaction tally "+label, len(rows)==1 and rows[0].get("emoji")=="👍"
              and rows[0].get("count")==len(ids) and set(rows[0].get("user_ids", []))==set(ids), str(rows))
    r = request("remove caller reaction", "DELETE", path+"/"+quote("👍"), member[1])
    rows = r.get("reactions", [])
    check("other user's reaction remains", len(rows)==1 and rows[0].get("user_ids")==[owner[0]] and rows[0].get("count")==1, str(rows))
    first = request("create DM", "POST", "/dms", owner[1], {"user_id":member[0]})
    repeat = request("repeat DM across node", "POST", "/dms", owner[1], {"user_id":member[0]}, node=2)
    reverse = request("reverse DM across node", "POST", "/dms", member[1], {"user_id":owner[0]}, node=1)
    check("one symmetric DM id", first.get("channel_id") is not None and first==repeat==reverse)
    request("DM requires auth", "POST", "/dms", body={"user_id":outsider[0]}, status=401)


def files():
    _, u, cid = fixtures()
    owner, member = u["owner"], u["member"]
    def upload(label, size, expected=201):
        boundary = "c-fixture-" + uuid.uuid4().hex
        data = b"F"*size
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.bin"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode()
        raw = head + data + f"\r\n--{boundary}--\r\n".encode()
        r = request(label, "POST", "/files", owner[1], status=expected, raw=raw,
                    content_type=f"multipart/form-data; boundary={boundary}")
        return r.get("file", {}), data
    f, data = upload("upload file", 23)
    fid = f["id"]
    check("upload metadata", f.get("uploader_id")==owner[0] and f.get("filename")=="fixture.bin"
          and f.get("content_type")=="application/octet-stream" and f.get("size")==23)
    try:
        stamp = datetime.fromisoformat(f.get("created_at", "").replace("Z", "+00:00"))
        check("file UTC timestamp", stamp.utcoffset() is not None and stamp.utcoffset().total_seconds()==0)
    except ValueError:
        check("file UTC timestamp", False)
    meta = request("read file metadata cross-node", "GET", f"/files/{fid}", owner[1], node=1)
    check("same file metadata", meta.get("file")==f)
    status, headers, raw = call("GET", f"/files/{fid}/download", owner[1], node=2)
    check("download bytes and headers", status==200 and raw==data and headers.get_content_type()=="application/octet-stream"
          and headers.get_filename()=="fixture.bin")
    request("unknown file", "GET", f"/files/{uuid.uuid4()}", owner[1], status=404)
    upload("reject upload over 10 MiB", 10*1024*1024+1, 413)
    request("reject another user's attachment", "POST", f"/channels/{cid}/messages", member[1],
            {"body":"wrong uploader", "file_ids":[fid]}, status=400)
    m = post(cid, owner[1], "valid attachment", file_ids=[fid])
    check("message embeds FileObj", [x.get("id") for x in m.get("files",[])]==[fid])
    request("reject reused attachment", "POST", f"/channels/{cid}/messages", owner[1],
            {"body":"duplicate attachment", "file_ids":[fid]}, status=400, node=1)


def slash():
    slug, u, cid = fixtures()
    token = u["owner"][1]
    path = f"/channels/{cid}/messages"
    for body in ("/me", "/fixture-unknown"):
        request("reject "+body, "POST", path, token, {"body":body}, status=400)
    for body, expected in (("/me waves","/me waves"),("/shrug unsure","unsure ¯\\_(ツ)_/¯"),
                           ("/shrug","¯\\_(ツ)_/¯")):
        m = post(cid, token, body, "post "+body)
        check("stored body "+body, m.get("body")==expected, repr(m.get("body")))
    m = post(cid, token, "//literal")
    # Staff say it is literal; the slash escape itself need not be asserted.
    check("slash escape posts text", m.get("body") in ("/literal", "//literal"))
    for cmd, field, value in (("/topic Fixture topic","topic","Fixture topic"),
                               ("/archive","is_archived",True),("/unarchive","is_archived",False)):
        r = request("side effect "+cmd,"POST",path,token,{"body":cmd},status=201)
        check("side-effect envelope "+cmd, "message" in r and r["message"] is None
              and r.get("channel",{}).get(field)==value)
    channel = request("create invite target channel", "POST", f"/workspaces/{slug}/channels", token,
                      {"name":"private-fixture", "is_private":True}, status=201)["channel"]["id"]
    r = request("slash invite", "POST", f"/channels/{channel}/messages", token,
                {"body":"/invite @"+u["member"][2]}, status=201)
    check("slash invite envelope", "message" in r and r["message"] is None and bool(r.get("invited_user")))


def groups_profiles():
    slug,u,cid = fixtures()
    owner,member,offline,outside = (u[k] for k in ("owner","member","offline","outside"))
    path=f"/workspaces/{slug}/groups"
    body={"handle":"fixture-team","name":"Fixture team","user_ids":[owner[0],member[0],offline[0]]}
    r=request("workspace member creates group","POST",path,member[1],body,status=201)
    group=r.get("group",{})
    check("group response shape", group.get("id") is not None and group.get("handle")==body["handle"]
          and group.get("name")==body["name"] and set(group.get("member_user_ids",[]))==set(body["user_ids"])
          and "members" not in group)
    rows=request("list groups","GET",path,owner[1],node=1).get("groups",[])
    check("group appears in list", any(x.get("id")==group.get("id") for x in rows))
    gp=path+"/fixture-team"
    request("noncreator cannot update group","PATCH",gp,offline[1],{"name":"No"},status=403)
    request("noncreator cannot delete group","DELETE",gp,offline[1],status=403)
    request("creator updates group","PATCH",gp,member[1],{"name":"New team"})
    channel=request("create group test channel","POST",f"/workspaces/{slug}/channels",owner[1],
                    {"name":"fixture-group"},status=201)["channel"]["id"]
    request("member joins new channel","POST",f"/channels/{channel}/join",member[1])
    m=post(channel,owner[1],"@fixture-team @"+member[2])
    check("group mentions filter channel and dedupe",m.get("mentions")==[member[0]])
    w=WS(member[1],1)
    try:
        w.send({"type":"subscribe","channel_id":cid});w.until("subscribed",cid)
        m=post(cid,owner[1],"@here")
        check("here mentions online nonauthor only",m.get("mentions")==[member[0]])
        request("profile validation rejects timezone","PATCH","/users/me",owner[1],{"timezone":"Invalid/Zone"},status=400)
        request("profile update","PATCH","/users/me",owner[1],{"status_text":"Fixture status"})
        event=w.until("user.updated")
        check("profile broadcasts globally without channel sequence",event.get("user",{}).get("id")==owner[0]
              and event.get("user",{}).get("status_text")=="Fixture status"
              and "channel_id" not in event and "event_id" not in event)
        r=request("read profile cross-node","GET",f"/users/{owner[0]}",member[1],node=2)
        check("profile persists",r.get("user",{}).get("status_text")=="Fixture status")
    finally:
        w.close()
    request("unknown profile","GET",f"/users/{uuid.uuid4()}",outside[1],status=404)
    for i in range(2):
        r=request("owner deletes group","DELETE",gp,owner[1])
        check("idempotent deleted envelope",r.get("deleted") is True)


def deadline(*_):
    raise TimeoutError("25-second check deadline")


if __name__ == "__main__":
    stages = {"1": reactions_dm, "2": files, "3": slash, "4": groups_profiles}
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(25)
    try:
        stages[sys.argv[1]]()
    except Exception as e:
        check("stage did not finish", False, f"{type(e).__name__}: {e}")
    finally:
        signal.alarm(0)
    print(f"RESULT: {passed} passed, {failed} failed")
    sys.exit(bool(failed))
