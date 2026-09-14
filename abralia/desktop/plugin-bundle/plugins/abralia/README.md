# Abralia

Connect enrolled Codex projects to the Abralia desktop keyboard app. The plugin
supplies semantic MCP tools, the Abralia skill and automatic lifecycle hooks.
Project enrollment, keyboard selection and project muting live in the app.

When the user explicitly asks to enable Abralia for a task, `enable_self` can
enroll that task's verified current project and acquire its own slot. It accepts
no arbitrary project path. Ordinary requests, plugin installation, passive
status checks and hooks never opt a task in. The skill uses explicit-only
invocation policy; registration and notification tools carry the same user-request
rule. Once enabled, the task can report progress and ask for attention until the
user disables it.

The `abralia` MCP server verifies a call's native Codex task ID and resolves its
workspace from local Codex metadata before it contacts the shared broker. A
model cannot choose another task or project through tool arguments. Unenrolled
projects and unavailable native context return a skipped result, allowing the
agent's main work to continue.

Hooks have **Abralia:** status labels and forward only bounded lifecycle/routing
metadata over private local IPC. They do not forward prompts, commands or answer
text, allocate slots, approve operations or launch a keyboard service. Hook trust
remains a separate review in Codex. Questions that are not exposed by hooks retain
the backend's existing native observation path.

Hooks use Codex's `PLUGIN_ROOT`; native plugin MCP uses an explicit plugin-relative
working directory and relative script argument. Both use the app-installed per-user Abralia runtime.
They do not assume an Applications installation, system Python or checkout path.
The environment variable `ABRALIA_RUNTIME_LAUNCHER` is available for isolated
development tests. Keep it unset for normal installations.

See the [bundle installation guide](../../README.md). License: [Apache-2.0](LICENSE).
