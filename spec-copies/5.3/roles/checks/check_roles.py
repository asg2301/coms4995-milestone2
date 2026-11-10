#!/usr/bin/env python3
"""Black-box Component A checks. Standard library only; fresh data per group."""
import argparse
import json
import signal
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

passed = failed = 0


def check(label, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('PASS ' + label, flush=True)
    else:
        failed += 1
        print('FAIL ' + label + (' -- ' + str(detail)[:240] if detail else ''), flush=True)
    return bool(condition)


def call(method, path, token=None, body=None, node=0):
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f'http://127.0.0.1:{8000+node}/api{path}', data=data,
                  headers=headers, method=method)
    try:
        with urlopen(req, timeout=0.8) as response:
            status, raw = response.status, response.read()
    except HTTPError as e:
        status, raw = e.code, e.read()
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        payload = {'_non_json': raw[:100].decode(errors='replace')}
    return status, payload


def expect(label, response, status):
    actual, payload = response
    check(label, actual == status, f'expected {status}, got {actual}; {payload}')
    return payload if isinstance(payload, dict) else {}


def request(label, method, path, token=None, body=None, status=200, node=0):
    return expect(label, call(method, path, token, body, node), status)


def account(prefix, tag):
    username = prefix + '_' + tag
    r = request('fixture register ' + tag, 'POST', '/auth/register',
                body={'username': username, 'password': 'SecurePass123', 'display_name': tag}, status=201)
    return {'token': r['token'], 'id': r['user']['id'], 'name': username}


def fixture():
    prefix = 't' + uuid.uuid4().hex[:10]
    users = {tag: account(prefix, tag) for tag in ['owner', 'admin', 'peer', 'member', 'guest', 'out']}
    owner = users['owner']['token']
    slug = prefix + '-ws'
    r = request('fixture create workspace', 'POST', '/workspaces', owner,
                {'slug': slug, 'name': 'Test workspace'}, status=201)
    general = r['general_channel']['id']
    for tag in ['admin', 'peer', 'member', 'guest']:
        request('fixture join ' + tag, 'POST', f'/channels/{general}/join', users[tag]['token'])
    for tag, role in [('admin', 'admin'), ('peer', 'admin'), ('guest', 'guest')]:
        request('fixture role ' + tag, 'PATCH', f'/workspaces/{slug}/members/{users[tag]["id"]}', owner, {'role': role})
    return prefix, users, slug, r['workspace'], general


def auth():
    prefix = 'a' + uuid.uuid4().hex[:10]
    u = account(prefix, 'user')
    check('auth token length >=16', isinstance(u['token'], str) and len(u['token']) >= 16)
    me = request('auth token works on another node', 'GET', '/auth/me', u['token'], node=2)
    check('auth identity matches', me.get('user', {}).get('id') == u['id'])
    required = {'id', 'username', 'display_name', 'timezone', 'avatar_url', 'status_text', 'status_emoji'}
    check('auth UserObj fields', required <= set(me.get('user', {})))
    request('auth duplicate username', 'POST', '/auth/register', body={'username': u['name'], 'password': 'SecurePass123'}, status=409)
    for label, name, password in [('space', 'bad name', 'SecurePass123'), ('symbol', 'bad!name', 'SecurePass123'), ('short password', prefix+'s', '1234567')]:
        request('auth invalid ' + label, 'POST', '/auth/register', body={'username': name, 'password': password}, status=400)
    login = request('auth login cross-node', 'POST', '/auth/login', body={'username': u['name'], 'password': 'SecurePass123'}, node=1)
    check('auth login envelope and identity', login.get('user', {}).get('id') == u['id'] and len(login.get('token', '')) >= 16)
    request('auth wrong password', 'POST', '/auth/login', body={'username': u['name'], 'password': 'wrongwrong'}, status=401)
    for endpoint in ['/auth/me', '/workspaces']:
        request('auth missing token ' + endpoint, 'GET', endpoint, status=401)
        request('auth bogus token ' + endpoint, 'GET', endpoint, 'invalid-token-not-issued', status=401)


