# Auth.2: login

Route covered: `POST /api/auth/login`.
Check: `bash /spec/auth-staged/checks/check_auth_2.sh`. The rule numbers below match its output.

Do not start this spec until `check_auth_1.sh` prints `RESULT: 4 passed, 0 failed`.

## Uses

Call these functions from Auth.1. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `hash_password(password)` | The stored form that `verify_password` compares against |
| `issue_token(user_id)` | The token returned by a successful login |
| `user_to_json(user)` | The `user` object in the response |

## Adds

| Function | What it does |
|---|---|
| `get_user_by_username(username)` | Returns the stored user, or `None` if there is none |
| `verify_password(password, stored_hash)` | True only if the password matches the stored form |

## Rules

2.1 **Login.** `POST /api/auth/login` with body `{username, password}` and the right
    password returns `200` and JSON with `user` and `token`.

2.2 **Wrong password.** Login with the wrong password returns `401`. Not `200`, not `400`.

Every request must answer within 5 seconds.
