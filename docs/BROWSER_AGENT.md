# Browser agent

`browser_task` controls a Playwright Chromium page using a constrained action loop.

## Supported actions

- `CLICK`
- `TYPE_TEXT`
- `SELECT`
- `SCROLL_UP`
- `SCROLL_DOWN`
- `WAIT`
- `DONE`
- `BLOCKED`

The LLM returns a JSON decision containing an operation, an observed target ID where required, optional text, confidence, and a short reason. Invalid decisions stop the task without mutation.

## Safety behavior

Every observation includes a fingerprint of the page state and visible action set. Immediately before execution, the browser is observed again. If the fingerprint changed, the action is rejected and the page must be observed again. Targets are also resolved through stable page-scoped DOM node IDs rather than model-generated selectors or positional model coordinates.

The executor does not accept model-generated selectors, JavaScript, shell commands, or coordinates. It only resolves IDs observed in the current snapshot.

`DONE` is independently checked against visible page evidence before success is returned. A model-only completion claim can produce an `unverified` result.
## Current limits

The current implementation does not yet provide:

- Full atomic DOM-node identity and independent browser verification from `jev-ultrafast`
- Shadow DOM, frames, canvas, uploads, or arbitrary keyboard scripts
- Confirmation policies for destructive or financial actions

Use `max_steps` conservatively and do not use the browser agent for destructive actions without adding explicit confirmation policies first.