def workspaces():
    p, u, slug, ws, general = fixture()
    owner, outsider = u['owner']['token'], u['out']['token']
    check('workspace required fields', {'id','slug','name','owner_id','join_mode'} <= set(ws))
    check('workspace default open and owner', ws.get('join_mode') == 'open' and ws.get('owner_id') == u['owner']['id'])
    request('workspace duplicate slug', 'POST', '/workspaces', owner, {'slug': slug, 'name': 'Again'}, status=409)
    for value in ['Xbad', 'has space', 'a', 'a'*33]:
        request('workspace invalid slug ' + value[:12], 'POST', '/workspaces', owner, {'slug': value, 'name': 'Bad'}, status=400)
    listed = request('workspace owner list', 'GET', '/workspaces', owner, node=1)
    check('workspace owned visible cross-node', ws['id'] in [w.get('id') for w in listed.get('workspaces', [])])
    listed = request('workspace outsider list', 'GET', '/workspaces', outsider)
    check('workspace unrelated hidden', ws['id'] not in [w.get('id') for w in listed.get('workspaces', [])])
    request('workspace open auto-onboarding', 'POST', f'/channels/{general}/join', outsider, node=2)
    request('workspace repeat join', 'POST', f'/channels/{general}/join', outsider)
    members = request('workspace members list', 'GET', f'/workspaces/{slug}/members', owner, node=1).get('members', [])
    rows = [m for m in members if m.get('user_id') == u['out']['id']]
    check('workspace onboarded once as member', len(rows) == 1 and rows[0].get('role') == 'member')
    check('workspace member roles complete', {m.get('user_id'):m.get('role') for m in members} ==
          {v['id']: {'owner':'owner','admin':'admin','peer':'admin','guest':'guest'}.get(k,'member') for k,v in u.items()})
    request('workspace nonexistent channel join', 'POST', '/channels/not-a-real-channel/join', owner, status=404)


