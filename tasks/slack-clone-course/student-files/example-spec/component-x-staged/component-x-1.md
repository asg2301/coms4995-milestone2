# X.1: <step name>

<!-- Replace everything in <angle brackets> with your own component step. -->

Route covered: `<METHOD /api/...>`.
Check: `bash /spec/component-x-staged/checks/check_component_x_1.sh`. The rule numbers below match its output.

Do not start this spec until `regression.sh` prints `REGRESSION: 0 broken`.

## Uses

Call these functions from auth. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `<auth function, for example issue_token(user_id)>` | `<what X.1 uses it for>` |

## Adds

| Function | What it does |
|---|---|
| `<new function>` | `<what it does>` |

## Rules

1.1 **<Rule name>.** `<request>` returns `<status>` and `<what the body must contain>`.

1.2 **<Rule name>.** `<request that should fail>` returns `<status>`. Not `<wrong status>`.

Every request must answer within 5 seconds.
