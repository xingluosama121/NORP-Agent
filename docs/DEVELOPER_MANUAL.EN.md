# NORP Agent Developer Manual

> **Version**: 0.9.4 | **License**: Copyright (c) 2026 xingluosama121, MIT Licensed
>
> 2026-08 revision: Chapter 24 rescue mode (low-level loop control + human takeover) | kernel fix: select timeout clamp (found by the stress suite; far timers crashed the loop on Windows) | new 35-item violent stress suite for the minimal async-loop core (test/stress_nasyncio_core.py) | 15.6 human-rescue manual tool takeover API (v0.9.3; operate all tools by hand when the model is down: tools / tool-call / manual / serve) | 3.9 task-level slot injection (submit(slot_overrides=...)) | 3.7 in-flight task races of assembly-slot hot rebuilds and the drain recommendation | 4.6.4 daemon worker-pool queue semantics and the stuck-task fallback matrix | 23.1 EventBus benchmark baseline and lock-contention boundary

---

## Table of Contents

- [Chapter 1 Quick Start](#chapter-1-quick-start)
- [Chapter 2 Overall Architecture: Layers and Data Flow](#chapter-2-overall-architecture-layers-and-data-flow)
- [Chapter 3 Architecture Layer and Address Functions](#chapter-3-architecture-layer-and-address-functions)
- [Chapter 4 Event Loop System: norpagent.nasyncio()](#chapter-4-event-loop-system-norpagentnasyncio)
- [Chapter 5 Frontend Family](#chapter-5-frontend-family)
- [Chapter 6 np() Startup and Lifecycle](#chapter-6-np-startup-and-lifecycle)
- [Chapter 7 Models and Tools](#chapter-7-models-and-tools)
- [Chapter 8 Sessions, Sandboxes, Schedulers, Context and Projects](#chapter-8-sessions-sandboxes-schedulers-context-and-projects)
- [Chapter 9 The 9-Layer 29-Hook System](#chapter-9-the-9-layer-29-hook-system)
- [Chapter 10 Security System: norpagent.safe()](#chapter-10-security-system-norpagentsafe)
- [Chapter 11 Plugin System](#chapter-11-plugin-system)
- [Chapter 12 Preset Modes](#chapter-12-preset-modes)
- [Chapter 13 Command-Line Entry](#chapter-13-command-line-entry)
- [Chapter 14 Embedded and Ultra-High-Concurrency Deployment](#chapter-14-embedded-and-ultra-high-concurrency-deployment)
- [Chapter 15 Work Rollback: Snapshots / Undo / Redo / Crash Rescue / Safe Mode](#chapter-15-work-rollback-snapshots--undo--redo--crash-rescue--safe-mode)
- [Chapter 16 Library Integration Examples](#chapter-16-library-integration-examples)
- [Chapter 17 Testing and Debugging](#chapter-17-testing-and-debugging)
- [Chapter 18 Migration Guide](#chapter-18-migration-guide)
- [Chapter 19 FAQ](#chapter-19-faq)
- [Appendix A Architecture Slot Quick Reference](#appendix-a-architecture-slot-quick-reference)
- [Appendix B 9-Layer Hook Quick Reference](#appendix-b-9-layer-hook-quick-reference)
- [Appendix C Public API Index](#appendix-c-public-api-index)
- [Chapter 20 Module Flow Orchestration (FLOW)](#chapter-20-module-flow-orchestration-flow)
- [Chapter 21 Built-in Components in Depth](#chapter-21-built-in-components-in-depth)
- [Chapter 22 Web UI and Frontend Deep Dive](#chapter-22-web-ui-and-frontend-deep-dive)
- [Chapter 23 Performance Design and Benchmarks](#chapter-23-performance-design-and-benchmarks)
- [Chapter 24 Rescue Mode: Low-Level Loop Control and Human Takeover](#chapter-24-rescue-mode-low-level-loop-control-and-human-takeover)
- [Appendix D Glossary](#appendix-d-glossary)
- [Appendix E 29-Hook Event Payload Quick Reference](#appendix-e-29-hook-event-payload-quick-reference)

---

## Chapter 1 Quick Start

### 1.1 Installation

```bash
pip install norpagent
```

The core package has no third-party dependencies and runs in a plain Python environment after installation (with the built-in mock model and tools).
Optional capabilities install on demand:

```bash
pip install norpagent[openai]       # OpenAI-compatible model adapter (DeepSeek/OpenAI/Qwen/vLLM/Ollama)
pip install norpagent[anthropic]    # Anthropic-protocol model adapter
pip install norpagent[web]          # Web search (web_search / web_fetch tools)
pip install norpagent[security]     # Plugin Ed25519 signature verification (cryptography)
pip install norpagent[all]          # everything
```

### 1.2 First Program

```python
import norpagent as np

np()                    # start with the default configuration (standard preset + Web frontend)
running = True
while running:
    if np.stop() == True:   # lifecycle function: exit when the application ends
        running = False
```

Save it as `hello.py` and run; the console prints:

```
[norpagent] frontend web listening on 127.0.0.1:8787
[norpagent] lazy-loaded modules: ...   # lazy-loaded modules actually used this run
```

Open the address in a browser to see the chat UI. See Chapter 6 for the startup flow. Two key points:

1. **`np()` is a module-level call** — the `norpagent` module itself is callable, equivalent to `norpagent.launch()`;
2. **`np.stop()` is a lifecycle function** — it returns `True` when the Agent application has ended and the main loop should exit.

### 1.3 Single-Task Mode

```python
import norpagent as np

np(prompt="explain in one sentence what an address function is")
running = True
while running:
    if np.stop() == True:
        running = False

engine = np.current()
print(engine.last_result.final_content)
```

With `prompt` given: the Agent executes this single task and stops automatically (`np.stop()` becomes `True`);
the result is stored in `np.current().last_result`.

### 1.4 Replacing the Frontend

```python
import norpagent as np

np(prompt="hi", frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
```

HeadlessFrontend reads no keyboard input and renders no UI; it is driven through the programmatic API.
Component replacement is done by filling a new address into a slot, without modifying framework core code.

### 1.5 Chapter Usage Cheat Sheet

| Usage | How |
|---|---|
| Start with the default configuration | `np()` |
| Check whether the application has ended | `np.stop()` |
| Single task | `np(prompt="...")` |
| Specify a preset mode | `np(preset="standard")` |
| Specify a model | `np(model="openai_compat")` |
| Specify the event loop | `np(async_loop="myapp.loop:create")` |
| Specify a frontend | `np(frontend="myapp.ui:create")` |
| Specify session storage | `np(session="sqlite")` |
| Specify the security level | `np(security="high")` |
| Web port / language | `np(port=9000, language="zh_CN")` |
| Custom main page | `np(html="/path/to/my.html")` |
| Custom module flow page | `np(flow_html="/path/to/flow.html")` |
| Frontend mounting an HTML path directly | `np(frontend="/path/to/my.html")` |
| Instance / value | `np(async_loop=loop_instance)` | Mount an existing object directly |

String-address resolution rules (`norpagent.arch.address.resolve_address`):

1. `"pkg.mod"` — loads that **file (module)**. It prefers the conventional factory
   attributes inside the module: `create` → `build` → `default`; if none exist,
   the **whole module** is mounted as the implementation;
2. `"pkg.mod:attr"` — loads the file and takes the named attribute inside the module;
3. `"pkg.mod:attr;key=value;key=value"` — extra config after the semicolon, injected
   into the factory's `config` parameter (e.g. `"norpagent.frontends.web:WebFrontend;port=9000"`).
   The semicolon clause is stripped before address resolution; the sub-config does
   not interfere with module-path resolution.

An address string points at a module file: the architecture layer loads the file
(or the object inside it) and mounts it into the slot.

Slot mounting parameter example (the `html` clause of the frontend slot):

```python
# Replace the / route's default page with a custom HTML file (without physically overwriting library files)
np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
```

### 3.3 String Semantics

For some slots the string value is not an address but a "registry component name". Each slot
declares its own string semantics in its SlotSpec:

| Semantics | Meaning | Slots |
|---|---|---|
| `address` | string = module address | async_loop, agent_runtime, frontend, context_store, project_manager |
| `name` | string = registry component name | tools (container semantics, see below) |
| `name_or_address` | try by name first, then by address | model, session, sandbox, scheduler, ui, preset |
| `literal` | string = literal value, address first | security (level), storage (path), hooks, plugins, logger, error_handler |

Here: `"mock"` in `np(model="mock")` is a model name in the registry;
the string in `np(model="myapp.model:create")` is an address.
`np(session="sqlite")` references the built-in SQLite session component;
`np(session="myapp.sessions:create")` loads a custom session implementation by address.

**v0.9.1: all slots support address loading + values inside dict values support pure-address resolution**

1. `name` / `name_or_address` slots: the string is looked up in the registry first;
   if not found it is loaded as a module address (`pkg.mod[:attr]`) — ui / preset have
   been upgraded from plain `name` to `name_or_address`: `np(ui="myapp.render:create")`,
   `np(preset="myapp.presets:build")` mount the implementation by address directly;
2. `literal` slots are "address first": a string **shaped like a pure address**
   (dotted identifier containing `.` or `:`, structurally judged by
   `norpagent.arch.address.is_address_like`) is loaded by address (resolution failure
   raises `AddressError`, no silent fallback); anything else keeps its literal value —
   `np(security="high")` is still a level, `np(storage="./data")` is still a path,
   while `np(security="myapp.sec:build_kit")` /
   `np(storage="myapp.store:create;root=./x")` load by address;
3. **values inside dict values support pure-address resolution**: any slot's dict-form
   value is processed uniformly (tools mappings / hooks mappings / custom-slot dict
   values, nested dicts recurse): if a value is a pure-address string it is resolved
   to an object by address (resolution failure raises `AddressError`)
   — `tools={"my_tool": "myapp.tools:create"}`,
   `hooks={"before_model_call": "myapp.guard:fn"}`. Resolved callables are called per
   the factory convention (layer / slot / config context injected, `;key=value`
   clauses parsed as factory config); **except the hooks slot** — its values are
   "callbacks themselves", and the callback function pointed to by an address is kept
   as-is, not called;
4. tools slot container: list elements support addresses just like a single string —
   `tools=["myapp.tools:create"]`, `tools="myapp.tools:create;tag=x"`;
   other string elements remain references to registered tool names (e.g. `tools=["echo"]`).

### 3.4 Factory Call Convention: Signature-Based Injection

When address resolution yields a **callable** (class or function), ArchLayer calls it
per `norpagent.arch.layer.call_factory`:

1. inspect the factory signature;
2. inject the context keys **the factory declared** (`layer` / `slot` / `config` /
   plus slot-specific keys, see Appendix A);
3. keys the factory did not declare are **silently ignored** (including factories that
   accept no context at all: called with no arguments);
4. non-callable objects (modules / instances / values) are **used as-is**, never called.

The following three forms are equivalent:

```python
# 1. class: no context declared -> instantiated with no arguments
class MyLoop:
    def __init__(self):
        ...

np(async_loop=MyLoop)

# 2. factory function: declares config -> injected automatically
def create(config=None, **kw):
    return MyLoop(timeout=float((config or {}).get("timeout", 0)))

np(async_loop=create)

# 3. string address + extra config clause
np(async_loop="myapp.loop:create;timeout=5")
```

### 3.5 ArchLayer: Observable Assembly Manifest

Every `np()` internally builds an ArchLayer and `connect()`s it.
The assembly result is observable:

```python
eng = np.current()
print(eng.layer.describe())
```

Example output:

```
== NorpAgent architecture-layer assembly manifest ==
  async_loop       <- default logic                         => NasyncioLoopRuntime
  agent_runtime    <- default logic                         => type
  model            <- default logic                         => (not connected)
  ...
  frontend         <- address 'norpagent.frontends.headless:HeadlessFrontend' => HeadlessFrontend
  preset           <- address 'minimal'                 => str
```

`(not connected)` means the slot was not specified and is handled by the preset's
declared default logic.

### 3.6 Example: Replacing Multiple Slots

```python
import norpagent as np

# model = openai_compat, session = sqlite, sandbox = pooled, frontend = Web (port 9000),
# loop = custom implementation, security level = high. All specified via slot parameters.
np(
    preset="standard",
    model="openai_compat",              # name reference
    session="sqlite",                   # name reference
    sandbox="pooled",                   # name reference
    frontend="norpagent.frontends.web:WebFrontend;port=9000",
    async_loop="myapp.nasync_loop:create",
    security="high",
)

while True:
    if np.stop():
        break
```

### 3.7 Runtime Hot Mount: Any Slot Can Be Replaced

After `np()` starts, **the engine keeps running**; replace any slot implementation at
any time, no restart needed:

```python
import norpagent as np

np()                                        # start (default Web frontend)
# ... application running ...

np.remount(model="openai_compat")           # swap the model: takes effect on the next run
np.remount(tools=["echo", "get_time"])      # swap the tool set: takes effect on the next run
np.remount(session="sqlite")                # swap session storage: AgentRuntime hot rebuild
np.remount(security="high")                 # swap the security level: old guard hooks unsubscribed first
np.remount(frontend="norpagent.frontends.console:ConsoleFrontend")
np.remount(async_loop="myapp.loop:create")  # swap the event loop: stop old, start new
np.remount(model="myapp.model:create")      # replace a module file at runtime (hot reload)
```

Underlying chain: `np.remount()` → `engine.remount()` → `ArchLayer.remount()`.
Before re-resolving a string address, the **module cache and .pyc bytecode cache are
invalidated**, so "edit the module file → remount" swaps in the changed code at runtime
without restarting the process.

Replacement semantics grouped by slot:

| Group | Slots | How it takes effect |
|---|---|---|
| Component slots | model / tools / hooks / security / plugins | remounted onto the registry and the final preset is rewritten; model / tools take effect on the next run() (the agent loop re-resolves the model and tool schemas on every run); repeatedly mounted architecture-level subscriptions are unsubscribed first then remounted, duplicate firing never stacks |
| Assembly slots | session / sandbox / scheduler / ui / agent_runtime / preset / context_store / project_manager | AgentRuntime hot rebuild: stop the old runtime (release sandboxes / components / unsubscribe the renderer) → build the new runtime per the current assembly → rebind the renderer on the frontend (the HTTP port stays the same) (in-flight task race and drain recommendation: see below) |
| Infrastructure slots | frontend / async_loop | stop the old implementation, start the new one; if the new implementation fails to start, the old one is rolled back automatically. On async_loop replacement, in-flight tasks on the old loop are abandoned — replace when idle |
| Base-service slots | logger / storage / error_handler | engine references updated directly, effective immediately |

**In-flight task race of assembly-slot hot rebuilds (Drain note)**: assembly-slot
remount goes through "stop old → build new → rebind frontend"; **none of these three
steps waits for in-flight `agent.run` tasks in the worker pool**. If the hot rebuild
happens while a task is executing:

1. `old.shutdown()` has closed the old sandbox; the in-flight run's next tool call
   hits a closed sandbox (failure or undefined behavior);
2. there is a window between unsubscribing the old renderer and subscribing the new
   one in which the in-flight run's events are lost; after the rebind the new data
   source receives **leftover events of the old run**, interleaved with the new run's;
3. the in-flight run's result is still returned to its submit caller — a result from
   a "dead runtime";
4. when the session slot is unchanged, old and new runs write the same session store
   (history stays continuous); when sandbox / scheduler / ui slots are swapped, the
   old instances referenced by the in-flight run have already been closed.

**Recommendation (production): two-phase hot mount (drain)** — ① first block new
tasks from entering (the business side maintains its own drain flag, checked before
`submit`); ② `loop.interrupt()` sets the cancel event of every in-flight task and
gives the worker pool a join grace period (e.g. 2s); ③ tasks that fail to exit in
time are covered by the sandbox-close semantics (PTC / pooled sandboxes force-kill
the process tree); ④ only then run the assembly-slot remount. The `interrupt()`
infrastructure already exists (`engine.request_stop` calls it as its first step), so
the implementation cost is low. Management-plane operations (config changes / component
swaps) are best done when tasks are idle; **the current framework version does not
ship built-in drain** — it is the business side's responsibility.

#### 3.7.2 Hot-Mounting Frontend Pages (html / flow_html parameters)

`frontend` is an infrastructure slot whose replacement semantics are "stop old, start
new". Together with WebFrontend's `html` / `flow_html` mounting parameters, the `/`
route page or the `/flow` module-flow page can be swapped at runtime — no process
restart, refresh the browser to see the new page:

```python
import norpagent as np

np(html="front.html")                       # start and mount a custom main page
# ... edit front.html or switch to another page file ...
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=front.html")
# the port stays the same; refreshing the browser (or reopening http://127.0.0.1:8787/) shows the new page

np.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=flow.html")
# swap the /flow module-flow orchestration page (the official mounting path for norp-flow.html)
```

Parameter priority: keys **explicitly given** by remount override startup parameters;
keys **not explicitly given** (e.g. port) reuse the startup parameters — so when only
the page changes, the browser URL stays the same. Explicit-key detection: for string
addresses take the keys in the `;key=value` clause; for instances take constructor
parameters whose values differ from defaults (html / flow_html judged by the
`_html` / `_flow_html` attributes). For example:

```python
np.remount(frontend="norpagent.frontends.web:WebFrontend;port=9000")  # change the port (restart HTTP listening)
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=")      # reset the main page to the library built-in
np.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=") # reset /flow to the library built-in
from norpagent.frontends.web import WebFrontend
np.remount(frontend=WebFrontend(html="front.html"))                   # instance form
np.remount(frontend=WebFrontend(flow_html="flow.html"))               # instance form
np.remount(frontend="front.html")     # HTML-path direct mount: equivalent to WebFrontend;html=front.html
```

**remount page hot-replace keys (v0.9, the simpler page-swap entry)**: `html` /
`flow_html` are not slots themselves but mounting parameters of the frontend slot —
`np.remount()` accepts these two keys directly and swaps the page immediately via
`mount_page` **without going through "stop old frontend / start new frontend"**
(the HTTP service is not restarted, the port stays the same; refresh the browser to
see the new page):

```python
np.remount(flow_html="flow-v2.html")       # /flow page swapped immediately (HTTP not restarted)
np.remount(html="front-v2.html")           # / main page swapped immediately
np.remount(flow_html="<html>...</html>")   # HTML content passed directly (leading "<" = content)
np.remount(flow_html=None)                 # unmount, fall back to the library built-in norp-flow.html
np.remount(flow_html="", html="")          # "" has the same semantics as None (unmount)
np.remount(flow_html="flow-v2.html",
           frontend="norpagent.frontends.web:WebFrontend")  # composable: set parameters first, then swap the frontend
```

Semantic details:

1. the value is first written into `engine.params` (the same data path as the
   `np(html=...)` startup passthrough); later frontend hot mounts / attach reuse the new value;
2. when the current frontend is the Web frontend, the page is swapped immediately via
   `mount_page`; for non-Web frontends (console / headless) only the parameter is
   updated, with no side effects;
3. bad paths fail fast with **pre-validation** (`ValueError`), leaving neither the
   slot change nor the page in a half-done state;
4. if the user registered a custom slot of the same name with `register_slot()`, the
   slot table takes priority
4. if the user registered a custom slot of the same name with `register_slot()`, the
   slot table takes priority (handled per the slot's semantics);
5. `remount(port=...)` / `remount(host=...)` and other network parameters are still
   not page keys — they error out and hint at the address-clause form (which restarts
   HTTP listening).

**Two mounting forms of the frontend slot (v0.9, equivalent coexistence)**:

1. Address form: `np(frontend="norpagent.frontends.web:WebFrontend;html=...")`
   — module address + clause parameters;
2. HTML-path direct mount: `np(frontend="front.html")` — when the slot value itself
   is a `.html/.htm` file path (with no `;` clause), the architecture layer no longer
   resolves it as a module address; the assembler automatically converts it to
   `WebFrontend(html=<that path>)`. A nonexistent file raises `ValueError` and fails
   fast (no silent fallback to the default frontend).

Note: HTML-path direct mount only affects the `/` main page; to swap the `/flow`
page use the `;flow_html=...` clause or `WebFrontend(flow_html=...)`.

**Swapping pages directly at runtime (HTTP service not restarted, port unchanged)**:

```python
# Way one: remount page hot-replace keys (recommended, v0.9)
np.remount(flow_html="flow.html")           # /flow swapped immediately
np.remount(html="front.html")               # / main page swapped immediately
np.remount(flow_html=None)                  # unmount, fall back to the library built-in

# Way two: frontend instance API
eng.frontend.mount_page("flow", "flow.html")   # /flow swapped immediately
eng.frontend.mount_page("flow", None)          # unmount, fall back to the library built-in
eng.frontend.mount_page("front", "<html>...</html>")  # same, for /
# equivalent entry: WebUI.mount_page(page, html)
```

**Physically replacing library HTML files takes effect automatically**: page byte
caches are validated by the resource file's mtime/size signature — overwrite
`norpagent/builtin/ui/assets/front.html` or `norp-flow.html` directly and refresh
the browser to see the new page (no remount, no restart); on a cache hit only a
single stat check runs, with no open+read disk I/O.

Notes: `np.remount()` is an **in-process API**; it must be called in the same Python
process that started the engine (it does not work across processes). When running in
cmd, put the remount inside the lifecycle loop or start a thread reading stdin to
implement "type a command to swap the page".

Notes:

1. it may only be called in the engine RUNNING state, otherwise `EngineError` is
   raised; illegal slot names also raise;
2. `remount(slot=None)` clears that slot's configuration (falls back to default logic);
3. preset object identity: after a hot mount, `registry.resolve_preset(name)` and
   `engine.agent.preset` remain the same instance (the frontend's hot rewriting of
   preset.tools relies on this convention);
4. `agent_runtime` is a `defer_factory` slot: the factory call is deferred to the
   engine assembly phase (after the registry / preset context is ready); the address
   clause `;key=value` is injected into the factory's config via `ArchLayer.subconfig()`.

### 3.8 Hot-Pluggable Slot Table: Registering Custom Slots

`np.remount()` swaps a slot's **implementation**; the slot table itself (`SLOT_SPECS`)
is also hot-pluggable — third-party libraries can register **brand-new custom slots**
at runtime, and registration plugs into the full pipeline (`np()` parameter validation,
ArchLayer assembly, `np.remount()` hot replacement, `layer.describe()` listing) with no
framework-source changes and no process restart:

```python
from norpagent.arch import SlotSpec, register_slot, unregister_slot

# custom slot = name + string semantics + application logic (applier)
register_slot(SlotSpec(
    name="audit_tag",                # slot name = np()'s keyword-argument name
    description="audit tag",
    protocol="literal string",
    string_semantics="literal",      # address / name / name_or_address / literal
    applier=_apply_audit_tag,        # called by the assembler when the slot value is non-empty
))
```

```python
import norpagent as np

np(audit_tag="release-1")            # applied at assembly time
np.remount(audit_tag="release-2")    # hot-replaced at runtime (the applier re-runs)
```

The `applier(reg, layer, value, params, ctx)` contract:

- `value` is the resolved slot value: for `address` semantics it is the instantiated
  implementation (the sub-config `;key=value` is obtained via `layer.subconfig(slot)`);
  for `name` / `name_or_address` / `literal` semantics it is the raw value;
- `ctx` provides four mutable containers: `components` (final-preset component
  declarations {kind: name}), `extras` (engine extra objects, consumed via
  `engine.extras[slot_name]`), `overrides` (preset-field overrides), `meta`
  (registry architecture metadata recording mountable/unsubscribable objects);
- **the same registry may be called repeatedly** (assembly + every `np.remount`), so
  the applier must be reentrancy-safe: repeated runs must not stack side effects —
  objects subscribed to the event bus are unsubscribed per `ctx["meta"]` records and
  then remounted (see the built-in hooks / security / plugins slots);
- `remount_rebuild_agent=True`: hot-rebuild the AgentRuntime after a hot replacement
  (assembly-type slots whose applier registers generic components into the preset's
  `components` should set True); default False: takes effect on the next run() or
  only updates extras.

Full example — registering a generic-component custom slot (the same assembly channel
as the built-in context_store):

```python
from norpagent.arch import SlotSpec, register_slot


def apply_vector_store(reg, layer, value, params, ctx):
    name = "_arch_vector"
    factory = value if callable(value) else (lambda v=value: v)
    reg.register_component("vector_store", name, factory)
    ctx["components"]["vector_store"] = name   # the preset declares the component
    ctx["extras"]["vector_store"] = value


register_slot(SlotSpec(
    name="vector_store",
    description="vector retrieval component (custom assembly slot)",
    protocol="any implementation (registered as a vector_store generic component)",
    string_semantics="literal",
    applier=apply_vector_store,
    remount_rebuild_agent=True,     # hot rebuild after hot replacement; the component takes effect immediately
))

np(vector_store=MyVectorStore())    # assembly: engine.agent.components["vector_store"]
np.remount(vector_store=Other())    # hot replacement: AgentRuntime hot rebuild
```

Protection and validation rules:

| Rule | Note |
|---|---|
| The 18 built-in slots are protected | cannot be registered / spec-overridden / unregistered (framework structural contract: engine, frontend, documentation references). Their **values** can be hot-replaced with `np.remount` at any time |
| Slot-name legality | a legal Python identifier (`np()` keyword argument), not a keyword, not `prompt` / `config` (launch special keys) |
| Duplicate names | raise `SlotError`; `register_slot(spec, replace=True)` hot-replaces the spec of a same-named custom slot (default address / semantics / applier / rebuild flag) |
| Illegal specs | a non-callable applier or an illegal `string_semantics` raises `SlotError`; a failed replace does not break the old spec |
| Unregister | `unregister_slot(name)` unregisters a custom slot and returns its spec; afterwards `np.remount(that_slot)` reports an unknown slot and `np(that_key=...)` falls back to a task parameter; already-mounted implementations stay as they are |
| Late registration | slots registered after the engine started: `layer.connect()` idempotently fills in (only connects missing slots), or directly `np.remount(slot=value)` goes through the full pipeline |

Top-level API: `np.register_slot` / `np.unregister_slot` /
`np.SlotSpec` / `np.SLOT_SPECS` / `np.is_builtin_slot` /
`np.snapshot_slots`; slot-table operations are thread-safe (RLock-protected; assembly
and hot mounts iterate over snapshots).

---

### 3.9 Task-Level Slot Injection: submit(slot_overrides=...)

`np()` startup assembly and `np.remount()` hot mounts are both **global** dimensions:
one change affects all subsequent tasks. Task-level slot injection is the third
dimension — temporarily overriding any slot implementation for the duration of a
**single task**, without affecting global configuration and without blocking other
in-flight tasks:

```python
import norpagent as np

engine = np(preset="standard")

# single task: temporarily swap the model + tools
r = engine.submit(
    "analyze this code",
    slot_overrides={
        "model": "anthropic",
        "tools": ["run_python", "file_read", "echo"],
        "sandbox": "isolated_python",
        "max_steps": 64,          # non-slot key: automatically falls back to a task parameter
    },
)
```

#### 3.9.1 Syntax and the Full Key Set

`engine.submit(text, session_id=None, task_params=None, slot_overrides=None)`
(the top-level `np.submit(...)` supports the same). The keys of `slot_overrides`
match `np()`'s slot parameters exactly (14 task-overridable keys):

| Key | Task-level semantics | Takes effect |
|---|---|---|
| `model` | swap this task's model provider (registered name / address / instance) | this run's model calls |
| `tools` | swap this task's tool set (registered-name list / addresses / Tool instances / {name: instance} mapping) | this run's schemas and tool execution |
| `sandbox` | a standalone temporary sandbox (registered name / address / instance), closed when the task ends | this run's tool execution |
| `session` | a standalone temporary session store (registered name / address / instance / `{"name": ..., "persist": True}`), by default does not pollute the global session table | this run's L4 session |
| `scheduler` | a standalone temporary scheduler (registered name / address / instance), closed when the task ends | this run's subtask submission |
| `hooks` | task-period hook subscriptions ({hook name: callback} or callable(registry)), unsubscribed when the task ends | this run's whole lifecycle |
| `security` | task-period security policy (level / dict / SecurityContext / callable), the original policy is restored when the task ends | this run's approvals / guards |
| `agent_runtime` | start a standalone Runtime instance for this task, destroyed after execution (does not affect the engine's default Runtime) | this task |
| `context_store` / `project_manager` | temporary generic components (registered component name / address / instance), closed when the task ends | this run's ctx.components |
| `async_loop` | this task executes on a standalone temporary event loop (no contention with the main loop's worker pool) | this task |
| `logger` / `storage` / `error_handler` | injected into this task's parameter context (params), readable by component factories and hooks (see 3.9.5) | this run |

Value forms match `np()` slots exactly: registered-name references / module addresses
(`pkg.mod[:attr]`, including `;key=value` clauses) / factories / instances; resolution
failure raises `AddressError`.

**Non-slot keys automatically fall back to task parameters**: when a key in
`slot_overrides` is not among the 14 keys above (e.g. `max_steps` / `task_timeout` /
`mock_script`), it is automatically merged into `task_params` and passed through to
the agent loop — the same data path as `np()`'s "slot-key split, the rest pass through
as parameters", so `slot_overrides={"max_steps": 64}` works out of the box.

**Keys that cannot be task-level overridden**: `frontend` / `ui` / `plugins` / `preset`
are process-level or engine-level structures (the I/O shell, the renderer, the plugin
loader, the component-composition baseline) and fall outside a single task's override
boundary; passing them falls back to task parameters (no error, but also no slot-override
effect — use `np.remount` instead).

#### 3.9.2 Priority: Task-Level > remount > Startup Assembly > Preset

| Level | Source | Priority |
|---|---|---|
| 1 | `submit(slot_overrides=...)` | highest |
| 2 | `np.remount(slot=...)` | second |
| 3 | startup `np(slot=...)` | third |
| 4 | preset declarations | lowest |

Task-level overrides take a **snapshot at submit() time**; later global `np.remount`
does not affect in-flight tasks (3.9.3). This complements "Drain + remount": if you
do not want to wait for draining, override; if you do not want to override, drain and
then remount.

#### 3.9.3 Implementation Boundary and Isolation Semantics

Implementation boundary (no kernel-loop changes, only one extra resolution layer):

1. `AgentRuntime.run()` accepts a `slot_overrides` dict at its entry;
2. a slot snapshot layer (`_TaskSlotLayer`) is created for this task and stored in
   `TaskContext` (`ctx.task_slot_layer` / `ctx.slot_overrides`);
3. during the `run()` lifecycle, all component resolution paths (model / tools /
   sandbox / session / scheduler / context_store / project_manager) **check the task
   layer first**, falling back to runtime defaults when not overridden;
4. when the task ends, the task layer is released together with the RunResult
   (`finally` fallback): all temporary sandboxes / sessions / schedulers / components
   are `close()`d, task-period hook subscriptions are unsubscribed, the temporary
   security policy is unloaded and the original `registry.security` is restored — no
   residual state.

**Relation to global remount (isolation semantics)**:

| Scenario | Global slot | Task override | What this task sees | What other tasks see |
|---|---|---|---|---|
| no override | model=A | — | model=A | model=A |
| model overridden | model=A | model=B | model=B | model=A |
| hot rebuild after override | model=A | model=B | model=B (this task still uses its own snapshot) | model=C (new tasks) |
| override cancelled | model=A | task ends | — | model=A |

Key semantics: a task-level override takes a slot snapshot at submit() time; later
global remounts do not affect in-flight tasks.

**agent_runtime override**: a standalone Runtime instance is started for this task
(constructed with the same factory-calling convention as `_build_agent`: registry /
preset / ui / task_params / layer / config injected by signature), destroyed after
execution; the engine's default Runtime is unaffected. Note: the child runtime shares
the same event bus and renderer as the default runtime, so task events may be rendered
once by each of their subscribed renderers (same behavior as `run_task` child agents —
an existing property of the shared bus).

**session override**: this task's dialog history is written to a standalone temporary
session store, not polluting the global session table — unless `persist=True` is
explicitly given:

```python
# temporary session: the session does not appear in the global session table
# temporary session: the session does not appear in the global session table
engine.submit("hi", slot_overrides={"session": "memory"}, session_id="s1")

# persist: write this task's history back into the global session table when the task ends
engine.submit("hi", slot_overrides={
    "session": {"name": "memory", "persist": True},
}, session_id="s2")
```

Note: disk-backed sessions (e.g. `sqlite`) build standalone instances, but if no
standalone database file is specified (address form `...;path=...`), records still
go to the default database file — for full isolation use the address form or an
instance value.

#### 3.9.4 Relation to Multi-Agent Orchestration

If you understand multi-agent as "multiple tasks independently configured with
different roles", task-level slot injection is the cleanest implementation — three
concurrent tasks, each with its own model + tools, without interference:

```python
import threading

futures = []
for key, ov in [
    ("write the plan", {"model": "claude", "tools": ["write"]}),
    ("review the code", {"model": "deepseek", "tools": ["read"]}),
    ("run the deployment", {"model": "mock", "tools": ["exec_cmd"]}),
]:
    t = threading.Thread(
        target=lambda k=k, o=ov: engine.submit(k, slot_overrides=o),
    )
    t.start()
    futures.append(t)
for t in futures:
    t.join()
```

Each task takes its own snapshot independently at submit time: concurrent tasks'
model / tools / session overrides do not affect each other, nor do they affect the
global configuration.

#### 3.9.5 Concurrency and Boundary Notes

- **zero registry pollution**: task-level model / tool instances are held directly by
  the snapshot layer and never `register_*`d into the registry; references are
  released when the task ends. Concurrent tasks' same-named tools each hold their own
  instance, never overwriting each other.
- **hooks / security are task-period temporary state**: subscribed / installed when
  the task starts, unsubscribed / restored when the task ends (including exception
  paths). The callable form of task-level security is managed by the caller itself
  (`registry.security`; the framework cannot track its internals); the string /
  dict / SecurityContext forms are restored automatically.
- **logger / storage / error_handler semantics are parameter injection**: task-level
  overrides of these three keys are injected into `params` (visible to component
  factory ctx and hook payloads), and do **not** replace engine-global references —
  avoiding concurrent tasks trampling engine-level objects.
- **concurrent override safety**: the snapshot layer is a per-task independent
  instance, thread-safe; any number of concurrent tasks on the same runtime each
  carry their own slot snapshot without interference.
- **submission-failure semantics**: resolution of task-level overrides (address
  loading etc.) happens at the `run()` entry (inside the worker thread); resolution
  failure raises `AddressError` / `ComponentError` which bubbles up at the blocking
  `submit()` return (same as ordinary task exceptions).

---

## Chapter 4 Event Loop System: norpagent.nasyncio()

### 4.1 The Loop System Is an Independent Architecture Function

The event loop determines how tasks are scheduled: which thread tasks run on, how
they are interrupted, and how they are woken up. NorpAgent provides the loop system
as an independent architecture function:

```python
import norpagent as np

loop = np.nasyncio()                       # default loop (self-developed nasyncio core)
loop = np.nasyncio("myapp.loop:create")    # custom loop
```

It is equivalent to the slot:

```python
np(async_loop="myapp.loop:create")   # equivalent to np.nasyncio("myapp.loop:create")
```

`np.nasyncio()` returns a **LoopRuntime** (protocol below). The scheduling core run
by the default implementation is the library's built-in **self-developed nasyncio
event loop** (`norpagent.nasyncio`, originally nasync_io, now packaged into the
library) — it **does not depend on or import the standard asyncio** (declaration and
reasons in 4.7). To use another event-loop implementation, implement the LoopRuntime
protocol and fill the `async_loop` slot with an address — no framework core changes.

> The top-level `norpagent.nasyncio` (i.e. `np.nasyncio`) binds to the self-developed
> core **module** (callable): `np.nasyncio()` returns the default LoopRuntime
> implementation; `np.nasyncio.EventLoop` / `Future` / `Task` directly access core
> types; `import norpagent.nasyncio` yields the same core module. The architecture
> function itself lives in `norpagent.loops.nasyncio`.

### 4.2 The LoopRuntime Protocol

```python
class LoopRuntime(Protocol):
    name: str
    def start(self) -> None: ...          # start the loop (usually a dedicated internal thread running run_forever)
    def stop(self) -> None: ...           # request a stop (thread-safe)
    def is_running(self) -> bool: ...     # whether it is still running
    def join(self, timeout=None) -> None: ...   # wait for the loop thread to exit
    def submit(self, fn, *args, **kwargs) -> Any: ...
        # execute the synchronous function fn in the loop context and block returning its result
```

The engine (`norpagent.runtime.engine.NorpEngine`) interacts with the loop only
through this protocol and never imports any concrete loop implementation.

### 4.3 Default Implementation: NasyncioLoopRuntime (Self-Developed nasyncio Core)

The default implementation is based on the library's built-in **self-developed
nasyncio event loop** (no dependency on the standard asyncio): a dedicated thread
runs `norpagent.nasyncio.EventLoop` (run_forever), and `submit()` hands synchronous
functions to the **own daemon worker pool** and waits for the result (why not the
standard thread pool: see 4.6 and 4.7).

Configuration (embedded / high-concurrency tuning; see Chapter 14):

| Config | Note | Default |
|---|---|---|
| `max_workers` | daemon worker-pool thread count | `max(4, cpu_count)` |
| `poll_interval` | submit/run_async completion-poll interval (s) | `0.05` |

Passed via `np(config={"loop": {"max_workers": 8}})` (or the environment variables
`NORPAGENT_MAX_WORKERS` / `NORPAGENT_SUBMIT_POLL`; equivalent forms
`np.nasyncio(max_workers=8)` and `np(async_loop="norpagent.loops.nasyncio:NasyncioLoopRuntime")`
share the same construction source).

```python
loop = np.nasyncio()
loop.start()
result = loop.submit(lambda: 1 + 1)   # -> 2
loop.stop()
loop.join()
```

The optional `run_async(coro)` capability submits a coroutine to the loop thread via
the self-developed core's `run_coroutine_threadsafe` and blocks returning the result
(the engine defaults to submit(); this is for custom coroutine entry points).

### 4.4 Custom Loop Example

```python
# myapp/simple_loop.py -- loop implementation example (synchronous direct execution; useful for tests or embedded scenarios)
class SimpleLoop:
    name = "simple"

    def __init__(self, **kw):
        self._running = False

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    def join(self, timeout=None):
        pass

    def submit(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)   # execute synchronously and directly


def create(**kw):                    # module-level factory (the address "myapp.simple_loop" hits it automatically)
    return SimpleLoop(**kw)
```

Mount it:

```python
import norpagent as np

np(async_loop="myapp.simple_loop", prompt="hi",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
```

### 4.5 Cross-Thread Bridging Notes (Fixed in the Self-Developed Core)

**Item one (fixed in the self-developed core)**: the standard asyncio's
`Future.result()` is not a thread-safe blocking wait; cross-thread
`add_done_callback()` goes through `loop.call_soon` when the future is already
finished (only pushes to the `_ready` queue without writing the self-pipe), so a
loop blocked on the selector never gets the wakeup and the waiter hangs. The
library's built-in self-developed core (`norpagent.nasyncio.Future`) fixes both
pitfalls: completion notification from any thread goes through
`call_soon_threadsafe` (writes the self-pipe, waking the loop immediately). The
default runtime's `submit()` still uses the "executor thread writes the result +
threading.Event set" pattern — submit tasks may block for a long time, so running
them in the daemon worker pool, fully decoupled from the loop, is more robust
(unrelated to asyncio).

**Item two (run_async cross-thread waiting)**: `NasyncioLoopRuntime.run_async`
submits the coroutine to the loop thread via the self-developed core's
`run_coroutine_threadsafe` (internally call_soon_threadsafe + self-pipe wakeup, no
wakeup race), and waits with a concurrent.futures.Future done-callback + polling
Event set (concurrent.futures guarantees the callback fires synchronously in the
thread that wrote the result). Note that `run_async` cannot be called inside the
loop thread (a blocking wait would stall the loop) — `await` the coroutine directly,
or use `submit()` instead.

Rule: cross-thread coordination uses "executor thread writes the result +
threading.Event set"; the loop thread is not a necessary link in the wakeup path.

### 4.6 Ctrl+C and Task-Cancellation Semantics

#### 4.6.1 The Problem: the main thread is not inside the event-loop entry, so why might Ctrl+C fail

After `np()` starts, the main thread only does lifecycle polling (`np.stop()`); the
real worker threads execute tasks in the background, and the caller (e.g. the console
REPL's main thread) blocks on `loop.submit()`'s wait. Two pitfalls on the signal chain:

1. **On Windows a single `Event.wait()` never sees Ctrl+C**: SIGINT is delivered to
   the main thread as a pending interrupt, checked only at **bytecode boundaries**;
   when the main thread blocks in `Event.wait()` (the underlying
   `WaitForSingleObject` waits forever), it never returns to a bytecode boundary and
   Ctrl+C is as good as dead.
   **Solution**: `NasyncioLoopRuntime.submit()` uses **polling wait** (passing a
   bytecode boundary every ≤`poll_interval` seconds, default 0.05s — tightened from
   0.2s since 0.9 for lower task-completion perception latency; embedded scenarios
   can raise it), so Ctrl+C surfaces immediately as `KeyboardInterrupt`.
2. **Worker threads stuck in blocking I/O cannot be killed; the process freezes**:
   SIGINT reaches only the main thread; a worker thread stuck in a sandbox
   `subprocess` / HTTP request can only wait for its own timeout; worse, the standard
   thread pool (ThreadPoolExecutor, asyncio's default executor) registers its worker
   threads in CPython's `threading._threads_queues`, which are **force-joined** at
   interpreter exit — if a task never ends, the process cannot exit.
   **Solution**: the worker pool uses bare daemon threads (no join at exit);
   meanwhile the "cancel" signal is explicitly handed to the task body (see 4.6.2)
   so it exits by itself as early as possible.

#### 4.6.2 The Cancel Signal: contextvars + cancel event

`NasyncioLoopRuntime.submit()` wraps every task in a contextvars context carrying a
cancel event (`norpagent.loops.cancel`); the body can check it at any time:

```python
from norpagent.loops.cancel import cancel_requested, current_cancel_event

def my_tool(args, ctx):
    for chunk in fetch_stream(...):      # long task / long streaming read
        if cancel_requested():           # Ctrl+C / engine stop -> True
            return ToolResult(output="task cancelled", success=False)
```

Trigger paths (setting the cancel event):

| Trigger | Timing | Behavior |
|---|---|---|
| `KeyboardInterrupt` | the submit waiter receives Ctrl+C | sets this task's cancel event and bubbles the exception |
| `loop.interrupt()` | first step of `engine.request_stop()` | sets the cancel event of every in-flight task |
| `loop.stop()` | stopping the loop | same as above (calls interrupt internally) |

Built-in components' response to cancellation (since 0.7.0):

- **PTC sandbox** (isolated_python): the execution loop checks every ≤0.5s; on
  cancellation it immediately force-kills the child process and returns
  `exit_code=-1` (stderr notes "task cancelled");
- **pooled sandbox**: `run_shell` waits in slices (checks every ≤0.5s); on
  cancellation it kills the process tree and marks the instance damaged;
- **model calls**: `params["_cancel_event"]` is injected even when call_timeout=0
  (previously only the timeout path had it); the openai_compat streaming loop checks
  it on every chunk;
- **the agent main loop**: checks `cancel_requested()` at every step boundary and
  wraps up as `stopped` (on_task_stopped).

A cancel event merely *suggests* the body exit; if a task is stuck in an
uninterruptible system call (e.g. DNS resolution, a D-state process), the final
fallback is still each component's own timeout (SDK connection timeout /
call_timeout / ptc_timeout), and process exit is guaranteed by daemon threads.
**Note: the daemon worker pool itself has no task execution time budget and no
pool-level watchdog** — a stuck task occupies the whole pool; the boundary and
fallback matrix are in 4.6.4.

#### 4.6.3 Thread-Boundary Notes: what goes into the loop and what does not

`norpagent.nasyncio()` is the **architecture function of the async_loop slot**; its
default implementation (NasyncioLoopRuntime) runs the library's built-in
self-developed nasyncio event loop. All task scheduling in the library
(engine.submit → loop.submit) goes through its protocol. The remaining bare threads
are **deliberate blocking-I/O pumps** and should not enter the event loop:

| Thread | Duty | Why not the loop thread |
|---|---|---|
| `norpagent-loop-pool-*` | executes submit's synchronous tasks | the task body may block (sandbox/HTTP); putting it on the loop thread would stall the whole loop |
| `norpagent-nasync-loop` | runs the self-developed nasyncio event loop | the loop itself |
| sandbox pipe readers (PTC/pooled/plugin hosts) | read child-process stdout/stderr | blocking pipe reads, uninterruptible |
| `norpagent-webui` / request threads | HTTP service and requests | socketserver's own thread model |
| `norpagent-model-*` | call_timeout hard-interrupt watchdog | time-limited join; abandoned on timeout |

Rule: **computation and scheduling go into the loop (replaceable); blocking I/O uses
daemon thread pumps (not cancellable, but never block exit)**. Replacing the
`async_loop` slot swaps the whole loop system (protocol in 4.2) without touching any
other part of the framework.

#### 4.6.4 Daemon Worker-Pool Queue Semantics and the Stuck-Task Fallback (honest boundaries)

Semantic boundaries of the `NasyncioLoopRuntime` daemon worker pool (`_DaemonPool`):

- **unbounded queue, no rejection policy**: internally a `queue.Queue()` (no
  maxsize); `submit_nowait` enqueues with `put_nowait` — never blocks, never raises
  `Full`. When the pool is saturated, new tasks **pile up unboundedly** in the queue,
  and the caller's `submit()` waits forever in the `done.wait(poll_interval)` polling
  loop, with unlimited latency (no fast-fail);
- **cancellation is cooperative**: the cancel event only takes effect at boundaries
  the body actively checks (4.6.2); two scenarios cannot be interrupted — ① stuck in
  C-extension pure computation (e.g. `re` regex backtracking): the cancel event is
  visible only at bytecode boundaries; ② custom tools doing raw `Popen + communicate`
  (non-sandbox path) have no slice checks and no process-tree kill: stuck means stuck;
- **one stuck task -> throughput drops to zero**: each worker runs one task at a
  time, with no task execution time budget, no thread abandonment, and no watchdog;
  if a task gets stuck and occupies the whole pool, all subsequent submits pile up
  and business-side throughput approaches 0 — there is no spare thread underneath.
all subsequent submits pile up and business-side throughput approaches 0 — there is
no spare thread underneath.

**Existing fallback precedents (the "time budget + thread abandonment" pattern)**:

| Scenario | Fallback mechanism |
|---|---|
| model calls | `_call_model_with_timeout`: worker thread + `join(timeout)`; on timeout the thread is abandoned as an orphan (daemon, filtered and reclaimed on every run) |
| engine stop | `request_stop`: close task with `t.join(timeout=5.0)`; on timeout `_close()` runs in the current thread as a fallback |
| PTC / pooled sandboxes | the execution loop checks the cancel event in slices every ≤0.5s; on timeout / cancellation the process tree is force-killed |
| worker-pool tasks (bare tools / user tasks) | **none** — no timeout budget; a stuck task occupies the pool |

**Evolution suggestion (roadmap, not implemented)**: introduce a pool-level "task
execution time budget" — a deadline watchdog inside each worker that sets the cancel
event on timeout, marks the task abandoned, and force-kills the associated sandbox
process (reusing the model-call timeout's abandoned-thread pattern); optionally make
the pool a bounded queue or add a submit-timeout fast-fail (rejecting new tasks
instead of piling up unboundedly). For embedded scenarios, prefer splitting tasks
smaller and tightening component-level timeouts (call_timeout / ptc_timeout) rather
than relying on a pool-level fallback.

### 4.7 Explicit Declaration: norpagent Does Not Depend on the Standard asyncio; the Scheduling Core Embraces the Self-Developed nasyncio

**Declaration**: since 0.8 the norpagent library has **zero `import asyncio`**. The
default event-loop core is the self-developed async-IO library `norpagent.nasyncio`
packaged into the library (originally nasync_io, v2.0.0); its underlying dependencies
are only the **non-asyncio** modules of the Python standard library: `threading` /
`queue` / `heapq` / `selectors` / `socket` / `concurrent.futures` / `subprocess` /
`os` / `time`. Verify with `grep -R "import asyncio" norpagent/` — empty.

**Why embrace the self-developed nasyncio and drop the standard asyncio**:

1. **scheduling, cancellation and cross-thread wakeup semantics are fully defined by
   in-library code (control)**. The standard asyncio has well-known semantic
   pitfalls, e.g.:
   - `Task.cancel()` is not thread-safe (directly manipulates the loop thread's
     ready queue);
   - cross-thread `add_done_callback()` on a finished Future goes through
     `call_soon` without writing the self-pipe; a loop blocked on the selector
     never gets the wakeup, and the waiter hangs;
   - there is no external "cancel the main task" entry — an outside thread cannot
     force-interrupt a coroutine being awaited (stop latency depends on the current
     operation and can reach minutes).
   The self-developed core fixes each: `Task.cancel()` is cross-thread safe; Future
   completion notification automatically goes through `call_soon_threadsafe`
   writing the self-pipe; `EventLoop.abort_main()` provides thread-safe immediate
   stopping.
2. **the dependency surface shrinks to an auditable size**. All event-loop behavior
   (trampoline scheduling, the timer heap, socketpair self-pipe wakeup, cancellation
   penetration) is in-library code — the audit surface is one self-developed core
   file; it does not drag in the standard asyncio's internal implementation details
   and version differences (selector behavior differs across Python versions).
3. **exit semantics are controllable**. The standard thread pool is not used for
   submit: ThreadPoolExecutor (including asyncio's default executor) registers its
   worker threads in `threading._threads_queues`, which are force-joined at
   interpreter exit — if a task is stuck in a sandbox subprocess / HTTP call, the
   process freezes. The default runtime uses a bare daemon thread pool + self-pipe
   wakeup, so the process wraps up immediately after Ctrl+C (4.6.1).
4. **API semantics align; migration is zero-cost**. The self-developed core provides
   same-named APIs one-to-one with asyncio usage (`EventLoop` / `Future` / `Task` /
   `Event` / `Lock` / `Condition` / `sleep` / `wait_for` / subprocess wrappers /
   `run_coroutine_threadsafe`); code familiar with asyncio migrates by swapping
   `import asyncio` for `import norpagent.nasyncio`, and `CancelledError` /
   `TimeoutError` semantics stay consistent.
5. **the self-developed core works standalone**. `norpagent.nasyncio` itself is a
   usable miniature async library (originally nasync_io, packaged into the library)
   that can be imported and run independently of the norpagent framework; the
   framework merely plugs it into the `async_loop` slot through the LoopRuntime
   protocol.

**Compatibility**: the old 0.7 address `norpagent.loops.std_asyncio:StdLoopRuntime`
remains as a compatibility shim (re-exports the same implementation, does not import
asyncio); historical code keeps working. New code uses
`norpagent.loops.nasyncio:NasyncioLoopRuntime` (see 4.3).

```python
import norpagent as np
import norpagent.nasyncio as core  # self-developed core module (callable)

print(core.__version__)          # 2.0.0
loop_rt = np.nasyncio()          # default LoopRuntime implementation (same as core())
print(loop_rt.name)              # nasyncio
print(core.EventLoop)            # self-developed event-loop class
```

---

## Chapter 5 Frontend Family

### 5.1 Two Layers: frontend (Shell) and ui (Renderer)

The frontend family has two layers:

| Layer | Slot | Protocol | Duty |
|---|---|---|---|
| shell | `frontend` | `Frontend` | where input is read from, when to start/stop, handing input to the engine |
| render | `ui` | `UIAdapter` | subscribes to the event bus and renders Agent events into text/UI |

A frontend usually carries a ui renderer; both can be replaced independently.

### 5.2 The Frontend Protocol

```python
class Frontend(Protocol):
    frontend_id: str
    def attach(self, engine) -> None: ...   # bind the engine (engine.submit / engine.request_stop)
    def start(self) -> None: ...            # start (internally spawns background threads; must not block)
    def stop(self) -> None: ...             # stop (thread-safe)
    def is_alive(self) -> bool: ...         # liveness check
```

### 5.3 Built-in Frontends

| Frontend | Address | Notes |
|---|---|---|
| Web (default) | `norpagent.frontends.web:WebFrontend` | HTTP + SSE, no third-party dependencies; page = front.html (multi-tab sessions / streaming render / settings / plugin panels), independent entry `/flow` = norp-flow.html module-flow orchestration; the console prints `listening on http://127.0.0.1:8787/`; configurable via `;port=9000`, `;html=custom main page`, `;flow_html=custom flow page` (slot mounting parameters, see 5.4) or `np(port=9000, language="zh_CN")`; the frontend slot value can be a `.html` path directly (HTML-path direct mount, v0.9) |
| Console REPL | `norpagent.frontends.console:ConsoleFrontend` | explicit opt-in; `/exit` (or `exit`/`quit`/`exit()`) exits, `/reset` starts a new session; switches to synchronous mode automatically inside the Python interactive interpreter |
| Headless | `norpagent.frontends.headless:HeadlessFrontend` | pure API; default in `prompt` mode; output (body / tools / results) prints to stdout |

### 5.4 Web UI Behavior and Configuration Persistence

Web UI behaviors and configuration:

| Capability | Notes |
|---|---|
| config persistence | after saving in the settings panel, written to `~/.norpagent/webui_config.json` (`NORPAGENT_WEBUI_CONFIG` overrides; `WebUI(config_path=...)` can specify, `None` disables). Disk loading accepts only the `DEFAULT_CONFIG` whitelist keys; unknown keys are dropped. Since 0.9 **disk loading is deferred to `start()`**: constructing WebUI triggers no disk I/O (embedded / read-only-root friendly); the priority explicit constructor params > disk values > defaults is unchanged |
| page anti-caching | page responses carry `Cache-Control: no-store`; the browser fetches the latest front.html on every refresh; the server reads the page bytes into an in-memory cache (0.9: GET / no longer reads disk per request), validated by the resource file's mtime/size signature — **physically replacing library HTML files takes effect automatically on refresh**, with only a single stat check on cache hits |
| page mounting (html / flow_html params) | both the `/` route default page and the `/flow` module-flow page are fully replaceable: `html` / `flow_html` accept **file paths** or **HTML content** (after strip, a leading `<` means content, otherwise a file path); a nonexistent file raises `ValueError` at construction (fast fail, no silent fallback). No need to physically overwrite `norpagent/builtin/ui/assets/front.html` / `norp-flow.html`. At runtime `mount_page(page, html)` swaps the page directly (HTTP service not restarted, port unchanged); `mount_page(page, None)` unmounts and falls back |
| disconnect handling | client disconnects (WinError 10053 / EPIPE etc.) are handled silently, internal errors logged at DEBUG; disconnected SSE connections are reclaimed by non-blocking probing within ≤1s (0.9, prevents thread pileup) |
| SSE backpressure (0.9) | per-connection bounded event buffer + batched frame writes; slow clients degrade automatically; memory stays bounded under ultra-high concurrency — see 14.3 |
| port bump | on bind failure (including Windows 10013 listening-occupied) bumps up to 10 ports backward/forward; the actual port wins |
| request-body guard | negative Content-Length treated as no body; bodies over 1MB rejected |
| shutdown idempotence | `shutdown()` idempotent + same-thread deadlock guard, callable across threads; `block_on_close=False` does not wait for connections to close on stop (0.9) |
| event routing | event sid resolution prefers the original browser-session id registered by `submit()`; the session manager supports creating with a specified id (`create_session(title=..., session_id=...)`); when the kernel resumes a session the id matches the browser tab |

```python
import norpagent as np
from norpagent.builtin.ui.web import WebUI
from norpagent.frontends.web import WebFrontend

# custom config persistence location (default ~/.norpagent/webui_config.json)
ui = WebUI(port=9000, config_path="./my_app/webui.json")
# config_path=None disables disk reads/writes
ui2 = WebUI(port=9000, config_path=None)
```

**Page mounting (html / flow_html params) — four equivalent forms:**

```python
import norpagent as np

# 1. slot-address clause (;key=value, recommended)
np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
np(frontend="norpagent.frontends.web:WebFrontend;flow_html=/path/to/flow.html")

# 2. constructor params directly (both WebFrontend / WebUI support)
np(frontend=WebFrontend(html="<html><body>my UI</body></html>"))
np(frontend=WebFrontend(flow_html="/path/to/flow.html"))

# 3. config dict
np(config={"web": {"html": "/path/to/my.html", "flow_html": "/path/to/flow.html"}})

# 4. runtime-parameter passthrough
np(html="/path/to/my.html", flow_html="/path/to/flow.html")

# 5. HTML-path direct mount (v0.9): the frontend slot value itself is a .html path,
#    equivalent to form 1's html= clause
np(frontend="/path/to/my.html")

# a nonexistent file path errors at construction (fast fail, no silent fallback to the default page)
# ValueError: WebUI html mount parameter is neither HTML content (starts with '<') nor an existing file: ...
```

**Hot-swapping pages at runtime (HTTP service not restarted, port unchanged):**

```python
eng = np()                                  # or np.current() to get the running engine

# way one: remount page hot-replace keys (recommended, v0.9)
np.remount(flow_html="/path/to/flow.html")  # /flow swapped immediately
np.remount(html="/path/to/front.html")      # / main page swapped immediately
np.remount(flow_html=None)                  # unmount, fall back to the library built-in

# way two: frontend instance API
eng.frontend.mount_page("flow", "/path/to/flow.html")  # /flow swapped immediately
eng.frontend.mount_page("flow", None)                  # unmount, fall back to the library built-in
eng.frontend.mount_page("front", "<html>...</html>")   # same for the / route
# equivalent lower-level API: WebUI.mount_page(page, html)
```

**The official module-flow frontend norp-flow.html**: the `/flow` standalone entry
(drag modules / beam connections / real backend execution / auto-save), shipped with
the library at `norpagent/builtin/ui/assets/norp-flow.html`; without a mount it is
the official page, and mounting `flow_html` replaces it wholesale. Physically
replacing the file also takes effect automatically (see "page anti-caching" above).

**Repository-root `front.html` (multi-host frontend)**: the pywebview desktop
protocol frontend has been refactored into a multi-host transport bridge — under a
browser host it automatically constructs `window.pywebview.api` (fetch + SSE
implementing all methods, translating library events into the text-event protocol
T:/R:/C:/U:/E:/Q:); the desktop host stays compatible as-is. Mount directly:

```python
np(html="front.html")   # relative to the working directory; the library reads it as a file path
```

After mounting, chat / sessions / settings / plugin panels / file browsing all go
through the library's REST API (contract in `norpagent/builtin/ui/web.py`'s
do_GET / do_POST); SSH remote and mobile remote control were stripped from the
library version — their UI entries hide automatically and bridge methods degrade as
placeholders.

Input-box selectors (first-intuition design):

- **Mode**: `/api/presets` lists all registry presets (minimal / standard / ptc /
  creative / longrun / embedded); selecting hot-switches via config `preset_name`
  (`engine.remount(preset=...)`, AgentRuntime hot rebuild; disabled while tasks run,
  `*_arch` derived presets are not shown);
- **Model**: pulls the remote model list from `api_base` (`/api/models`); selecting
  saves `model` and takes effect immediately, with "fetch from URL / model settings"
  entries;
- **Reasoning strength**: clicking cycles through off / low / medium / high and saves
  immediately.

The debug panel (Settings → Agent Debug) itemizes version / frontend / preset /
model / tools / plugins / sessions, with the raw JSON folded under the "raw data"
section.

### 5.5 Custom Frontend Example

```python
# myapp/tray_frontend.py -- frontend implementation example
import threading

class TrayFrontend:
    """Tray-style frontend: reads no keyboard; input is submitted via method calls."""

    frontend_id = "tray"

    def __init__(self, **kw):
        self.engine = None
        self.alive = False

    def attach(self, engine):
        self.engine = engine
        self.alive = True

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def is_alive(self):
        return self.alive

    # frontend custom capability: application code hands user input to the engine from here
    def send(self, text):
        return self.engine.submit(text)
```

Mount and drive it:

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend", preset="standard")
fe = np.current().frontend
result = fe.send("hello")        # the engine executes the Agent in the background loop
print(result.final_content)
np.shutdown()
```

### 5.6 The UIAdapter Renderer Layer

```python
class UIAdapter(Protocol):
    ui_id: str
    def on_event(self, event) -> None: ...            # render one AgentEvent
    def ask_user(self, question, default="") -> str: ...  # human approval / clarification Q&A
    def notify(self, message, level="info") -> None: ...
```

Swap renderers: `np(ui=MyRenderer())` or `np(ui="web")` (reference a registered name).

### 5.7 Module-Flow Canvas (FLOW) and FE Frontend Modules

`/flow` is a standalone frontend category "module flow": it draws the agent assembly
process as a canvas (modules = blocks, ports = registered hooks, beam connections =
execution links); when RUN is pressed the graph is submitted to the backend and
executed topologically with real registry components. The canvas auto-saves to
`~/.norpagent/flow_graph.json` and restores automatically on refresh / restart; when
"apply to agent" in the top bar is on, front chat tasks execute per that flow
(behavior hot-switch). See `docs/flow.md`.

**FE frontend modules (file-as-frontend)**: dragging a `.html / .js / .ts` file onto
the canvas registers it as a frontend module (the "frontend FE" group in the module
dock); the backend hosts it at `/fe/<name>` (the card's "↗" opens a new tab). Each FE
has an **independent config scope** (no interference; defaults come from the global
"connection settings" config), read/written via `GET/POST /api/fe/config?fe_id=...`,
persisted to `~/.norpagent/fe_configs/<fe_id>.json`.

FE nodes have **three forms 1/2/3** (switched with the card-title-bar button):

| Form | Meaning |
|---|---|
| 1 | global-settings node: config written to "connection settings" (scope=global) |
| 2 | FE-as-settings-set: independent config scope (scope=fe, default form) |
| 3 | split into setting-item subcards: one member row per config item (draggable / connectable individually) |

**Input-box family (every place that needs input is an input box)**:

- FE / global-settings node cards render each config item (api_key / api_base /
  model / project_root / plugin_dirs / temperature / max_tokens / max_steps /
  task_timeout / system_prompt / language) as a row of
  "IN port · label · input box · OUT port"; values are written directly on the card
  and auto-save with a 500ms debounce; each member row of form 3 is also an input box;
- model / tool / sandbox / other node cards carry a **value input-box strip** at the
  bottom (context / query / code / value edited directly); the TR card's prompt input
  box,
(context / query / code / value edited directly); the TR card's prompt input box and
the PATH card's path input box stay embedded;
- model fields are always **hand-editable input boxes + datalist hints** (the flow
  connection-settings dialog, the WebUI settings dialog, the model node's instance
  field): if the remote model list fetch fails or the model is not in the list, type
  any model name directly (empty = engine default);
- card input boxes and the right-side node panel sync bidirectionally; when a beam
  connects to a settings port the connection action itself takes effect immediately
  (written to the independent / global config).

**Canvas-management trio (new in 0.6.8, keeps the canvas tidy)**:

- `Alt+left-drag` on empty space = **box select**; modules intersecting the box all
  highlight; `Del` deletes them in bulk;
- `Ctrl+A` = **select all**; `Del` deletes in bulk;
- the top bar's **"Clear canvas"** = one-click deletion of all modules and beams
  (confirmation dialog prevents accidental clicks), auto-save immediately after
  confirmation; after clearing, refreshing stays empty (the backend saves the empty
  graph; loading no longer falls back to the example template);
- **accidental-injection entries removed**: double-clicking empty canvas to inject
  and one-click dock-card quick-inject are gone for good — previously, accidental
  `other` nodes were auto-saved and frozen, causing "a pile of other nodes the moment
  the canvas opens". Now dragging is the only injection path.

**DeepSeek model names**: `deepseek-chat` / `deepseek-reasoner` were officially
retired on 2026-07-24; the current models are `deepseek-v4-flash` / `deepseek-v4-pro`.
The backend `list_models` cache, flow snapshots (`filter_remote_models`), the frontend
hint list and the remote-model dock all filter retired names
(`RETIRED_REMOTE_MODELS` / `RETIRED_MODELS` / `RETIRED_REMOTE`); historical caches
never show old names again.

**Connection-settings dialog** (flow top bar): shows immediately, not blocked by the
remote model fetch; "fetch model list" uses the form's current Key/Base for an
immediate request (no need to save first); clicking outside does not close it
(Esc / × / Cancel close); input boxes auto-save and apply on blur.

**Agent-tool mounting (new in 0.6.10, module tools → front auto-invocation)**:

- **config keys** (`DEFAULT_CONFIG`): `agent_tools` (explicit full tool-set list)
  + `agent_tools_explicit` (bool; True = explicit; False = follow the preset default set);
- **write entries**: ① the `AGENT` badge on tool cards in the `/flow` module dock
  (the frontend calls `POST /api/agent/tools {tools, explicit}`); ② the "🧰 agent
  tools" checkbox list in the WebUI settings dialog (saved into `agent_tools` via
  `save_config`). `GET /api/config` returns `tools_info` (all tools + native/module
  origins) and `agent_effective_tools` / `agent_base_tools`, which the frontend uses
  to render;
- **hot application**: `WebFrontend._apply_agent_tools()` rewrites `preset.tools`
  to the explicit set (unregistered tool names filtered automatically) or a snapshot
  of the preset default set (`WebFrontend._base_tools`, captured at attach); the next
  `run()`'s `registry.tool_schemas(preset.tools)` then includes module tools, and the
  model calls them automatically per the OpenAI function schema;
- **restart restore**: at the end of `WebFrontend.attach()`, the saved config
  re-applies the tool set (without changing existing model-config behavior);
- **fallback semantics**: `set_agent_tools` automatically falls back to non-explicit
  (`agent_tools=[]`) when the explicit set equals the preset default set, so preset
  evolution is followed automatically;
- **snapshot fields**: `/api/flow/snapshot` top level returns `agent_tools` /
  `agent_base_tools`, driving the flow page's badge state.

---

## Chapter 6 np() Startup and Lifecycle

### 6.1 Reading the Startup Code

```python
import norpagent as np
np()                    # ①
running = True
while running:
    if np.stop() == True:   # ②
        running = False
```

① `np()` — the `norpagent` module is callable (module-class replacement). It is
equivalent to `norpagent.launch()`, which internally does, in order:

1. **parameter sorting**: keyword arguments whose names match the slot table (the 18
   built-in slots + custom slots registered at runtime via register_slot) → slot
   values; the rest → task parameters (e.g. `max_steps` / `task_timeout` /
   `workspace_root`); special keys `prompt` (single-task text) and `config` (dict-form
   slot assignment);
2. **architecture-layer assembly**: `ArchLayer(config, **slots)` → `mount_defaults()`
   (registers each slot's library built-in default logic) → `layer.connect()`
   (resolves addresses, calls factories, obtains each slot's implementation);
3. **registry assembly**: `build_registry(layer)` installs built-in components and
   presets, then writes the slot overrides into the final preset;
4. **engine start**: `NorpEngine(layer, registry, preset, loop, frontend, ...)`
   → `engine.start()`: assemble the agent runtime → bind the frontend → start the
   loop thread → start the frontend thread → enter RUNNING;
5. **singleton semantics**: when an engine is already running, another `np()` returns
   the current engine directly.

② `np.stop()` — the lifecycle function. Returns `True` when the engine has entered
STOPPED (the application has ended; the main loop should exit); also returns `True`
when there is no engine.

### 6.2 Engine Lifecycle State Machine

```
STARTING ──start()──▶ RUNNING ──request_stop()──▶ STOPPING ──▶ STOPPED
```

| State | Meaning | Entry condition |
|---|---|---|
| STARTING | assembling | inside `np()` |
| RUNNING | accepts input, executes tasks | `engine.start()` finished |
| STOPPING | winding down | `request_stop()` |
| STOPPED | finished | wind-down complete (`np.stop()` is True) |

Stop-request wind-down order (`NorpEngine.request_stop`):

1. stop the frontend (input loop exits);
2. close the Agent (release sandboxes/components, broadcast `on_agent_shutdown` —
   the L1 lifecycle hook);
3. unsubscribe the renderers additionally subscribed by the engine;
4. stop the loop thread and wait for it to exit;
5. set STOPPED.

### 6.3 Three Run Modes

**Main-loop mode** (default Web frontend):

```python
np()                            # default frontend = Web (frontend web listening on 127.0.0.1:8787)
while True:
    if np.stop():
        break
```

Open the printed address in a browser to see the chat UI (front.html). With other
frontends, specify explicitly:
`np(frontend="norpagent.frontends.console:ConsoleFrontend")`.

**Single-task mode** (when `prompt` is given, the headless frontend is used
automatically and output prints to stdout):

```python
np(prompt="summarize README", preset="standard")
while True:
    if np.stop():
        break
print(np.current().last_result.final_content)
```

**Pure-API mode** (headless + programmatic submit):

```python
np(preset="minimal", frontend="norpagent.frontends.headless:HeadlessFrontend")
eng = np.current()
result1 = eng.submit("first question")
result2 = eng.submit("follow-up", session_id=result1.session_id)   # continue the same session
eng.request_stop()
```

> **Note**: `np()` does not block; the engine runs on background threads. The main
> thread should poll with `np.stop()` (or call `np.current().wait()`). If the main
> thread simply ends the process, the daemon engine threads exit with it; the library
> registers an atexit fallback cleanup.
>
> **Special case**: with the **console frontend** explicitly selected, calling `np()`
> inside the Python interactive interpreter (`>>>` REPL) automatically switches to
> **synchronous mode** — `np()` blocks until the user exits (`/exit`, `exit()`,
> Ctrl+C or EOF); no polling loop needed during that time. In synchronous mode the
> main thread owns stdin exclusively. The default Web frontend also works in the REPL
> (background service + page interaction, without blocking the interpreter).

### 6.4 The Full np() Parameter Set

```python
np(
    # -- architecture slots (18 built-in; empty = default logic; custom slots
    #    registered via register_slot take parameters here too, see 3.8) --
    async_loop=..., agent_runtime=..., model=..., tools=...,
    session=..., sandbox=..., scheduler=..., context_store=...,
    project_manager=..., hooks=..., security=..., plugins=...,
    frontend=..., ui=..., preset=..., logger=..., storage=...,
    error_handler=...,
    # -- special keys --
    prompt="single-task text",      # stops automatically after finishing
    config={"slot": value, ...},    # dict-form slot assignment
    # -- model shortcut parameters (effective when model is a built-in adapter name) --
    model_name="deepseek-v4-flash", # remote model name
    base_url="https://api.deepseek.com/v1",   # remote service address
    api_key="sk-...",               # API key (also read from environment variables)
    # -- Web frontend runtime parameters (passed through to WebFrontend when frontend=web) --
    port=8787,                      # HTTP port (auto-bumps up to 10 when occupied)
    host="127.0.0.1",               # listen address
    open_browser=False,             # whether to open the browser automatically
    language="zh_CN",               # UI language (en / zh_CN)
    html="/path/to/my.html",        # custom main page: file path or HTML content
                                    # (replaces the / route default page, see 5.4)
    sse_queue_size=1024,            # SSE per-connection buffer cap (0=unlimited, 0.9)
    sse_queue_policy="drop_oldest", # drop_oldest / drop_newest / unlimited
    # -- remaining keys = task parameters, passed through to the agent loop --
    max_steps=32, task_timeout=0, call_timeout=0,
    workspace_root=..., system_prompt=...,
)
```

Sub-key conventions of the `config` dict (0.9, embedded / high-concurrency tuning;
see Chapter 14):

```python
np(config={
    "loop": {"max_workers": 8, "poll_interval": 0.02},   # loop worker pool and polling
    "web": {"port": 9000, "sse_queue_size": 2048,        # Web UI and SSE backpressure
            "sse_queue_policy": "drop_oldest"},
    "preset": "embedded",                                 # slot assignment same as keywords
})
```

> With `preset="embedded"` and no explicit frontend, the default frontend is
> automatically headless (no HTTP service; pure-API mode), see 12.1 and Chapter 14.

### 6.5 Lifecycle ↔ L1 Hook Correspondence

The engine state machine aligns with the L1 layer of the 9-layer hook system:

| Engine event | Hook / event |
|---|---|
| agent runtime constructed | `on_agent_init` (L1) |
| task submitted | `on_task_start` |
| engine stopped | `on_agent_shutdown` (L1) |

Lifecycle subscription: `np(hooks={"on_agent_init": fn, ...})`
(the hook system is in Chapter 9).

---

## Chapter 7 Models and Tools

### 7.1 The Model Slot

The model slot accepts:

```python
np(model="mock")                  # registry name (built-in mock / openai_compat / anthropic)
np(model=MyProvider())            # instance
np(model="myapp.model:create")    # address (resolved as an address when the string matches no registered name)
```

The ModelProvider protocol (`norpagent.protocols.model`):

```python
class ModelProvider(Protocol):
    def generate(self, messages, tool_schemas, params) -> ModelOutput: ...
    def stream(self, messages, tool_schemas, params): ...   # optional: incremental output
```

When `stream` exists the kernel prefers the streaming path (broadcasting
`on_content` per segment); otherwise it calls `generate` once. `params["_cancel_event"]`
is the cancel event injected by the kernel; adapters should exit as early as possible
accordingly (paired with the hard timeout).

### 7.2 The Tool Slot

Three assignment forms:

```python
np(tools=["echo", "get_time"])           # name list: registry references
np(tools={"my_tool": MyTool()})          # mapping: register and enable
np(tools=[ToolA(), ToolB()])             # instance list: registered by name
```

The Tool protocol (`norpagent.protocols.tool`):

```python
class Tool(Protocol):
    name: str
    def schema(self) -> dict: ...        # OpenAI function schema
    def run(self, args: dict, ctx: RunContext) -> ToolResult: ...
```

Built-in tool list (`install_defaults` registration): `echo`, `get_time`,
`run_python` (PTC sandbox execution), `file_read / file_write / file_list /
file_delete` (path-safe), `exec_cmd` (sandbox protocol), `web_search /
web_fetch / web_extract_links` (SSRF protection), `context_add / search /
list / delete` (FTS5 context store), `project_status`, `task_submit /
list / status / cancel` (long-running task cooperation).

### 7.3 Example: Model Benchmarks

The minimal preset uses a deterministic environment and the smallest tool set,
suitable for comparing different models' outputs on a fixed input set:

```python
import norpagent as np

for model_name in ("mock", "openai_compat"):
    np(preset="minimal", model=model_name, prompt="1+1=?",
       frontend="norpagent.frontends.headless:HeadlessFrontend")
    while True:
        if np.stop():
            break
    r = np.current().last_result
    print(model_name, r.steps, r.usage.total_tokens, r.final_content[:40])
    np.shutdown()
```

---

## Chapter 8 Sessions, Sandboxes, Schedulers, Context and Projects

### 8.1 Sessions

```python
np(session="memory")     # in-process (default)
np(session="sqlite")     # persisted to ~/.norpagent/sessions.db
np(session=MySessionManager())          # instance
np(session="myapp.sessions:create")     # address
```

SessionManager protocol: `create_session / get_session / append_message /
history`. Continue a conversation across sessions via `session_id`:

```python
eng = np.current()
r1 = eng.submit("remember: my favorite color is blue")
r1 = eng.submit("remember: my favorite color is blue")
r2 = eng.submit("what is my favorite color?", session_id=r1.session_id)
```

### 8.2 Sandboxes

```python
np(sandbox="subprocess")   # child process (default)
np(sandbox="pooled")       # pooled reuse + concurrency cap + timeout force-kill of the process tree
np(sandbox="myapp.docker_sandbox:create")
```

Sandbox protocol: `run / close`. The `exec_cmd` tool executes through the sandbox
protocol; swapping in a container/pooled sandbox implementation requires no tool-code
changes.

### 8.3 Schedulers

```python
np(scheduler="simple")       # in-memory queue (default)
np(scheduler="persistent")   # persistent + crash resume() continuation
```

TaskScheduler protocol: `submit / drain / cancel`. The `task_*` tool family lets the
model orchestrate long-running tasks; `agent.run_task()` is the multi-agent
orchestration entry (subtasks can specify a different mode via `preset_name` =
different child agents).

### 8.4 Context Store and Project Management (the generic-component namespace)

```python
np(context_store="norpagent.builtin.context:FTS5ContextStore")
np(project_manager=MyProjectManager())
```

These two slots use the **generic-component namespace**
(`registry.register_component`); the component kinds are open — you can register new
kinds and declare them in presets without modifying the kernel.

### 8.5 Base-Service Slots

```python
np(logger=logging.getLogger("my.app"))       # logging
np(storage="./my_data")                       # persistence root
np(error_handler=lambda exc, eng: print(exc))  # last line of defense for errors
```

`error_handler` is called on task-level exception fallback (signature
`(error, engine)`); when omitted, errors are recorded to the logger.

---

## Chapter 9 The 9-Layer 29-Hook System

> Design principle: **every execution structure must be exposed as an API and be
> intervenable by hooks.** Every hook is an independent module-level API; custom
> hooks and custom layers are supported; zero dependencies, standard library only.
> Read this chapter together with `docs/hooks.md` (the standalone hook-system
> document).

### 9.1 Hook Layers and the Full 29-Hook Table

The agent loop is cut into 9 layers, each exposing hooks:

```
L1 runtime lifecycle ─ L2 task ─ L3 input ─ L4 session & history ─ L5 message assembly
   ─ L6 step ─ L7 model call ─ L8 tool call ─ L9 result finalization
```

All 29 hooks are first-class objects importable under `norpagent.hooks`
(`from norpagent.hooks import before_model_call, ...`); full table:

| Layer | Hook | Mutating | Payload keys |
|---|---|---|---|
| L1 runtime | `on_agent_init` | | preset |
| L1 runtime | `on_agent_shutdown` | | preset |
| L2 task | `on_task_start` | | task_id, session_id, preset, user_input |
| L2 task | `on_task_done` | | task_id, session_id, content, steps, context |
| L2 task | `on_task_error` | | task_id, error |
| L2 task | `on_task_stopped` | | task_id, reason |
| L2 task | `on_task_timeout` | | task_id, timeout, kind |
| L3 input | `before_input` | ✓ | task_id, user_input, session_id, params |
| L3 input | `after_input` | | task_id, user_input, session_id |
| L3 input | `on_user_input_required` | | question, default |
| L4 session | `before_session_create` | ✓ | session_id, title, params, task_id |
| L4 session | `after_session_create` | | session_id, title, task_id |
| L4 session | `before_message_append` | ✓ | session_id, message, task_id |
| L4 session | `after_message_append` | | session_id, message, task_id |
| L5 assembly | `before_build_messages` | ✓ | system_prompt, session_id, step, task_id, tool_names |
| L5 assembly | `after_build_messages` | ✓ | messages, system_prompt, step, task_id |
| L6 step | `before_step` | ✓ | task_id, step, messages, context, params |
| L6 step | `after_step` | | task_id, step, content, tool_calls |
| L7 model | `before_model_call` | ✓ | task_id, step, messages, tool_schemas, params |
| L7 model | `after_model_call` | ✓ | task_id, step, output |
| L7 model | `on_reasoning` | | task_id, content, stream |
| L7 model | `on_content` | | task_id, content, stream, final |
| L7 model | `on_event` | | event_type, data, task_id |
| L7 model | `on_usage_update` | | task_id, input, output, total |
| L8 tool | `before_tool_call` | ✓ | task_id, tool_name, args, context |
| L8 tool | `after_tool_call` | ✓ | task_id, tool_name, args, result, success, context |
| L8 tool | `on_tool_error` | | task_id, tool_name, error, args |
| L9 finalize | `before_result` | ✓ | task_id, result |
| L9 finalize | `after_result` | ✓ | task_id, result |

A ✓ marks a **mutating hook** (mutating=True): subscribers can rewrite the data flow
through return values, or veto with one vote by raising `HookVeto`; the rest are
observation hooks (emit) whose return values are ignored. Each hook's complete
payload keys are governed by `Hook.payload_keys` (consistent with the hook comments
in `norpagent.hooks.standard`).

### 9.2 Three Usage Forms and Subscription-Target Resolution

```python
from norpagent.hooks import before_model_call

# 1. module-level independent API: without system, it lands on the "process default hook system"
before_model_call.subscribe(log_request)                # default system (private bus)
before_model_call.subscribe(log_request, system=reg)    # specify a Registry

# 2. runtime view: same bus as registry.hooks (recommended for multiple instances)
agent.hooks.before_model_call.subscribe(log_request)

# 3. slot bulk subscription: np(hooks={"before_model_call": my_fn})
```

- The module-level `Hook` object's `subscribe / unsubscribe / emit / intercept`
  all need a `system` to locate the bus; you may pass a `HookSystem / EventBus /
  Registry / AgentRuntime` (unified resolution via `_resolve_bus`);
  **by default it lands on the process-level default system**
  (`hooks.get_default_system()`, with its own private bus) — it is NOT the same bus
  as the `np()` engine's. When using a standalone Registry, **always pass `system`
  explicitly** (every Registry carries a private bus, guaranteeing multi-instance
  isolation); otherwise the subscription hangs on the default system and never
  receives engine events;
- `agent.hooks.before_model_call` returns a `BoundHook` (bound to that engine's
  bus); its four methods no longer need `system`;
- the `np(hooks={...})` slot (literal semantics): dict keys are event names, values
  are subscribers, mounted on the engine bus at assembly time; hot-mounting
  `np.remount(hooks=...)` unsubscribes the previous architecture-level subscriptions
  first and then remounts — **never stacking**. The slot value can also be a
  `callable(reg)` factory: called first, then the returned dict is subscribed.

### 9.3 Mutating-Hook Return Semantics and HookVeto

Mutating hooks dispatch through `EventBus.intercept`: **subscribers are called in
subscription order; the first non-None return value wins; all-None means no
intervention.** Full return-semantics table:

| Hook | Return | Effect |
|---|---|---|
| `before_input` | `str` | replace the user input |
| | `HookVeto(reason)` | the task wraps up as stopped; the reason enters the error info |
| `before_session_create` | `str` / `{"title": str}` | rewrite the session title |
| | `HookVeto` | abandon creation (the task wraps up as stopped) |
| `before_message_append` | `ChatMessage` | replace the message |
| | `False` / `HookVeto` | drop that message (not persisted) |
| `before_build_messages` | `str` / `{"system_prompt": str}` | replace the system prompt |
| `after_build_messages` | `List[ChatMessage]` | replace the whole message set |
| `before_step` | `List[ChatMessage]` | replace this round's messages |
| | `HookVeto` | skip this round's model call (go to the next round) |
| `before_model_call` | `{"messages": [...], "params": {...}}` | replace the request as needed |
| | `HookVeto` | refuse this round's call (the task wraps up as stopped) |
| `after_model_call` | `ModelOutput` | replace this output |
| `before_tool_call` | `dict` | replace the tool arguments |
| | `False` / `HookVeto` | block the call (backfilled with a blocked_by_hook result) |
| `after_tool_call` | `str` / `ToolResult` | replace the tool result |
| `before_result` / `after_result` | `RunResult` | replace the final result |
| both | `HookVeto` | keep the original result (the veto is ignored) |

`HookVeto` behavior details:

- the type is defined in `norpagent.kernel.events` (re-exported via
  `norpagent.hooks`); it subclasses `Exception`; the constructor argument is the
  veto reason;
- `EventBus.intercept` does **not catch** HookVeto — the veto semantics must reach
  the kernel, guaranteeing that a one-vote veto always takes effect; ordinary
  subscriber exceptions are caught and logged (stderr, or a logger specified via
  `bus.set_error_logger()`) and dispatch continues — a single subscriber can never
  drag down the main loop;
- each execution point's veto wrap-up semantics differ (see the table):
  before_input / before_model_call / before_session_create → the task is stopped;
  before_step → skip this round; before_tool_call → backfill blocked_by_hook;
  before_message_append → drop that message; before_result / after_result → the
  veto is ignored and the original result stands;
- subscriber dispatch order: all-event subscribers (`bus.subscribe(fn)` without an
  event name) run before named subscribers; within a group, in subscription order;
  emit calls one by one (exception-isolated), intercept calls one by one until a
  non-None return appears.

### 9.4 Custom Hooks and Custom Layers

Three extension forms, with exactly the same rights as the standard 29 hooks:

```python
from norpagent.hooks import HookLayer

# form one: custom layer + hooks declared inside the layer (the plugin-loading pipeline is the standard-library use case, 11.4)
network_layer = HookLayer("L10_network", order=100, description="network access layer")
before_net = network_layer.hook("before_network_call", mutating=True,
                                description="before a network request goes out (rewrite the URL or veto)")
agent.hooks.install_layer(network_layer)          # usable immediately after installation
agent.hooks.before_network_call.subscribe(monitor)

# form two: define a hook directly on the hook system (no layer; belongs to the dynamic layer)
agent.hooks.define_hook("after_cache_hit", mutating=False,
                        description="cache hit")

# form three: zero-definition trigger — an unregistered named event automatically becomes a dynamic-layer hook
agent.hooks.hook("my_custom_event").emit(data=42)
```

- `HookLayer(name, order, description)` declares a layer; `layer.hook()` returns a
  `Hook` definition (the module-level API), exportable in advance for third parties
  to `subscribe(fn, system=reg)`;
- `install_layer` sorts layers by `order`; **when a same-named hook already exists
  the original definition is kept** (only the layer metadata is recorded); repeated
  installation never overwrites existing subscriptions;
- `HookSystem` query API: `list_hook_names()` / `list_hooks()` / `layers()` /
  `layer_of(name)` / `get(name)`;
- custom hooks support `subscribe / unsubscribe / emit / intercept` as usual.

### 9.5 Relation to the EventBus

The hook system is a **structured view of the event bus**, not another mechanism:

- `HookSystem` mounts the 9 standard layers onto an `EventBus` at construction;
  subscriptions and publications ultimately land on `registry.bus`;
- `reg.hooks.before_step.subscribe(fn)` and
  `reg.bus.subscribe(fn, "before_step")` are **exactly equivalent** and can be mixed;
- therefore hooks keep working after replacing the loop system (async_loop slot) —
  hooks hang on the event bus, independent of the loop implementation (FAQ Q6);
- event names align one-to-one with the early plugin_system's 15 hooks; old plugins
  / old code need no changes (Chapter 11's plugin-hook bridge relies on this);
- performance (0.9): EventBus uses copy-on-write — subscribe / unsubscribe replace
  the list inside the lock; emit / intercept take one snapshot reference and iterate
  lock-free; high-frequency streaming events (on_content per token) incur no
  per-event list-copy overhead (14.3 ultra-high concurrency).

### 9.6 Every Execution Structure Is Overridable

Besides hooks, the seven execution structures of `AgentRuntime` are public methods;
subclassing overrides that stage without touching the loop body:

```python
from norpagent import AgentRuntime, ChatMessage

class MyRuntime(AgentRuntime):
    def build_messages(self, system_prompt, session_id, *, step,
                       task_id, tool_names=None):
        messages = super().build_messages(...)     # run the L5 hooks first
        messages.append(ChatMessage(role="system", content="custom injection"))
        return messages

    def call_model(self, provider, history, schemas, params,
                   task_id, result, step):
        ...                                         # take over L7 entirely
```

Method list: `prepare_input` (L3) / `create_session`,
`append_message` (L4) / `build_messages` (L5) / `call_model`
(L7) / `execute_tool_call` (L8) / `finalize_result` (L9).
Override-vs-hook relation: the default implementation **runs hooks first, then the
default logic**; overrides may keep the super() call (hooks keep working) or take
over entirely (skipping hooks); you can also replace the whole loop class via the
`agent_runtime` slot (3.1).

### 9.7 Behavior Details and Best Practices

- blocked / vetoed / approval-denied tool calls uniformly flow through
  `after_tool_call` — an "execution structure" passes the hook regardless of outcome;
- `before_step` vetoing this round → skip the model call and go to the next round;
  `after_step` broadcasts only when there are tool calls (no tool calls: straight to
  the final-reply path);
- hook rewrites act on the **actual data flow** (messages / parameters / results),
  not side-channel notifications; rewritten values continue into the downstream
  pipeline;
- subscribing the same fn repeatedly executes it multiple times: when hot-mounting
  the hooks slot the framework unsubscribes architecture-level subscriptions first
  then remounts (no stacking); for your own subscriptions, pair them with
  unsubscribe (`EventBus.unsubscribe` removes only the first equal element);
- avoid heavy computation and blocking I/O inside high-frequency hooks
  (on_content / on_reasoning streaming per token); subscriber exceptions are
  isolated and logged, but the exception path has overhead;
- observation hooks' (emit) return values are ignored — to intervene in the data
  flow you must use a mutating hook (intercept) or a method override;
- thread safety: HookSystem / EventBus registry operations are locked; subscribing /
  unsubscribing while running is safe (3.7's hot mount relies on this);
- when you need intervention but do not want a global subscription: write the logic
  as a standalone function, gated by explicit task-level params (e.g.
  jailbreak_guard / harden_prompt, see 10.4), or use the `np(hooks=...)` slot to
  subscribe on a specific engine only.

---

## Chapter 10 Security System: norpagent.safe()

> In one sentence: `safe()` converges the whole security suite (jailbreak
> protection / prompt hardening / human approval / network policy / source audit /
> import restrictions / signature trust / plugin isolation policy) into one
> standalone function. Companion document `docs/security.md`.

### 10.1 How to Enable

```python
import norpagent as np

# 1. np() slot form
np(security="high")                                  # string: runtime policy only, zero hook intervention
np(security={"level": "high", "hooks": True})        # dict: + explicit hook intervention
np(security=lambda reg: safe(reg, config={...}))     # callable: fully custom assembly

# 2. safe() direct form
from norpagent import safe
kit = safe(registry, level="standard")               # basic / standard / high
kit = safe(registry, level="standard", hooks=True)

# 3. two-phase: get the kit first, install later
kit = safe(level="high")
kit.install(registry)                                # runtime policy only (no hooks by default)
kit.install(registry)                                # runtime policy only (no hooks by default)
kit.install_hooks(registry)                          # mount hooks manually when intervention is needed
kit.uninstall_hooks(registry)                        # take them down any time; the hook pipeline stays pure
```

Design points — **the security system is stripped out as a whole plug**:

- kernel modules never import any `norpagent.security` module at module level;
  guards / hardening / approval / audit / signatures are injected through
  `registry.security` (the kernel lazily imports guard only when task-level params
  explicitly request it, see 10.4);
- **zero hook intervention (default)**: safe() subscribes to no hooks by default —
  jailbreak protection and prompt hardening are not automatically mounted on the
  bus as hook subscribers; the hook pipeline stays pure; intervention is enabled
  explicitly by the user (hooks=True / kit.install_hooks());
- protection capabilities are always available as **independent APIs**
  (kit.scan_input / kit.harden / ..., 10.5) for use in your own hook subscribers or
  method overrides;
- runtime decisions (human approval / network policy / plugin-loading policy) always
  take effect through `registry.security` (SecurityContext), regardless of whether
  hooks are mounted;
- CLI equivalents: `--safe basic|standard|high` (runtime policy only),
  `--safe-hooks` mounts hooks explicitly.

### 10.2 The Three Levels

| Capability | basic | standard | high |
|---|---|---|---|
| input jailbreak/injection protection (L3 hook, explicit opt-in) | ✓ | ✓ | ✓ |
| system-prompt hardening (L5 hook, explicit opt-in) | ✓ | ✓ | ✓ |
| plugin-source AST audit | warn | warn | **block** |
| plugin import restrictions | off | safe | safe |
| permission declarations (manifest permissions) | | | ✓ |
| plugin network policy | allow_all | deny | deny |
| plugin-tool human approval | | ✓ | ✓ |
| enforced trusted signatures | | | ✓ |

- `basic`: only input protection and prompt hardening (and no hooks by default;
  enabled explicitly as needed); no plugin-side restrictions — for trusted-source
  local plugin development;
- `standard` (default): + plugin import restriction safe, network deny, plugin-tool
  approval; signature verification on but not enforced;
- `high`: + audit block (critical rejects), manifest permission declarations
  required, trusted signatures enforced (unsigned / untrusted are all refused).

### 10.3 SecurityContext: the Single Source of Truth for Runtime Security Policy

`registry.security` is a `SecurityContext` instance: AgentRuntime reads it for tool
approval; the plugin loader reads its `plugin_config()` when config is omitted.
Fields (preset by safe(level=...); override per-item with a config dict; key names
match norpagent.security / plugin-loader config):

| Field | Default (standard) | Note |
|---|---|---|
| `level` | standard | basic / standard / high |
| `guard_enabled` | True | input-protection master switch (hook intervention path) |
| `harden_enabled` | True | prompt-hardening master switch (hook intervention path) |
| `audit_level` | warn | off / warn / block |
| `import_restrict` | safe | off / safe / strict |
| `require_permissions` | False | manifest.permissions enforced |
| `signature_verify` | True | Ed25519 verification (invalid rejects) |
| `signature_required` | False | when True only trusted loads |
| `trusted_keys` | [] | trusted public-key hex list |
| `network_policy` | deny | deny / audited_public / public_only / allow_all |
| `approval_config` | None | approval-policy dict (10.6) |
| `plugin_isolation` | auto | auto / inproc / process |
| `hook_intervention` | False | True = mount protection hooks at install time |
| `extra` | {} | extension fields |

- `plugin_config()` converts this context into plugin-loader config (the fallback
  when config is omitted); `to_dict()` outputs the full posture (consistent with
  `kit.describe()`);
- a `SecurityContext` instance can be used directly as the `np(security=ctx)` slot value;
- config keys favor the plugin-loader style (plugin_security_audit /
  plugin_network_policy / plugin_trusted_keys etc.), plus plain keys
  guard_enabled / harden_enabled / hook_intervention / approval /
  approval_config; safe()'s `_apply_config` maps them uniformly into
  SecurityContext.

### 10.4 Hook Intervention (explicit opt-in)

`hooks=True` / `kit.install_hooks()` mounts two subscribers:

- **L3 input protection** = a `before_input` subscriber: on jailbreak / injection
  features it raises `HookVeto` (the task wraps up as stopped). The task-level param
  `params["jailbreak_guard"] = False` explicitly disables hook protection for that
  task; `True` (or any truthy value) goes the kernel explicit-scan path — the kernel
  calls scan_message directly, **independent of whether hooks are mounted**;
- **L5 prompt hardening** = a `before_build_messages` mutating subscriber: injects
  the core rules and the tool list into the system prompt. The task-level param
  `params["harden_prompt"] = False` explicitly disables hardening for that task;
  `True` goes the kernel explicit-hardening path.

Mount / unmount semantics:

- `kit.install_hooks(reg)` is idempotent: repeated calls on the same registry never stack;
- `kit.uninstall_hooks(reg)` removes only **this kit's own** subscribers, leaving
  user / plugin subscriptions untouched; the hook pipeline returns to pure;
- `kit.hooks_installed(reg)` queries the current state;
- `kit.uninstall(reg)` unsubscribes hook subscriptions and clears
  `registry.security`. Hot-mounting the security slot at runtime
  (`np.remount(security=...)`) uninstalls the old kit before installing the new one,
  preventing protection hooks from stacking on the same bus;
- after a hot mount, runtime decisions take effect immediately; hook intervention
  applies to subsequent tasks' before_input / before_build_messages.

### 10.5 Standalone Check APIs (usable without hooks)

SafetyKit proxies the norpagent.security modules; all are directly callable
standalone:

| Method | Capability |
|---|---|
| `kit.scan_input(text)` → (blocked, reason, hits) | jailbreak / injection scan |
| `kit.is_jailbreak_attempt(text)` → bool | scan result as boolean |
| `kit.harden(prompt, tool_names)` → str | prompt hardening |
| `kit.audit_file(path)` / `kit.audit_source(src)` → issues | source AST audit |
| `kit.verify_plugin(path, manifest)` → SignatureResult | plugin signature verification |
| `kit.check_network(url)` → bool | adjudicate per the current network policy (SSRF) |
| `kit.approval_policy(hints)` → ApprovalPolicy | approval-policy instance |
| `kit.network_policy()` → NetworkPolicy | network-policy instance |
| `kit.describe()` → dict | current security posture |

Example of calling the standalone APIs inside your own hook subscriber:

```python
from norpagent.hooks import HookVeto

def my_guard(event):
    blocked, reason, _ = kit.scan_input(event.get("user_input") or "")
    if blocked:
        raise HookVeto(reason or "input blocked by security protection")
reg.hooks.before_input.subscribe(my_guard)
```

The underlying modules (`norpagent.security`, zero framework dependency, importable
standalone): `guard` (scan / harden), `approval` (approval decisions),
`network_policy` (SSRF adjudication), `audit` (AST audit), `signature` (Ed25519,
requires the `norpagent[security]`-provided cryptography).

### 10.6 Runtime Decision Points

**Human approval** (AgentRuntime tool-execution path):

- policy-source priority: `params["approval_policy"]` instance >
  `params["approval_config"]` dict > `registry.security.approval_config`;
- native tools approve per "tool name → level" mapping (file_write /
  file_delete / exec_cmd etc. at WRITE / DELETE / EXEC level, with old tool-name
  compatibility); plugin tools go through the `approval_enabled` master switch plus
  the plugin's `APPROVAL_HINTS` fine control (approval="none" exempts, Chapter 11);
- interaction happens via the UI adapter through `ctx.ask_user` (broadcast with the
  on_user_input_required hook); a user denial cancels the call (approval_denied),
  which still flows through after_tool_call.

**Network policy / SSRF protection** (norpagent.security.network_policy):

- four granularities: `deny` (default) → `audited_public` (must hit the URL /
  domain whitelist) → `public_only` (private networks forbidden) → `allow_all`;
- except allow_all, private / loopback / link-local / reserved ranges / cloud
  metadata addresses (169.254.169.254 etc.) are always refused; text-level judgment
  first, then DNS-resolution re-check (covering the common rebinding path).

**Plugin-loading policy**: when `install_plugin_dirs` is given no explicit config,
it automatically adopts `registry.security.plugin_config()` — with the security
system stripped out, plugin loading inherits the global security posture by default
(11.8).

### 10.7 Security-No-Downgrade Principle and Defense in Depth

- without cryptography installed, signature verification returns "untrusted" and
  does **not** let it through (the security posture never downgrades);
  `norpagent[security]` provides the verification capability;
- plugin-loading order is fixed: signature verification → AST audit → permission
  declarations → import restrictions → registration (failure at any stage rejects;
  no next stage, 11.3);
- import restrictions are double-layered: AST static pre-check (preventing
  already-cached sys.modules modules from bypassing meta_path) + sys.meta_path
  runtime interception;
- process-level isolation (high / custom config) moves untrusted plugins out of the
  main process — even if the audit misses something, a plugin crash never affects
  the host (11.7);
- the kernel-side explicit param switches (jailbreak_guard / harden_prompt) coexist
  with safe()'s hook path: as long as either is on, protection is active
  (independent of whether hooks are mounted).

### 10.8 Typical Combinations

```python
# production default: standard + explicit hook intervention
np(security={"level": "standard", "hooks": True})

# strict: high + whitelist network + trusted keys
kit = safe(level="high", config={
    "plugin_network_policy": "audited_public",
    "plugin_network_domain_allowlist": ["api.example.com"],
    "plugin_trusted_keys": ["<public key hex>"],
})
np(security=kit.context)                     # install the SecurityContext directly

# decisions only, zero intervention: use only approval and network policy; wire the guard logic yourself
np(security={"level": "standard",
             "config": {"guard_enabled": False, "harden_enabled": False}})
```

---

## Chapter 11 Plugin System

> External plugins ship as standalone `.py` files (or manifest packages); the host
> mounts them through the `norpagent.plugins` loader and automatically gains the
> full security suite: signature verification / AST audit / import restrictions /
> network policy / human approval. The plugin format is fully compatible with the
> existing application's plugin_system; old plugins migrate without code changes.
> Companion documents: `docs/plugins.md` (host side), `norpagent插件开发指南.md`
> (plugin-author side).

### 11.1 Two APIs and the np() Slot

```python
# convenient entry: load a directory in one call
from norpagent.plugins import install_plugin_dirs
loader = install_plugin_dirs(reg, ["my_plugins"], config={...})

# library facade: full lifecycle + status + hot reload
from norpagent.plugins import PluginSystem
ps = PluginSystem(reg, ["my_plugins"], config={"plugin_isolation": "auto"})
infos = ps.load()        # discover -> secure load -> register
ps.status()              # plugin list + isolation-host status
ps.reload("my_tool")     # hot-reload a single plugin during development
ps.shutdown()            # release process-isolated host subprocesses

# np() slot (literal semantics, directory list)
np(plugins=["./my_plugins"])
# runtime hot replacement: old subscriptions auto-unsubscribed, never stack (3.7)
np.remount(plugins=["./my_plugins_v2"])
```

The `np(plugins=[...])` slot assembles with fixed config (audit=warn, verification
on, **does not read** registry.security's overrides); for fine-grained config use
PluginSystem / install_plugin_dirs directly on a Registry (or a callable slot value
like `np(plugins=lambda reg: ps.load())`).

### 11.2 Plugin Format (compatible with the existing application)

**Module-level interface** (single-file plugin `my_plugin.py`):

| Name | Type | Note |
|---|---|---|
| `PLUGIN_NAME` | str | plugin display name (required) |
| `PLUGIN_VERSION` | str | version, default 0.0.0 |
| `PLUGIN_PUBLISHER` | str | publisher |
| `PLUGIN_DESCRIPTION` | str | description |
| `TOOLS` | list | OpenAI function schema list |
| `execute(tool_name, args, ctx)` | callable | unified tool entry, returns str / None |
| `APPROVAL_HINTS` | dict | tool → approval hint (11.8) |
| `ISOLATION` | str | `"process"` = process-level isolation (read statically via AST; code never executes in the host) |
| `__norpagent_type__` | str | file-as-module type declaration ("tool" / "plugin", for FLOW drag-in) |
| 15 hook functions | callable | fully aligned with the old application's hook names (11.5 bridge) |

```python
# my_plugin.py -- minimal plugin
PLUGIN_NAME = "greet_plugin"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "greet",
        "description": "greet the user",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "additionalProperties": False},
    },
}]

def execute(tool_name, args, ctx):
    if tool_name == "greet":
        return f"hello, {args.get('name') or 'world'}!"
    return None
```

**Manifest-package format**: a directory + `manifest.json` (name / version /
publisher / description / entry default plugin.py / permissions / signature /
isolation fields).

### 11.3 The Security Pipeline (the full load flow)

```
discovery (directory scan: *.py single files / manifest packages)
  -> before_plugin_load (HookVeto can refuse, 11.4)
  -> 1. signature verification: invalid rejects directly; signature_required allows only trusted
  -> 2. trust grading: trusted signature -> audit relaxed to warn
  -> 3. AST audit: dangerous calls / dangerous imports / getattr, __dict__ reflection bypass
       detection; at block level, critical findings reject
  -> 4. permission declarations: manifest.permissions validated when require_permissions
  -> 5. isolation decision: process -> the plugin loads only in a host subprocess (11.7)
  -> 6. module loading under import restrictions (static pre-check + meta_path interception, 11.6)
  -> 7. read metadata (PLUGIN_NAME / TOOLS / hooks / APPROVAL_HINTS)
  -> before_plugin_register (HookVeto can refuse)
  -> 8. adapt to the Plugin protocol -> register into the Registry (tools into the table, hooks subscribe the bus)
```

A failure at any stage → `PluginInfo(enabled=False, error=...)` records the reason
and scanning **continues with other plugins**; loading is never interrupted.
`PluginInfo` carries `name / path / version / publisher / description / enabled /
error / tools / hook_names / signature_status / trusted /
approval_hints / audit_issues`; for debugging see `loader.plugins[i].error` and
`audit_issues` (with line numbers).

### 11.4 Pipeline Hooks (the standard-library use case for custom layers)

PluginSystem mounts `PLUGIN_PIPELINE_LAYER` (a custom HookLayer with order=200 — the
official example of the 9.4 capability) onto `registry.hooks` at construction;
(a custom HookLayer with order=200 — the official example of the 9.4 capability) on
`registry.hooks`; 8 pipeline hooks:

`before/after_plugin_discover`, `before/after_plugin_load`,
`before/after_plugin_audit`, `before/after_plugin_register`.

```python
from norpagent.hooks import HookVeto
from norpagent.plugins import before_plugin_load

def block_listed(event):
    if (event.get("name"), event.get("path")) in HOST_BLOCKLIST:
        raise HookVeto("this plugin is rejected by the host policy")
before_plugin_load.subscribe(block_listed, system=reg)
```

- mutating pipeline hooks (before_plugin_load / before_plugin_audit /
  before_plugin_register) raising HookVeto = refusing that plugin's loading /
  registration (enabled=False + error records the reason); after_* are observation
  hooks (after_plugin_audit's payload includes allowed / issues);
- pipeline hooks register dynamically via `registry.hooks.hook(name)` and do not
  conflict with plugin module-level hooks.

### 11.5 Old-Plugin Hook Bridge (15-hook alignment)

Hook functions defined at plugin module level (on_task_start / before_step /
before_tool_call / after_tool_call — 15 of them) are wrapped by the loader into
EventBus subscribers:

- signature convention: **business parameters first, PluginContext last** (ctx
  provides plugin_name / project_root / app_dir / config / current_step);
- mutating hooks' (before_step / before_tool_call / after_tool_call) return values
  pass through intercept to the kernel; other hooks' returns are ignored;
- the event-payload → old-argument-list mapping is fully identical to the existing
  application's plugin_system dispatch logic (loader._HOOK_ARG_KEYS); old plugins
  migrate with zero changes;
- process-isolated plugins go through the same bridge: fire_hook RPC forwarding
  (5s time limit).

### 11.6 Import Restrictions

- `off`: no restrictions (local debugging; a trusted signature only relaxes the
  audit — import restrictions still follow the config);
- `safe` (standard / high default): blocks dangerous modules (subprocess / ctypes /
  cffi / socket / pickle / marshal / telnetlib / ftplib / smtplib; ctypes / cffi
  blocked unconditionally), **double-layered**: AST static pre-check (preventing
  already-cached sys.modules modules from bypassing meta_path) + loading-time
  sys.meta_path interception (stack-frame probing whether the caller is a plugin
  module);
- `strict`: only the safe-module whitelist is allowed (json / re / datetime / math /
  random / collections / itertools / functools / typing / enum / pathlib / os.path /
  textwrap / string / hashlib / base64 / traceback / logging / warnings / copy /
  uuid / time / norpagent.protocols.tool etc.); anything outside the whitelist
  raises ImportError.

Plugin modules load under the `norpagent_ext_<name>` module name (unified
namespace); the restrictor **only applies to plugin modules**, never to host code.

### 11.7 Process-Level Isolation

`ISOLATION = "process"` (module constant, read statically via AST — **plugin code
never executes in the host**) / the manifest `isolation` field / host config
`plugin_isolation` (auto takes the former two; explicit inproc / process forces).
Isolation semantics:

- the plugin module object exists only in a host subprocess (`python -m
  norpagent.plugins.host`, JSON-lines-protocol RPC);
- tool execution returns via RPC; hooks forward via fire_hook, each hook limited to
  5s (HOOK_TIMEOUT), abandoned on timeout — plugin hooks can never stall the main loop;
- crash self-healing: child-process death → auto restart + reload of all plugins →
  one retry;
- tool errors never bubble: remote exceptions become failed ToolResults;
- import restrictions keep working inside the child process (defense in depth).

### 11.8 Interplay with the Security System

- `install_plugin_dirs(reg, dirs)` **without config** automatically adopts
  `registry.security.plugin_config()` — call `safe(reg, ...)` first, then install
  plugins, and plugin loading inherits the global security posture (note: the
  `np(plugins=...)` slot path passes fixed config and does not read
  registry.security);
- `safe(level="high")`'s effect on plugins: audit block, permission declarations
  enforced, trusted signatures enforced (unsigned / untrusted rejected);
- trust mechanism: `python -m norpagent plugin-sign --gen` generates a key pair;
  `plugin-sign my_plugin.py --key <private key hex>` generates a signature; after
  adding the public key to `plugin_trusted_keys`, that plugin is trusted → the audit
  relaxes to warn (import restrictions follow the config, unaffected by trust);
- network access is adjudicated by the host's `plugin_network_policy` (default
  deny); plugins cannot bypass it — the policy executes in the host process (10.6);
- approval: plugin tools go through the `approval_enabled` master switch by default;
  `{"approval": "none", "risk": "L0"}` in the plugin's `APPROVAL_HINTS` exempts a
  single tool; undeclared tools follow the master switch (backward compatible).

### 11.9 Config-Key Reference and Lifecycle

```python
config = {
    "plugin_security_audit": "warn",            # off / warn / block
    "plugin_security_import_restrict": "off",   # off / safe / strict
    "plugin_security_require_permissions": False,
    "plugin_signature_verify": True,
    "plugin_signature_required": False,         # True: only trusted loads
    "plugin_trusted_keys": ["<public key hex>"],
    "plugin_network_policy": "deny",            # deny/audited_public/public_only/allow_all
    "plugin_network_url_allowlist": ["https://api.example.com/"],
    "plugin_network_domain_allowlist": ["api.example.com"],
    "approval_enabled": True,                   # plugin-tool approval master switch
    "plugin_isolation": "auto",                 # auto / inproc / process
}
```

Lifecycle notes:

- `ps.load()` is re-callable (clears the manifest first, then rescans);
  `ps.configure()` updates config and invalidates the loader for a rebuild;
- `ps.unload(name)` / `ps.reload(name)` are development-time tools: the old
  instance's tools / hook subscriptions stay in the Registry (the tool table has
  name-override semantics; hooks cannot be bulk-unsubscribed by name) —
  **in production, prefer rebuilding the Registry and reloading**;
- for a full runtime replacement use `np.remount(plugins=[...])`: the framework
  unsubscribes the old architecture-level plugin subscriptions first, then
  reinstalls (no stacking, 3.7);
- `ps.shutdown()` / `loader.shutdown()` release the process-isolation host
  subprocesses; hot-mounting the plugins slot (`np.remount(plugins=...)`) makes the
  framework uninstall the old loader first (unsubscribe hooks + clear sys.modules +
  release the isolation host).

## Chapter 12 Preset Modes

### 12.1 The Six Built-In Modes

| Mode | Purpose | Component combination |
|---|---|---|
| `minimal` | model benchmarks | mock + echo/get_time + memory |
| `standard` | general coding tasks | sqlite + pooled + persistent + fts5 + all built-in tools |
| `longrun` | long-running complex tasks | same as standard; max_steps=512, no time limit, phased planning prompts |
| `ptc` | code-orchestrated tool calls | run_python (sandbox execution) |
| `creative` | custom-mode debugging | mode-file loading (--mode-file) |
| `embedded` | embedded / edge / low-resource (0.9) | pure in-memory components + minimal tool set, **headless frontend by default**, no disk / no network dependencies; the model falls back to mock without credentials. See 14.2 |

```python
np(preset="standard")
np(preset="ptc")
np(preset="embedded")                     # headless by default, pure-API mode
np(preset=Preset(name="mine", model="mock", tools=["echo"], ...))
```

### 12.2 Custom Presets

```python
from norpagent import Preset

my = Preset(
    name="mine",
    description="custom mode",
    model="mock",
    tools=["echo", "get_time"],
    session="sqlite",
    sandbox="pooled",
    scheduler="simple",
    ui="console",
    mode="single",
    params={"max_steps": 16},
    components={},
)
np(preset=my)
```

---

## Chapter 13 Command-Line Entry

```bash
norpagent --list-modes
norpagent --mode minimal                          # interactive REPL
norpagent --mode embedded                         # embedded mode (no-disk components, 0.9)
norpagent --mode ptc --prompt "..."               # single task
norpagent --mode-file my_mode.py                  # mode file
norpagent --mode standard --ui web --port 8787    # Web UI
norpagent --mode standard --plugin-dir ./my_plugins
norpagent --mode standard --safe high               # runtime security policy (zero hook intervention)
norpagent --mode standard --safe high --safe-hooks  # hook intervention on as well
norpagent plugin-sign --gen                       # plugin signature keys
```

The CLI is equivalent to `np()`: the CLI's internal flow is
"install default components → register presets → apply security → load plugins →
build the runtime".

---

## Chapter 14 Embedded and Ultra-High-Concurrency Deployment

Since 0.9 the framework has dedicated optimizations for **embedded (low memory /
low CPU / no disk / edge devices)** and **ultra-high-concurrency servers**. This
chapter covers the optimizations, config entries and usage.

### 14.1 Optimization List

**Embedded scenarios (resource consumption minimized):**

| Optimization | Content |
|---|---|
| `install_core()` minimal assembly | registers only the components needed for the minimal agent loop: mock / openai_compat models, echo / get_time / run_python / file_* tools, memory sessions, subprocess sandbox, simple scheduler, console UI — **zero disk dependencies, no HTTP components, empty component namespace** (no sqlite3 / http.server imports) |
| builtin package lazy imports | `import norpagent.builtin` no longer pulls in sqlite3 / http.server; FTS5 context store / SQLite sessions / persistent scheduler / Web UI all became on-demand imports inside install_defaults + module-level `__getattr__` lazy resolution (`from norpagent.builtin import WebUI` etc. stay compatible) |
| WebUI construction does zero disk I/O | reading of the three disk states (config / FE config / flow graph) is deferred to `start()` (`_ensure_disk_loaded`); constructing without starting reads nothing — safe on read-only root filesystems / environments without HOME |
| page byte cache | `page_bytes()` caches into memory: GET / no longer reads disk per request (previously one open+read per request) |
| `embedded` preset | the built-in sixth mode: pure in-memory components + minimal tool set + headless frontend by default (no listening port); the model falls back to mock without credentials |
| worker-pool tightening | `NORPAGENT_MAX_WORKERS=1` or `config={"loop": {"max_workers": 1}}` squeezes daemon worker threads to the minimum; `NORPAGENT_SUBMIT_POLL=0.5` raises the poll interval to save CPU |

**Ultra-high-concurrency servers (throughput and memory bounds):**

| Optimization | Content |
|---|---|
| EventBus copy-on-write | subscriber tables become immutable snapshots: emit / intercept only take a reference inside the lock and iterate lock-free, **saving one listener-list copy per event** (the biggest win for per-token streaming pushes). subscribe / unsubscribe create a new list and replace the reference; thread-safety semantics unchanged (measured emit throughput >1.5M events/sec) |
| SSE bounded backpressure | each connection gets a `_SSESubscriber` bounded buffer (default 1024): slow clients **drop the oldest event** (`drop_oldest`, default) and degrade automatically; optional `drop_newest` / `unlimited`; memory usage is bounded and decoupled from the client count |
| SSE batched frame writes | write+flush once when 32 frames accumulate or 50ms elapse (`sse_batch` / `sse_batch_interval` configurable): system-call counts drop sharply under high-frequency streaming; single-event-stream latency ≤ batch interval |
| SSE fast disconnect reclamation | after a TCP half-close the first write does not error; discovery via heartbeat alone would take up to 15s — idle connections are probed with a non-blocking select every 1s; after disconnect, threads and buffers are released within ≤1s; heartbeats stay at 15s intervals, adding no network burden |
| HTTP concurrency tuning | listen backlog `request_queue_size=256`; `block_on_close=False` does not wait for connections on stop; `X-Accel-Buffering: no` (nginx reverse proxy does not buffer); responses keep-alive (HTTP/1.1) |
| submit polling tightened | completion-poll interval 0.2s → 0.05s (default): the calling thread's completion-perception latency cap drops from 200ms → 50ms; configurable / env-overridable |
| loop-core micro-tuning | `traceback` promoted to module level (zero import on the callback-exception path); ready-queue snapshot batch execution keeps anti-starvation semantics |

### 14.2 Embedded Deployment

**Way one: `install_core()` with a self-built registry (cleanest dependency
surface):**

```python
from norpagent import Registry, AgentRuntime, install_core
from norpagent.modes import build_embedded_preset

reg = Registry()
install_core(reg)                       # does not import sqlite3 / http.server
reg.register_preset(build_embedded_preset())
agent = AgentRuntime(reg, preset="embedded")
result = agent.run("hello")
print(result.final_content)
```

Note: `install_core`'s registry has no context_store / project_manager / persistent
components — presets declaring them (standard / longrun / creative etc.) are
explicitly refused when assembled on this registry (the error lists the missing
component names).

**Way two: `np(preset="embedded")` (out of the box):**

```python
import norpagent as np

np(preset="embedded")                   # headless by default: no HTTP service
eng = np.current()
result = eng.submit("hello")            # pure-API submission
eng.request_stop()
```

Behavioral conventions of the embedded preset:

- **the default frontend automatically falls back to headless** (the assembler's
  default factory judges the preset name); for a Web UI specify explicitly
  `np(preset="embedded", frontend="norpagent.frontends.web:WebFrontend")`;
- the model declares `openai_compat`: with any credential (parameter / environment
  variable) the real model is used, otherwise it falls back to mock (offline
  devices work out of the box);
- all components are pure in-memory (memory / subprocess / simple), no generic
  components declared — FTS5 / SQLite are never built and no files land on disk.

**Way three (resource switches, stackable with ways one/two):**

```python
# squeeze worker threads to 1; relax polling to save CPU
os.environ["NORPAGENT_MAX_WORKERS"] = "1"
os.environ["NORPAGENT_SUBMIT_POLL"] = "0.5"
# or equivalent:
np(config={"loop": {"max_workers": 1, "poll_interval": 0.5}})
```

### 14.3 Ultra-High-Concurrency Deployment

**SSE backpressure config (startup params → env vars → runtime hot change):**

```python
import norpagent as np

# pass at startup
np(config={"web": {"sse_queue_size": 2048, "sse_queue_policy": "drop_oldest"}})
# or runtime params / environment variables
np(sse_queue_size=2048, sse_queue_policy="drop_oldest")
# NORPAGENT_SSE_QUEUE_SIZE=2048 NORPAGENT_SSE_QUEUE_POLICY=drop_oldest

# hot change while running (no restart; takes effect on existing connections immediately)
from norpagent.builtin.ui.web import WebUI
ui = np.current().frontend._ui      # or hold the WebUI instance directly
ui.set_sse_queue(sse_queue_size=4096, sse_queue_policy="drop_newest")
print(ui.streams_info())
```

REST operations entries:

| Endpoint | Note |
|---|---|
| `GET /api/streams` | query SSE backpressure config and stats (subscriber count / dropped-event count / per-connection buffer depth) |
| `POST /api/streams` | hot change: `{"sse_queue_size": 2048, "sse_queue_policy": "drop_oldest"}` |
| `GET /api/status` | `sse_queue_size` / `sse_queue_policy` / `sse_dropped_total` fields |

Backpressure-policy semantics:

| Policy | Behavior when the buffer is full | Suitable for |
|---|---|---|
| `drop_oldest` (default) | drop the oldest event; the client degrades automatically but **stays connected** | display-style frontends (chat streams) |
| `drop_newest` | drop the newest event; keep the old state | "state sync" consumers |
| `unlimited` | no limit (the pre-0.8 behavior) | when you know every client consumes everything |

A buffer size of `sse_queue_size=0` means unlimited. `sse_batch` (default 32 frames)
and `sse_batch_interval` (default 0.05s) control the frame batch-write granularity:
the larger, the fewer system calls and the higher single-event latency — balance
against your traffic pattern.

**Reverse-proxy notes**: SSE responses already carry `X-Accel-Buffering: no` (nginx
does not buffer); the proxy timeout (proxy_read_timeout) should be > 15s (the
library heartbeat period).

**Loop tuning**: `config={"loop": {...}}` and `NORPAGENT_MAX_WORKERS` /
`NORPAGENT_SUBMIT_POLL` see 4.3 and 14.1. Task-completion perception latency =
poll_interval (default 50ms); raise it in CPU-sensitive environments, lower it in
latency-sensitive ones (floor 1ms).

**Thread model**: the Web frontend has one HTTP thread per SSE connection (the
standard-library socketserver model); tasks enter the engine serially through
`WebFrontend._gate` and execute on the loop worker pool. The event-publishing path
is O(subscriber count) with O(1) amortized per subscriber (bounded deque + one
empty→non-empty notify), so ten-thousand-scale concurrent pushes do not amplify
lock contention.

**Monitoring metrics** (`GET /api/streams`): `subscribers` (online subscribers),
`dropped_total` (cumulative backpressure drops — sustained growth means clients are
too slow; enlarge the buffer or inspect the consumers), `max_buffered` (peak buffer
depth per connection).

### 14.4 How to Verify

In-library verification scripts (`test/`):

```bash
python test/_verify_embedded_concurrency.py   # 34 items: minimal assembly / lazy imports / e2e / concurrency correctness / throughput
python test/_smoke_webui_09.py                # WebUI: lazy disk I/O / page cache / backpressure hot change / HTTP concurrency / SSE
python test/_smoke_embedded.py                # embedded preset e2e
```

Coverage points: `install_core` component whitelist and blacklist,
`import norpagent.builtin` does not pull sqlite3 / http.server, embedded defaults to
headless + mock fallback, environment variables tighten the worker pool, EventBus
copy-on-write concurrent subscribe/unsubscribe correctness, submit interrupt
wakeup, SSE three policies and hot change, 40 concurrent HTTP, disconnect
reclamation.

---

## Chapter 15 Work Rollback: Snapshots / Undo / Redo / Crash Rescue / Safe Mode

> In one sentence: Agent work can be rolled back — Web UI / shortcuts / API
> one-click undo and restore (Undo / Redo); browse the full snapshot history and
> roll back to any version in one click (Rollback); when the main program cannot
> start at all, use the standalone CLI crash rescue (which also suggests the last
> known-good snapshot); safe mode loads only the minimal kernel, keeping the core
> rollback capabilities.

### 15.1 Concepts and the Four Layers

| Layer | Capability | Entry |
|---|---|---|
| Undo / Redo | undo / restore the most recent operation, in-process immediate | Web UI buttons / Ctrl+Z / Ctrl+Shift+Z / `np.undo()` / `np.redo()` |
| Rollback | browse all historical snapshots, roll back to any version | Web UI "rollback" panel / `np.rollback(id)` |
| Crash Rescue | roll back snapshots when the main program cannot start; suggest the last known-good snapshot | `norpagent-rescue` (standalone CLI, pure standard library) |
| Safe Mode | load only the minimal kernel (skip all plugins), keep the core rollback capabilities | `np(safemode="on")` / CLI `--safe-mode` |

Snapshot content (mode A, default): all architecture-layer slot configurations
(mode / model / tools / session / sandbox / frontend / plugin dirs / security
level...) + engine runtime parameters + WebUI settings-file content + custom
provider data. Sensitive keys (api_key / token etc.) are written only after
**redaction**. Non-serializable values (instances / classes / functions) record a
type marker; on replay they are skipped with a hint (honest degradation, never
fabricates state).

Snapshot mode B: `np(snapshot_sessions="on")` additionally copies session-store
files into the snapshot attachments; rollback restores the whole files (this may
overwrite conversations recorded after the rollback point).

Storage: default `~/.norpagent/snapshots/` (manifest.json timeline + snap/ one
JSON per snapshot + attachments/ session attachments + rollback_target.json the
rescue rollback target). Overridable with the environment variable
`NORPAGENT_SNAPSHOT_DIR` or `np(snapshot_dir=...)`; while running,
`np.set_snapshot_dir()` hot-switches the storage directory (explicit programmatic
calls have the highest priority). Auto snapshots are on by default
(`np(snapshots="off")` disables); auto-prune keeps the most recent 200.

### 15.2 Snapshots and Undo / Redo

Auto-snapshot timing: the startup baseline and after every system-state change
(`np.remount` / WebUI settings saved / plugin installed / mode switched). Manual
snapshots: the "manual snapshot" button in the Web UI rollback panel or
`np.snapshot_system("description")`.

```python
import norpagent as np

np()                                          # start (baseline snapshot taken automatically)
np.snapshot_system("before installing plugins")   # manual snapshot
# ...make a few changes (remount / settings saved / install plugins)...
np.undo()                                     # undo the most recent operation (in-process immediate)
np.redo()                                     # restore the undo
np.rollback("20260818T230101_ab12cd")         # roll back to any snapshot
np.rollback()                                 # roll back to the last known-good snapshot
np.list_snapshots()                           # timeline (is_current / is_last_good)
np.mark_good_snapshot("<id>")                 # manually mark "known good"
```

Semantic points:

1. **pointer model**: timeline + current pointer. Undo = apply the previous
   snapshot and move the pointer back; Redo = apply the next snapshot; after an
   undo, a new operation **truncates the redo branch** (standard undo semantics).
2. **in-process immediate**: replay reuses the remount hot-mount pipeline —
   component slots take effect on the next run, assembly slots hot-rebuild the
   AgentRuntime, the HTTP port stays the same; slots whose values equal the
   snapshot are skipped (avoiding needless frontend restarts). Changes during
   replay are **not** auto-snapshotted again (preventing an undo from taking a
   selfie that overwrites the redo branch).
3. **Web UI**: the left "rollback" page + buttons + shortcuts (Ctrl+Z /
   Ctrl+Shift+Z, not intercepted when an input box has focus); backend APIs
   `GET /api/snapshots`, `POST /api/snapshots {action: capture|undo|redo|rollback|mark_good}`.
4. **"known good" auto-marking**: 30 seconds after a successful engine start (or
   the first task completing) auto-marks good; manual marking is also available. ★
   in the rollback panel and the rescue CLI is the last known-good snapshot.

### 15.3 Custom Snapshot Content (hook-style extension)

```python
from norpagent import recovery

# capture hook + restore hook (optional)
recovery.register_snapshot_provider(
    "my_state",
    capture=lambda engine: {"mark": 42},          # any JSON value
    restore=lambda engine, value: apply_mark(value),
)
# registered -> effective: all subsequent snapshots carry a providers.my_state
# section, and restore is called on replay. Duplicate names overwrite;
# unregister_snapshot_provider unregisters.
```

### 15.4 Crash Rescue (standalone CLI, pure standard library)

`norpagent-rescue` deliberately **depends only on the standard library** (reads /
writes snapshot JSON and the WebUI settings file); even if the main program cannot
start at all due to config errors or plugin problems, the rescue tool still works:

```bash
norpagent-rescue list                        # timeline (★ = last known good)
norpagent-rescue show <id>                   # inspect a snapshot (redacted)
norpagent-rescue rollback <id>               # roll back: restore WebUI settings + write the rollback target
norpagent-rescue rollback --last-good        # one-step rollback to the last known-good snapshot
norpagent-rescue mark-good <id>              # manually mark "known good"
norpagent-rescue prune --keep 50             # keep only the most recent N
```

After a rollback, the next `norpagent` / `np()` startup **automatically consumes**
the rollback target (rollback_target.json, deleted after consumption): file-level
restore (WebUI settings / session files) executes immediately, and the snapshot's
slot config merges into this startup — **parameters explicitly given this time
take priority** (rescue is a fallback; it never overrides the user's conscious
choices). On startup failure both the CLI and np() print self-rescue guidance
(safe mode + rescue command).

### 15.5 Safe Mode

Entry: `np(safemode="on")`, CLI `norpagent --safe-mode`. Safe mode is not entered
by default (any value other than on does not trigger it).

Behavior (loads only the minimal kernel):

1. **skip all plugin directories** (plugins are the most likely startup-failure source);
2. **force the minimal preset** (ignores user-given preset / plugins / security
   slot parameters);
3. **does not read the WebUI settings file** (a bad config may be exactly why the
   last run crashed; runs purely in memory, and saving no longer writes to disk);
4. **keeps the core rollback capabilities**: the Web UI rollback panel and
   `/api/snapshots`, `norpagent-rescue` all remain usable — after starting you can
   roll back to any known-good snapshot, then restart normally after repair.

```python
import norpagent as np
np(safemode="on")          # minimal kernel + Web rollback panel
```

```bash
norpagent --safe-mode      # CLI equivalent
```

### 15.6 Human Rescue: Manual Tool Takeover API (when the model is dead)

**Scenario**: the model provider is down / the API key is invalid / model output
is corrupted — the agent's "brain" is unavailable, but its "hands" still work:
workspace files, sandbox, context store and task queue are all alive. Human
Rescue (v0.9.3) exposes **all 20 built-in tools** in a human-operable form: the
operator passes arguments by hand (manual input) and reads the raw execution
result (manual output), keeping the work moving until the model recovers.

**Design principles**:

1. **exactly the same execution path as the model** — manual calls and
   model-issued calls share the same `tool.run(args, ctx)` and `RunContext`
   (registry / sandbox / session / scheduler / context_store / project_manager,
   nothing missing) and write to the same state: files land in the same
   workspace, `context_add` writes to the same context store, `task_submit`
   enters the same task queue;
2. **no plugins are ever loaded** (plugins are the most likely failure source);
3. **hard timeout + cancel signal**: every call runs in a dedicated worker
   thread; on timeout the call is abandoned (daemon orphan thread, the same
   pattern as the model-call timeout) and the cancel event is set — the sandbox
   force-kills child process trees and streaming loops exit early;
4. **binds 127.0.0.1 by default**, optional bearer token — this endpoint really
   executes commands / writes files and must never be exposed beyond localhost.

Four entry points:

```bash
norpagent-rescue tools                              # inventory of all 20 tools
norpagent-rescue tool-call echo --args '{"text":"ping"}'
norpagent-rescue tool-call exec_cmd --args '{"command":"git status"}' --timeout 30
norpagent-rescue manual                             # interactive manual console
norpagent-rescue serve --port 8799                  # HTTP API + operator page
norpagent-rescue serve --token my-secret            # with bearer auth
```

#### 15.6.1 Interactive Manual Console (manual)

```text
rescue> echo {"text": "ping"}                        # <tool name> <JSON args>
rescue> {"tool": "file_list", "args": {}}            # JSON-object form
rescue> /tools                                       # list all tools
rescue> /exit                                        # quit
```

Boundary with the snapshot commands (list / rollback / ...): the `rescue.py`
module top level **still depends only on the standard library**; the
`tools / tool-call / manual / serve` commands lazily import the framework
(`norpagent.rescue_api`) inside the command functions — snapshot rollback keeps
working no matter how broken the main program is; manual takeover requires a
working framework installation but never touches the broken main process.

#### 15.6.2 HTTP API and Operator Page

`norpagent-rescue serve` starts a zero-dependency HTTP service (stdlib
ThreadingHTTPServer); opening the root path in a browser shows the operator
page (inline HTML/JS, no external resources): pick a tool from the dropdown →
the schema and required parameters are shown automatically → fill in JSON args
and a timeout by hand → call → raw result plus a call history.

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | operator page (inline HTML) |
| `/api/health` | GET | status / tool count / workspace root |
| `/api/tools` | GET | full inventory: `{tools:[{name,description,parameters,required,category}]}` |
| `/api/tools/<name>` | GET | single tool schema |
| `/api/tools/call` | POST | body `{"tool":"echo","args":{...},"timeout":N,"params":{...}}` |
| `/api/tools/<name>/call` | POST | body `{"args":{...},"timeout":N}` |

```bash
curl -s http://127.0.0.1:8799/api/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool":"context_search","args":{"query":"marker"}}'
```

The response is always:

```json
{
  "ok": true, "tool": "echo", "task_id": "1a2b3c4d5e6f",
  "output": "[echo] ping", "error": "", "success": true,
  "timed_out": false, "duration_ms": 0.4
}
```

Semantics: `ok=false` with HTTP 200 = the tool ran and failed (bad args /
business failure); HTTP 404 = tool not registered; 400 = invalid JSON body;
401 = missing or wrong token (only when a token is configured); 413 = body over
1 MB. A hard-timeout abandonment returns `timed_out=true` (HTTP 200; the caller
never waits for the orphan).

#### 15.6.3 Environment Assembly and Defaults

`RescueToolEnvironment` assembles the full built-in stack with
`install_defaults()` (the same source as the standard preset) and shares the
components across calls for the environment's lifetime:

| Component | Default | Notes |
|---|---|---|
| sandbox | subprocess | one child process per command; path-safety constraints still apply |
| session | memory | one fixed rescue session (rescue-manual) |
| scheduler | **persistent** | all task_* tools work; by default shares the main app's queue DB `~/.norpagent/tasks.db` — inspect tasks the agent submitted before the model died |
| context store | fts5 | by default shares `~/.norpagent/context.db`; `--context-db` isolates |
| project management | basic | `project_status` works |
| workspace | current directory | `--workspace` overrides; the file_* path-safety boundary moves with it |

Note: the persistent scheduler means `norpagent-rescue tools` also creates the
task DB on disk (if it does not exist). For a fully diskless environment pass
`--scheduler simple` (the task-query tools then honestly report "the current
scheduler does not support queries").

#### 15.6.4 Isolation and Safety Boundaries

- **no model, no plugins, no hooks**: `before_tool_call` etc. do not intervene
  (the rescue environment subscribes no hooks; the operator is the final approver);
- **path safety still applies**: absolute-path / `..` traversal rejection for
  `file_*`, the `run_python` AST static precheck (no import / no dunder
  attributes) all stay in force;
- **two-layer timeout**: tool-level timeouts (exec_cmd max 300s, run_python's
  `ptc_timeout`) plus environment-level hard timeout (default 300s, tunable via
  the HTTP body or `--timeout`) — a single timed-out call is abandoned (thread
  + cancel event) and never affects later calls;
- **concurrency-safe**: the environment is thread-safe (every component has its
  own lock); multiple HTTP requests can operate in parallel, sharing the same
  context-store / task-queue connections.

#### 15.6.5 Programmatic Embedding

```python
from norpagent.rescue_api import RescueToolEnvironment, RescueToolAPI

env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")
print(env.call_tool("echo", {"text": "ping"})["output"])

api = RescueToolAPI(env, port=0, token="secret")   # port=0 = ephemeral port
port = api.start()                                 # returns the actual port
api.shutdown()
```

An application can also bring up the rescue service automatically after a model
health-check failure (for example mounting a `RescueToolAPI` inside the existing
process) so on-call operators can take over.

#### 15.6.6 Relationship with the Other Rescue Layers

| Layer | Goal | Entry |
|---|---|---|
| snapshot rollback | config / plugins broken — roll back to a good state | `norpagent-rescue rollback --last-good` |
| safe mode | keep rollback ability even when nothing starts | `np(safemode="on")` / `--safe-mode` |
| **human rescue** | **model dead — a human does the model's work** | `norpagent-rescue tools / tool-call / manual / serve` |

The three complement each other: first roll back (or enter safe mode) to rescue
the configuration, then push the work forward with manual takeover, and when the
model recovers the agent continues from the same state (files / context store /
task queue).

---

## Chapter 16 Library Integration Examples

### 16.1 FastAPI Integration

```python
import norpagent as np
from fastapi import FastAPI

np(preset="standard", frontend="norpagent.frontends.headless:HeadlessFrontend")
app = FastAPI()

@app.post("/chat")
def chat(text: str, session_id: str | None = None):
    result = np.current().submit(text, session_id=session_id)
    return {"content": result.final_content, "session_id": result.session_id,
            "status": result.status}
```

### 16.2 Desktop-App Integration (pywebview style)

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend")
fe = np.current().frontend

# the JS bridge forwards user input to fe.send();
# subscribe to on_content on the event bus to push streaming output back to the frontend.
```

### 16.3 Integration Points

1. **singleton engine**: the running engine is a singleton; `np()` idempotently
   returns the current engine;
2. **lifecycle**: the main loop polls `np.stop()`; process exit has an atexit
   fallback cleanup;
3. **assembly observation**: `np.current().layer.describe()` prints the assembly
   manifest.

---

## Chapter 17 Testing and Debugging

```bash
python tests/test_p1_smoke.py    # kernel/protocol smoke
python tests/test_p2_smoke.py    # adapters/tools/sessions
python tests/test_p3_smoke.py    # context/scheduler/sandbox/security/plugins/Web
python tests/test_p4_smoke.py    # hooks/security/PTC/isolation
python tests/test_p5_arch.py     # architecture layer/address functions/np()/nasyncio
```

Debugging aids:

```python
eng = np.current()
print(eng.state)              # engine state
print(eng.layer.describe())   # assembly manifest
print(eng.last_result)        # most recent task result
```

---

## Chapter 18 Migration Guide

### 18.1 Migrating from Old norpagent (≤0.4)

```python
# old style: manual assembly
reg = Registry(); install_defaults(reg); register_all_presets(reg)
agent = AgentRuntime(reg, preset="minimal")
result = agent.run("hello")

# new style: np() assembly (the manual assembly API stays usable)
import norpagent as np
np(preset="minimal", prompt="hello",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
result = np.current().last_result
```

The manual assembly API (Registry / AgentRuntime / Preset) **remains usable**;
`np()` is its declarative wrapper.

### 18.2 Migrating from the Old Desktop Application

Modules in the old application mount into slots per the mapping below:

| Old application module | New slot | How to mount |
|---|---|---|
| `nasync_io` (self-developed event loop) | `async_loop` | **already packaged into the library**: `norpagent.nasyncio` is the default scheduling core (originally nasync_io v2.0.0); no self-hosted file needed; fill an address only when swapping implementations |
| `async_loop.AsyncAgentLoop` | `agent_runtime` | implement run/shutdown -> fill the address |
| FastAPI backend + desktop UI | `frontend` | implement the Frontend protocol -> fill the address |
| `plugin_system` | `plugins` | pass the directory list directly |
| `sandbox_pool` | `sandbox` | `"pooled"` or a custom address |
| `config.json` switches | preset params | task-parameter passthrough |

### 18.3 Version Compatibility

- the protocol modules (protocols) and the kernel (kernel) have been backward
  compatible since 0.1;
- 0.5 added the arch / loops / frontends / runtime packages;
- 0.6 added FLOW flow orchestration / FE frontend modules / the input-box family /
  agent-tool mounting (agent_tools) / the canvas-management trio; DeepSeek's
  `deepseek-chat` / `deepseek-reasoner` were officially retired on 2026-07-24, and
  the adapter defaults to `deepseek-v4-flash`;
- 0.7 added the Web frontend `html` slot mounting parameter (four ways to replace
  the `/` route page: `;key=value` address clause / constructor / config dict /
  runtime params), fixed the `;key=value` address-clause resolution chain, and
  added **runtime hot mount** (`np.remount()` replacing any slot while running, see 3.7);
- 0.8 migrated the default event loop to the **self-developed nasyncio core**
  (originally nasync_io, packaged into the library as `norpagent.nasyncio`): the
  library has **zero `import asyncio`** and no longer depends on the standard
  asyncio (reasons in 4.7). The default address became
  `norpagent.loops.nasyncio:NasyncioLoopRuntime`; the 0.7 old address
  `norpagent.loops.std_asyncio:StdLoopRuntime` remains as a compatibility shim
  (same implementation, does not import asyncio); historical code keeps working;
- 0.9 embedded and ultra-high-concurrency optimizations (Chapter 14):
  `install_core()` minimal assembly and the builtin package lazy imports
  (`import norpagent.builtin` no longer pulls sqlite3 / http.server), the
  `embedded` preset (sixth mode, headless frontend by default), WebUI
  construction does zero disk I/O with page byte caching, EventBus copy-on-write,
  SSE bounded backpressure (default drop_oldest, hot-changeable) + batched frame
  writes + fast disconnect reclamation, HTTP concurrency tuning, submit polling
  default tightened to 0.05s (configurable). Behavior compatibility: SSE default
  buffer cap 1024 (slow clients drop the oldest; previously unbounded); the
  `unlimited` policy + `sse_queue_size=0` restore the old behavior;
- 0.9 hot-pluggable slot table (3.8): `register_slot()` / `unregister_slot()`
  register / unregister **custom slots** at runtime (`SlotSpec.applier` declares
  assembly logic, `remount_rebuild_agent` declares whether to hot-rebuild the
  AgentRuntime after a hot replacement); registration plugs into the full
  pipeline — `np()` parameter validation, ArchLayer assembly (connect
  idempotently fills in late-registered slots), `np.remount()` hot replacement,
  `layer.describe()` listing; `replace=True` spec hot-replacement is supported;
  the 18 built-in slots are protected (their values can still be hot-replaced at
  any time); slot-table operations are thread-safe. Behavior compatibility: the
  assembly / hot-mount semantics of the existing 18 slots are completely unchanged;
- 0.9 work rollback (Chapter 15): snapshot timeline + Undo / Redo / Rollback
  (in-process immediate, reusing the remount hot-mount pipeline) + the standalone
  crash-rescue CLI
(in-process immediate, reusing the remount hot-mount pipeline) + the standalone
crash-rescue CLI `norpagent-rescue` (pure standard library; suggests the last
known-good snapshot for one-step restore; the rollback target is consumed
automatically at the next startup) + safe mode (`np(safemode="on")` / CLI
`--safe-mode`, loads only the minimal kernel); auto snapshots are on by default
(after remount / settings saved / plugins installed), sensitive keys are redacted
before persisting, custom snapshot providers and snapshot mode B (including session
data files) are supported;
- breaking changes appear only in major versions.

---

## Chapter 19 FAQ

**Q1: does `np()` block?**
No. The engine runs on background threads and the main thread keeps executing —
this is exactly why the `while running: if np.stop()` pattern exists.

**Q2: when does `np.stop()` become True?**
When the engine is STOPPED: the single task finished, the frontend `/exit`, an
explicit `shutdown()`, or any `request_stop()`. Always True with no engine.

**Q3: how do I pass the model API key?**
```python
np(model="openai_compat", model_name="deepseek-v4-flash",
   base_url="https://api.deepseek.com/v1", api_key="sk-...")
```
`model_name / base_url / api_key` are model shortcut parameters: when the model is
a built-in adapter name the provider is reconstructed automatically (same as the
CLI); or set the environment variable `OPENAI_API_KEY` directly; or pass a
constructed provider instance `np(model=MyProvider())`.

**Q4: can address strings cause arbitrary code execution?**
Yes. An address string names the module to load; address values are passed by the
library's users in code. External plugin loading goes through signature
verification, AST audit and import restrictions.

**Q5: can I run two different Agents at once?**
The running engine is a singleton. For multiple instances use the manual assembly
API directly: `Registry() + AgentRuntime(...)`, not bound by the singleton
(17.1).

**Q6: do hooks keep working after replacing the loop system?**
Yes. Hooks hang on the event bus (the bottom minimal kernel), independent of the
loop system.

**Q7: what is the difference between `np(async_loop=...)` and `np.nasyncio(...)`?**
None — the same path; the former is the slot form, the latter the architecture
function form.

**Q8: what are the `/flow` canvas, FE frontend modules, and the input-box family?**
`/flow` is a standalone frontend category "module flow": the canvas graph
auto-saves and can hot-switch front chat behavior with "apply to agent". FE =
frontend modules registered by dragging in `.html/.js/.ts` files (hosted at
`/fe/<name>`, independent config scope). The input-box family = every place that
needs input is an input box: one input-box row per config item on FE /
global-settings node cards, a value input-box strip at the bottom of
model/tool/sandbox node cards, and every model field is a hand-editable input box
+ datalist hint (type manually even when the fetch fails). See 5.7 and
`docs/flow.md`.

**Q9: how do I bulk-clean the canvas? Can deepseek-chat still be used?**
Canvas: `Alt+drag` box select / `Ctrl+A` select all then `Del` bulk-delete; the
top bar's "clear canvas" wipes everything in one click (auto-saves immediately
after confirmation; refreshing stays blank); double-click-inject and
single-click-dock-card quick-inject were removed (to prevent accidental node
spam). deepseek-chat / deepseek-reasoner were retired by DeepSeek on 2026-07-24;
the current models are deepseek-v4-flash / deepseek-v4-pro; old names are
auto-filtered from the hint list, the remote-model dock and the backend cache
(`RETIRED_REMOTE_MODELS`).

**Q10: the web side has too few native tools — how do I wire third-party custom
tools and let the agent call them automatically?**
"File-as-module" mounting: drag a `.py` file declaring
`__norpagent_type__ = "tool"` onto the `/flow` canvas for real registration
(through the plugin security pipeline); the tool node's ports come automatically
from the OpenAI function schema. To let the agent in front chat call them
automatically (tool calling): ① click the `AGENT` badge on the tool card in the
`/flow` module dock; ② tick the "🧰 agent tools" list in the WebUI settings dialog.
Both go through `POST /api/agent/tools` (config keys `agent_tools` /
`agent_tools_explicit`) and hot-apply `preset.tools`, effective on the next
run(), no restart. See 5.7 "agent-tool mounting" and `docs/flow.md` section 9.

**Q11: can I swap the model / frontend / modules after startup? (runtime hot
mount)**
Yes. `np.remount(slot=value)` replaces any slot while the engine runs: component
slots (model / tools / hooks / security / plugins) take effect on the next run();
assembly slots (session / sandbox / scheduler / ui / agent_runtime / preset /
context_store / project_manager) trigger an AgentRuntime hot rebuild;
frontend / async_loop stop the old and start the new; logger / storage /
error_handler update immediately. String addresses invalidate the module cache
and .pyc before remounting, so "edit the module file →
np.remount(model="myapp.model:create")" is hot reload. Repeatedly mounted
architecture-level subscriptions are unsubscribed first then remounted, never
stacking. See 3.7.

**Q12: why did Ctrl+C fail before? How is interruption guaranteed now?**
Two root causes (see 4.6): ① on Windows the main thread blocked in a single
`Event.wait()` (`WaitForSingleObject`) never sees SIGINT — the pending interrupt
is only checked at bytecode boundaries; now `submit()` uses polling wait (a
boundary every ≤poll_interval seconds, default 0.05s, configurable), so Ctrl+C
surfaces immediately as `KeyboardInterrupt`.
② worker threads stuck in sandbox `subprocess` / HTTP cannot be killed, and the
standard thread pool (ThreadPoolExecutor, also asyncio's default executor) is
force-joined at interpreter exit — the process freezes until the task ends; now
the worker pool uses bare daemon threads (no join at exit), and Ctrl+C / engine
stop sets the task's cancel event: the PTC sandbox force-kills the child process
immediately, the pooled sandbox kills the process tree, model streams interrupt,
and the agent wraps up as stopped at turn boundaries. Task bodies can proactively
respond to cancellation with `norpagent.loops.cancel.cancel_requested()`.

**Q13: does norpagent depend on the standard asyncio?**
No. Since 0.8 the library has **zero `import asyncio`**: the default scheduling
core is the self-developed async-IO library `norpagent.nasyncio` packaged into
the library (originally nasync_io), using only non-asyncio standard modules like
threading / selectors / socket. Declaration, reasons and verification in 4.7.

**Q14: how do I deploy on embedded devices / ultra-high-concurrency servers?
(0.9)**
- **embedded**: `install_core()` with a self-built registry (no sqlite3 /
  http.server imports) + `build_embedded_preset()`, or directly
  `np(preset="embedded")` (headless frontend by default, mock fallback); tighten
  worker threads with `NORPAGENT_MAX_WORKERS=1` (or `config={"loop":
  {"max_workers": 1}}`), relax polling with `NORPAGENT_SUBMIT_POLL`.
- **ultra-high-concurrency**: SSE per-connection bounded buffer default 1024,
  slow clients drop the oldest (`drop_oldest`); configure at startup with
  `np(config={"web": {"sse_queue_size": 2048}})`, hot-change while running with
  `WebUI.set_sse_queue(...)` / `POST /api/streams`; batched frame writes (default
  32 frames / 50ms) reduce system calls; EventBus copy-on-write eliminates
  per-event list copies. Full details in Chapter 14.

**Q15: what if the framework lacks the slot I need? (hot-pluggable slot table,
0.9)**
Register your own: `register_slot(SlotSpec(name=..., string_semantics=...,
applier=...))`. Registration plugs into the full pipeline — `np()` parameter
validation, assembly, `np.remount()` hot replacement, `layer.describe()`
listing; the applier receives the resolved slot value and four mutable containers
(components / extras / overrides / meta) and can register generic components
(`remount_rebuild_agent=True` hot-rebuilds the AgentRuntime after a hot
replacement), mount event subscriptions (recorded in meta for unsubscribe, so
reentrancy is safe), or provide extra objects to the engine. The 18 built-in
slots are protected (cannot be overridden / unregistered); their values can be
hot-replaced with `np.remount` at any time. Full contract in 3.8.

**Q16: how do I undo a config change / roll back to a previous state? (work
rollback, 0.9)**
Three steps: in-process `np.undo()` / `np.redo()` (Web UI Ctrl+Z / Ctrl+Shift+Z
or the "rollback" panel buttons, immediate); roll back to any version with
`np.rollback("<snapshot id>")` (`np.list_snapshots()` browses the timeline;
`np.rollback()` with no args = the last known-good snapshot); when the main
program cannot start use `norpagent-rescue rollback --last-good` (pure
standard-library CLI; applied automatically at the next startup), or
`norpagent --safe-mode` / `np(safemode="on")` to load only the minimal kernel and
fix the config. Snapshots default to `~/.norpagent/snapshots/`; sensitive keys
are redacted; auto snapshots are on by default (disable with
`np(snapshots="off")`). Full semantics in Chapter 15.

---

## Appendix A Architecture Slot Quick Reference

| Slot | String semantics | Default | Factory context keys |
|---|---|---|---|
| async_loop | address | NasyncioLoopRuntime (self-developed nasyncio core; config.loop tunes max_workers / poll_interval) | layer, config |
| agent_runtime | address | AgentRuntime | registry, preset, ui, task_params, layer, config |
| model | name_or_address | preset declaration | layer, config |
| tools | name | preset declaration | - |
| session | name_or_address | preset declaration | - |
| sandbox | name_or_address | preset declaration | - |
| scheduler | name_or_address | preset declaration | - |
| context_store | address | preset declaration | layer, config |
| project_manager | address | preset declaration | layer, config |
| hooks | literal | the standard 9 layers | - |
| security | literal | not enabled | - |
| plugins | literal | not loaded | - |
| frontend | address | prompt / embedded→headless, otherwise web | layer, config |
| ui | name | preset declaration | - |
| preset | name | standard | - |
| logger | literal | logging.getLogger("norpagent") | - |
| storage | literal | ~/.norpagent | - |
| error_handler | literal | records to the log | - |

> Runtime hot mount (3.7): every slot can be replaced with `np.remount(slot=value)`.
> `agent_runtime` is a `defer_factory` slot (the factory call is deferred to the
> engine assembly phase). Hot-pluggable slot table (3.8): `register_slot()` can
> register custom slots into this table.

## Appendix B 9-Layer Hook Quick Reference

| Layer | Hooks | Mutating | Key params |
|---|---|---|---|
| L1 lifecycle | on_agent_init / on_agent_shutdown | - | preset |
| L2 task | on_task_start / on_task_done / on_task_stopped / on_task_error / on_task_timeout | - | task_id, session_id |
| L3 input | before_input / after_input / on_user_input_required | before | user_input, params, question |
| L4 session | before_session_create / after_session_create / before_message_append / after_message_append | before | title, message |
| L5 assembly | before_build_messages / after_build_messages | both | system_prompt, messages |
| L6 step | before_step / after_step | before | step, messages |
| L7 model | before_model_call / after_model_call / on_reasoning / on_content / on_event / on_usage_update | before/after_model_call | messages, output, params |
| L8 tool | before_tool_call / after_tool_call / on_tool_error | both | tool_name, args, result |
| L9 finalize | before_result / after_result | both | result |

> Full payload keys and the complete 29-hook table in 9.1; full return semantics
> of mutating hooks (HookVeto wrap-up / rewrite rules) in 9.3.
> The plugin-loading pipeline has 8 more hooks (PLUGIN_PIPELINE_LAYER), see 11.4.

## Appendix C Public API Index

```python
# module entry
np()                      # launch()
np.stop()                 # lifecycle polling
np.nasyncio(address=...)  # event-loop architecture function (np.nasyncio binds the self-developed core module, callable)
np.current() / np.submit() / np.shutdown()
np.remount(model=..., ...)   # runtime hot mount: any slot replaceable

# work rollback (Chapter 15)
from norpagent.recovery import (snapshot_system, undo, redo, rollback,
                                list_snapshots, mark_good, last_good_id,
                                register_snapshot_provider, set_snapshot_dir,
                                prune, RecoveryError)
np.snapshot_system("description")  # manual snapshot (top-level convenience entry)
np.undo() / np.redo()             # undo / restore (in-process immediate)
np.rollback("<id>")               # roll back to any snapshot (default = last known good)
np.mark_good_snapshot("<id>")     # mark "known good"
np(safemode="on")                 # safe mode: loads only the minimal kernel
np(snapshot_dir=..., snapshots="off", snapshot_sessions="on")  # snapshot config
# crash rescue: norpagent-rescue list|show|rollback|mark-good|prune
# human rescue (15.6): norpagent-rescue tools|tool-call|manual|serve
from norpagent.rescue_api import (RescueToolEnvironment, RescueToolAPI)
env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")
env.call_tool("echo", {"text": "ping"})     # manual args in + raw result out
api = RescueToolAPI(env, port=0, token=...) # HTTP API + operator page
api.start() / api.shutdown()

# architecture layer
from norpagent.arch import ArchLayer, SlotSpec, SLOT_SPECS
from norpagent.arch import resolve_address, call_factory, AddressError
layer.remount(slot, value)  # architecture-layer hot mount (module cache + pyc invalidation)
layer.subconfig(slot)       # slot extra sub-config (";key=value")

# hot-pluggable slot table (3.8)
from norpagent.arch import (register_slot, unregister_slot, SlotError,
                            all_slot_names, snapshot_slots, is_builtin_slot)
register_slot(SlotSpec(name=..., string_semantics=..., applier=...,
                       remount_rebuild_agent=...))   # register a custom slot
register_slot(spec, replace=True)   # hot-replace a custom slot's spec
unregister_slot(name)               # unregister a custom slot

# loop system
from norpagent.loops import (nasyncio, LoopRuntime,
                             NasyncioLoopRuntime, StdLoopRuntime)
from norpagent.loops.cancel import cancel_requested, current_cancel_event
loop.interrupt()   # request cancellation of all in-flight submit tasks (the engine-stop path)

# self-developed async core (packaged into the library, no standard-asyncio dependency)
import norpagent.nasyncio as core
core.EventLoop / core.Future / core.Task        # self-developed types
core.sleep / core.wait_for / core.ensure_future # utility coroutines
core.run_coroutine_threadsafe(coro, loop)       # cross-thread coroutine submission
core.Event / core.Lock / core.Condition         # synchronization primitives

# frontends
from norpagent.frontends import (Frontend, ConsoleFrontend,
                                 HeadlessFrontend, WebFrontend)

# runtime
from norpagent.runtime import (launch, current, stop, submit,
                               shutdown, NorpEngine, EngineState, EngineError)
# task-level slot injection (3.9): submit(text, slot_overrides={...})
#   engine.submit("task", slot_overrides={"model": "anthropic", "tools": [...]})
#   np.submit("task", slot_overrides={"session": {"name": "memory", "persist": True}})

# kernel (manual assembly, equivalently kept)
from norpagent import (Registry, EventBus, Preset, AgentRuntime,
                       RunResult, install_defaults, install_core,
                       register_all_presets, build_embedded_preset)
# install_core(reg): embedded minimal assembly (no sqlite3 / http.server dependencies)
# build_embedded_preset(): embedded preset (sixth mode)

# security / hooks / plugins
from norpagent import safe, SafetyKit, SecurityContext
from norpagent import hooks                      # hook system (HookSystem)
from norpagent.hooks import (Hook, BoundHook, HookLayer, HookSystem,
                             HookVeto, get_default_system,
                             before_input, before_model_call,
                             before_tool_call, after_tool_call, ...)  # 29 standard hooks
from norpagent.plugins import (PluginSystem, PluginLoader, PluginInfo,
                               install_plugin_dirs, PLUGIN_PIPELINE_LAYER,
                               before_plugin_load, after_plugin_register, ...)
from norpagent.plugins.isolation import ProcessIsolationManager, ProcessPluginHost
from norpagent.security import (scan_message, harden_system_prompt,
                                ApprovalPolicy, NetworkPolicy, SourceAuditor,
                                SignatureVerifier, generate_keypair, sign_plugin_file)

# Web UI: page mounting and hot replacement (5.4) + SSE backpressure (ultra-high concurrency, 14.3)
from norpagent.builtin.ui.web import WebUI
ui = WebUI(port=8787, html="/path/to/my.html",
           flow_html="/path/to/flow.html")     # replace the / and /flow pages wholesale
ui = WebUI(port=8787, sse_queue_size=2048,
           sse_queue_policy="drop_oldest")
ui.mount_page("flow", "/path/to/new-flow.html")  # hot page swap while running (no service restart)
ui.mount_page("flow", None)                      # unmount, fall back to the library built-in
ui.page_bytes("flow")                            # current /flow page bytes
ui.set_sse_queue(4096, "drop_newest")   # hot change while running (equivalent to POST /api/streams)
ui.streams_info()                        # subscriber count / dropped-event count / buffer depth
# WebFrontend homogeneous entry: frontend.mount_page(page, html)
# remount page hot-replace keys (v0.9):
#   np.remount(flow_html="/path/to/new-flow.html")  # /flow page swapped immediately
#   np.remount(html="/path/to/new-front.html")      # / main page swapped immediately
#   np.remount(flow_html=None)                      # unmount, fall back to the library built-in
# frontend slot HTML-path direct mount (v0.9):
#   np(frontend="/path/to/my.html")  ==  np(frontend="...WebFrontend;html=/path/to/my.html")
```

---

## Chapter 20 Module Flow Orchestration (FLOW)

> This chapter corresponds to `norpagent/flows/__init__.py` (1535 lines, one of
> the framework's largest orchestration kernels). 5.7 covered the `/flow` page's
> frontend form; this chapter dives into its **kernel**: registry snapshots,
> file-as-module, topological execution and agent interplay.

### 20.1 Positioning and Overview

"Module flow" (`/flow`) is not an animated demo — it executes the canvas graph
with **real registered components**:

```
build_snapshot(registry, agent)   registry snapshot: model / tools / session / sandbox /
                                 scheduler / plugins / preset / hooks
                                 -> the frontend "core module dock" renders cards per real component
ModuleWorkspace.register(...)    "file-as-module": dragging in a .py goes through the full security
                                 pipeline (signature verification -> AST audit -> import restrictions -> registration)
FlowRunner                       topologically executes per the canvas graph (nodes + beams),
FlowRunner                       topologically executes per the canvas graph (nodes + beams),
                                 progress pushed via flow.* events over SSE
```

Three core classes / functions:

| Name | Duty |
|---|---|
| `build_snapshot(registry, agent)` | serializes the registry's current state into a snapshot dict, driving the frontend module dock |
| `ModuleWorkspace` | the on-disk workspace of flow modules: register / load .py / .json / .yaml modules |
| `FlowRunner` | the canvas-graph executor: node + beam topological execution, zero-interruption, event publishing |

### 20.2 Registry Snapshot: build_snapshot

```python
from norpagent.flows import build_snapshot

snap = build_snapshot(registry, agent)
# includes: models / tools / sessions / sandboxes / schedulers /
#           plugins / presets / hooks (one record per registered component)
```

- the snapshot is **live**: whatever is registered in the registry is what the
  frontend module dock shows;
- each component carries metadata (description / source / port inference) for
  canvas rendering;
- the snapshot drives the whole "module dock → drag onto the canvas → instance
  selection" interaction.

### 20.3 File-as-Module: ModuleWorkspace

```python
ws = ModuleWorkspace(registry, base_dir=default_modules_dir())
info = ws.register("my_node.py")      # goes through the plugin security pipeline
info = ws.register("graph.json")      # pure description module (pass-through node)
info = ws.register("graph.yaml")      # same
```

| File type | Handling |
|---|---|
| `.py` | full security pipeline: signature verification → AST audit → import restrictions → registration into the registry; every hook of a successfully registered plugin becomes a hook node on the canvas |
| `.json` / `.yaml` | registered as pure description modules (pass-through nodes, no execution logic) |
| others | explicit error; the frontend falls back to the official module |

- module directory: overridable with the environment variable
  `NORPAGENT_FLOW_MODULES`, default `~/.norpagent/flow_modules`;
- single-file size cap 200KB (`_MAX_MODULE_SIZE`).

### 20.4 Canvas-Graph Format: normalize_graph

The canvas graph is a "nodes + beams" dict structure; `normalize_graph` normalizes
and validates it:

```python
graph = {
    "nodes": [
        {"id": "n1", "type": "trigger"},
        {"id": "n2", "type": "model", "model": "openai_compat",
         "system_prompt": "...", "tools": ["echo"]},
        {"id": "n3", "type": "tool", "tool": "file_read",
         "inputs": {"path": {"from": "n4", "port": "path"}}},
        {"id": "n4", "type": "path", "value": "./readme.md"},
        {"id": "n5", "type": "output"},
    ],
    "beams": [["n1", "n2"], ["n2", "n3"], ["n3", "n5"], ["n4", "n3"]],
}
```

Node types and execution semantics (type → real action):

| Node | Real action |
|---|---|
| `trigger` | reads the prompt input, produces the start signal |
| `model` | calls the registry's real model; the `tools` port = the container-mounted tool set (schemas auto-resolved and passed to the provider); the `system_prompt` port = the system prompt (priority: beam value > input panel > node config > engine preset params; empty values do not inject a system message) |
| `tool` | calls the registry's real tool: each input port = one schema parameter (no longer a global query/result black box) |
| `toolbox` | tool container: input ports = the union of member-tool parameters (fanned out by port name); output ports = each member's "toolname.portname" qualified names + the tools packed port |
| `sandbox` | executes code in the registry's real sandbox (child-process isolation) |
| `security` | scans payloads for jailbreak / injection (`norpagent.security.guard`) |
| `session` | reads/writes the session manager (the engine's default session store) |
| `plugin` | plugin container (members = tool + hook members, port-union semantics) or standalone plugin-tool execution |
| `hook` | triggers a single hook of a plugin (one hook = one node; mutating hooks go through intercept, the return value becomes the node output) |
| `other` | pass-through (payload forwarded as-is) |
| `output` | aggregates the final result |
| `path` | path module: produces a relative path value validated by common path-safety checks (absolute paths / `..` traversal rejected); empty value = the workspace root `.` |
| `file` | file module: when registered as a plugin, executes as a plugin; otherwise pass-through |

### 20.5 FlowRunner: Topological Execution and Zero-Interruption Semantics

```python
runner = FlowRunner(graph, registry, agent, publish=on_event, workspace=ws)
result = runner.run(prompt="...", session_id="...", params={...})
```

Key designs:

1. **topological execution**: execution order determined by beam dependencies; each
   node has its own `try/except`;
2. **zero-interruption semantics**: a single node failure records `error` but does
   not interrupt the whole chain (other nodes execute normally);
3. **event publishing**: progress is pushed via the `publish` callback as `flow.*`
   events; the Web UI reuses the SSE channel (`/events`) to deliver in real time;
4. **stop support**: `runner.request_stop()` takes effect at node boundaries;
5. **cancellation propagation**: node execution also checks
   `params["_cancel_event"]`; engine stop / Ctrl+C exits as early as possible.

### 20.6 Flow-Agent Interplay (apply to agent)

A canvas graph can be "applied" as the main UI's execution engine:

```python
# Web UI side (the "/flow" page's "apply to agent" button):
ui.flow_save(graph, activate=True)    # save and activate
_active = ui._active_chat_flow()      # the currently active flow (None when not activated)

# once activated, main-UI chat tasks execute per the flow:
ui._run_flow_task(prompt, session_id, task_params)
# the flow execution result is written into the session history (_append_flow_history),
# consistent with ordinary tasks
```

That is: **ordinary chat → canvas orchestration → session history** are fully
connected.

### 20.7 Web UI Integration and APIs

| API | Purpose |
|---|---|
| `flow_snapshot` | registry snapshot, driving the `/flow` page's module dock and instance selection |
| `flow_run` | start one canvas-graph execution (background thread; progress pushed over SSE) |
| `flow_stop` | stop a running flow (effective at node boundaries) |
| `flow_register` | "file-as-module" real registration (.py plugin security pipeline / .json / .yaml descriptions) |
| `flow_save` | save the canvas graph (auto-save entry), optionally activate "apply to agent" |
| `flow_load` | return the last auto-saved canvas graph and activation state (restored after a page refresh) |
| `_load_flow_graph_from_disk` | restore the last saved flow at startup (silently ignored when the file is missing / corrupted) |
| `fe_read_file` / `fe_load_config` / `fe_save_config` | FE frontend-module file reading and independent config (no interference) |

### 20.8 Module Directory and Security Boundaries

- module directory: the `NORPAGENT_FLOW_MODULES` environment variable or
  `default_modules_dir()`;
- `.py` modules share the same security pipeline as external plugins (Chapter 11):
  signature → audit → import restrictions → registration;
- `path` nodes enforce common path-safety validation (absolute paths / `..`
  traversal rejected);
- single-file 200KB cap + output truncated at 4000 chars (`_MAX_OUTPUT_CHARS`) to
  prevent resource blowups.

---

## Chapter 21 Built-in Components in Depth

> This chapter dissects the built-in implementations under `builtin/` one by one:
> internal mechanisms, protocol relations, selection advice. All built-in
> components have equal status with third-party components — they go through the
> registry and can be replaced by anything.

### 21.1 Model Adapters

| Adapter | File | Features |
|---|---|---|
| `mock` | `builtin/models/mock.py` | deterministic output: built-in Q&A pairs + guidance, zero dependencies, for testing / benchmarks / no-network environments |
| `openai_compat` | `builtin/models/openai_compat.py` | OpenAI-compatible protocol (DeepSeek / OpenAI / Qwen / vLLM / Ollama etc.), SDK provided by `norpagent[openai]`; supports reasoning effort (`model_supports_reasoning_effort` / `normalize_effort`), DeepSeek v4 special-casing (`model_is_deepseek_v4`), chain-of-thought extraction (`_extract_reasoning`) |
| `anthropic` | `builtin/models/anthropic.py` | Anthropic-protocol adapter, SDK provided by `norpagent[anthropic]` |

Common points (protocol `ModelProvider`):

- `generate(messages, tool_schemas, params) -> ModelOutput` (including usage);
- optional `stream(...)`: streaming `ModelStreamChunk` output (content deltas /
  chain-of-thought / tool calls);
- cancellation support: adapters read `params["_cancel_event"]` and exit the
  streaming loop as early as possible on engine stop / Ctrl+C;
- credential fallback: when no key is provided at all, the assembly layer falls
  back to `mock` (`runtime/mount.py`'s `_has_model_credentials` checks
  `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` /
  `DASHSCOPE_API_KEY` / `NORPAGENT_API_KEY`).

### 21.2 The Tool Set (21 built-in tools)

| Group | Tools | Notes |
|---|---|---|
| P1 basics | `echo` / `get_time` / `run_python` | echo / clock / Python execution (the PTC prototype) |
| P2 engineering | `file_read` / `file_write` / `file_list` / `file_delete` | file operations, **strictly confined to the workspace root** (`pathsafe` validation: absolute paths and `..` traversal rejected; configurable root) |
| P2 commands | `exec_cmd` | command execution through the sandbox protocol (`sandbox.run_shell`), timeout clamped (`_MAX_TIMEOUT`) |
| P2 web | `web_search` / `web_fetch` / `web_extract_links` | web retrieval; **SSRF protection** (`is_private_url` rejects private networks / metadata addresses); uses requests when available, otherwise urllib fallback; bs4 structured extraction when available, otherwise regex fallback — usable with zero dependencies |
| P3 context | `context_add` / `context_search` / `context_list` / `context_delete` | cross-session searchable knowledge base (FTS5, see 21.6) |
| P3 project | `project_status` | project management (git-aware, see 21.7) |
| P3 tasks | `task_submit` / `task_list` / `task_status` / `task_cancel` | long-running task cooperation (persistent scheduler, see 21.5) |

Tool protocol (`protocols/tool.py`): `name` / `schema()` / `run(args, ctx)`,
returning `ToolResult`; `ctx` carries a `RunContext` (component access:
`ctx.component("context_store")` etc.).

### 21.3 Session Stores

| Implementation | Features | Suitable for |
|---|---|---|
| `memory` | pure in-memory dict, lost when the process ends | embedded / tests / single tasks |
| `sqlite` | SQLite persistence: schema + message migration (`_MESSAGE_MIGRATIONS` incremental upgrades), `close` / `clear` lifecycle | default (standard preset); conversations continue across restarts |

Protocol (`protocols/session.py`): `create_session / get_session / append_message /
history / list_sessions / delete_session`.

### 21.4 Sandboxes

| Implementation | Mechanism | Suitable for |
|---|---|---|
| `subprocess` | spawns a child process per call, simple and direct | lightweight / embedded |
| `pooled` | **sandbox pool**: child-process reuse + concurrency cap + timeout force-kill of the process tree (`_kill_process_tree`, `taskkill /T` on Windows); `PooledSandboxProvider` manages the pool lifecycle (create / release / discard / kill_task / close_all / stats) | default (standard preset); balance of performance and isolation |
| `isolated_python` | PTC child-process isolated execution: `check_ptc_source` static validation + wrapper template (`_WRAPPER_TEMPLATE`) + result return (`_CALLER_SRC`) | the isolated execution path of the `run_python` tool |

Protocol (`protocols/sandbox.py`): `Sandbox.run_shell / run_python / close`,
`SandboxProvider.create`.

### 21.5 Schedulers

| Implementation | Mechanism | Suitable for |
|---|---|---|
| `simple` | in-memory queue: submit / pending / drain | embedded / tests |
| `persistent` | **SQLite-persisted scheduling**: tasks on disk (`_SCHEMA`), terminal states (`_TERMINAL_STATUSES`), `resume` after a crash, `counts` / `list_tasks` / `cancel` / `clear` | default (standard preset), long-running task cooperation |

### 21.6 The Context Store (FTS5)

`builtin/context/fts5.py`: a cross-session knowledge base implemented with SQLite
FTS5 full-text indexing.

- **Chinese tokenization**: `_tokenize` has built-in Chinese segmentation
  (bigram + single characters), no jieba dependency — zero third-party dependencies;
- **queries**: `_tokenize_for_query` + `_fts5_phrase` build phrase queries;
- API: `add / update / search / get / list / delete / clear / stats / close`.

### 21.7 Project Management (BasicProjectManager)

`builtin/projects/basic.py`:

- project metadata: `_META_DIR` / `_META_FILE` (meta read/write, `init /
  load_meta / save_meta / touch`);
- directory scanning: `scan` (skips `_SKIP_DIRS`);
- **git-aware**: `git_status` (probes the `git` command; degrades gracefully
  without git);
- API: `status` (used by the `project_status` tool).

---

## Chapter 22 Web UI and Frontend Deep Dive

> Corresponding code: `builtin/ui/web.py` (2596 lines) + `frontends/web.py`
> (407 lines). 5.4 covers usage; this chapter covers **internal mechanisms and
> the complete API set**.

### 22.1 Architecture and Thread Model

```
browser ──HTTP──▶ _RobustHTTPServer (tuned ThreadingHTTPServer)
              ├── /           main chat page (front.html, hot-replaceable)
              ├── /flow       flow-canvas page (norpflow.html, hot-replaceable)
              ├── /api/*      dozens of REST endpoints
              └── /events     SSE long connection (all events pushed in real time)
                └── _SSESubscriber (bounded buffer + condition-variable wakeup, one per connection)

WebUI.start()        background daemon thread serve_forever (non-blocking)
WebUI.submit()       submit a task (background thread execution, does not block HTTP)
WebUI.on_event()     receives AgentEvent -> pushes to all SSE subscribers + records history
```

`_RobustHTTPServer` tuning points: silent disconnect noise (WinError 10053 etc.),
`daemon_threads=True`, `allow_reuse_address=True` (reuse the port immediately on
restart), `request_queue_size=256` (listen backlog), `block_on_close=False` (fast
shutdown).

### 22.2 REST API Reference

| Group | Endpoint (method) | Purpose |
|---|---|---|
| tasks | `submit` / `stop_task` | submit a task / stop a task (effective at step boundaries) |
| sessions | `create_session` / `session_info` / `list_sessions` / `session_messages` / `close_session` / `set_session_title` / `set_session_workspace` | full session lifecycle + message history + workspace |
| config | `get_config` / `save_config` / `reset_config` / `set_api_key` / `validate_api_key` / `first_run` | the config panel (persisted to `~/.norpagent/webui_config.json`, atomic writes) |
| models | `list_models` / `set_agent_tools` / `agent_effective_tools` / `tools_info` | model list (including remote), tool-set management |
| plugins | `get_plugin_dirs` / `list_plugins` / `add_plugin_dir` / `remove_plugin_dir` / `reload_plugins` | plugin directories and hot reload |
| security | `get_security` / `set_security` | security-level read / set |
| monitoring | `health` / `usage` / `debug_info` / `streams_info` | health check / usage / debug / SSE backpressure stats |
| files | `list_fs` / `read_fs_file` / `upload_files` | directory navigation / file reading / dataURL upload (no binaries) |
| flow | `flow_snapshot` / `flow_run` / `flow_stop` / `flow_register` / `flow_save` / `flow_load` | canvas orchestration (Chapter 20) |
| rollback | `recovery_handle` | snapshot / Undo / Redo / Rollback API (`/api/snapshots`) |
| frontend modules | `fe_read_file` / `fe_load_config` / `fe_save_config` | FE module reading and independent config |

### 22.3 The SSE Event Protocol

- channel: the `/events` long connection;
- frame format: `data: {json}\n\n` (`_encode_sse_frame`, module-level reuse to
  avoid building a lambda per frame);
- event content: same-named events as the EventBus (`on_task_start` / `on_content`
  / `on_reasoning` / `after_tool_call` / `flow.*` ...) serialized as JSON frames;
- **backpressure** (`_SSESubscriber`):

| Policy | Semantics | Suitable for |
|---|---|---|
| `drop_oldest` (default) | buffer full drops the oldest; the client degrades without disconnecting | display-style frontends |
| `drop_newest` | buffer full drops the newest; keeps the old state | state-sync consumers |
| `unlimited` | no cap (old behavior) | when you explicitly need everything |

- hot change while running: `ui.set_sse_queue(maxsize, policy)` (effective on
  existing connections immediately);
- wakeup optimization: one wakeup on the empty→non-empty transition; readers drain
  the buffer per wakeup (less locking under high concurrency);
- disconnect reclamation: subscribers are reclaimed within ≤1s after disconnect
  (copy-on-write replacement, never blocks publishing).

### 22.4 Config Panel and Persistence

- config items: model (name / base_url / api_key / sampling params like
  temperature), tool set, plugin directories, security level, port / language etc.;
- persistence: `_save_config_to_disk` atomic writes (failure only logs, never
  drags down saving); startup `_load_config_from_disk` restores (missing /
  corrupted files silently ignored);
- after saving, `set_config_apply` callbacks re-register models / plugins /
  security (`WebFrontend._apply_config`);
- config snapshot: the work-rollback system includes the WebUI settings file in
  snapshots (Chapter 15); `restore_config` restores it on rollback.

### 22.5 Pages and Frontend Modules (FE)

- pages: `/` (front.html) and `/flow` (norpflow.html), both hot-replaceable at
  runtime (`mount_page` / `np.remount(html=...)` / `np.remount(flow_html=...)`),
  HTTP not restarted, port unchanged;
- FE frontend modules: `.html` / `.js` / `.ts` files (`fe_read_file` returns by
  mime); after startup `_scan_fe_modules` rescans the module directory to restore
  the list (survives restarts);
- independent config: `fe_save_config` / `fe_load_config` (when no record exists,
  a copy of the global config is returned as the default source — no interference).

### 22.6 Upload and Security Limits

- upload sizes: `_MAX_JSON` / `_MAX_UPLOAD_JSON` / `_MAX_UPLOAD_FILE` multiple limits;
- upload content: text only (dataURL-decoded); binaries explicitly unsupported;
- remote-model filtering: `filter_remote_models` (`RETIRED_REMOTE_MODELS` retired
  model list);
- sensitive fields: `json_safe` redacts key-like fields during serialization.

---

## Chapter 23 Performance Design and Benchmarks

> Corresponding code: `kernel/events.py` (EventBus), `builtin/ui/web.py`
> (SSE / HTTP), `nasyncio.py` (the scheduling core), `loops/nasyncio.py`
> (LoopRuntime).

### 23.1 EventBus: Copy-on-Write + Lock-Free Iteration

```python
# subscribe / unsubscribe: create a new list inside the lock and replace the reference (never mutate in place)
self._all = self._all + [listener]
# emit / intercept: take one reference inside the lock, then iterate directly lock-free
# emit / intercept: take one reference inside the lock, then iterate directly lock-free
listeners = self._snapshot(event_type)   # no copy, only the reference
for fn in listeners: fn(event)
```

- old snapshots held by readers are never modified by concurrent writers — thread
  safety is guaranteed by "immutable + reference replacement";
- high-frequency events (streaming `on_content` pushed per token) skip the
  per-event list-copy overhead;
- measured >1.6M events/sec (**single-machine single-thread publishing scenario**).

**Benchmark baseline (important)**: the 1.6M/sec figure is measured with a
**single publishing thread + a static subscription table (no concurrent subscribe /
remount)**. **Lock-free iteration ≠ lock-free emit** — `emit` still takes one
subscription-table snapshot reference inside the lock (`_snapshot`) each time, then
iterates lock-free. Under single-thread publishing the lock has no contention, hence
the 1.6M measurement; under dynamic hot mounts (high-frequency subscribe / remount),
while a writer holds the lock copying the list, emits are blocked outside the lock
(µs-level — with n<50 subscribers the copy is microsecond-scale, far below ms; it
is only perceptible when hot-mount frequencies reach hundreds/thousands per second).
Streaming `on_content` emits happen inside the worker thread (self-publish,
self-iterate), so they do not constitute multi-thread contention.

**"Briefly seeing the old table" is COW's linearization semantics, not a bug**:
emits that complete before a subscribe use the old table, guaranteeing
"subscribe-then-publish" causal consistency. To eliminate the lock acquisition in
emit entirely, one could make "emit take no lock at all and read the reference
directly" (CPython attribute reads are naturally atomic; writers only replace the
reference inside the lock and never mutate in place; readers seeing the old or new
table are both legal snapshots) — the current implementation conservatively keeps
the read lock; this is an optimizable item (a dynamic hot-mount mixed benchmark
would be added alongside).

### 23.2 SSE Bounded Backpressure

- per-connection independent buffer: bounded deque + condition variable;
- full → drop per policy (default drop the oldest); **slow clients no longer eat
  unbounded memory**;
- batched flush + disconnect reclamation within ≤1s; the `dropped` counter is
  monitorable (`streams_info`).

### 23.3 HTTP Concurrency Tuning

| Parameter | Value | Effect |
|---|---|---|
| `request_queue_size` | 256 | larger listen backlog; high-concurrency connections do not drop SYNs |
| `allow_reuse_address` | True | reuse the port immediately on restart (avoids TIME_WAIT) |
| `daemon_threads` | True | request threads are daemons; process exit does not hang |
| `block_on_close` | False | shutdown does not wait for connections to close; faster stop |

### 23.4 The nasyncio Scheduling Core

- one EventLoop bound to one thread; `run_forever` belongs to whichever thread calls it;
- cross-thread wakeup: thread-safe queue + socketpair self-pipe (no asyncio
  internal mechanisms);
- cancellation semantics: `Task.cancel()` cross-thread safe, done callbacks write
  the self-pipe, `loop.interrupt()` cancels all in-flight tasks;
- the worker pool: `_DaemonPool` (daemon threads, no join at process exit),
  tunable with the `NORPAGENT_MAX_WORKERS` env var (squeeze to 1 for embedded);
- the worker-pool queue is **unbounded**: `put_nowait` never fails; when the pool
  is full tasks pile up indefinitely — no rejection policy, no task time budget
  (boundary and stuck-task fallback matrix in 4.6.4).

### 23.5 How to Verify

The repository ships a full set of specialized verification scripts
(`test/_verify_*.py` / `test/_smoke_*.py`):

| Script | Coverage |
|---|---|
| `_verify_install.py` / `_verify_wheel.py` | installation and packaging |
| `_verify_js*.py` / `_verify_front.py` / `_verify_css_*` | frontend pages and assets |
| `_e2e_webui.py` / `_e2e_shot.py` | WebUI end-to-end and screenshots |
| `_verify_ppt_*.py` / `_pixel_check*.py` | presentations and pixel checks |
| `_final_check.py` / `_verify_coverage.py` | overall regression and coverage audit |

Performance-benchmark methodology suggestion: a fixed input set + a fixed tool set
(the minimal preset), comparing output quality, step count and token consumption
across models / component implementations (7.3 model benchmarks).

---

## Chapter 24 Rescue Mode: Low-Level Loop Control and Human Takeover

> Code: `rescue.py` (pure-stdlib CLI), `rescue_api.py` (human-takeover
> environment), `nasyncio.py` / `loops/nasyncio.py` (loop core and LoopRuntime).
> Section 15.6 covers **usage** (commands, endpoints, parameters); this chapter
> covers **principles** — the relationship between rescue mode and the minimal
> main async loop: how to control the loop directly when the main program is
> unavailable, and how to operate every tool by hand.

### 24.1 A Three-Layer Failure Model: Loop, Engine, Model

Rescue mode picks its entry points by "which layer failed":

| Failed layer | Symptom | Available entry | Dependency |
|---|---|---|---|
| Model down | loop / engine healthy, model calls fail | `norpagent-rescue tools / tool-call / manual / serve` | framework importable (`rescue_api` lazily loaded) |
| Engine down | the loop may still be alive, AgentRuntime cannot start | `norpagent-rescue rollback` / `np(safemode="on")` | pure stdlib |
| Loop down | scheduling / tasks fully paralyzed | `norpagent-rescue list / show / rollback / mark-good / prune` | pure stdlib |

**Core principle (rescue.py's isolation boundary)**:

1. The snapshot layer (list / show / rollback / mark-good / prune) depends
   **only on the standard library** — it works even when the main program cannot
   be imported at all;
2. The human-takeover layer (tools / tool-call / manual / serve) lazily imports
   the framework (`norpagent.rescue_api`) **inside the command functions** — it is
   meaningful only when the framework can be imported;
3. The `RescueToolEnvironment` assembled by `rescue_api` **does not use the main
   engine's loop** and loads no plugins / hooks / models — it is an independent
   minimal tool environment.

### 24.2 Controlling the Minimal Main Async Loop in Rescue Mode

Controlling the loop in rescue scenarios has three levels: operating the **loop
core** directly (EventLoop), operating through the **LoopRuntime protocol**, and
**bypassing the loop** to drive tools directly.

#### 24.2.1 The Direct Control Surface of the Loop Core (norpagent.nasyncio.EventLoop)

The minimal main async loop is the self-developed `norpagent.nasyncio.EventLoop`
(zero asyncio dependency; thread model: one loop bound to one thread —
`run_forever` owns the loop on whichever thread calls it). In rescue you can
operate it directly from any script:

| API | Calling thread | Purpose |
|---|---|---|
| `run_forever()` | binding thread | start the loop (blocks); repeated calls raise `RuntimeError` |
| `run_until_complete(coro)` | binding thread | run one coroutine then stop; returns its result |
| `stop()` | any | graceful stop: exits after the current round's ready queue drains |
| `abort_main()` | any | **hard stop**: injects `CancelledError` into the main task, interrupting the current await (tool / API stream / user-input wait); the loop exits when the task completes — the stdlib asyncio has no equivalent public entry |
| `call_soon_threadsafe(cb)` | any | cross-thread callback; writes the self-pipe to wake the loop blocked in select |
| `run_coroutine_threadsafe(coro, loop)` | any | submit a coroutine cross-thread; returns a `concurrent.futures.Future` (result / exception / cancellation relayed correctly) |
| `create_task(coro)` / `create_future()` | loop thread | create tasks / result containers |
| `call_later(delay, cb)` | loop thread | timed callback (cancellable); **cross-thread calls are unsafe** (same contract as asyncio) |
| `interrupt()` (LoopRuntime layer) | any | sets the cancel event of every in-flight task (sandbox force-kills child processes / streaming loops exit) |
| `close()` | not running | releases the selector and the self-pipe socketpair |

**Three rescue-critical semantics of the self-developed core**:

1. **Cross-thread cancellation**: `Task.cancel()` auto-detects the calling thread —
   `call_soon` inside the loop thread, `call_soon_threadsafe` + self-pipe wakeup
   outside. Any external thread can cancel any in-flight task directly, no wrapper
   needed;
2. **Self-pipe wakeup**: `call_soon_threadsafe` / cross-thread `set_result` /
   `Event.set` all write the socketpair self-pipe, so a loop blocked in `select()`
   wakes immediately — no "callback queued but the loop is still sleeping" hang;
3. **Select timeout ceiling**: `_run_once` clamps the select wait to 24 hours
   (`_MAX_SELECT_TIMEOUT`, the same value as CPython asyncio). Far-future timers
   (`sleep(1e18)`, distant `call_later`) make `select()` raise `OverflowError` on
   Windows and crash the loop thread — a real defect found by the violent stress
   suite (24.2.6) and fixed: the loop wakes every 24h to re-check the timer heap,
   and `abort_main()` still interrupts at any time.

#### 24.2.2 Protocol-Level Control (NasyncioLoopRuntime)

`loops/nasyncio.py`'s `NasyncioLoopRuntime` is the default `async_loop` slot
implementation; it wraps EventLoop in a thread plus a daemon worker pool. Rescue
scripts can control it through the protocol:

```python
from norpagent.loops.nasyncio import NasyncioLoopRuntime

rt = NasyncioLoopRuntime(config={"max_workers": 2})   # not auto-started
rt.start()                                            # start loop thread + lazy worker pool
rt.submit(lambda: run_a_tool_by_hand(...))            # sync fn -> worker pool, blocks for result
rt.run_async(some_coroutine())                        # coroutine -> loop thread (cross-thread self-pipe wakeup)
rt.interrupt()                                        # cancel all in-flight tasks (Ctrl+C / rescue hard-stop path)
rt.stop()                                             # graceful loop stop
rt.join(timeout)                                      # wait for the loop thread to exit and release resources
```

`submit()`'s cancellation semantics (section 4.6): every task carries its own
cancel event via contextvars; after `interrupt()` sets it, `cancel_requested()`
in the task body returns True — sandboxes force-kill child-process trees, streaming
loops exit early. Calling `run_async` inside the loop thread raises `RuntimeError`
explicitly (a blocking wait would stall the loop; refuse rather than hang).

#### 24.2.3 Scenario A: Model Down, Loop Alive — Drive It Past the Model

When the engine is healthy and only the model is unavailable, rebuild nothing:
submit sync functions or coroutines through the loop held by the engine
(`engine.async_loop`, LoopRuntime protocol), bypassing AgentRuntime's model-call
path:

```python
import norpagent as np

engine = np.current()                      # running engine (loop thread + worker pool alive)
loop = engine.async_loop                   # LoopRuntime protocol instance

# way 1: sync function (worker pool; blocks for the result)
out = loop.submit(
    lambda: engine.registry.resolve_tool("file_read")
                .run({"path": "readme.md"}, make_rescue_context(engine))
)

# way 2: coroutine (loop thread; cross-thread wakeup)
out = loop.run_async(read_file_and_log(engine))
```

`submit`'s polling wait (`poll_interval`) keeps the main thread back at a
bytecode boundary every ≤50ms, so Ctrl+C on Windows surfaces immediately as
`KeyboardInterrupt`, and the task's cancel event is set at the same time.

#### 24.2.4 Scenario B: Engine Also Down — Drive a Bare EventLoop by Hand

When the engine / AgentRuntime cannot start at all, bypass the whole assembly
layer and drive a bare loop by hand:

```python
import threading
import norpagent.nasyncio as nio            # self-developed core: zero deps, no plugins, no hooks

loop = nio.EventLoop()
thread = threading.Thread(target=loop.run_forever, daemon=True)
thread.start()

# submit a coroutine cross-thread and wait for the result
cf = nio.run_coroutine_threadsafe(do_manual_work(), loop)
result = cf.result(timeout=30.0)            # exceptions / cancellation relayed as-is

loop.call_soon_threadsafe(loop.stop)        # graceful stop
thread.join(5.0)
loop.close()
```

Combined with `RescueToolEnvironment` (24.3.3) you get "bare-loop scheduling +
manual tool execution": the loop handles orchestration (timers, retries,
concurrency), while tool execution still goes through `call_tool`'s dedicated
threads and cancel events — the two complement each other without blocking.

#### 24.2.5 Scenario C: Loop Stuck — Hard Stop and Rebuild

When the loop thread is stuck in `select()` or a coroutine awaits too long:

1. **Soft first, hard second**: `loop.call_soon_threadsafe(loop.stop)` (graceful)
   → if that fails, `loop.abort_main()` (inject `CancelledError`, interrupt the
   current await);
2. **Task-level cancel**: `rt.interrupt()` sets the cancel events of in-flight
   tasks — sandboxes force-kill child-process trees, streamed reads exit (4.6.2);
3. **Watchdog pattern**: a health-check coroutine periodically does a `loop.time()`
   heartbeat; on heartbeat timeout (loop unresponsive) do `abort_main()` + close +
   rebuild a fresh loop (the bare-loop template in 24.2.4);
4. **Unreclaimable tasks**: tasks stuck in the worker pool (C-extension blocking /
   non-sandboxed subprocess) have no task time budget (4.6.4 states this honestly)
   — the rescue fallback is daemon threads that die with the process, or
   `RescueToolEnvironment.call_tool(timeout=...)`'s hard timeout that abandons the
   worker thread.

#### 24.2.6 Violent Stress Suite for the Loop Core (test/stress_nasyncio_core.py)

A new 35-item violent stress suite covering "testway.txt selections + event-loop
supplements":

| Source | Items |
|---|---|
| testway.txt selections (B/C/D/E mappings) | cold start & readiness (B01), fast start/stop x200 (D10), lifecycle & resource release (B02/B24), 100/500/1000 concurrency (D02), 5000 batch (D08), 200k cross-thread storm (D05), timeout & inner cancel (B17/C02), exception isolation (C04), empty & extreme numeric input (D15/D16), deadlock rejection (C14), 500k-handle resource exhaustion (C17), 2000-deep recursion (D19), duplicate-callback storm (D27), memory baseline (D11), 60s mixed soak (D09), hard-stop latency (B15), watchdog interrupt (E06) |
| supplements (not in the matrix) | 8-thread wakeup race, 1000-timer precision & shuffled registration order, 100k cancel storm, ready queue does not starve timers (fairness), single-thread binding, cross-thread Future completion, cross-thread Event wakeup, Lock/Condition contention, Task.cancel pierce (BaseException), executor result/exception relay, closed-loop rejection, idle loop does not busy-spin (select blocks), 1000 concurrent sleep timers, subprocess-cancel kills child (zombie protection) |

Run: `python test/stress_nasyncio_core.py` (~2 minutes, including the 60s soak).

**Real defect found and fixed by the suite**: `select()` timeout overflow
(Windows `OverflowError: timestamp out of range`) — fixed by the
`_MAX_SELECT_TIMEOUT` clamp (24.2.1 item 3). All other items are confirming
passes of existing behavior (35 items / 110 assertions / 0 failures).

### 24.3 Operating Tools by Hand (Human Takeover)

#### 24.3.1 Four Entry Points and the "Pass In / Pass Out" Semantics

| Entry | Form | Pass in | Pass out |
|---|---|---|---|
| `norpagent-rescue tools` | CLI | — | full schemas of all 20 tools (name / description / parameters / required / category) |
| `norpagent-rescue tool-call <name> --args '<json>'` | CLI | hand-written JSON args | structured result `{ok, tool, output, error, timed_out, duration_ms}` |
| `norpagent-rescue manual` | interactive | `<tool> <json>` or `{"tool":..., "args":...}` | raw output printed line by line |
| `norpagent-rescue serve` | HTTP API | POST body `{"args":{...},"timeout":N}` | unified JSON response |

A manual call goes through **exactly the same execution path** as a
model-issued call: `tool.run(args, ctx)` with the same `RunContext` (registry /
sandbox / session / scheduler / context_store / project_manager — all present),
writing the same state — files land in the same workspace, `context_add` writes
to the same context store, `task_submit` enters the same task queue. "Pass in"
= a human generates `args` in place of the model; "pass out" = the result is
returned in the model's structured format, so a recovered model can continue
from the same state.

#### 24.3.2 The Loop-Interaction Model of Manual Calls

`RescueToolEnvironment.call_tool()` does **not depend on the main loop** and does
not occupy the loop thread:

```
caller (CLI / HTTP thread / main thread)
  └─ call_tool(name, args, timeout)
       ├─ resolve tool + assemble RunContext (with a per-call cancel-event ContextVar)
       ├─ spawn a dedicated worker thread (contextvars.copy_context isolates the cancel signal)
       ├─ worker: tool.run(args, ctx) -> box the result
       ├─ caller join(timeout): on timeout -> set cancel event + abandon as a daemon orphan
       └─ return the structured result (ok / output / error / timed_out / duration_ms)
```

Key points:

- **Parallel-safe**: every call has its own thread; multiple manual calls can run
  concurrently; components are internally locked;
- **Cancel signal**: `cancel_requested()` is visible in the worker thread —
  sandboxes force-kill child-process trees, streaming loops exit early; after a
  timeout the caller returns immediately without waiting for the task to finish;
- **Relation to 24.2**: if you want manual calls to be orchestrated by a loop
  (timing / concurrency / retry), just put `call_tool` inside `loop.submit(...)`
  (worker pool) or a bare loop's `run_coroutine_threadsafe` — `call_tool` is a
  pure sync function and any loop can schedule it.

#### 24.3.3 Programmatic Embedding: Rescue Environment + Custom Loop Control

```python
from norpagent.rescue_api import RescueToolEnvironment, RescueToolAPI
import norpagent.nasyncio as nio

env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")

# 1) direct manual call (sync; dedicated thread + hard timeout)
r = env.call_tool("exec_cmd", {"command": "git status"}, timeout=30)
print(r["output"])

# 2) mount the HTTP takeover service (127.0.0.1, optional Bearer token)
api = RescueToolAPI(env, port=8799, token="my-secret")
api.start()

# 3) orchestrate manual calls with a bare loop (e.g. poll the task queue)
loop = nio.EventLoop()
threading.Thread(target=loop.run_forever, daemon=True).start()
nio.run_coroutine_threadsafe(poll_and_act(env, loop), loop).result(timeout=60)
loop.call_soon_threadsafe(loop.stop)
```

Applications can also auto-start a `RescueToolAPI` after a failed model health
check (mount it inside the existing process); on-call staff take over through
the operator page, and the recovered model continues from the same state.

#### 24.3.4 Timeouts and Safety Boundaries (Quick Reference)

- **Double-layer timeout**: tool's own timeout (exec_cmd max 300s, run_python's
  `ptc_timeout`) + environment-level hard timeout (default 300s; `--timeout` /
  HTTP body adjustable);
- **Abandoned threads**: after a timeout the worker becomes a daemon orphan
  (the same pattern as the model-call timeout); `_orphan_threads` is pruned per
  call;
- **Zero plugins / zero hooks**: the rescue environment subscribes to no hooks;
  the operator is the final approver;
- **Path safety still applies**: `file_*` absolute-path / `..` traversal
  rejection and `run_python`'s AST pre-check still apply; HTTP binds 127.0.0.1 by
  default, token optional.

### 24.4 Failure Decision Tree

```
Model calls failing?
├─ yes -> engine / loop still alive?
│         ├─ alive -> norpagent-rescue tools / tool-call / manual / serve
│         │           (or programmatically: engine.async_loop.submit(lambda manual tool))
│         └─ dead -> rollback --last-good (pure stdlib)
│                   -> still won't start -> np(safemode="on") minimal kernel
└─ no  -> but tasks stuck / unresponsive?
          ├─ rt.interrupt() / loop.abort_main() hard stop (24.2.5)
          ├─ loop thread also dead -> rebuild a bare EventLoop + RescueToolEnvironment (24.2.4)
          └─ everything down -> norpagent-rescue list (pure-stdlib last resort)
```

### 24.5 Division of Labor with 15.6

| Chapter | Viewpoint | Content |
|---|---|---|
| 15.6 | user | commands / endpoints / parameters / response format / environment defaults (usage quick reference) |
| 24 | principles & internals | three-layer failure model, direct loop control (EventLoop / LoopRuntime), the loop-interaction model of manual calls, bare-loop rebuild, stress suite and the defect-fix record |

They complement each other: start with 15.6 to get going; come back to 24.2 for
low-level control when the loop itself is in trouble.

---

## Appendix D Glossary

| Term | Definition |
|---|---|
| Address Function | the framework's core abstraction: filling a slot value with an "address" (module path / factory / instance) mounts it; not filling uses the default |
| Slot | a replaceable component position; `np(...)`'s keyword-argument names are slot names |
| Hot-pluggable slot table | `register_slot()` registers custom slots at runtime; registration plugs into the full assembly / validation / hot-replacement pipeline |
| Minimal kernel | only four things in the whole framework are non-replaceable: ArchLayer, the address resolver, Registry, EventBus |
| String-address semantics | the four string interpretation modes `address` / `name` / `name_or_address` / `literal` |
| Extra config clause | the `;key=value` pairs after an address, injected into the factory's `config` parameter |
| defer_factory | a slot factory deferred to the engine assembly phase (used by agent_runtime) |
| Hot mount (remount) | replacing a slot implementation while running, no process restart |
| nasyncio | the self-developed async-IO core (no standard-asyncio dependency), the default event-loop implementation |
| LoopRuntime | the event-loop system's protocol interface (start / stop / submit / interrupt ...) |
| Hook | a named event of an execution structure; subscribable / rewritable (mutating) / vetoable (HookVeto) |
| HookLayer | hook grouping (9 standard layers + custom layers + the dynamic layer) |
| HookVeto | the one-vote-veto exception thrown by mutating hooks; the runtime wraps up safely per the execution point's semantics |
| Registry | the name → component mapping center; everything is a registered item |
| EventBus | the inter-component event channel; copy-on-write + lock-free iteration |
| Preset | declarative assembly: the default combination of slots when unfilled (six built-in modes) |
| Protocol | a component's interface contract (ModelProvider / Tool / SessionManager / Sandbox ...) |
| Sandbox | the isolation boundary for tool execution (subprocess / pooled / isolated_python) |
| PTC | Programmatic Tool Composition: the model generates Python code composing multi-step tool calls |
| FTS5 | the SQLite full-text index engine, the context store's underlying storage |
| Snapshot | a serialized archive of system state (architecture slots + runtime params + WebUI settings) |
| Last known-good snapshot | the good version auto-marked after a 30-second post-startup health window; the rescue CLI's one-step restore target |
| Crash rescue (Rescue) | `norpagent-rescue`: the pure-standard-library CLI that can roll back snapshots even when the main program cannot start |
| Safe Mode | `np(safemode="on")`: loads only the minimal kernel, skips all plugins |
| Human Rescue | manual takeover when the model fails: `norpagent-rescue tools / tool-call / manual / serve` pass args by hand to every tool and read raw results |
| SafetyKit | the security-policy suite installed by `norpagent.safe()` (approval / network / plugins / protection APIs) |
| Plugin security pipeline | the full plugin-loading flow: signature → audit → import restrictions → registration |
| FLOW | module-flow orchestration: a visual canvas wired to the real registry (node + beam topological execution) |
| File-as-module | dragging in .py / .json / .yaml registers it as a canvas module (.py goes through the plugin security pipeline) |
| Frontend module (FE) | .html / .js / .ts frontend extensions loadable on the `/flow` page |
| SSE backpressure | per-connection bounded buffer; slow clients drop events, not memory (three hot-changeable policies) |
| Copy-on-write (COW) | immutable subscriber-table snapshots + reference replacement; lock-free emit iteration |

---

## Appendix E 29-Hook Event Payload Quick Reference

> Each hook's `payload_keys` are its event-payload fields; mutating hooks' return
> semantics in 9.3 and `test/docs/hooks.md`.

### L1 Runtime Lifecycle

| Hook | Mutating | payload_keys |
|---|---|---|
| `on_agent_init` | - | `preset` |
| `on_agent_shutdown` | - | `preset` |

### L2 Task Lifecycle

| Hook | Mutating | payload_keys |
|---|---|---|
| `on_task_start` | - | `task_id`, `session_id`, `preset`, `user_input` |
| `on_task_done` | - | `task_id`, `session_id`, `content`, `steps`, `context` |
| `on_task_error` | - | `task_id`, `error` |
| `on_task_stopped` | - | `task_id`, `reason` |
| `on_task_timeout` | - | `task_id`, `timeout`, `kind` |

### L3 Input Pipeline

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_input` | ✅ | `task_id`, `user_input`, `session_id`, `params` |
| `after_input` | - | `task_id`, `user_input`, `session_id` |
| `on_user_input_required` | - | `question`, `default` |

### L4 Session & History

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_session_create` | ✅ | `session_id`, `title`, `params`, `task_id` |
| `after_session_create` | - | `session_id`, `title`, `task_id` |
| `before_message_append` | ✅ | `session_id`, `message`, `task_id` |
| `after_message_append` | - | `session_id`, `message`, `task_id` |

### L5 Message Assembly

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_build_messages` | ✅ | `system_prompt`, `session_id`, `step`, `task_id`, `tool_names` |
| `after_build_messages` | ✅ | `messages`, `system_prompt`, `step`, `task_id` |

### L6 Steps

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_step` | ✅ | `task_id`, `step`, `messages`, `context`, `params` |
| `after_step` | - | `task_id`, `step`, `content`, `tool_calls` |

### L7 Model Calls

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_model_call` | ✅ | `task_id`, `step`, `messages`, `tool_schemas`, `params` |
| `after_model_call` | ✅ | `task_id`, `step`, `output` |
| `on_reasoning` | - | `task_id`, `content`, `stream` |
| `on_content` | - | `task_id`, `content`, `stream`, `final` |
| `on_event` | - | `event_type`, `data`, `task_id` |
| `on_usage_update` | - | `task_id`, `input`, `output`, `total` |

### L8 Tool Calls

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_tool_call` | ✅ | `task_id`, `tool_name`, `args`, `context` |
| `after_tool_call` | ✅ | `task_id`, `tool_name`, `args`, `result`, `success`, `context` |
| `on_tool_error` | - | `task_id`, `tool_name`, `error`, `args` |

### L9 Result Finalization

| Hook | Mutating | payload_keys |
|---|---|---|
| `before_result` | ✅ | `task_id`, `result` |
| `after_result` | ✅ | `task_id`, `result` |

> The plugin-loading pipeline has 8 more hooks (`PLUGIN_PIPELINE_LAYER`:
> `before_plugin_load` / `after_plugin_register` etc.), see 11.4; FLOW
> orchestration additionally has `flow.*` events (20.5).

---

*NorpAgent Developer Manual · v0.9.4 · Copyright (c) 2026 xingluosama121, MIT Licensed*