def roles():
    p, u, slug, ws, general = fixture()
    t = lambda k: u[k]['token']
    path = lambda k: f'/workspaces/{slug}/members/{u[k]["id"]}'
    result = request('roles owner promotes member', 'PATCH', path('member'), t('owner'), {'role':'admin'})
    check('roles update envelope', result.get('member', {}).get('user_id') == u['member']['id'] and result.get('member', {}).get('role') == 'admin')
    request('roles owner demotes admin', 'PATCH', path('member'), t('owner'), {'role':'member'})
    request('roles member forbidden', 'PATCH', path('guest'), t('member'), {'role':'member'}, status=403)
    request('roles guest forbidden', 'PATCH', path('member'), t('guest'), {'role':'guest'}, status=403)
    request('roles admin cannot modify peer', 'PATCH', path('peer'), t('admin'), {'role':'member'}, status=403)
    request('roles owner protected from admin', 'PATCH', path('owner'), t('admin'), {'role':'member'}, status=403)
    request('roles owner protected from self', 'PATCH', path('owner'), t('owner'), {'role':'admin'}, status=403)
    request('roles cannot PATCH owner', 'PATCH', path('member'), t('owner'), {'role':'owner'}, status=400)
    request('roles invalid enum', 'PATCH', path('member'), t('owner'), {'role':'superuser'}, status=400)
    request('roles admin demotes member to guest', 'PATCH', path('member'), t('admin'), {'role':'guest'})
    request('roles admin promotes guest to member', 'PATCH', path('member'), t('admin'), {'role':'member'})
    cpath = f'/workspaces/{slug}/channels'
    pub = request('roles member creates public', 'POST', cpath, t('member'), {'name':'public-'+p}, status=201)
    request('roles member private denied', 'POST', cpath, t('member'), {'name':'private-'+p,'is_private':True}, status=403)
    request('roles guest create denied', 'POST', cpath, t('guest'), {'name':'guest-'+p}, status=403)
    request('roles guest public join denied', 'POST', f'/channels/{pub["channel"]["id"]}/join', t('guest'), status=403)
    priv = request('roles admin private allowed', 'POST', cpath, t('admin'), {'name':'secret-'+p,'is_private':True}, status=201)
    request('roles private join without invite denied', 'POST', f'/channels/{priv["channel"]["id"]}/join', t('member'), status=403)
    request('roles channel duplicate', 'POST', cpath, t('owner'), {'name':'public-'+p}, status=409)
    transfer = f'/workspaces/{slug}/transfer_ownership'
    request('roles nonowner transfer denied', 'POST', transfer, t('admin'), {'new_owner_id':u['peer']['id']}, status=403)
    request('roles nonadmin transfer target denied', 'POST', transfer, t('owner'), {'new_owner_id':u['member']['id']}, status=400)
    request('roles owner transfer', 'POST', transfer, t('owner'), {'new_owner_id':u['admin']['id']})
    rows = request('roles transfer visible cross-node', 'GET', f'/workspaces/{slug}/members', t('admin'), node=2).get('members', [])
    mapping = {m.get('user_id'):m.get('role') for m in rows}
    check('roles transfer updates both roles', mapping.get(u['owner']['id']) == 'admin' and mapping.get(u['admin']['id']) == 'owner')
    check('roles exactly one owner', list(mapping.values()).count('owner') == 1)
    request('roles previous owner loses owner-only permission', 'POST', transfer, t('owner'), {'new_owner_id':u['peer']['id']}, status=403)
    request('roles new owner demotes former owner', 'PATCH', path('owner'), t('admin'), {'role':'member'}, node=1)
    request('roles demotion effective with old token', 'PATCH', path('guest'), t('owner'), {'role':'member'}, status=403, node=2)
    request('roles owner_id consistent after transfer', 'GET', '/workspaces', t('admin'), node=1)
    other = request('roles separate workspace', 'POST', '/workspaces', t('out'), {'slug':p+'-other','name':'Other'}, status=201)
    request('roles no authority in other workspace', 'POST', f'/workspaces/{p}-other/invitations', t('admin'), {}, status=403)


def invitations():
    p, u, slug, ws, general = fixture()
    owner, admin, member, outsider = [u[k]['token'] for k in ['owner','admin','member','out']]
    path = f'/workspaces/{slug}/invitations'
    accept = lambda code: f'/invitations/{code}/accept'
    def create(label, body=None, token=owner):
        return request(label, 'POST', path, token, body if body is not None else {}, status=201)['invitation']['code']
    for tag in ['member','guest']:
        for method, endpoint, body in [('POST',path,{}),('GET',path,None),('DELETE',path+'/bogus',None)]:
            request('invite '+tag+' '+method+' forbidden', method, endpoint, u[tag]['token'], body, status=403)
    code = create('invite owner creates default')
    check('invite nonempty code', isinstance(code,str) and bool(code))
    active = request('invite admin lists active', 'GET', path, admin).get('invitations', [])
    check('invite active code listed', code in [i.get('code') for i in active])
    request('invite accept across node', 'POST', accept(code), outsider, node=1)
    request('invite default exhausted', 'POST', accept(code), member, status=404, node=2)
    rows = request('invite acceptance adds workspace member', 'GET', f'/workspaces/{slug}/members', owner).get('members', [])
    check('invite accepted user present', any(m.get('user_id') == u['out']['id'] for m in rows))
    # Fresh users avoid imposing undefined repeat-acceptance semantics on members.
    x, y, z = [account(p, k) for k in ['extra1','extra2','extra3']]
    multi = create('invite explicit two uses', {'max_uses':2}, admin)
    request('invite multi use 1', 'POST', accept(multi), x['token'])
    request('invite multi use 2', 'POST', accept(multi), y['token'], node=1)
    request('invite multi exhausted', 'POST', accept(multi), z['token'], status=404, node=2)
    targeted = create('invite targeted id', {'invited_user_id':z['id'],'max_uses':3})
    request('invite wrong target denied', 'POST', accept(targeted), x['token'], status=403)
    request('invite target still usable', 'POST', accept(targeted), z['token'], node=2)
    request('invite targeted forced single use', 'POST', accept(targeted), x['token'], status=404)
    target_user = account(p, 'name_target')
    by_name = create('invite targeted username', {'invited_username':target_user['name']})
    request('invite username target accepts', 'POST', accept(by_name), target_user['token'])
    revoked = create('invite for revocation')
    result = request('invite admin revokes', 'DELETE', path+'/'+revoked, admin)
    check('invite revoke envelope', result.get('deleted') is True)
    request('invite revoked across node', 'POST', accept(revoked), owner, status=404, node=1)
    expired = create('invite short expiration', {'expires_in_seconds':1})
    time.sleep(1.15)
    request('invite expired', 'POST', accept(expired), owner, status=404)
    request('invite unknown', 'POST', accept('unknown-'+p), owner, status=404)
    active = request('invite active listing after use/revoke/expiry', 'GET', path, owner).get('invitations', [])
    check('invite inactive codes absent', not ({code,multi,targeted,by_name,revoked,expired} & {i.get('code') for i in active}))


