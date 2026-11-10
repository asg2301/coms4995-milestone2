# Auth.1: register

Route covered: `POST /api/auth/register`.
Check: `bash /spec/auth-staged/checks/check_auth_1.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`.

## Uses

Nothing. This is the first spec.

## Adds

Define these as separate Python functions with these names. Auth.2 and Auth.3 call them.

| Function | What it does |
|---|---|
| `validate_username(username)` | True only for letters, digits and underscore, with no spaces |
| `validate_password(password)` | True only for 8 or more characters |
| `hash_password(password)` | Returns the stored form of a password. Never store the plain password |
| `create_user(username, password, display_name=None)` | Stores a new user and returns it. Signals a conflict if the username exists |
| `issue_token(user_id)` | Creates, stores and returns a new bearer token of 16 or more characters |
| `user_to_json(user)` | Returns the `user` object sent in responses. Never includes the password |

Users and tokens must be stored where all three nodes can read them.

## Rules

1.1 **Register.** `POST /api/auth/register` with body `{username, password}`, a valid
    username and a password of 8 or more characters, returns `201` and JSON with `user` and
    `token`. `user.username` is the username sent. `token` is at least 16 characters.

1.2 **Duplicate username.** Registering the same username a second time returns `409`.

1.3 **Invalid username.** A username containing a space returns `400`.

1.4 **Short password.** A password of 7 characters returns `400`.

Every request must answer within 5 seconds.
