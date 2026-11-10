# Auth.3: sessions (`me`, and tokens on every node)

Route covered: `GET /api/auth/me`.
Check: `bash /spec/auth-staged/checks/check_auth_3.sh`. The rule numbers below match its output.

Do not start this spec until `check_auth_1.sh` and `check_auth_2.sh` both pass.

## Uses

Call these functions from the earlier specs. Do not write a second version of any of them.

| Function | From | Used here for |
|---|---|---|
| `issue_token(user_id)` | Auth.1 | The tokens this spec looks up. Register and login both issue them |
| `user_to_json(user)` | Auth.1 | The `user` object in the response |
| `get_user_by_username(username)` | Auth.2 | Login on another node finds a user registered elsewhere |

## Adds

| Function | What it does |
|---|---|
| `get_user_by_token(token)` | Returns the user who owns the token, or `None` |
| `require_user(request)` | Reads `Authorization: Bearer <token>` and returns the user. Answers `401` if the header is missing or the token is unknown |

Every later route that needs a logged-in user must call `require_user`.

## Rules

3.1 **Me.** `GET /api/auth/me` with header `Authorization: Bearer <token>` returns `200` and
    JSON with `user`, where `user.username` is the username that registered.

3.2 **Me without a valid token.** `GET /api/auth/me` with no `Authorization` header returns
    `401`. So does a made-up token.

3.3 **Every node.** A user registered on port `8000` can log in on port `8002`. A token
    issued on port `8000` is accepted by `GET /api/auth/me` on ports `8001` and `8002`.

Every request must answer within 5 seconds.