def join_mode():
    p, u, slug, ws, general = fixture()
    owner, admin, member, outsider = [u[k]['token'] for k in ['owner','admin','member','out']]
    path = f'/workspaces/{slug}'
    request('settings member denied', 'PATCH', path, member, {'name':'No'}, status=403)
    request('settings invalid mode', 'PATCH', path, owner, {'join_mode':'invalid'}, status=400)
    changed = request('settings admin sets invite_only', 'PATCH', path, admin, {'join_mode':'invite_only'})
    check('settings mode persisted and name preserved', changed.get('workspace',{}).get('join_mode') == 'invite_only' and changed.get('workspace',{}).get('name') == 'Test workspace')
    request('join invite-only outsider forbidden', 'POST', f'/channels/{general}/join', outsider, status=403, node=2)
    request('join invite-only existing member allowed', 'POST', f'/channels/{general}/join', member)
    renamed = request('settings partial name update', 'PATCH', path, owner, {'name':'Renamed'})
    check('settings rename preserves mode', renamed.get('workspace',{}).get('name') == 'Renamed' and renamed.get('workspace',{}).get('join_mode') == 'invite_only')
    inv = request('join create invitation', 'POST', path+'/invitations', admin, {}, status=201)['invitation']['code']
    request('join redeem invitation', 'POST', '/invitations/'+inv+'/accept', outsider, node=1)
    request('join invited member allowed cross-node', 'POST', f'/channels/{general}/join', outsider, node=2)
    reopened = request('settings reopen workspace', 'PATCH', path, owner, {'join_mode':'open'})
    fresh = account(p, 'fresh')
    request('join open outsider allowed', 'POST', f'/channels/{general}/join', fresh['token'], node=1)
    listed = request('join membership visible cross-node', 'GET', '/workspaces', fresh['token'], node=2)
    check('join new workspace listed', ws['id'] in [w.get('id') for w in listed.get('workspaces',[])])


def main():
    parser = argparse.ArgumentParser()
    groups = {'auth':auth,'workspaces':workspaces,'roles':roles,'invitations':invitations,'join-mode':join_mode}
    parser.add_argument('--group', choices=groups)
    args = parser.parse_args()
    def deadline(signum, frame):
        raise TimeoutError('25-second deadline reached; run individual --group checks')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(25)
    try:
        for name, fn in groups.items():
            if args.group and args.group != name:
                continue
            try:
                fn()
            except TimeoutError:
                raise
            except Exception as e:
                check(name+' could not finish', False, type(e).__name__+': '+str(e))
    except TimeoutError as e:
        check('check deadline', False, str(e))
    finally:
        signal.alarm(0)
    print(f'RESULT: {passed} passed, {failed} failed', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
