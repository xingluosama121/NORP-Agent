# NORP Agent Developer Manual

> **Version**: 0.9.5 | **License**: Copyright (c) 2026 xingluosama121, MIT Licensed
>
> 2026-08 revision: Chapter 27 minimal kernel in depth (EventBus / the slot connector ArchLayer / the Registry / the address resolver: data structures, APIs, internals and a startup + hot-mount collaboration walkthrough) | Chapter 26 registration flow in detail (the Registry's 9 namespaces / four value forms and string semantics / the full npa() assembly pipeline / three registration timings and hot reload / slot registration vs component registration / validation and error handling / a checklist) | Chapter 25 developer practice (module / slot / plugin / tool development in depth; slot development contract incl. the hot-reload red line: dict key-value pairs must be valid modules; architecture overview and the minimal main async-loop core) | Chapter 24 rescue mode (low-level loop control + human takeover) | kernel fix: select timeout clamp (found by the stress suite; far timers crashed the loop on Windows) | new 35-item violent stress suite for the minimal async-loop core (test/stress_nasyncio_core.py) | 15.6 human-rescue manual tool takeover API (v0.9.3; operate all tools by hand when the model is down: tools / tool-call / manual / serve) | 3.9 task-level slot injection (submit(slot_overrides=...)) | 3.7 in-flight task races of assembly-slot hot rebuilds and the drain recommendation | 4.6.4 daemon worker-pool queue semantics and the stuck-task fallback matrix | 23.1 EventBus benchmark baseline and lock-contention boundary

---

## Table of Contents

- [Chapter 1 Quick Start](#chapter-1-quick-start)
- [Chapter 2 Overall Architecture: Layers and Data Flow](#chapter-2-overall-architecture-layers-and-data-flow)
- [Chapter 3 Architecture Layer and Address Functions](#chapter-3-architecture-layer-and-address-functions)
- [Chapter 4 Event Loop System: norpagent.nasyncio()](#chapter-4-event-loop-system-norpagentnasyncio)
- [Chapter 5 Frontend Family](#chapter-5-frontend-family)
- [Chapter 6 npa() Startup and Lifecycle](#chapter-6-npa-startup-and-lifecycle)
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
- [Chapter 25 Developer Practice: Modules, Slots, Plugins and Tools](#chapter-25-developer-practice-modules-slots-plugins-and-tools)
- [Chapter 26 Registration Flow in Detail](#chapter-26-registration-flow-in-detail)
- [Chapter 27 Minimal Kernel in Depth: EventBus, the Slot Connector, the Registry and the Address Resolver](#chapter-27-minimal-kernel-in-depth-eventbus-the-slot-connector-the-registry-and-the-address-resolver)
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
import norpagent as npa

npa()                    # start with the default configuration (standard preset + Web frontend)
running = True
while running:
    if npa.stop() == True:   # lifecycle function: exit when the application ends
        running = False
```

Save it as `hello.py` and run; the console prints:

```
[norpagent] frontend web listening on 127.0.0.1:8787
[norpagent] lazy-loaded modules: ...   # lazy-loaded modules actually used this run
```

Open the address in a browser to see the chat UI. See Chapter 6 for the startup flow. Two key points:

1. **`npa()` is a module-level call** — the `norpagent` module itself is callable, equivalent to `norpagent.launch()`;
2. **`npa.stop()` is a lifecycle function** — it returns `True` when the Agent application has ended and the main loop should exit.

### 1.3 Single-Task Mode

```python
import norpagent as npa

npa(prompt="explain in one sentence what an address function is")
running = True
while running:
    if npa.stop() == True:
        running = False

engine = npa.current()
print(engine.last_result.final_content)
```

With `prompt` given: the Agent executes this single task and stops automatically (`npa.stop()` becomes `True`);
the result is stored in `npa.current().last_result`.

### 1.4 Replacing the Frontend

```python
import norpagent as npa

npa(prompt="hi", frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if npa.stop():
        break
```

HeadlessFrontend reads no keyboard input and renders no UI; it is driven through the programmatic API.
Component replacement is done by filling a new address into a slot, without modifying framework core code.

### 1.5 Chapter Usage Cheat Sheet

| Usage | How |
|---|---|
| Start with the default configuration | `npa()` |
| Check whether the application has ended | `npa.stop()` |
| Single task | `npa(prompt="...")` |
| Specify a preset mode | `npa(preset="standard")` |
| Specify a model | `npa(model="openai_compat")` |
| Specify the event loop | `npa(async_loop="myapp.loop:create")` |
| Specify a frontend | `npa(frontend="myapp.ui:create")` |
| Specify session storage | `npa(session="sqlite")` |
| Specify the security level | `npa(security="high")` |
| Web port / language | `npa(port=9000, language="zh_CN")` |
| Custom main page | `npa(html="/path/to/my.html")` |
| Custom module flow page | `npa(flow_html="/path/to/flow.html")` |
| Frontend mounting an HTML path directly | `npa(frontend="/path/to/my.html")` |
| Instance / value | `npa(async_loop=loop_instance)` | Mount an existing object directly |

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
npa(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
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

Here: `"mock"` in `npa(model="mock")` is a model name in the registry;
the string in `npa(model="myapp.model:create")` is an address.
`npa(session="sqlite")` references the built-in SQLite session component;
`npa(session="myapp.sessions:create")` loads a custom session implementation by address.

**v0.9.1: all slots support address loading + values inside dict values support pure-address resolution**

1. `name` / `name_or_address` slots: the string is looked up in the registry first;
   if not found it is loaded as a module address (`pkg.mod[:attr]`) — ui / preset have
   been upgraded from plain `name` to `name_or_address`: `npa(ui="myapp.render:create")`,
   `npa(preset="myapp.presets:build")` mount the implementation by address directly;
2. `literal` slots are "address first": a string **shaped like a pure address**
   (dotted identifier containing `.` or `:`, structurally judged by
   `norpagent.arch.address.is_address_like`) is loaded by address (resolution failure
   raises `AddressError`, no silent fallback); anything else keeps its literal value —
   `npa(security="high")` is still a level, `npa(storage="./data")` is still a path,
   while `npa(security="myapp.sec:build_kit")` /
   `npa(storage="myapp.store:create;root=./x")` load by address;
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

npa(async_loop=MyLoop)

# 2. factory function: declares config -> injected automatically
def create(config=None, **kw):
    return MyLoop(timeout=float((config or {}).get("timeout", 0)))

npa(async_loop=create)

# 3. string address + extra config clause
npa(async_loop="myapp.loop:create;timeout=5")
```

### 3.5 ArchLayer: Observable Assembly Manifest

Every `npa()` internally builds an ArchLayer and `connect()`s it.
The assembly result is observable:

```python
eng = npa.current()
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
import norpagent as npa

# model = openai_compat, session = sqlite, sandbox = pooled, frontend = Web (port 9000),
# loop = custom implementation, security level = high. All specified via slot parameters.
npa(
    preset="standard",
    model="openai_compat",              # name reference
    session="sqlite",                   # name reference
    sandbox="pooled",                   # name reference
    frontend="norpagent.frontends.web:WebFrontend;port=9000",
    async_loop="myapp.nasync_loop:create",
    security="high",
)

while True:
    if npa.stop():
        break
```

### 3.7 Runtime Hot Mount: Any Slot Can Be Replaced

After `npa()` starts, **the engine keeps running**; replace any slot implementation at
any time, no restart needed:

```python
import norpagent as npa

npa()                                        # start (default Web frontend)
# ... application running ...

npa.remount(model="openai_compat")           # swap the model: takes effect on the next run
npa.remount(tools=["echo", "get_time"])      # swap the tool set: takes effect on the next run
npa.remount(session="sqlite")                # swap session storage: AgentRuntime hot rebuild
npa.remount(security="high")                 # swap the security level: old guard hooks unsubscribed first
npa.remount(frontend="norpagent.frontends.console:ConsoleFrontend")
npa.remount(async_loop="myapp.loop:create")  # swap the event loop: stop old, start new
npa.remount(model="myapp.model:create")      # replace a module file at runtime (hot reload)
```

Underlying chain: `npa.remount()` → `engine.remount()` → `ArchLayer.remount()`.
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
import norpagent as npa

npa(html="front.html")                       # start and mount a custom main page
# ... edit front.html or switch to another page file ...
npa.remount(frontend="norpagent.frontends.web:WebFrontend;html=front.html")
# the port stays the same; refreshing the browser (or reopening http://127.0.0.1:8787/) shows the new page

npa.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=flow.html")
# swap the /flow module-flow orchestration page (the official mounting path for norp-flow.html)
```

Parameter priority: keys **explicitly given** by remount override startup parameters;
keys **not explicitly given** (e.g. port) reuse the startup parameters — so when only
the page changes, the browser URL stays the same. Explicit-key detection: for string
addresses take the keys in the `;key=value` clause; for instances take constructor
parameters whose values differ from defaults (html / flow_html judged by the
`_html` / `_flow_html` attributes). For example:

```python
npa.remount(frontend="norpagent.frontends.web:WebFrontend;port=9000")  # change the port (restart HTTP listening)
npa.remount(frontend="norpagent.frontends.web:WebFrontend;html=")      # reset the main page to the library built-in
npa.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=") # reset /flow to the library built-in
from norpagent.frontends.web import WebFrontend
npa.remount(frontend=WebFrontend(html="front.html"))                   # instance form
npa.remount(frontend=WebFrontend(flow_html="flow.html"))               # instance form
npa.remount(frontend="front.html")     # HTML-path direct mount: equivalent to WebFrontend;html=front.html
```

**remount page hot-replace keys (v0.9, the simpler page-swap entry)**: `html` /
`flow_html` are not slots themselves but mounting parameters of the frontend slot —
`npa.remount()` accepts these two keys directly and swaps the page immediately via
`mount_page` **without going through "stop old frontend / start new frontend"**
(the HTTP service is not restarted, the port stays the same; refresh the browser to
see the new page):

```python
npa.remount(flow_html="flow-v2.html")       # /flow page swapped immediately (HTTP not restarted)
npa.remount(html="front-v2.html")           # / main page swapped immediately
npa.remount(flow_html="<html>...</html>")   # HTML content passed directly (leading "<" = content)
npa.remount(flow_html=None)                 # unmount, fall back to the library built-in norp-flow.html
npa.remount(flow_html="", html="")          # "" has the same semantics as None (unmount)
npa.remount(flow_html="flow-v2.html",
           frontend="norpagent.frontends.web:WebFrontend")  # composable: set parameters first, then swap the frontend
```

Semantic details:

1. the value is first written into `engine.params` (the same data path as the
   `npa(html=...)` startup passthrough); later frontend hot mounts / attach reuse the new value;
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

1. Address form: `npa(frontend="norpagent.frontends.web:WebFrontend;html=...")`
   — module address + clause parameters;
2. HTML-path direct mount: `npa(frontend="front.html")` — when the slot value itself
   is a `.html/.htm` file path (with no `;` clause), the architecture layer no longer
   resolves it as a module address; the assembler automatically converts it to
   `WebFrontend(html=<that path>)`. A nonexistent file raises `ValueError` and fails
   fast (no silent fallback to the default frontend).

Note: HTML-path direct mount only affects the `/` main page; to swap the `/flow`
page use the `;flow_html=...` clause or `WebFrontend(flow_html=...)`.

**Swapping pages directly at runtime (HTTP service not restarted, port unchanged)**:

```python
# Way one: remount page hot-replace keys (recommended, v0.9)
npa.remount(flow_html="flow.html")           # /flow swapped immediately
npa.remount(html="front.html")               # / main page swapped immediately
npa.remount(flow_html=None)                  # unmount, fall back to the library built-in

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

Notes: `npa.remount()` is an **in-process API**; it must be called in the same Python
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

`npa.remount()` swaps a slot's **implementation**; the slot table itself (`SLOT_SPECS`)
is also hot-pluggable — third-party libraries can register **brand-new custom slots**
at runtime, and registration plugs into the full pipeline (`npa()` parameter validation,
ArchLayer assembly, `npa.remount()` hot replacement, `layer.describe()` listing) with no
framework-source changes and no process restart:

```python
from norpagent.arch import SlotSpec, register_slot, unregister_slot

# custom slot = name + string semantics + application logic (applier)
register_slot(SlotSpec(
    name="audit_tag",                # slot name = npa()'s keyword-argument name
    description="audit tag",
    protocol="literal string",
    string_semantics="literal",      # address / name / name_or_address / literal
    applier=_apply_audit_tag,        # called by the assembler when the slot value is non-empty
))
```

```python
import norpagent as npa

npa(audit_tag="release-1")            # applied at assembly time
npa.remount(audit_tag="release-2")    # hot-replaced at runtime (the applier re-runs)
```

The `applier(reg, layer, value, params, ctx)` contract:

- `value` is the resolved slot value: for `address` semantics it is the instantiated
  implementation (the sub-config `;key=value` is obtained via `layer.subconfig(slot)`);
  for `name` / `name_or_address` / `literal` semantics it is the raw value;
- `ctx` provides four mutable containers: `components` (final-preset component
  declarations {kind: name}), `extras` (engine extra objects, consumed via
  `engine.extras[slot_name]`), `overrides` (preset-field overrides), `meta`
  (registry architecture metadata recording mountable/unsubscribable objects);
- **the same registry may be called repeatedly** (assembly + every `npa.remount`), so
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

npa(vector_store=MyVectorStore())    # assembly: engine.agent.components["vector_store"]
npa.remount(vector_store=Other())    # hot replacement: AgentRuntime hot rebuild
```

Protection and validation rules:

| Rule | Note |
|---|---|
| The 18 built-in slots are protected | cannot be registered / spec-overridden / unregistered (framework structural contract: engine, frontend, documentation references). Their **values** can be hot-replaced with `npa.remount` at any time |
| Slot-name legality | a legal Python identifier (`npa()` keyword argument), not a keyword, not `prompt` / `config` (launch special keys) |
| Duplicate names | raise `SlotError`; `register_slot(spec, replace=True)` hot-replaces the spec of a same-named custom slot (default address / semantics / applier / rebuild flag) |
| Illegal specs | a non-callable applier or an illegal `string_semantics` raises `SlotError`; a failed replace does not break the old spec |
| Unregister | `unregister_slot(name)` unregisters a custom slot and returns its spec; afterwards `npa.remount(that_slot)` reports an unknown slot and `npa(that_key=...)` falls back to a task parameter; already-mounted implementations stay as they are |
| Late registration | slots registered after the engine started: `layer.connect()` idempotently fills in (only connects missing slots), or directly `npa.remount(slot=value)` goes through the full pipeline |

Top-level API: `npa.register_slot` / `npa.unregister_slot` /
`npa.SlotSpec` / `npa.SLOT_SPECS` / `npa.is_builtin_slot` /
`npa.snapshot_slots`; slot-table operations are thread-safe (RLock-protected; assembly
and hot mounts iterate over snapshots).

---

### 3.9 Task-Level Slot Injection: submit(slot_overrides=...)

`npa()` startup assembly and `npa.remount()` hot mounts are both **global** dimensions:
one change affects all subsequent tasks. Task-level slot injection is the third
dimension — temporarily overriding any slot implementation for the duration of a
**single task**, without affecting global configuration and without blocking other
in-flight tasks:

```python
import norpagent as npa

engine = npa(preset="standard")

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
(the top-level `npa.submit(...)` supports the same). The keys of `slot_overrides`
match `npa()`'s slot parameters exactly (14 task-overridable keys):

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

Value forms match `npa()` slots exactly: registered-name references / module addresses
(`pkg.mod[:attr]`, including `;key=value` clauses) / factories / instances; resolution
failure raises `AddressError`.

**Non-slot keys automatically fall back to task parameters**: when a key in
`slot_overrides` is not among the 14 keys above (e.g. `max_steps` / `task_timeout` /
`mock_script`), it is automatically merged into `task_params` and passed through to
the agent loop — the same data path as `npa()`'s "slot-key split, the rest pass through
as parameters", so `slot_overrides={"max_steps": 64}` works out of the box.

**Keys that cannot be task-level overridden**: `frontend` / `ui` / `plugins` / `preset`
are process-level or engine-level structures (the I/O shell, the renderer, the plugin
loader, the component-composition baseline) and fall outside a single task's override
boundary; passing them falls back to task parameters (no error, but also no slot-override
effect — use `npa.remount` instead).

#### 3.9.2 Priority: Task-Level > remount > Startup Assembly > Preset

| Level | Source | Priority |
|---|---|---|
| 1 | `submit(slot_overrides=...)` | highest |
| 2 | `npa.remount(slot=...)` | second |
| 3 | startup `npa(slot=...)` | third |
| 4 | preset declarations | lowest |

Task-level overrides take a **snapshot at submit() time**; later global `npa.remount`
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
import norpagent as npa

loop = npa.nasyncio()                       # default loop (self-developed nasyncio core)
loop = npa.nasyncio("myapp.loop:create")    # custom loop
```

It is equivalent to the slot:

```python
npa(async_loop="myapp.loop:create")   # equivalent to npa.nasyncio("myapp.loop:create")
```

`npa.nasyncio()` returns a **LoopRuntime** (protocol below). The scheduling core run
by the default implementation is the library's built-in **self-developed nasyncio
event loop** (`norpagent.nasyncio`, originally nasync_io, now packaged into the
library) — it **does not depend on or import the standard asyncio** (declaration and
reasons in 4.7). To use another event-loop implementation, implement the LoopRuntime
protocol and fill the `async_loop` slot with an address — no framework core changes.

> The top-level `norpagent.nasyncio` (i.e. `npa.nasyncio`) binds to the self-developed
> core **module** (callable): `npa.nasyncio()` returns the default LoopRuntime
> implementation; `npa.nasyncio.EventLoop` / `Future` / `Task` directly access core
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

Passed via `npa(config={"loop": {"max_workers": 8}})` (or the environment variables
`NORPAGENT_MAX_WORKERS` / `NORPAGENT_SUBMIT_POLL`; equivalent forms
`npa.nasyncio(max_workers=8)` and `npa(async_loop="norpagent.loops.nasyncio:NasyncioLoopRuntime")`
share the same construction source).

```python
loop = npa.nasyncio()
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
import norpagent as npa

npa(async_loop="myapp.simple_loop", prompt="hi",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if npa.stop():
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

After `npa()` starts, the main thread only does lifecycle polling (`npa.stop()`); the
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
import norpagent as npa
import norpagent.nasyncio as core  # self-developed core module (callable)

print(core.__version__)          # 2.0.0
loop_rt = npa.nasyncio()          # default LoopRuntime implementation (same as core())
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
| Web (default) | `norpagent.frontends.web:WebFrontend` | HTTP + SSE, no third-party dependencies; page = front.html (multi-tab sessions / streaming render / settings / plugin panels), independent entry `/flow` = norp-flow.html module-flow orchestration; the console prints `listening on http://127.0.0.1:8787/`; configurable via `;port=9000`, `;html=custom main page`, `;flow_html=custom flow page` (slot mounting parameters, see 5.4) or `npa(port=9000, language="zh_CN")`; the frontend slot value can be a `.html` path directly (HTML-path direct mount, v0.9) |
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
import norpagent as npa
from norpagent.builtin.ui.web import WebUI
from norpagent.frontends.web import WebFrontend

# custom config persistence location (default ~/.norpagent/webui_config.json)
ui = WebUI(port=9000, config_path="./my_app/webui.json")
# config_path=None disables disk reads/writes
ui2 = WebUI(port=9000, config_path=None)
```

**Page mounting (html / flow_html params) — four equivalent forms:**

```python
import norpagent as npa

# 1. slot-address clause (;key=value, recommended)
npa(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
npa(frontend="norpagent.frontends.web:WebFrontend;flow_html=/path/to/flow.html")

# 2. constructor params directly (both WebFrontend / WebUI support)
npa(frontend=WebFrontend(html="<html><body>my UI</body></html>"))
npa(frontend=WebFrontend(flow_html="/path/to/flow.html"))

# 3. config dict
npa(config={"web": {"html": "/path/to/my.html", "flow_html": "/path/to/flow.html"}})

# 4. runtime-parameter passthrough
npa(html="/path/to/my.html", flow_html="/path/to/flow.html")

# 5. HTML-path direct mount (v0.9): the frontend slot value itself is a .html path,
#    equivalent to form 1's html= clause
npa(frontend="/path/to/my.html")

# a nonexistent file path errors at construction (fast fail, no silent fallback to the default page)
# ValueError: WebUI html mount parameter is neither HTML content (starts with '<') nor an existing file: ...
```

**Hot-swapping pages at runtime (HTTP service not restarted, port unchanged):**

```python
eng = npa()                                  # or npa.current() to get the running engine

# way one: remount page hot-replace keys (recommended, v0.9)
npa.remount(flow_html="/path/to/flow.html")  # /flow swapped immediately
npa.remount(html="/path/to/front.html")      # / main page swapped immediately
npa.remount(flow_html=None)                  # unmount, fall back to the library built-in

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
npa(html="front.html")   # relative to the working directory; the library reads it as a file path
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
import norpagent as npa

npa(frontend="myapp.tray_frontend:TrayFrontend", preset="standard")
fe = npa.current().frontend
result = fe.send("hello")        # the engine executes the Agent in the background loop
print(result.final_content)
npa.shutdown()
```

### 5.6 The UIAdapter Renderer Layer

```python
class UIAdapter(Protocol):
    ui_id: str
    def on_event(self, event) -> None: ...            # render one AgentEvent
    def ask_user(self, question, default="") -> str: ...  # human approval / clarification Q&A
    def notify(self, message, level="info") -> None: ...
```

Swap renderers: `npa(ui=MyRenderer())` or `npa(ui="web")` (reference a registered name).

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

## Chapter 6 npa() Startup and Lifecycle

### 6.1 Reading the Startup Code

```python
import norpagent as npa
npa()                    # ①
running = True
while running:
    if npa.stop() == True:   # ②
        running = False
```

① `npa()` — the `norpagent` module is callable (module-class replacement). It is
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
5. **singleton semantics**: when an engine is already running, another `npa()` returns
   the current engine directly.

② `npa.stop()` — the lifecycle function. Returns `True` when the engine has entered
STOPPED (the application has ended; the main loop should exit); also returns `True`
when there is no engine.

### 6.2 Engine Lifecycle State Machine

```
STARTING ──start()──▶ RUNNING ──request_stop()──▶ STOPPING ──▶ STOPPED
```

| State | Meaning | Entry condition |
|---|---|---|
| STARTING | assembling | inside `npa()` |
| RUNNING | accepts input, executes tasks | `engine.start()` finished |
| STOPPING | winding down | `request_stop()` |
| STOPPED | finished | wind-down complete (`npa.stop()` is True) |

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
npa()                            # default frontend = Web (frontend web listening on 127.0.0.1:8787)
while True:
    if npa.stop():
        break
```

Open the printed address in a browser to see the chat UI (front.html). With other
frontends, specify explicitly:
`npa(frontend="norpagent.frontends.console:ConsoleFrontend")`.

**Single-task mode** (when `prompt` is given, the headless frontend is used
automatically and output prints to stdout):

```python
npa(prompt="summarize README", preset="standard")
while True:
    if npa.stop():
        break
print(npa.current().last_result.final_content)
```

**Pure-API mode** (headless + programmatic submit):

```python
npa(preset="minimal", frontend="norpagent.frontends.headless:HeadlessFrontend")
eng = npa.current()
result1 = eng.submit("first question")
result2 = eng.submit("follow-up", session_id=result1.session_id)   # continue the same session
eng.request_stop()
```

> **Note**: `npa()` does not block; the engine runs on background threads. The main
> thread should poll with `npa.stop()` (or call `npa.current().wait()`). If the main
> thread simply ends the process, the daemon engine threads exit with it; the library
> registers an atexit fallback cleanup.
>
> **Special case**: with the **console frontend** explicitly selected, calling `npa()`
> inside the Python interactive interpreter (`>>>` REPL) automatically switches to
> **synchronous mode** — `npa()` blocks until the user exits (`/exit`, `exit()`,
> Ctrl+C or EOF); no polling loop needed during that time. In synchronous mode the
> main thread owns stdin exclusively. The default Web frontend also works in the REPL
> (background service + page interaction, without blocking the interpreter).

### 6.4 The Full npa() Parameter Set

```python
npa(
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
npa(config={
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

Lifecycle subscription: `npa(hooks={"on_agent_init": fn, ...})`
(the hook system is in Chapter 9).

---

## Chapter 7 Models and Tools

### 7.1 The Model Slot

The model slot accepts:

```python
npa(model="mock")                  # registry name (built-in mock / openai_compat / anthropic)
npa(model=MyProvider())            # instance
npa(model="myapp.model:create")    # address (resolved as an address when the string matches no registered name)
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
npa(tools=["echo", "get_time"])           # name list: registry references
npa(tools={"my_tool": MyTool()})          # mapping: register and enable
npa(tools=[ToolA(), ToolB()])             # instance list: registered by name
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
import norpagent as npa

for model_name in ("mock", "openai_compat"):
    npa(preset="minimal", model=model_name, prompt="1+1=?",
       frontend="norpagent.frontends.headless:HeadlessFrontend")
    while True:
        if npa.stop():
            break
    r = npa.current().last_result
    print(model_name, r.steps, r.usage.total_tokens, r.final_content[:40])
    npa.shutdown()
```

---

## Chapter 8 Sessions, Sandboxes, Schedulers, Context and Projects

### 8.1 Sessions

```python
npa(session="memory")     # in-process (default)
npa(session="sqlite")     # persisted to ~/.norpagent/sessions.db
npa(session=MySessionManager())          # instance
npa(session="myapp.sessions:create")     # address
```

SessionManager protocol: `create_session / get_session / append_message /
history`. Continue a conversation across sessions via `session_id`:

```python
eng = npa.current()
r1 = eng.submit("remember: my favorite color is blue")
r1 = eng.submit("remember: my favorite color is blue")
r2 = eng.submit("what is my favorite color?", session_id=r1.session_id)
```

### 8.2 Sandboxes

```python
npa(sandbox="subprocess")   # child process (default)
npa(sandbox="pooled")       # pooled reuse + concurrency cap + timeout force-kill of the process tree
npa(sandbox="myapp.docker_sandbox:create")
```

Sandbox protocol: `run / close`. The `exec_cmd` tool executes through the sandbox
protocol; swapping in a container/pooled sandbox implementation requires no tool-code
changes.

### 8.3 Schedulers

```python
npa(scheduler="simple")       # in-memory queue (default)
npa(scheduler="persistent")   # persistent + crash resume() continuation
```

TaskScheduler protocol: `submit / drain / cancel`. The `task_*` tool family lets the
model orchestrate long-running tasks; `agent.run_task()` is the multi-agent
orchestration entry (subtasks can specify a different mode via `preset_name` =
different child agents).

### 8.4 Context Store and Project Management (the generic-component namespace)

```python
npa(context_store="norpagent.builtin.context:FTS5ContextStore")
npa(project_manager=MyProjectManager())
```

These two slots use the **generic-component namespace**
(`registry.register_component`); the component kinds are open — you can register new
kinds and declare them in presets without modifying the kernel.

### 8.5 Base-Service Slots

```python
npa(logger=logging.getLogger("my.app"))       # logging
npa(storage="./my_data")                       # persistence root
npa(error_handler=lambda exc, eng: print(exc))  # last line of defense for errors
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

# 3. slot bulk subscription: npa(hooks={"before_model_call": my_fn})
```

- The module-level `Hook` object's `subscribe / unsubscribe / emit / intercept`
  all need a `system` to locate the bus; you may pass a `HookSystem / EventBus /
  Registry / AgentRuntime` (unified resolution via `_resolve_bus`);
  **by default it lands on the process-level default system**
  (`hooks.get_default_system()`, with its own private bus) — it is NOT the same bus
  as the `npa()` engine's. When using a standalone Registry, **always pass `system`
  explicitly** (every Registry carries a private bus, guaranteeing multi-instance
  isolation); otherwise the subscription hangs on the default system and never
  receives engine events;
- `agent.hooks.before_model_call` returns a `BoundHook` (bound to that engine's
  bus); its four methods no longer need `system`;
- the `npa(hooks={...})` slot (literal semantics): dict keys are event names, values
  are subscribers, mounted on the engine bus at assembly time; hot-mounting
  `npa.remount(hooks=...)` unsubscribes the previous architecture-level subscriptions
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
  jailbreak_guard / harden_prompt, see 10.4), or use the `npa(hooks=...)` slot to
  subscribe on a specific engine only.

---

## Chapter 10 Security System: norpagent.safe()

> In one sentence: `safe()` converges the whole security suite (jailbreak
> protection / prompt hardening / human approval / network policy / source audit /
> import restrictions / signature trust / plugin isolation policy) into one
> standalone function. Companion document `docs/security.md`.

### 10.1 How to Enable

```python
import norpagent as npa

# 1. npa() slot form
npa(security="high")                                  # string: runtime policy only, zero hook intervention
npa(security={"level": "high", "hooks": True})        # dict: + explicit hook intervention
npa(security=lambda reg: safe(reg, config={...}))     # callable: fully custom assembly

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
- a `SecurityContext` instance can be used directly as the `npa(security=ctx)` slot value;
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
  (`npa.remount(security=...)`) uninstalls the old kit before installing the new one,
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
npa(security={"level": "standard", "hooks": True})

# strict: high + whitelist network + trusted keys
kit = safe(level="high", config={
    "plugin_network_policy": "audited_public",
    "plugin_network_domain_allowlist": ["api.example.com"],
    "plugin_trusted_keys": ["<public key hex>"],
})
npa(security=kit.context)                     # install the SecurityContext directly

# decisions only, zero intervention: use only approval and network policy; wire the guard logic yourself
npa(security={"level": "standard",
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

### 11.1 Two APIs and the npa() Slot

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

# npa() slot (literal semantics, directory list)
npa(plugins=["./my_plugins"])
# runtime hot replacement: old subscriptions auto-unsubscribed, never stack (3.7)
npa.remount(plugins=["./my_plugins_v2"])
```

The `npa(plugins=[...])` slot assembles with fixed config (audit=warn, verification
on, **does not read** registry.security's overrides); for fine-grained config use
PluginSystem / install_plugin_dirs directly on a Registry (or a callable slot value
like `npa(plugins=lambda reg: ps.load())`).

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
  `npa(plugins=...)` slot path passes fixed config and does not read
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
- for a full runtime replacement use `npa.remount(plugins=[...])`: the framework
  unsubscribes the old architecture-level plugin subscriptions first, then
  reinstalls (no stacking, 3.7);
- `ps.shutdown()` / `loader.shutdown()` release the process-isolation host
  subprocesses; hot-mounting the plugins slot (`npa.remount(plugins=...)`) makes the
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
npa(preset="standard")
npa(preset="ptc")
npa(preset="embedded")                     # headless by default, pure-API mode
npa(preset=Preset(name="mine", model="mock", tools=["echo"], ...))
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
npa(preset=my)
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

The CLI is equivalent to `npa()`: the CLI's internal flow is
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

**Way two: `npa(preset="embedded")` (out of the box):**

```python
import norpagent as npa

npa(preset="embedded")                   # headless by default: no HTTP service
eng = npa.current()
result = eng.submit("hello")            # pure-API submission
eng.request_stop()
```

Behavioral conventions of the embedded preset:

- **the default frontend automatically falls back to headless** (the assembler's
  default factory judges the preset name); for a Web UI specify explicitly
  `npa(preset="embedded", frontend="norpagent.frontends.web:WebFrontend")`;
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
npa(config={"loop": {"max_workers": 1, "poll_interval": 0.5}})
```

### 14.3 Ultra-High-Concurrency Deployment

**SSE backpressure config (startup params → env vars → runtime hot change):**

```python
import norpagent as npa

# pass at startup
npa(config={"web": {"sse_queue_size": 2048, "sse_queue_policy": "drop_oldest"}})
# or runtime params / environment variables
npa(sse_queue_size=2048, sse_queue_policy="drop_oldest")
# NORPAGENT_SSE_QUEUE_SIZE=2048 NORPAGENT_SSE_QUEUE_POLICY=drop_oldest

# hot change while running (no restart; takes effect on existing connections immediately)
from norpagent.builtin.ui.web import WebUI
ui = npa.current().frontend._ui      # or hold the WebUI instance directly
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
| Undo / Redo | undo / restore the most recent operation, in-process immediate | Web UI buttons / Ctrl+Z / Ctrl+Shift+Z / `npa.undo()` / `npa.redo()` |
| Rollback | browse all historical snapshots, roll back to any version | Web UI "rollback" panel / `npa.rollback(id)` |
| Crash Rescue | roll back snapshots when the main program cannot start; suggest the last known-good snapshot | `norpagent-rescue` (standalone CLI, pure standard library) |
| Safe Mode | load only the minimal kernel (skip all plugins), keep the core rollback capabilities | `npa(safemode="on")` / CLI `--safe-mode` |

Snapshot content (mode A, default): all architecture-layer slot configurations
(mode / model / tools / session / sandbox / frontend / plugin dirs / security
level...) + engine runtime parameters + WebUI settings-file content + custom
provider data. Sensitive keys (api_key / token etc.) are written only after
**redaction**. Non-serializable values (instances / classes / functions) record a
type marker; on replay they are skipped with a hint (honest degradation, never
fabricates state).

Snapshot mode B: `npa(snapshot_sessions="on")` additionally copies session-store
files into the snapshot attachments; rollback restores the whole files (this may
overwrite conversations recorded after the rollback point).

Storage: default `~/.norpagent/snapshots/` (manifest.json timeline + snap/ one
JSON per snapshot + attachments/ session attachments + rollback_target.json the
rescue rollback target). Overridable with the environment variable
`NORPAGENT_SNAPSHOT_DIR` or `npa(snapshot_dir=...)`; while running,
`npa.set_snapshot_dir()` hot-switches the storage directory (explicit programmatic
calls have the highest priority). Auto snapshots are on by default
(`npa(snapshots="off")` disables); auto-prune keeps the most recent 200.

### 15.2 Snapshots and Undo / Redo

Auto-snapshot timing: the startup baseline and after every system-state change
(`npa.remount` / WebUI settings saved / plugin installed / mode switched). Manual
snapshots: the "manual snapshot" button in the Web UI rollback panel or
`npa.snapshot_system("description")`.

```python
import norpagent as npa

npa()                                          # start (baseline snapshot taken automatically)
npa.snapshot_system("before installing plugins")   # manual snapshot
# ...make a few changes (remount / settings saved / install plugins)...
npa.undo()                                     # undo the most recent operation (in-process immediate)
npa.redo()                                     # restore the undo
npa.rollback("20260818T230101_ab12cd")         # roll back to any snapshot
npa.rollback()                                 # roll back to the last known-good snapshot
npa.list_snapshots()                           # timeline (is_current / is_last_good)
npa.mark_good_snapshot("<id>")                 # manually mark "known good"
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

After a rollback, the next `norpagent` / `npa()` startup **automatically consumes**
the rollback target (rollback_target.json, deleted after consumption): file-level
restore (WebUI settings / session files) executes immediately, and the snapshot's
slot config merges into this startup — **parameters explicitly given this time
take priority** (rescue is a fallback; it never overrides the user's conscious
choices). On startup failure both the CLI and npa() print self-rescue guidance
(safe mode + rescue command).

### 15.5 Safe Mode

Entry: `npa(safemode="on")`, CLI `norpagent --safe-mode`. Safe mode is not entered
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
import norpagent as npa
npa(safemode="on")          # minimal kernel + Web rollback panel
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
| safe mode | keep rollback ability even when nothing starts | `npa(safemode="on")` / `--safe-mode` |
| **human rescue** | **model dead — a human does the model's work** | `norpagent-rescue tools / tool-call / manual / serve` |

The three complement each other: first roll back (or enter safe mode) to rescue
the configuration, then push the work forward with manual takeover, and when the
model recovers the agent continues from the same state (files / context store /
task queue).

---

## Chapter 16 Library Integration Examples

### 16.1 FastAPI Integration

```python
import norpagent as npa
from fastapi import FastAPI

npa(preset="standard", frontend="norpagent.frontends.headless:HeadlessFrontend")
app = FastAPI()

@app.post("/chat")
def chat(text: str, session_id: str | None = None):
    result = npa.current().submit(text, session_id=session_id)
    return {"content": result.final_content, "session_id": result.session_id,
            "status": result.status}
```

### 16.2 Desktop-App Integration (pywebview style)

```python
import norpagent as npa

npa(frontend="myapp.tray_frontend:TrayFrontend")
fe = npa.current().frontend

# the JS bridge forwards user input to fe.send();
# subscribe to on_content on the event bus to push streaming output back to the frontend.
```

### 16.3 Integration Points

1. **singleton engine**: the running engine is a singleton; `npa()` idempotently
   returns the current engine;
2. **lifecycle**: the main loop polls `npa.stop()`; process exit has an atexit
   fallback cleanup;
3. **assembly observation**: `npa.current().layer.describe()` prints the assembly
   manifest.

---

## Chapter 17 Testing and Debugging

```bash
python tests/test_p1_smoke.py    # kernel/protocol smoke
python tests/test_p2_smoke.py    # adapters/tools/sessions
python tests/test_p3_smoke.py    # context/scheduler/sandbox/security/plugins/Web
python tests/test_p4_smoke.py    # hooks/security/PTC/isolation
python tests/test_p5_arch.py     # architecture layer/address functions/npa()/nasyncio
```

Debugging aids:

```python
eng = npa.current()
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

# new style: npa() assembly (the manual assembly API stays usable)
import norpagent as npa
npa(preset="minimal", prompt="hello",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if npa.stop():
        break
result = npa.current().last_result
```

The manual assembly API (Registry / AgentRuntime / Preset) **remains usable**;
`npa()` is its declarative wrapper.

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
  added **runtime hot mount** (`npa.remount()` replacing any slot while running, see 3.7);
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
  pipeline — `npa()` parameter validation, ArchLayer assembly (connect
  idempotently fills in late-registered slots), `npa.remount()` hot replacement,
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
automatically at the next startup) + safe mode (`npa(safemode="on")` / CLI
`--safe-mode`, loads only the minimal kernel); auto snapshots are on by default
(after remount / settings saved / plugins installed), sensitive keys are redacted
before persisting, custom snapshot providers and snapshot mode B (including session
data files) are supported;
- breaking changes appear only in major versions.

---

## Chapter 19 FAQ

**Q1: does `npa()` block?**
No. The engine runs on background threads and the main thread keeps executing —
this is exactly why the `while running: if npa.stop()` pattern exists.

**Q2: when does `npa.stop()` become True?**
When the engine is STOPPED: the single task finished, the frontend `/exit`, an
explicit `shutdown()`, or any `request_stop()`. Always True with no engine.

**Q3: how do I pass the model API key?**
```python
npa(model="openai_compat", model_name="deepseek-v4-flash",
   base_url="https://api.deepseek.com/v1", api_key="sk-...")
```
`model_name / base_url / api_key` are model shortcut parameters: when the model is
a built-in adapter name the provider is reconstructed automatically (same as the
CLI); or set the environment variable `OPENAI_API_KEY` directly; or pass a
constructed provider instance `npa(model=MyProvider())`.

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

**Q7: what is the difference between `npa(async_loop=...)` and `npa.nasyncio(...)`?**
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
Yes. `npa.remount(slot=value)` replaces any slot while the engine runs: component
slots (model / tools / hooks / security / plugins) take effect on the next run();
assembly slots (session / sandbox / scheduler / ui / agent_runtime / preset /
context_store / project_manager) trigger an AgentRuntime hot rebuild;
frontend / async_loop stop the old and start the new; logger / storage /
error_handler update immediately. String addresses invalidate the module cache
and .pyc before remounting, so "edit the module file →
npa.remount(model="myapp.model:create")" is hot reload. Repeatedly mounted
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
  `npa(preset="embedded")` (headless frontend by default, mock fallback); tighten
  worker threads with `NORPAGENT_MAX_WORKERS=1` (or `config={"loop":
  {"max_workers": 1}}`), relax polling with `NORPAGENT_SUBMIT_POLL`.
- **ultra-high-concurrency**: SSE per-connection bounded buffer default 1024,
  slow clients drop the oldest (`drop_oldest`); configure at startup with
  `npa(config={"web": {"sse_queue_size": 2048}})`, hot-change while running with
  `WebUI.set_sse_queue(...)` / `POST /api/streams`; batched frame writes (default
  32 frames / 50ms) reduce system calls; EventBus copy-on-write eliminates
  per-event list copies. Full details in Chapter 14.

**Q15: what if the framework lacks the slot I need? (hot-pluggable slot table,
0.9)**
Register your own: `register_slot(SlotSpec(name=..., string_semantics=...,
applier=...))`. Registration plugs into the full pipeline — `npa()` parameter
validation, assembly, `npa.remount()` hot replacement, `layer.describe()`
listing; the applier receives the resolved slot value and four mutable containers
(components / extras / overrides / meta) and can register generic components
(`remount_rebuild_agent=True` hot-rebuilds the AgentRuntime after a hot
replacement), mount event subscriptions (recorded in meta for unsubscribe, so
reentrancy is safe), or provide extra objects to the engine. The 18 built-in
slots are protected (cannot be overridden / unregistered); their values can be
hot-replaced with `npa.remount` at any time. Full contract in 3.8.

**Q16: how do I undo a config change / roll back to a previous state? (work
rollback, 0.9)**
Three steps: in-process `npa.undo()` / `npa.redo()` (Web UI Ctrl+Z / Ctrl+Shift+Z
or the "rollback" panel buttons, immediate); roll back to any version with
`npa.rollback("<snapshot id>")` (`npa.list_snapshots()` browses the timeline;
`npa.rollback()` with no args = the last known-good snapshot); when the main
program cannot start use `norpagent-rescue rollback --last-good` (pure
standard-library CLI; applied automatically at the next startup), or
`norpagent --safe-mode` / `npa(safemode="on")` to load only the minimal kernel and
fix the config. Snapshots default to `~/.norpagent/snapshots/`; sensitive keys
are redacted; auto snapshots are on by default (disable with
`npa(snapshots="off")`). Full semantics in Chapter 15.

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

> Runtime hot mount (3.7): every slot can be replaced with `npa.remount(slot=value)`.
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
npa()                      # launch()
npa.stop()                 # lifecycle polling
npa.nasyncio(address=...)  # event-loop architecture function (npa.nasyncio binds the self-developed core module, callable)
npa.current() / npa.submit() / npa.shutdown()
npa.remount(model=..., ...)   # runtime hot mount: any slot replaceable

# work rollback (Chapter 15)
from norpagent.recovery import (snapshot_system, undo, redo, rollback,
                                list_snapshots, mark_good, last_good_id,
                                register_snapshot_provider, set_snapshot_dir,
                                prune, RecoveryError)
npa.snapshot_system("description")  # manual snapshot (top-level convenience entry)
npa.undo() / npa.redo()             # undo / restore (in-process immediate)
npa.rollback("<id>")               # roll back to any snapshot (default = last known good)
npa.mark_good_snapshot("<id>")     # mark "known good"
npa(safemode="on")                 # safe mode: loads only the minimal kernel
npa(snapshot_dir=..., snapshots="off", snapshot_sessions="on")  # snapshot config
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
#   npa.submit("task", slot_overrides={"session": {"name": "memory", "persist": True}})

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
#   npa.remount(flow_html="/path/to/new-flow.html")  # /flow page swapped immediately
#   npa.remount(html="/path/to/new-front.html")      # / main page swapped immediately
#   npa.remount(flow_html=None)                      # unmount, fall back to the library built-in
# frontend slot HTML-path direct mount (v0.9):
#   npa(frontend="/path/to/my.html")  ==  npa(frontend="...WebFrontend;html=/path/to/my.html")
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
  runtime (`mount_page` / `npa.remount(html=...)` / `npa.remount(flow_html=...)`),
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
| Engine down | the loop may still be alive, AgentRuntime cannot start | `norpagent-rescue rollback` / `npa(safemode="on")` | pure stdlib |
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
import norpagent as npa

engine = npa.current()                      # running engine (loop thread + worker pool alive)
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
│                   -> still won't start -> npa(safemode="on") minimal kernel
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

## Chapter 25 Developer Practice: Modules, Slots, Plugins and Tools

> The first 24 chapters answer "what the framework can do"; this chapter answers
> "how you develop for it": module-by-module development methods (protocol →
> implementation → registration → integration → hot reload), the complete slot
> development contract (including the hot-reload red line: **the values of dict
> key-value pairs must be valid modules**), complete plugin and tool development
> examples, and finally a section back to the architecture and the minimal main
> async-loop core — understand it, and you understand why every extension point
> exists and why hot reload is safe.

### 25.1 Architecture Overview and the Minimal Main Async-Loop Core

#### 25.1.1 The Architecture in One Picture (Quick Overview)

NorpAgent's architecture in one sentence: **everything except the minimal kernel
is a replaceable slot**.

```
Your app: npa() / npa.stop() / npa.nasyncio() / npa.current().submit()
    │
runtime layer runtime/    lifecycle state machine + thread orchestration (NorpEngine)
    │
arch layer arch/          slot table (SLOT_SPECS) + address resolution (address) + assembly (ArchLayer)
    │
loop system loops/        LoopRuntime protocol + default NasyncioLoopRuntime (self-developed nasyncio core)
    │
kernel kernel/            Registry (registry) + EventBus (event bus) + AgentRuntime (agent loop)
    │
protocol layer protocols/ all interface contracts: model / tool / session / sandbox / scheduler / UI / plugin
    │
implementation builtin/   built-in components (absolutely equal in status to third-party components; registered the same way)
```

- **The minimal kernel is only four things**: `ArchLayer` (slot connector),
  `address` (address resolution), `Registry` (registry), `EventBus` (event bus)
  — everything else is a slot (2.3);
- **Dependencies point strictly downward**: upper layers import lower layers;
  lower layers must never depend on upper layers (2.6.1);
- **Four kinds of extension points**: event subscription (Chapter 9) /
  component replacement (Chapter 3) / generic components (2.6.3) / brand-new
  slots (3.8 and 25.10) / external plugins (Chapter 11 and 25.11);
- **Zero-modification red line**: the framework core is never modified; all
  extension goes through slots / hooks / the registry.

#### 25.1.2 The Minimal Main Async-Loop Core: EventLoop Internals

`norpagent.nasyncio.EventLoop` (self-developed, zero asyncio dependency) is the
heart of all scheduling. After one `npa()` startup, the engine's
submit → loop submit → worker pool → result path all revolves around the five
structures below:

| Structure | Role |
|---|---|
| `_ready` (deque) | callbacks waiting to run: `call_soon` / expired timers / Task advancement |
| `_scheduled` (timer heap) | timers: `(when, seq, TimerHandle)`, used by `call_later` / `sleep` |
| `_ts_queue` (thread-safe queue) | cross-thread submissions: `call_soon_threadsafe` |
| self-pipe (socketpair) | wakes the loop thread blocked in `selector.select` from other threads |
| `_selector` | listens only for self-pipe readability |

The flow of each `_run_once()` round (this is also the canonical pattern of a
"minimal main async loop"):

```
1. pop expired timers from the heap -> move them to _ready
2. compute the select wait time (ready non-empty: 0; timers pending: wait until
   the earliest expiry; otherwise: wait forever)
3. drain the thread-safe queue once first (fewer spurious wakeups)
4. selector.select(wait) — wake on self-pipe readability or timer expiry
5. drain the self-pipe + thread-safe queue -> merge everything into _ready
6. run _ready by snapshot length (anti-starvation); a callback exception is
   printed and the loop continues — it never breaks the loop
```

**Future / Task trampoline advancement**: `Task._step()` calls `coro.send(None)`;
when the coroutine `yield`s a Future it suspends and registers
`_on_waiter_done`; when the Future completes, the callback re-queues `_step`
into the ready queue to keep advancing — the loop thread never waits on any
coroutine; it only "runs the queued callbacks one by one".

**Cancel propagation**: `Task.cancel()` automatically detects the calling
thread — from the loop thread it goes through `call_soon`, from any other thread
through `call_soon_threadsafe` (writes the self-pipe to wake up immediately),
so **external threads can cancel any task directly** (standard asyncio's
`Task.cancel()` is not thread-safe; this is a key fix of the self-developed
core, see 4.7); cancellation is injected with `coro.throw(CancelledError)` and
the coroutine decides whether to respond or swallow it.

**Three classic pitfalls that were fixed** (detailed in 4.5):

1. cross-thread `Future.add_done_callback` on an already-completed future must
   go through `call_soon_threadsafe` (write the self-pipe), otherwise the loop
   blocks in the selector without a wakeup and the waiter hangs forever;
2. `Future.result()` is thread-safe (no bare waiting without a wakeup);
3. `EventLoop.abort_main()` provides a thread-safe "immediate stop" — it
   injects `CancelledError` into the main task without waiting for the current
   await to finish naturally (detailed in 24.2).

#### 25.1.3 A Teaching-Grade Minimal Event Loop (~40 Lines)

The best way to understand the core is to write a minimal version yourself.
Below is a runnable "minimal main async loop" (isomorphic to the self-developed
core, for illustration only):

```python
# myapp/mini_loop.py -- teaching-purpose minimal event loop (for illustration;
#                       use the library built-in core in production)
import heapq, socket, selectors, time, threading
from collections import deque


class MiniLoop:
    def __init__(self):
        self._ready = deque()          # callbacks ready to run
        self._timers = []              # timer heap [(when, seq, cb)]
        self._seq = 0
        self._sel = selectors.DefaultSelector()
        self._ssock, self._csock = socket.socketpair()
        self._ssock.setblocking(False)
        self._sel.register(self._ssock, selectors.EVENT_READ)

    def call_soon(self, cb, *args):    # from the loop thread
        self._ready.append((cb, args))
        self._wake()

    def call_later(self, delay, cb, *args):   # timer
        self._seq += 1
        heapq.heappush(self._timers, (time.monotonic() + delay,
                                      self._seq, cb, args))

    def call_soon_threadsafe(self, cb, *args):  # cross-thread: write the self-pipe to wake up
        self._ready.append((cb, args))
        self._wake()

    def _wake(self):                   # wake the loop thread blocked in select
        try:
            self._csock.send(b"\0")
        except OSError:
            pass

    def run_forever(self):
        while True:
            # 1. expired timers -> ready
            now = time.monotonic()
            while self._timers and self._timers[0][0] <= now:
                _, _, cb, args = heapq.heappop(self._timers)
                self._ready.append((cb, args))
                now = time.monotonic()
            # 2. select wait duration
            wait = 0.0 if self._ready else (
                max(0.0, self._timers[0][0] - now) if self._timers else None)
            # 3. block until readable (self-pipe or timer expiry)
            try:
                events = self._sel.select(wait)
            except (InterruptedError, OSError):
                events = []
            for _key, _mask in events:
                self._ssock.recv(4096)          # drain the wake-up bytes
            # 4. run ready callbacks by snapshot (anti-starvation)
            n = len(self._ready)
            for _ in range(n):
                cb, args = self._ready.popleft()
                try:
                    cb(*args)
                except Exception:
                    import traceback
                    traceback.print_exc()       # a failing callback must not break the loop


if __name__ == "__main__":
    loop = MiniLoop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    loop.call_later(0.5, lambda: print("timer fired"))
    loop.call_soon_threadsafe(lambda: print("hello from main thread"))
    time.sleep(1)
    loop.call_soon(loop._ssock.close)
```

The real core adds on top of this: Future / Task (trampoline), cancel
injection, `run_until_complete` / `abort_main`, subprocess wrapping, and
synchronization primitives (Event / Lock / Condition). Master the 40 lines
above and you master the whole skeleton of the "minimal main async-loop core" —
none of the module development in 25.2 ~ 25.11 will require touching it.

#### 25.1.4 The Universal Five Steps of Module Development

No matter which kind of module you develop (tool / model / session / sandbox /
scheduler / frontend / loop / generic component), the flow is exactly the same
(an expanded version of 2.6.4):

1. **Read the protocol**: the interface contracts under `norpagent/protocols/`
   (model / tool / session / sandbox / scheduler / UI / plugin), and confirm the
   protocol and data classes to implement;
2. **Write the implementation**: create a new module that depends only on
   protocols and the standard library (follow the style under `builtin/`;
   built-in components and third-party components are absolutely equal);
3. **Register**: `reg.register_*(...)` (Registry API table in 25.2.5) or
   `registry.register_component(kind, name, factory)`;
4. **Declare it for use**: declare it in a preset (`session="my_impl"`), or at
   startup `npa(session="my_impl")` / an address string
   `npa(session="myapp.sessions:create")`;
5. **Wire hooks** (optional): publish / subscribe events through the registry
   inside the implementation (Chapter 9).

Hot reload is the natural extension of step 4: `npa.remount(session=
"myapp.sessions:create")` re-resolves the address at runtime and **first
invalidates the module cache and .pyc files** (3.7), so "edit the implementation
code → remount" hot-updates it without restarting the process.

---

### 25.2 Tool Development in Detail (Key Section)

Tools are the Agent's "skills": the model decides **whether** to call them, you
decide **how** they execute. Developing tools is the most frequent and most
rewarding way to extend NorpAgent.

#### 25.2.1 Protocol and Data Classes

```python
# norpagent/protocols/tool.py
class Tool(Protocol):
    name: str                                        # unique tool name (what the model calls)
    def schema(self) -> dict: ...                    # OpenAI function schema
    def run(self, args: dict, ctx: RunContext) -> ToolResult: ...

@dataclass
class ToolResult:
    output: str = ""                                 # text fed back to the model
    success: bool = True
    error: str = ""
```

Key points:

- `schema()` returns the OpenAI function format
  (`type/function/name/description/parameters`) — this is the world the model
  sees, so **how well you write the description directly determines whether the
  model calls correctly**;
- `args` of `run()` is the JSON argument generated by the model according to
  the schema (already parsed into a dict);
- return a `ToolResult`: on success fill `output`; on failure set
  `success=False` and fill `error` (the model sees a `[tool execution failed]`
  prefix);
- you may raise an exception — the kernel catches it and converts it into a
  unified failed ToolResult (`tool_error`) — but **explicitly returning a failed
  result is more controllable**.

#### 25.2.2 Complete Example: A "Weather Query" Tool from Scratch

```python
# myapp/weather_tool.py -- your own tool module
from __future__ import annotations

import json
from typing import Any, Dict
from urllib.request import urlopen

from norpagent.protocols.tool import Tool, ToolResult


class WeatherTool:
    name = "weather"

    def __init__(self, api_key: str = "", base_url: str = "https://wttr.in"):
        self._api_key = api_key          # constructor param: injectable via the address clause ;api_key=...
        self._base_url = base_url

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Query the current weather of a city. The city may be given in Chinese or pinyin.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "city name, e.g. Beijing / Shanghai"},
                    },
                    "required": ["city"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        city = str(args.get("city", "")).strip()
        if not city:
            return ToolResult(output="missing city parameter", success=False, error="city is required")
        try:
            with urlopen(f"{self._base_url}/{city}?format=j1", timeout=10) as resp:
                data = json.load(resp)
            cur = data["current_condition"][0]
            return ToolResult(output=(
                f"{city} now {cur['temp_C']} C, "
                f"feels like {cur['FeelsLikeC']} C, {cur['weatherDesc'][0]['value']}"
            ))
        except Exception as exc:
            return ToolResult(output=f"query failed: {exc}", success=False, error=str(exc))


def create(**kw):                        # module-level factory: address "myapp.weather_tool" auto-resolves to it
    return WeatherTool(**kw)
```

#### 25.2.3 RunContext: What a Tool Can Access

`ctx` of `run(args, ctx)` is a `RunContext` (`norpagent.kernel.context`) — the
entire environment of one task execution:

| Field | Description |
|---|---|
| `ctx.registry` | component registry (resolve other tools / models: `reg.resolve_tool(name)`) |
| `ctx.session_manager` / `ctx.session_id` | session read/write (cross-turn memory) |
| `ctx.sandbox` | the current task's sandbox (`run_shell` / `run_python`, isolated execution) |
| `ctx.scheduler` | task scheduler (submit subtasks; the multi-agent collaboration entry) |
| `ctx.ui` | UI adapter (`ctx.ask_user(...)` human interaction / approval) |
| `ctx.params` | merged result of preset params and task-level params (`max_steps` / `task_timeout` / custom keys) |
| `ctx.components` | generic component instances declared in the preset (`{kind: instance}`) |
| `ctx.component("context_store")` | get a generic component by kind (context store / project manager, etc.) |
| `ctx.task_id` / `ctx.preset_name` | task metadata |

```python
# idiomatic way to use a component inside a tool
store = ctx.component("context_store")     # None if absent; fall back yourself
if store is not None:
    store.add(ctx.session_id, chunk, meta={"tool": self.name})
```

#### 25.2.4 Cancellation Cooperation (Mandatory for Long Tasks)

A task may be cancelled by Ctrl+C / `engine.request_stop()` / timeout. The
built-in cancellation signal is injected through contextvars and can be checked
anywhere inside a tool (4.6.2):

```python
from norpagent.loops.cancel import cancel_requested

def run(self, args, ctx):
    for chunk in self._fetch_stream(args["url"]):
        if cancel_requested():            # engine stopped / cancelled -> True
            return ToolResult(output="task cancelled", success=False)
        self._write(chunk)
    return ToolResult(output="done")
```

A long tool that never checks cancellation will occupy the daemon worker pool
(boundary in 4.6.4) — always check on **streaming / loop / chunked** paths.

#### 25.2.5 The Four Ways to Register and Integrate

| Way | Form | Scenario |
|---|---|---|
| Registry entry | `reg.register_tool("weather", WeatherTool())` | programmatic assembly; other components can reference it by name |
| Instance list | `npa(tools=[WeatherTool(), MyTool()])` | ready at startup |
| Name mapping | `npa(tools={"weather": WeatherTool(api_key="x")})` | ready at startup (key = tool name) |
| Address mapping | `npa(tools={"weather": "myapp.weather_tool:create"})` | ready at startup + hot-reloadable |

`npa(tools=["weather"])` references a **registered name**; the value of an
address mapping `"myapp.weather_tool:create"` is a **module address**, resolved
into a factory at assembly time and called per the factory convention (3.4: the
`;api_key=xxx` clause injects the factory's `config`):

```python
npa(tools={"weather": "myapp.weather_tool:create;api_key=MY_KEY"})
# equivalent to WeatherTool(api_key="MY_KEY")
```

The complete set of Registry registration APIs (`norpagent.kernel.registry`):

| API | Description |
|---|---|
| `register_tool(name, tool)` / `resolve_tool(name)` / `list_tools()` | tools |
| `register_model(name, provider)` / `resolve_model(name)` | models |
| `register_session(name, factory)` / `build_session(name)` | sessions (factory) |
| `register_sandbox(name, factory)` / `build_sandbox(name)` | sandboxes (factory) |
| `register_scheduler(name, factory)` / `build_scheduler(name)` | schedulers (factory) |
| `register_ui(name, adapter)` / `resolve_ui(name)` | UI renderers |
| `register_component(kind, name, factory)` / `build_component(kind, name)` | generic components (any kind) |
| `register_preset(preset)` / `resolve_preset(name)` | presets |

#### 25.2.6 Hot-Reloading Tools: Dict Key-Value Values Must Be Valid Modules (Red Line)

The tool set is a **component slot**; `npa.remount(tools=...)` takes effect at
the next `run()` (the agent loop re-resolves tool schemas on every run). All
three forms are hot-reloadable:

```python
npa.remount(tools=["echo", "weather"])                    # registered-name list
npa.remount(tools={"weather": WeatherTool(api_key="new key")})  # instance mapping
npa.remount(tools={"weather": "myapp.weather_tool:create"})   # address mapping (recommended)
```

**Red line: at hot reload the value of a dict key-value pair must be a valid
module.** For dict-form values of the tools mapping, and of any slot (hooks
mapping, custom-slot dict values, nested dicts recursively), if a string
**looks like a pure address** (dotted identifier containing `.` or `:`,
detected by `norpagent.arch.address.is_address_like`), the assembler resolves
it as an address:

- value = **registered name** (e.g. `"echo"`) → kept verbatim as a name
  reference;
- value = **valid module address** (e.g. `"myapp.weather_tool:create"`) → load
  the module, take the attribute, call it per the factory convention;
- value = **valid instance / factory object** → used as-is;
- value = **address-like but unresolvable** (module missing / attribute missing
  / syntax error) → raises `AddressError` (`AddressError(ImportError)`);
  **the hot reload fails; no silent fallback, no partial effect**.

```python
# wrong: module name misspelled / attribute missing -> AddressError, remount raises
npa.remount(tools={"weather": "myapp.weather_toll:create"})   # typo
npa.remount(tools={"weather": "myapp.weather_tool:WeatherTool"})  # class not instantiated? -> callable is called as a factory (legal)
npa.remount(tools={"weather": "myapp.not_exist:create"})      # module does not exist

# correct: choose one of three
npa.remount(tools={"weather": "myapp.weather_tool:create"})   # address (module importable)
npa.remount(tools={"weather": "weather"})                     # registered name (registered via register_tool)
npa.remount(tools={"weather": WeatherTool()})                 # instance
```

Why "if you wrote an address it should raise": hot reload is an ops action; a
silently falling-back address would quietly leave the old / an empty
implementation running online, which is much harder to debug than an explicit
failure. Therefore the assembler strictly resolves every "address-like string"
(item 3 of 3.3; the original comment in `layer.py` `_resolve_dict_values`:
"if you wrote an address it must raise explicitly, never fall back
silently"). **Strings that are not address-like (e.g. `"high"`, `"./dir"`)
are unaffected and keep their literal semantics**.

Another hot-reload detail: **address hot reload invalidates the module cache
first**. `remount` runs `_invalidate_address_module` on string addresses —
deletes the .pyc corresponding to `__cached__` and pops the `sys.modules`
entry, so the next resolution re-imports from disk (3.7). Therefore "edit
`myapp/weather_tool.py` → `npa.remount(tools={"weather":
"myapp.weather_tool:create"})`" hot-updates the code. Note: **instance /
registered-name forms do no module invalidation** (there is no address to
invalidate); use the address form after editing code.

Debugging tip: on a failed hot reload check the assembly manifest of
`eng.layer.describe()` (3.5) and `reg.list_tools()` to confirm the name was
really registered.

---

### 25.3 Model Development in Detail

The model is the Agent's "brain". Integrating any model (local / cloud /
private protocol) only requires implementing `ModelProvider`
(`norpagent.protocols.model`).

#### 25.3.1 Protocol

```python
class ModelProvider(Protocol):
    model_id: str
    def generate(self, messages, tools, params) -> ModelOutput: ...
    def stream(self, messages, tools, params) -> Iterator[ModelStreamChunk]: ...  # optional
```

- `messages`: `List[ChatMessage]` (role: system / user / assistant / tool;
  tool turns carry `tool_calls` / `tool_call_id`);
- `tools`: a list of OpenAI function schemas (None when no tools);
- `params`: runtime parameter dict (temperature / max_tokens / top_p / custom
  keys), freely usable by the implementation;
- `ModelOutput`: `content` / `reasoning` (chain of thought) / `tool_calls` /
  `usage` (`ModelUsage`) / `finish_reason`;
- `ModelStreamChunk`: streaming deltas `delta_content` / `reasoning` /
  `tool_call_delta` / `usage` / `finish_reason`.

**If `stream` is implemented, the kernel prefers the streaming path** (broadcasting
`on_content` chunk by chunk); otherwise it falls back to one-shot `generate`.
Implementing both is recommended.

#### 25.3.2 Complete Example: An HTTP JSON Model Adapter

```python
# myapp/models/http_json.py -- any HTTP JSON protocol model
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional
from urllib.request import Request, urlopen

from norpagent.protocols.model import (
    ChatMessage, ModelOutput, ModelProvider, ModelStreamChunk,
    ModelUsage, ToolCallSpec,
)


class HttpJsonModel:
    model_id = "http-json"

    def __init__(self, endpoint: str, api_key: str = "", **kw):
        self._endpoint = endpoint
        self._api_key = api_key

    def _payload(self, messages, tools, params):
        body = {
            "messages": [m.to_openai() for m in messages],
            "temperature": params.get("temperature", 0.7),
        }
        if tools:
            body["tools"] = tools
        return body

    def _post(self, body, timeout=60.0):
        req = Request(self._endpoint, data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
        if self._api_key:
            req.add_header("Authorization", f"Bearer {self._api_key}")
        with urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    def generate(self, messages, tools, params) -> ModelOutput:
        data = self._post(self._payload(messages, tools, params))
        msg = data["choices"][0]["message"]
        tool_calls = None
        if msg.get("tool_calls"):
            tool_calls = [
                ToolCallSpec(id=tc["id"], name=tc["function"]["name"],
                             arguments=json.loads(tc["function"]["arguments"] or "{}"))
                for tc in msg["tool_calls"]
            ]
        usage = data.get("usage")
        return ModelOutput(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            usage=ModelUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ) if usage else None,
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
        )

    def stream(self, messages, tools, params) -> Iterator[ModelStreamChunk]:
        # optional streaming implementation; check the cancel event per chunk (below)
        from norpagent.loops.cancel import cancel_requested
        body = self._payload(messages, tools, params)
        body["stream"] = True
        with urlopen(Request(self._endpoint,
                             data=json.dumps(body).encode("utf-8"),
                             headers={"Content-Type": "application/json"}),
                     timeout=120.0) as resp:
            for line in resp:
                if cancel_requested():           # engine stop / Ctrl+C: exit as early as possible
                    return
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:])
                delta = chunk["choices"][0].get("delta", {})
                yield ModelStreamChunk(delta_content=delta.get("content") or "")
```

#### 25.3.3 Registration, Credential Fallback and Hot Reload

```python
# programmatic registration
reg.register_model("my_http", HttpJsonModel(endpoint="http://127.0.0.1:8000/v1"))

# npa() integration: name / address / instance, choose one
npa(model="my_http")
npa(model="myapp.models.http_json:create;endpoint=http://127.0.0.1:8000/v1")
npa(model=HttpJsonModel(endpoint="..."))

# hot reload: swap model / config / code (the address form invalidates the module cache)
npa.remount(model="myapp.models.http_json:create;endpoint=http://127.0.0.1:9000/v1")
```

Notes:

- **Credential fallback**: when the assembly layer finds no key at all
  (`OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` /
  `DASHSCOPE_API_KEY` / `NORPAGENT_API_KEY`) it automatically falls back to
  `mock` (21.1) — custom models should likewise give a readable error instead
  of a bare raise when credentials are missing;
- **Cancellation**: `params["_cancel_event"]` is the cancel event injected by
  the kernel (also injected when `call_timeout=0`); check it per chunk in
  streaming loops (4.6.2);
- **DeepSeek V4 special case**: in tool turns, the assistant message's
  `reasoning_content` must be echoed back verbatim (even an empty string);
  `ChatMessage.to_openai()` already handles it (21.1);
- The model slot is a **component slot** (`name_or_address` semantics); a hot
  reload takes effect at the next run().

---

### 25.4 Session Development in Detail

Sessions are the Agent's "memory": persistence and retrieval of conversation
history. Implementing `SessionManager` (`norpagent.protocols.session`) lets you
plug in any backend (file / database / cloud sync).

#### 25.4.1 Protocol and Complete Example

```python
class SessionManager(Protocol):
    def create_session(self, title: str = "") -> Session: ...
    def get_session(self, session_id: str) -> Optional[Session]: ...
    def append_message(self, session_id: str, message: ChatMessage) -> bool: ...
    def history(self, session_id: str) -> List[ChatMessage]: ...
    def list_sessions(self) -> List[Session]: ...
    def delete_session(self, session_id: str) -> bool: ...
```

```python
# myapp/sessions/jsonfile.py -- JSON file session storage
import json, os, threading, time
from norpagent.protocols.session import Session, SessionManager
from norpagent.protocols.model import ChatMessage


class JsonFileSessions:
    """One .json file per session. All methods are thread-safe (lock-protected)."""

    def __init__(self, root: str = "./sessions", **kw):
        self._root = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)

    def _path(self, sid):
        return os.path.join(self._root, f"{sid}.json")

    def create_session(self, title=""):
        s = Session(id=f"s{int(time.time() * 1000)}", title=title,
                    created_at=time.time())
        with self._lock:
            with open(self._path(s.id), "w", encoding="utf-8") as f:
                json.dump({"title": title, "messages": []}, f, ensure_ascii=False)
        return s

    def get_session(self, session_id):
        with self._lock:
            p = self._path(session_id)
            if not os.path.exists(p):
                return None
            data = json.load(open(p, encoding="utf-8"))
            return Session(id=session_id, title=data.get("title", ""),
                           created_at=os.path.getmtime(p),
                           messages=[ChatMessage(**m) for m in data["messages"]])

    def append_message(self, session_id, message):
        with self._lock:
            p = self._path(session_id)
            if not os.path.exists(p):
                return False
            data = json.load(open(p, encoding="utf-8"))
            data["messages"].append({
                "role": message.role, "content": message.content,
                "tool_call_id": message.tool_call_id,
            })
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return True

    def history(self, session_id):
        s = self.get_session(session_id)
        return list(s.messages) if s else []

    def list_sessions(self):
        with self._lock:
            return [self.get_session(fn[:-5]) for fn in os.listdir(self._root)
                    if fn.endswith(".json")]

    def delete_session(self, session_id):
        with self._lock:
            p = self._path(session_id)
            if os.path.exists(p):
                os.remove(p)
                return True
            return False


def create(config=None, **kw):       # module-level factory
    root = (config or {}).get("root", "./sessions")
    return JsonFileSessions(root=root)
```

#### 25.4.2 Registration and Hot-Reload Semantics

```python
reg.register_session("jsonfile", lambda: JsonFileSessions(root="./sessions"))
npa(session="jsonfile")                                   # name
npa(session="myapp.sessions.jsonfile:create;root=./data")  # address
npa.remount(session="myapp.sessions.jsonfile:create;root=./data")  # hot reload

# continue a conversation across sessions via session_id
r1 = eng.submit("Remember: my favorite color is blue")
r2 = eng.submit("What is my favorite color?", session_id=r1.session_id)
```

**Hot-reload semantics differ from tools**: sessions are **assembly slots**;
`remount` goes through "AgentRuntime hot rebuild" — stop the old runtime →
build a new runtime per the current assembly → rebind the frontend renderer
(grouping table in 3.7). In-flight tasks during the rebuild race with the
rebuild; in production drain first, then swap (the "two-phase hot mount"
recommendation in 3.7). Note: swapping the session implementation does not
auto-migrate history — the old and new implementations each manage their own
storage; continuing across implementations requires migrating the data yourself.

---

### 25.5 Sandbox Development in Detail

A sandbox is the Agent's "isolated execution environment": `exec_cmd` /
`run_python` and other tools execute through the sandbox protocol; swapping the
sandbox implementation (container / remote / VM) **requires no changes to any
tool code**.

#### 25.5.1 Protocol

```python
class Sandbox(Protocol):                       # one created sandbox instance
    def run_shell(self, command, timeout=60.0, cwd=None, env=None) -> SandboxResult: ...
    def close(self) -> None: ...

class PythonSandbox(Protocol):                 # optional capability: isolated Python execution
    def run_python(self, code, tool_dispatch, timeout=60.0) -> SandboxResult: ...

class SandboxProvider(Protocol):               # provider: creates sandbox instances on demand
    kind: str
    def create(self) -> Sandbox: ...

class SandboxResult:                           # execution result
    stdout: str; stderr: str; exit_code: int; timed_out: bool
    # ok = exit_code == 0 and not timed_out
```

#### 25.5.2 Complete Example: A Docker Sandbox

```python
# myapp/sandboxes/docker_sb.py -- Docker container sandbox (illustrative)
import subprocess
from norpagent.protocols.sandbox import Sandbox, SandboxProvider, SandboxResult


class DockerSandbox:
    def __init__(self, image: str = "python:3.11-slim", **kw):
        self._image = image

    def run_shell(self, command, timeout=60.0, cwd=None, env=None):
        try:
            proc = subprocess.run(
                ["docker", "run", "--rm", "-i", self._image, "sh", "-c", command],
                capture_output=True, text=True, timeout=timeout,
            )
            return SandboxResult(stdout=proc.stdout, stderr=proc.stderr,
                                 exit_code=proc.returncode)
        except subprocess.TimeoutExpired:
            return SandboxResult(stderr="timeout", exit_code=-1, timed_out=True)
        except FileNotFoundError:
            return SandboxResult(stderr="docker not found", exit_code=-1)

    def close(self):
        pass                                        # docker run --rm cleans up automatically


class DockerSandboxProvider:
    kind = "docker"

    def __init__(self, image="python:3.11-slim", **kw):
        self._image = image

    def create(self):
        return DockerSandbox(image=self._image)


def create(config=None, **kw):
    return DockerSandboxProvider(image=(config or {}).get("image", "python:3.11-slim"))
```

```python
reg.register_sandbox("docker", lambda: DockerSandboxProvider(image="python:3.11"))
npa(sandbox="docker")
npa(sandbox="myapp.sandboxes.docker_sb:create;image=python:3.12-slim")
npa.remount(sandbox="myapp.sandboxes.docker_sb:create;image=python:3.12-slim")
```

Development key points:

- **Timeout and cancellation**: `run_shell`'s `timeout` must be honored
  (`subprocess.run`'s timeout suffices); when the engine stops, the cancel
  event is set (4.6.2); long tasks should check in slices (the built-in pooled
  sandbox checks every ≤0.5s and force-kills the process tree);
- **Process-tree cleanup**: the child process tree of `sh -c` must be killed
  together on timeout (on Windows use `taskkill /T`, see 21.4);
- **`close` must be idempotent**: a sandbox may be closed from multiple places
  — `shutdown` / hot rebuild / task end.

---

### 25.6 Scheduler Development in Detail

A scheduler is the Agent's "orchestration": task queueing, execution order,
concurrency policy. Tools such as `task_submit` / `task_list` and multi-agent
orchestration are all built on top of it.

#### 25.6.1 Protocol and Complete Example

```python
class TaskScheduler(Protocol):
    def submit(self, task: AgentTask) -> str: ...           # enqueue; returns the task id
    def pending(self) -> int: ...                           # number of pending tasks
    def drain(self, run_task) -> List[TaskResult]: ...      # execute all pending tasks in order
```

```python
# myapp/schedulers/priority.py -- priority scheduler (smaller number runs first)
import heapq
from norpagent.protocols.scheduler import AgentTask, TaskScheduler


class PriorityScheduler:
    def __init__(self, **kw):
        self._heap = []                       # [(priority, seq, task)]

    def submit(self, task):
        priority = int(task.params.get("priority", 0))   # priority comes from the task params
        heapq.heappush(self._heap, (priority, id(task), task))
        return task.id

    def pending(self):
        return len(self._heap)

    def drain(self, run_task):
        results = []
        while self._heap:
            _p, _seq, task = heapq.heappop(self._heap)
            results.append(run_task(task))    # run_task is injected by the runtime
        return results


def create(**kw):
    return PriorityScheduler()
```

```python
reg.register_scheduler("priority", lambda: PriorityScheduler())
npa(scheduler="priority")
npa.remount(scheduler="myapp.schedulers.priority:create")
```

Key point: `drain`'s `run_task` callback is injected by the runtime (decoupling
the agent loop from the scheduler; with multi-agent it can point to a different
agent). The built-in `persistent` implementation (21.5) resumes after a crash
via `resume()`; a custom scheduler can follow it.

---

### 25.7 Frontend and Renderer Development in Detail

The frontend is a two-layer structure (5.1): **frontend** (input/output shell,
Frontend protocol) + **ui** (event renderer, UIAdapter protocol).

#### 25.7.1 Protocol

```python
class Frontend(Protocol):              # user interaction shell
    frontend_id: str
    def attach(self, engine) -> None: ...   # bind the engine: engine.submit / request_stop
    def start(self) -> None: ...            # start (usually spawns a background thread)
    def stop(self) -> None: ...             # stop (thread-safe)
    def is_alive(self) -> bool: ...

class UIAdapter(Protocol):             # event renderer
    ui_id: str
    def on_event(self, event) -> None: ...  # render one AgentEvent
    def ask_user(self, question, default="") -> str: ...
    def notify(self, message, level="info") -> None: ...
```

#### 25.7.2 Complete Example: A Toast-Notification Frontend (Simplified)

```python
# myapp/frontends/toast.py -- no input, notifications only (good for desktop assistants)
import threading
from norpagent.frontends.base import Frontend


class ToastFrontend:
    frontend_id = "toast"

    def __init__(self, **kw):
        self._engine = None
        self._stop = threading.Event()

    def attach(self, engine):
        self._engine = engine
        # subscribe to the event bus: only care about final results
        engine.registry.bus.subscribe("on_task_done", self._on_done)

    def _on_done(self, event):
        result = event.get("result")
        if result is not None:
            print(f"[notice] task done: {result.final_content[:80]}")

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.wait(0.2):
            pass

    def stop(self):
        self._stop.set()

    def is_alive(self):
        return not self._stop.is_set()


def create(**kw):
    return ToastFrontend()
```

```python
npa(frontend="myapp.frontends.toast:create")
# or runtime hot replacement (infrastructure slot: stop the old, start the new; on failure roll back to the old)
npa.remount(frontend="myapp.frontends.toast:create")
```

Key point: the frontend **does not render directly** — rendering is the job of
the ui renderer (`npa(ui=...)`); the frontend is responsible for "read input →
submit, receive events → hand them to the renderer". The built-in Web frontend
and WebUI renderer are the reference implementation (Chapter 22).

---

### 25.8 Event-Loop Development in Detail

The event loop decides how tasks are scheduled: thread model, interruption
method, wakeup method. The default `NasyncioLoopRuntime` covers most scenarios;
special scenarios (embedded, tests, custom scheduling) can replace it by
implementing the `LoopRuntime` protocol (4.2).

```python
class LoopRuntime(Protocol):
    name: str
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def join(self, timeout=None) -> None: ...
    def submit(self, fn, *args, **kwargs) -> Any: ...   # run in the loop context and block for the result
```

```python
# myapp/loops/sync_loop.py -- synchronous direct-run loop (tests / embedded)
class SyncLoop:
    name = "sync"

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
        return fn(*args, **kwargs)          # execute synchronously, directly


def create(**kw):
    return SyncLoop()
```

```python
npa(async_loop="myapp.loops.sync_loop")          # address (auto-resolves the module-level create)
npa(async_loop=SyncLoop())                        # instance
npa.remount(async_loop="myapp.loops.sync_loop")   # hot reload (stop the old, start the new)
```

Development key points (engineering lessons from 4.5 / 4.6):

- `submit` is a **blocking** contract: the engine waits for the result on the
  calling thread; the implementation must honor that;
- long tasks go on the **daemon thread pool**, never the loop thread (a stuck
  loop thread = all scheduling paralyzed);
- implement the cancel signal (checkable via `cancel_requested`) and Ctrl+C
  polling wait (the main thread is not at the loop entry, see 4.6.1);
- when replacing the loop, in-flight tasks are abandoned (grouping table in
  3.7); replace when there are no tasks.

---

### 25.9 Generic Component Development in Detail

"Extra capability" modules (context store, project manager, task storage,
vector store...) do not use dedicated slots; they use the **open component
namespace** (2.6.3): `kind` is the category (arbitrarily extensible), `name`
is the component name, `factory` is the factory.

#### 25.9.1 Registration and Usage

```python
# myapp/components/redis_store.py -- example: Redis context store
class RedisContextStore:
    def __init__(self, host="127.0.0.1", port=6379, **kw):
        self._host, self._port = host, port

    def add(self, session_id, text, meta=None):
        ...                                            # implement add / search / list / delete

    def search(self, query, limit=10):
        ...

    def close(self):
        ...


def create(config=None, **kw):
    return RedisContextStore(host=(config or {}).get("host", "127.0.0.1"))
```

```python
# registration (kind context_store already has the built-in fts5; add a redis implementation)
reg.register_component("context_store", "redis",
                       lambda: RedisContextStore(host="127.0.0.1"))

# npa() integration (context_store slot: address / name_or_address semantics)
npa(context_store="redis")
npa(context_store="myapp.components.redis_store:create;host=10.0.0.5")

# custom new kind: any kind can be registered; declare the reference in a preset
reg.register_component("vector_store", "pg", lambda: PgVectorStore())
Preset(name="mine", components={"context_store": "redis",
                                "vector_store": "pg"})
```

Usage from the tool side:

```python
store = ctx.component("vector_store")          # get by kind; None if absent
```

#### 25.9.2 Hot Reload and Factory Injection

- `context_store` / `project_manager` are **assembly slots**: `remount` hot
  rebuilds the AgentRuntime (grouping table in 3.7);
- a factory declaring a `workspace_root` parameter (or **kwargs) gets the
  workspace root injected automatically (2.6.3);
- custom slots can also register generic components (the `vector_store` slot
  example in 3.8; the full version in 25.10.4).

---

### 25.10 Slot Development in Detail (Key Section)

25.2 ~ 25.9 develop "implementations of slots"; this section develops **the
slot itself** — registering a brand-new slot name that gets the exact same full
pipeline as the 18 built-in slots (`npa()` argument validation, ArchLayer
assembly, `npa.remount()` hot replacement, `layer.describe()` manifest). 3.8
gives the contract overview; this section gives the full development flow.

#### 25.10.1 The Essence of a Slot

A slot = **name + string semantics + application logic (applier)**:

- `name`: the slot name, i.e. the keyword-argument name of `npa()` (must be a
  valid Python identifier);
- `string_semantics`: how string values are interpreted — `address` (module
  address) / `name` (registry component name) / `name_or_address` (name first,
  then address) / `literal` (literal value, address preferred) (3.3);
- `applier(reg, layer, value, params, ctx)`: called by the assembler when the
  slot value is non-empty, to "apply" the value to the system (register a
  component / subscribe hooks / write extras).

#### 25.10.2 All SlotSpec Fields

| Field | Type | Description |
|---|---|---|
| `name` | str | slot name (required) |
| `description` | str | description (visible in the `layer.describe()` manifest) |
| `protocol` | str | protocol description (human-readable) |
| `default_address` | Optional[str] | default implementation address (used when the slot is not filled) |
| `string_semantics` | str | `address` / `name` / `name_or_address` / `literal` |
| `factory_kwargs` | Dict[str, str] | extra factory keys (injected call context) |
| `examples` | List[str] | examples (for docs / hints) |
| `defer_factory` | bool | defer factory creation to the engine assembly phase (used by agent_runtime) |
| `applier` | callable | application logic (called when the slot value is non-empty) |
| `remount_rebuild_agent` | bool | whether to hot-rebuild the AgentRuntime after hot replacement |

#### 25.10.3 The Applier Contract and Reentrancy Safety

```python
def applier(reg, layer, value, params, ctx): ...
```

- `value`: the resolved slot value — under `address` semantics it is the
  instantiated implementation (the `;key=value` sub-config is obtained via
  `layer.subconfig(slot)`); under `name` / `name_or_address` / `literal`
  semantics it is the original value;
- `ctx` provides four mutable containers:
  - `ctx["components"]`: the final preset component declarations
    `{kind: name}` (for registering generic components; the AgentRuntime builds
    `ctx.components` from them);
  - `ctx["extras"]`: engine extra objects (consumed via
    `engine.extras[slot_name]`);
  - `ctx["overrides"]`: preset field overrides (may rewrite preset fields);
  - `ctx["meta"]`: registry architecture metadata recording the **unsubscribable
    objects** you mounted (used for cleanup at hot reload);
- **reentrancy safety is a hard requirement**: the same registry calls the
  applier repeatedly (assembly + every `npa.remount`); repeated execution must
  not stack side effects — before re-subscribing to the event bus, unsubscribe
  the objects recorded in `ctx["meta"]` (the built-in hooks / security /
  plugins slots are the reference implementations);
- `remount_rebuild_agent=True`: assembly-type slots whose applier registers
  generic components into the preset `components` should set True (hot rebuild
  after hot replacement, taking effect immediately).

#### 25.10.4 Complete Example: Developing a "Vector Search" Slot

Goal: add a new `vector_store` slot — pass in any vector-store implementation
(instance / factory / module address), register it as a `vector_store`-kind
generic component, tools access it via `ctx.component("vector_store")`; it
takes effect immediately after hot replacement.

```python
# myapp/slots/vector_store.py
from norpagent.arch import SlotSpec, register_slot


def _apply_vector_store(reg, layer, value, params, ctx):
    # 1. the resolved value is the implementation (instance / factory / module
    #    object) -- the arch layer resolves and instantiates address forms
    #    before calling the applier
    factory = value if callable(value) else (lambda v=value: v)
    # 2. register as a generic component (fixed name; overwrite semantics)
    reg.register_component("vector_store", "_arch_vector", factory)
    # 3. write the preset component declaration -> AgentRuntime builds ctx.components
    ctx["components"]["vector_store"] = "_arch_vector"
    # 4. write extras (engine side can access engine.extras["vector_store"] directly)
    ctx["extras"]["vector_store"] = value


register_slot(SlotSpec(
    name="vector_store",
    description="vector-search component (custom assembly-slot example)",
    protocol="any vector-store implementation (registered as a vector_store generic component)",
    string_semantics="literal",        # the value is passed to the applier as-is (incl. address resolution)
    applier=_apply_vector_store,
    remount_rebuild_agent=True,        # hot rebuild after hot replacement so the component takes effect immediately
))
```

Usage (the experience is identical to the built-in slots):

```python
import norpagent as npa

npa(vector_store=MyVectorStore())                     # instance
npa(vector_store="myapp.vector:create;index=./idx")   # address + clause (literal's address-first semantics)
npa.remount(vector_store=OtherStore())                # hot replacement: AgentRuntime hot rebuild
print(npa.current().engine.extras["vector_store"])    # consume extras
# inside a tool: ctx.component("vector_store")
```

The "address-first" semantics of `string_semantics="literal"` (item 2 of 3.3):
a string **looking like a pure address** (dotted identifier containing `.` /
`:`) is loaded as an address (resolution failure raises `AddressError`);
anything else keeps its literal value — so the `"myapp.vector:create;index=
./idx"` above is resolved, instantiated, and then passed to the applier.

#### 25.10.5 Hot-Reload Red Line: Dict Key-Value Values Must Be Valid Modules (Key Point)

**This is the single most important rule of slot development and hot reload.**
It shares its origin with the tool-mapping red line in 25.2.6, but applies
more broadly:

> For **dict key-value pairs** in any slot value (tools mapping / hooks mapping
> / custom-slot dict values, **nested dicts recursively**), if the value is a
> **pure-address-like string** (`is_address_like`: dotted identifier containing
> `.` or `:`), the assembler resolves it as a **module address** — hot reload
> (`npa.remount`) and startup assembly (`npa()`) are treated identically. **On
> resolution failure it raises `AddressError`; the hot reload fails; there is
> never a silent fallback.**

The value of a key-value pair must be one of the following three "valid
modules":

| Value form | Example | Result |
|---|---|---|
| registered name (name-semantics slot) | `tools={"a": "echo"}` | name reference |
| valid module address (importable + attribute exists) | `tools={"a": "myapp.tools:create"}` | loaded and instantiated (factory convention) |
| valid instance / factory object | `tools={"a": MyTool()}` | used as-is |
| address-like but invalid (typo / module missing / attribute missing) | `tools={"a": "myapp.tolls:create"}` | **AddressError; failure** |

Why it must be strict: hot reload is an online ops action. If an address typo
fell back silently, the old / an empty implementation would quietly stay
online — much harder to debug than an explicit failure. So "if you wrote an
address it must raise explicitly". The implementation lives in
`_resolve_dict_values` in `norpagent/arch/layer.py` (uniform dict-value
handling, nested recursion, raise on resolution failure).

```python
# custom-slot dict values: just as strict at hot reload
npa.remount(vector_store={"embedder": "myapp.embed:create",   # valid address -> resolved
                         "index": "./idx"})                   # not address-like -> literal value
npa.remount(vector_store={"embedder": "myapp.embd:create"})    # typo -> AddressError
```

Exceptions and boundaries:

- **hooks-slot exception**: the hooks mapping's values are "callbacks
  themselves"; an address pointing to a callback function is **kept verbatim,
  not called** (item 3 of 3.3) — but the address must still resolve (module /
  attribute exists), otherwise it raises too;
- **list elements are not resolved**: lists keep literal semantics (e.g. the
  directory path in `plugins=["./dir"]`; tools list elements get the special
  "name or address" treatment by the assembler, see item 4 of 3.3);
- **non-address strings are unaffected**: strings without dotted identifiers
  such as `"high"`, `"./data"`, `"sqlite"` keep literal / name semantics.

**The module cache is invalidated before hot reload**: `remount` deletes the
.pyc for string addresses, pops `sys.modules`, then re-imports (3.7). Edit
code → remount and it takes effect; instance forms do no cache invalidation.
To debug a failed hot reload use `layer.describe()` for the assembly manifest
and the `AddressError` traceback to locate the address.

---

### 25.11 Plugin Development in Detail (Key Section)

A plugin = a set of tools + a set of lifecycle hooks + metadata, distributed as
an independent `.py` file (or a manifest package). When the host loads it, it
automatically gets the full security protection: signature verification / AST
audit / import restrictions / network policy / human approval (Chapter 11).
This section gives plugin authors a complete development example. Companion
standalone doc: `norpagent插件开发指南.md` (Chinese plugin development guide).

#### 25.11.1 Complete Single-File Plugin Example

```python
# my_plugins/weather_plugin.py -- complete plugin: tools + hooks + approval hints
PLUGIN_NAME = "Weather Plugin"
PLUGIN_VERSION = "1.0.0"
PLUGIN_PUBLISHER = "xingluosama121"
PLUGIN_DESCRIPTION = "Query city weather; greet at task start."

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Query the current weather of a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "city name"},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    },
]

# approval hints: weather needs no approval (read-only); undeclared tools follow the host master switch
APPROVAL_HINTS = {
    "weather": {"approval": "none", "risk": "L0"},
}


def execute(tool_name, args, ctx):
    """Unified tool entry: handle and return str / None; unhandled returns None."""
    if tool_name == "weather":
        city = args.get("city") or "Beijing"
        return f"{city}: sunny today, 25 C"   # example implementation; replace with a real API
    return None


# lifecycle hook (one of the 15; names aligned with the legacy app hooks;
# signature: business params first, ctx last)
def on_task_start(prompt, ctx):
    print(f"[plugin] new task: {prompt[:50]}")


def before_tool_call(tool_name, args, ctx):
    """Mutating hook: returning a dict can rewrite the args (11.5)."""
    if tool_name == "weather" and "city" not in args:
        args = dict(args)
        args["city"] = "Beijing"                  # default-city fallback
    return args
```

Loading (host side):

```python
from norpagent.plugins import install_plugin_dirs
loader = install_plugin_dirs(reg, ["./my_plugins"], config={})
for info in loader.plugins:
    print(info.name, info.enabled, info.error or "ok")

# one-shot load via the npa() slot
npa(plugins=["./my_plugins"])
```

#### 25.11.2 Manifest Package Format

Directory distribution: `my_pkg/` contains `manifest.json` + an entry module
(default `plugin.py`):

```json
{
  "name": "my_pkg",
  "version": "1.0.0",
  "publisher": "xingluosama121",
  "description": "package-style plugin",
  "entry": "plugin.py",
  "isolation": "process",
  "permissions": [],
  "signature": ""
}
```

The only difference from a single-file plugin is that the entry module carries
the same module-level interfaces (`PLUGIN_NAME` / `TOOLS` / `execute` / hooks...).

#### 25.11.3 Lifecycle Hooks (15)

A plugin may define the following hook functions at module level (signature
convention: **business params first, PluginContext last**; `ctx` provides
`plugin_name` / `project_root` / `app_dir` / `config` / `current_step`):

| Hook | Timing | Mutating |
|---|---|---|
| `on_task_start(prompt, ctx)` | task starts | no |
| `on_task_done(result, ctx)` | task ends | no |
| `before_step(step, ctx)` | each step starts | yes (return value passes through to the kernel) |
| `after_step(step, result, ctx)` | each step ends | yes |
| `before_model_call(messages, ctx)` | before a model call | yes |
| `after_model_call(output, ctx)` | after a model call | yes |
| `before_tool_call(tool_name, args, ctx)` | before a tool runs | yes (may rewrite args) |
| `after_tool_call(tool_name, result, ctx)` | after a tool runs | yes |
| `on_content(content, ctx)` | streaming output delta | no |
| `on_error(error, ctx)` | an error occurs | no |
| ...... | (15 in total; 11.5 / Appendix E) | |

#### 25.11.4 Isolation, Signing and Publishing

- **Process-level isolation**: declare `ISOLATION = "process"` at the module
  header; the plugin code then only loads and runs in a host child process;
  tools return results via RPC and hooks are forwarded with a time limit
  (11.7) — a crashing plugin never drags down the main process;
- **Signing** (11.8): `python -m norpagent plugin-sign --gen` generates a key
  pair; `plugin-sign my_plugin.py --key <private-key-hex>` produces a signature
  (written into the file header); after the host adds the public key to
  `plugin_trusted_keys`, the plugin is trusted and the audit relaxes to warn;
- **Publishing**: a single-file plugin ships as a `.py`; a package plugin ships
  as a zipped directory.

#### 25.11.5 Debugging and Hot Reload

```python
# development phase: library facade
from norpagent.plugins import PluginSystem
ps = PluginSystem(reg, ["./my_plugins"], config={"plugin_isolation": "inproc"})
infos = ps.load()
ps.status()                 # plugin manifest + isolation-host status
ps.reload("weather_plugin")  # dev-phase hot reload of one plugin
ps.shutdown()               # release the isolation host

# whole-set replacement at runtime (the framework unsubscribes old subscriptions
# before reinstalling, so nothing stacks)
npa.remount(plugins=["./my_plugins_v2"])

# troubleshooting: inspect PluginInfo
for info in ps.loader.plugins:
    if not info.enabled:
        print(info.name, info.error, info.audit_issues)
```

Debugging tips: `plugin_security_audit: "warn"` only warns, never blocks;
`plugin_isolation: "inproc"` runs in-process for easy breakpoint debugging;
switch back to `auto` before going live (AST-reads `ISOLATION` statically; with
process isolation **the plugin code is never executed** by the host).

---

### 25.12 Development Checklist

Self-check every item before delivering a new module (corresponding sections
of this chapter):

| # | Check item | Section |
|---|---|---|
| 1 | implements the full protocol (depends only on protocols, never on concrete implementation classes) | 25.1.4 |
| 2 | the factory supports signature-based injection (`layer` / `slot` / `config` / `workspace_root`) | 3.4 |
| 3 | registered in the Registry (`register_*` or `register_component`); the name does not collide with built-ins | 25.2.5 |
| 4 | at least one integration way verified (name / address / instance); the address form is hot-reloadable | 25.2.5 |
| 5 | **hot-reload verification**: dict key-value values are valid modules (registered name / resolvable address / instance); a misspelled address raises `AddressError` instead of falling back silently | 25.2.6 / 25.10.5 |
| 6 | edit implementation code → remount → new code takes effect (address form invalidates the module cache) | 3.7 |
| 7 | long / streaming paths check the cancel signal (`cancel_requested`) | 25.2.4 |
| 8 | timeouts honored: sandbox `timeout`, model `call_timeout`, network timeouts | 25.5.2 |
| 9 | thread-safe: use locks / immutable data when concurrent tasks share an instance | 25.4.1 |
| 10 | `close()` is idempotent; may be called from shutdown / hot rebuild / task end | 25.5.2 |
| 11 | no exception leaks: tools return `ToolResult(success=False)`, model adapters catch network exceptions | 25.2.1 |
| 12 | the assembly manifest is observable: `layer.describe()` shows your implementation | 3.5 |
| 13 | no upward dependencies (dependency direction points strictly downward) | 2.6.1 |
| 14 | docs and examples (SlotSpec.examples / module docstring) | 25.10.2 |

Completing these 14 items puts your module on exactly the same footing as the
built-in components: assemblable, hot-reloadable, auditable, replaceable.

---

## Chapter 26 Registration Flow in Detail

Chapter 25 explains how to develop modules, slots, plugins and tools. This
chapter explains the act of **registration** itself: how the **Registry**,
the **slot table (SLOT_SPECS)** and the **address resolver** cooperate, which
steps a component goes through from "registered" to "actually used by the
Agent", and the three registration timings, four value forms, validation and
error handling. After reading this chapter you should be able to answer three
questions:

1. Which container does a component register into? (the Registry's 9 namespaces)
2. How does the framework find it after registration? (name / address / instance)
3. What happens between pressing `npa()` and a tool being called? (the assembly pipeline)

### 26.1 The Registration Landscape: Responsibilities of the Three Concepts

Registration is not a single action; it is the cooperation of three existing
mechanisms:

| Mechanism | Module | Responsibility | Typical API |
|---|---|---|---|
| Registry | `norpagent.kernel.registry` | **stores** the "name → implementation" mappings; part of the kernel, unaware of any concrete implementation | `register_*` / `resolve_*` / `build_*` / `list_*` |
| Slot table (SLOT_SPECS) | `norpagent.arch.slots` | **describes** the mount points: 18 built-in slots + runtime hot-pluggable `register_slot` | `get_slot` / `snapshot_slots` / `register_slot` |
| Address resolver | `norpagent.arch.address` | **locates**: turns a string address (`pkg.mod[:attr]`) into an object | `resolve_address` / `is_address_like` |
| Assembler | `norpagent.runtime.mount` | **installs**: translates slot values into registry entries and preset overrides | `build_registry` / `apply_slot_overrides` |

One sentence distinguishes the three: **a slot is a mount point (what to fill),
the Registry is the namespace (what is stored), and an address is a locating
mechanism (how to find it)**. The assembler ties them together.

- A slot value that is a **registered name** (e.g. `"sqlite"`) → the assembler
  looks it up in the Registry and writes the name into the final preset;
- A slot value that is an **address** (e.g. `"myapp.session:create"`) → the
  assembler resolves the address to a factory, registers it under an internal
  name (`_arch_session`) and writes that name into the final preset;
- A slot value that is an **instance / factory** → likewise registered under an
  internal name and referenced.

### 26.2 The Registry: 9 Namespaces

`Registry` (`norpagent.kernel.registry`) holds 9 independent namespaces, each
a "name → implementation" dict:

| Namespace | Register API | Resolve / Build API | Stored content |
|---|---|---|---|
| models | `register_model(name, provider)` | `resolve_model(name)` | model provider instance |
| tools | `register_tool(name, tool)` | `resolve_tool(name)` | Tool instance |
| sessions | `register_session(name, factory)` | `build_session(name)` | factory (new instance each time) |
| sandboxes | `register_sandbox(name, factory)` | `build_sandbox(name)` | factory (new instance each time) |
| schedulers | `register_scheduler(name, factory)` | `build_scheduler(name)` | factory (new instance each time) |
| uis | `register_ui(name, adapter)` | `resolve_ui(name)` | UIAdapter instance |
| plugins | `register_plugin(plugin)` | `unregister_plugin(name)` | Plugin object (tools + hooks) |
| presets | `register_preset(preset)` | `resolve_preset(name)` | Preset instance |
| components | `register_component(kind, name, factory)` | `build_component(kind, name, workspace_root=...)` | any kind: `kind → {name: factory}` |

Key points:

- **`resolve_*` vs `build_*`**: `resolve_*` returns the object as registered;
  `build_*` invokes the factory and **creates a new instance each time** —
  sessions, sandboxes and schedulers are built on demand (each task may get
  its own), while models, tools and UIs are shared (one instance reused
  globally). Therefore you must pass a **factory** (function or class) when
  registering sessions / sandboxes / schedulers, and an **instance** for
  models / tools / UIs.
- **Name-override semantics**: plain dict assignment; a later registration
  silently overrides an earlier one. When a plugin registers a tool whose name
  already exists, it prints `[Registry] tool xxx already exists, overridden by
  plugin xxx` and then overrides.
- **Thread safety**: protected by an internal RLock; any thread may register /
  resolve at any time.
- **Components are an open namespace**: `kind` is not limited to built-ins
  (`context_store` / `project_manager` ...); third parties can register brand
  new kinds (e.g. `vector_store`) without touching the kernel (25.9.1).
- `register_plugin` is a compound registration: plugin tools enter the tool
  table one by one, hooks subscribe to the event bus one by one, and the
  plugin object enters the plugin table; `unregister_plugin` unsubscribes the
  hooks and removes the plugin record (tool entries remain under name-override
  semantics, unreachable when not in the preset's tool set).

### 26.3 The Four Value Forms and String Semantics

A component moves from the developer's hands into the Registry in one of four
forms:

| Form | Example | Notes |
|---|---|---|
| Instance | `npa(tools=[MyTool()])` | directly usable; the assembler wraps it in a factory returning the same instance |
| Factory function / class | `npa(model="myapp.model:create")` resolves to a callable | signature-based context injection (`layer` / `slot` / `config` / `workspace_root`, section 3.4) |
| Registered-name reference | `npa(model="openai_compat")` | the string is looked up in the Registry first; found → name reference |
| Address string | `npa(model="myapp.model:create")` | name lookup fails → resolved as an address |

A string entering a slot is interpreted by that slot's **string semantics**
(`SlotSpec.string_semantics`, one of four):

| Semantics | Meaning | Example slots |
|---|---|---|
| `address` | the string is a module address (`pkg.mod[:attr]`), must resolve | `async_loop` / `frontend` / `context_store` / `project_manager` |
| `name` | the string is a registered component name, passed through as-is | `tools` |
| `name_or_address` | look up the Registry name first; if not found, resolve as an address | `model` / `session` / `sandbox` / `scheduler` / `ui` / `preset` |
| `literal` | the string is a literal value (level / path / directory); since v0.9.1, strings shaped like a pure address (dotted identifier containing `.` or `:`) are loaded by address | `hooks` / `security` / `plugins` / `logger` / `storage` / `error_handler` |

Since v0.9.1, **dict key-value pairs** of every slot uniformly support
address resolution (`_resolve_dict_values` in `layer.py`, recursive for
nested dicts):

- a value shaped like a pure address → resolved to an object; a resolution
  failure raises `AddressError` — **no silent fallback** (the red line,
  section 25.2.6);
- a resolved callable → invoked by the factory convention (except the `hooks`
  slot: values are callbacks themselves and are kept as-is, never invoked);
- non-string values pass through unchanged.

Addresses support **extra config clauses**: `"pkg.mod:create;port=9000;theme=dark"`
— the `key=value` pairs after the semicolon are parsed into a dict injected
into the factory's `config` parameter (sections 3.3 / 3.4).

### 26.4 The Full Pipeline from Registration to Assembly (npa() Startup)

Follow the complete chain with `npa(model="myapp.model:create", tools={"weather": "myapp.weather_tool:create"})`:

```
npa(...) startup
│
├─ 1. launch() splits parameters (runtime/__init__.py)
│     by the "live slot-table snapshot":
│     - slot keys (model / tools / ...) → slot_values
│     - other keys (max_steps / workspace_root / ...) → runtime params
│
├─ 2. ArchLayer(config, **slot_values) + mount_defaults(layer)
│     registers the built-in default factories (async_loop / frontend / agent_runtime)
│
├─ 3. layer.connect() (idempotent; resolves slot by slot)
│     - model (name_or_address): the string is left untouched, passed to the
│       assembler for the name-first decision
│     - tools (dict): _resolve_dict_values resolves key-value pairs
│       recursively — the value "myapp.weather_tool:create" is address-like
│       → resolve_address imports myapp.weather_tool → takes the create
│       attribute (callable) → call_factory invokes it by signature
│       → a WeatherTool instance
│
├─ 4. build_registry(layer, params) (runtime/mount.py)
│     a. Registry() creates an empty registry
│     b. install_defaults(reg)    built-in components enter the table
│        (models openai_compat / anthropic / mock; 21 built-in tools;
│         sessions sqlite / memory; sandboxes pooled / subprocess;
│         scheduler persistent; ...)
│     c. register_all_presets(reg)  six built-in presets enter the table
│        (standard etc.)
│     d. apply_slot_overrides(reg, layer, params) assembles in fixed order:
│        ├─ preset slot → baseline preset (default standard)
│        ├─ model: address resolved → register_model("_arch_model", factory)
│        │    → overrides["model"] = "_arch_model"
│        ├─ tools: dict → register_tool("weather", instance) one by one
│        │    → overrides["tools"] = ["weather"] (only yours are enabled)
│        ├─ session / sandbox / scheduler: registered as _arch_xxx or referenced
│        ├─ ui: register_ui("_arch_ui", instance) → extras["ui_adapter"]
│        ├─ context_store / project_manager: register_component(kind,
│        │    "_arch_xxx", factory) → written into the components declaration
│        ├─ hooks: previous architecture-level subscriptions unsubscribed
│        │    first → bus.subscribe re-mounted
│        ├─ security: safe() installs the kit (recorded in meta, unsubscribable)
│        ├─ plugins: install_plugin_dirs runs the full load pipeline
│        ├─ logger / storage / error_handler → extras (consumed by the engine)
│        ├─ custom slots: iterate snapshot_slots(); for each spec with a
│        │    non-None applier, call applier(reg, layer, value, params, ctx)
│        └─ assemble the final Preset(...) (slot overrides + baseline merge)
│           → reg.register_preset(final)
│
├─ 5. NorpEngine(layer, registry, preset, loop, frontend, extras)
│     engine.start() → _build_agent():
│       call_factory(agent_runtime slot implementation, {registry, preset, ui,
│       task_params, layer, config}) → AgentRuntime constructed
│
└─ 6. Consumption (engine.submit(text) → agent.run())
      registry.resolve_model(preset.model)    → the model instance
      registry.tool_schemas(preset.tools)     → the tool schema list
      registry.resolve_tool(name)             → the tool instance (on call)
      registry.build_session(preset.session)  → session instance (on demand)
      registry.build_sandbox(preset.sandbox)  → sandbox instance (on demand)
```

Three key conclusions:

1. **Registration happens in the assembler; consumption happens in the Agent
   runtime** — once your component is in the table, the core code works with
   it with zero changes;
2. **Every slot value ends up as "a registry entry + a preset declaration"**:
   the internal names (`_arch_xxx`) are the assembler's universal device,
   making "your implementation" and "the built-in implementation" consumed
   through exactly the same path;
3. **Order matters**: the preset sets the baseline first, then each slot
   overrides it, and the final preset merges everything — so
   `npa(preset="minimal", model="myapp.model:create")` yields
   minimal baseline + your model override.

### 26.5 Three Registration Timings and Hot Reload

| Timing | Way | Takes effect | Typical scenario |
|---|---|---|---|
| Startup assembly | `npa()` slot params (declarative); or `reg.register_*` before `npa()` (programmatic) | at startup | application assembly, library integration |
| Runtime | `npa.remount(slot=...)`; or direct `reg.register_*` while running | component slots: next run(); assembly slots: AgentRuntime hot rebuild | switching models / tool sets / security levels |
| Code hot reload | edit the module file → `npa.remount(model="myapp.model:create")` | immediately (module cache invalidated) | dev iteration, live bug fixes |

Programmatic registration and the `npa()` ordering convention:

```python
reg = Registry()
reg.register_tool("weather", WeatherTool())   # register first
reg.register_preset(Preset(name="mine", tools=["weather"], ...))
npa(preset="mine")                              # then start, reference by name
```

Note: `npa()` creates its own fresh `Registry()` internally and installs the
built-ins, but it does **not** wipe registrations you made on the same `reg`
before `npa()` — provided you actually use that `reg` (as in the programmatic
assembly above, or by passing `reg` to a custom `agent_runtime` factory).
The simplest approach: use the `reg` produced by `build_registry(layer)` for
programmatic assembly, and `npa()` slot parameters for declarative assembly.

Module-cache invalidation on hot reload (section 3.7): `remount` first runs
`_invalidate_address_module` on string addresses — it deletes the .pyc at
`module.__cached__` and pops the `sys.modules` entry, so the next resolution
re-imports from disk. Hence "edit `myapp/weather_tool.py` →
`npa.remount(tools={"weather": "myapp.weather_tool:create"})`" hot-updates the
code; **instance / registered-name forms have no address to invalidate — use
the address form after editing code**.

Re-entrancy safety: `apply_slot_overrides` may run repeatedly against a
running registry (every `npa.remount` calls it); before re-applying, it
unsubscribes the architecture-level subscriptions it mounted last time (hook
extensions / security kits / plugins), so hot mounts never duplicate
subscriptions — custom-slot appliers must follow the same convention (record
objects to unsubscribe in `ctx["meta"]`, section 25.10.3).

### 26.6 Slot Registration vs Component Registration: Two Kinds of "Register"

The framework has two "register" APIs that are easy to confuse:

| Dimension | `register_slot` (slot-table hot plug) | `register_component` (generic component) |
|---|---|---|
| What is registered | a **mount point**: a new `npa()` keyword (slot name) | an **implementation**: a named implementation under some kind |
| Entry point | `norpagent.arch.slots.register_slot` | `reg.register_component(kind, name, factory)` |
| Scope of effect | the whole pipeline: `npa()` param validation, ArchLayer assembly, `npa.remount` hot replacement, `layer.describe()` manifest | preset `components` declaration + `ctx.component(kind)` lookup |
| Assembly | the `SlotSpec.applier(reg, layer, value, params, ctx)` callback | the framework `build_component`s directly from `preset.components` |
| Protection | the 18 built-in slot names can neither be registered / overridden / unregistered | none (dict-override semantics) |

One sentence: **first a mount point (slot), then an implementation to plug in
(a registry entry)**. In most cases you only need to register implementations
(`register_tool` / `register_component`); you need `register_slot` only when
you want a brand-new `npa()` keyword (full development flow: sections 3.8
and 25.10).

`register_slot` validation rules (violations raise `SlotError`):

- the slot name must be a valid Python identifier (it becomes an `npa()`
  keyword), not a Python keyword, and not `prompt` / `config` (launch special
  keys);
- the 18 built-in slot names are protected (`is_builtin_slot`);
- `string_semantics` must be one of `address` / `name` / `name_or_address` /
  `literal`; `applier` must be callable or None;
- re-registering a name requires `replace=True` (custom slots only, hot-
  replacing the spec; already-assembled implementations keep working until
  the next `remount` re-resolves with the new spec).

`register_slot` takes effect immediately: registering before `npa()` makes the
new slot recognized (launch splits parameters by the live slot table);
registering at runtime works too — a connected ArchLayer fills in late slots
idempotently (`connect`), and `remount` accepts the new slot right away.

### 26.7 Registration Validation and Error Handling

The three exception types of the registration / assembly phase mean different
things:

| Exception | Raised when | What to do |
|---|---|---|
| `ComponentError` | resolving an unregistered name; `register_preset` with a non-Preset; custom-slot applier failure (wrapped) | check whether the name is registered and spelled correctly; cross-check with `reg.list_*()` |
| `SlotError` | illegal slot-table operations: registering a built-in slot name / re-registering without replace / unregistering a missing slot / illegal slot name | fix the spec per the message; change built-in slot values with `npa.remount`, never by editing specs |
| `AddressError` (inherits ImportError) | address resolution failure: module missing / attribute missing / empty address | check the module path and attribute name; import the module yourself to test; **do not silently fall back** (red line, 25.2.6) |

The completeness of a preset's references can be validated in advance:

```python
missing, missing_tools = reg.validate_preset(my_preset)
# the preset is usable only when missing == [] and missing_tools == []
# missing example: ["model=openai_compat", "component=vector_store:pg"]
```

Runtime diagnosis, three moves:

```python
reg.list_tools()                    # what is actually in the table (move 1)
reg.tool_schemas(["weather"])       # is the tool's schema usable (move 2)
eng.layer.describe()                # assembly manifest: where each slot came from (move 3)
```

### 26.8 Registration Best Practices and a Checklist

Best practices:

1. **Naming**: lowercase with underscores; do not collide with built-ins
   (`sqlite` / `pooled` / `persistent` / `openai_compat` ...); prefix plugin
   tools with the plugin name to avoid overrides (`weather_current` beats
   `get_time`).
2. **Factory vs instance**: pass **factories** for sessions / sandboxes /
   schedulers (new instance each time); pass **instances** for models / tools /
   UIs (globally shared). Wrap a tool in a factory only when it carries
   per-run state; otherwise share one instance (mind thread safety).
3. **Timing**: declarative (`npa()` slots) suits application assembly;
   programmatic (`reg.register_*`) suits library integration and dynamic
   conditional assembly; hot reload (`npa.remount`) suits dev iteration and
   live adjustments — all three can be mixed; everything flows into the same
   Registry.
4. **Prefer the address form**: a module address (`pkg.mod[:attr]`) buys you
   factory injection, `;key=value` clauses and code hot reload in one shot.
5. **The hot-reload red line**: dict key-value values must be valid modules —
   a registered name / a resolvable address / an instance; an address-like
   string that fails to resolve raises `AddressError`, never a silent fallback
   (25.2.6 / 25.10.5).
6. **Assembly observability**: before delivery, run `layer.describe()` and
   confirm your implementation appears in the manifest with the right source
   (address / direct value / default logic).

Registration checklist (self-check every item before delivering a new
component):

| # | Check item | Reference |
|---|---|---|
| 1 | the component is registered in the right namespace (tool→tools, session→sessions, component→components) | 26.2 |
| 2 | sessions / sandboxes / schedulers take factories; models / tools / UIs take instances | 26.2 |
| 3 | the name does not collide with built-ins; lowercase with underscores | 26.8 |
| 4 | at least one form verified: `reg.list_*()` shows it; `resolve_*` returns it | 26.7 |
| 5 | the address form is importable: `import myapp.xxx` succeeds, the attribute exists | 26.7 |
| 6 | the address form hot-reloads: edit code → remount → new code takes effect | 26.5 |
| 7 | dict key-value values are valid modules (the red line) | 26.7 / 25.2.6 |
| 8 | preset reference validation passes: `validate_preset` reports no gaps | 26.7 |
| 9 | `layer.describe()` shows the right source | 26.4 / 3.5 |
| 10 | the custom-slot applier is re-entrant (records objects to unsubscribe in `ctx["meta"]`) | 26.5 / 25.10.3 |
| 11 | exception semantics are right: unregistered→`ComponentError`, bad address→`AddressError`, slot table→`SlotError` | 26.7 |
| 12 | an unload path exists: plugins `unregister_plugin`, slots `unregister_slot` | 26.2 / 26.6 |

---

## Chapter 27 Minimal Kernel in Depth: EventBus, the Slot Connector, the Registry and the Address Resolver

> Prerequisite reading: Chapter 2, 2.3 (the four minimal-kernel modules), Chapter 3
> (architecture layer and address functions), Chapter 9, 9.5 (hooks and the EventBus),
> Chapter 26 (the registration flow).
> This chapter takes the four "irreplaceable" components apart one by one: their data
> structures, APIs, internals, and how the four collaborate in one startup and one hot mount.

### 27.1 Overview: The Four Form the Assembly Closed Loop

Section 2.3 already gave the definition of the minimal kernel — the whole framework
has only four irreplaceable pieces:

| # | Component | Class / module | One-line responsibility |
|---|---|---|---|
| 1 | Slot connector | `norpagent.arch.layer.ArchLayer` | Assembles "slot values" into "implementation objects"; supports hot mount at runtime |
| 2 | Address resolver | `norpagent.arch.address` (`resolve_address`) | Resolves an "address string" into a "usable object" |
| 3 | Registry | `norpagent.kernel.registry.Registry` | The name → component mapping center; everything is a registered item |
| 4 | Event bus | `norpagent.kernel.events.EventBus` | The event-passing channel between components; copy-on-write + lock-free iteration |

Everything else — the event loop, agent loop, models, tools, sessions, sandboxes,
schedulers, context store, project management, hook extensions, security, plugins,
frontends, renderers, presets, logging, storage, error handling — is a slot and can
all be replaced.

The collaboration closed loop of the four (one `npa()` startup):

```
npa(...) slot values
   │
   ▼
┌───────────────────────────────────────────────────────┐
│ ArchLayer (slot connector)                            │
│   1. set_default()    registers each slot's built-in  │
│                       default logic                   │
│   2. connect() assembles slot by slot (_connect_slot):│
│        value=None     → default factory               │
│        value=str      → resolve_address() (address    │
│                         resolver)                     │
│        value=dict     → recursive resolution of       │
│                         key-value addresses           │
│   3. layer[slot] gets the implementation directly;    │
│      describe() prints the assembly manifest          │
└───────────────────────────────────────────────────────┘
   │ assembly result lands in the registry
   ▼
┌───────────────────────────────────────────────────────┐
│ Registry                                               │
│   components registered / resolved by name; bus and    │
│   hooks hang off the registry                          │
│   build_registry() / apply_slot_overrides() fill it    │
└───────────────────────────────────────────────────────┘
   │ at runtime
   ▼
┌───────────────────────────────────────────────────────┐
│ EventBus                                               │
│   AgentRuntime / UI / plugins / hooks all subscribe    │
│   emit() broadcast; intercept() mutating dispatch +    │
│   one-vote veto                                        │
└───────────────────────────────────────────────────────┘
```

One-line responsibility boundaries:

- the address resolver only answers "what object is this address";
- the slot connector only answers "what goes in this slot, how, and can it be swapped";
- the registry only answers "which component does this name map to, and how is it made";
- the event bus only answers "who receives which notification when".

The four are independent of each other and none knows the concrete implementation of
the others (the address resolver knows nothing of the Registry, the EventBus nothing of
ArchLayer); the `runtime.mount` assembler strings them together. The following sections
expand on each one.

### 27.2 The Event Bus (EventBus)

#### 27.2.1 Positioning and Design Goals

EventBus is "the only decoupling point between the kernel and all external components
(UI / plugins / hooks)": AgentRuntime does not call UI methods directly; it emits events
to the bus; the UI only subscribes to the bus and never perceives the kernel internals.

Code location: `src/norpagent/kernel/events.py`.

Core types:

- `EventType(str, Enum)`: the 16 standard event names, aligned one-to-one with the old
  plugin system's `HOOK_NAMES` (the old comment claims 15, but with on_usage_update it
  is actually 16); migration maps seamlessly: hook = event subscription (11.5 / Appendix E);
- `AgentEvent`: one event = `type` + `payload`(dict) + `ts`, with `.get()` access;
- `HookVeto`: the one-vote-veto exception (`intercept` does not catch it; it reaches the kernel);
- `EventBus`: the bus itself (thread-safe).

#### 27.2.2 Data Structures and the Thread-Safety Model

```python
self._all: List[Listener]                  # listeners subscribed to all events
self._typed: Dict[str, List[Listener]]     # listeners grouped by event type
self._lock = threading.RLock()             # write lock
self._log_error: Optional[Callable]        # subscriber-exception callback
```

Thread safety uses "copy-on-write + lock-free iteration":

- `subscribe` / `unsubscribe`: build a **new list** inside the lock and replace the
  reference; never mutate in place;
- `emit` / `intercept`: take one reference inside the lock (`_snapshot`), then iterate
  directly **lock-free**;
- an old snapshot held by a reader is never mutated (writers replace with a new list
  object), so concurrency safety is unchanged;
- for high-frequency events (e.g. per-token on_content) this saves copying the listener
  list on every event.

Measured numbers (23.1): static subscription table + single-thread publishing, over
1.6 million events/second.

#### 27.2.3 Subscribe and Unsubscribe

```python
from norpagent.kernel import EventBus

bus = EventBus()

def on_content(e):
    print(e.type, e.get("content"))

bus.subscribe(on_content, "on_content")        # only on_content
bus.subscribe(lambda e: print("all:", e.type)) # None = all events
bus.unsubscribe(on_content, "on_content")      # unsubscribe
```

- `event_type=None` goes into the all list (receives every event);
- a specific type goes into the typed list;
- unsubscribe removes the "first equal element" (`_without_one`); a duplicate
  subscription removes only one.

#### 27.2.4 emit vs intercept: Broadcast vs Mutating Dispatch

| Dimension | `emit(event_type, **payload)` | `intercept(event_type, **payload)` |
|---|---|---|
| Purpose | observe / notify (UI refresh, logging) | rewrite the data flow, one-vote veto |
| Return value | ignored | first non-None return wins; all None = no intervention |
| Subscriber exception | caught and logged, keep going | ordinary exceptions same as left; **HookVeto not caught**, reaches the kernel |
| Call order | all listeners first, then typed listeners | same as left |

`intercept` matches the old plugin system's `_broadcast_mutating` semantics: mutating
hooks such as before_step / before_tool_call / after_tool_call rewrite the data flow
through their return values (None = no intervention).

Subscriber-exception isolation: by default printed to stderr; customize with
`set_error_logger(cb)` — a subscriber must never break the main flow (all ordinary
exceptions caught + `_report_error`). This is the hard design of "bus availability first".

#### 27.2.5 The 16 Standard Events

| Layer | Event | Trigger point |
|---|---|---|
| L1 agent lifecycle | on_agent_init / on_agent_shutdown | engine start / shutdown |
| L2 tasks | on_task_start / on_task_done / on_task_error / on_task_stopped / on_task_timeout | the five task states |
| L3 steps | before_step / after_step / before_tool_call / after_tool_call / on_user_input_required | steps and tool calls |
| L4 streaming | on_reasoning / on_content / on_event / on_usage_update | token-level pushes |

#### 27.2.6 Relationship with HookSystem

`HookSystem(bus)` is the "9-layer 29-hook view" over the same bus:
`registry.hooks.before_model_call.subscribe(fn)` is equivalent to subscribing to the
same-named event on `registry.bus`; an unregistered named event automatically becomes a
dynamic-layer hook when emitted. See Chapter 9, 9.5 and Appendix E.

```python
from norpagent import Registry

reg = Registry()
reg.hooks.before_model_call.subscribe(my_fn)   # hook view (recommended)
reg.bus.subscribe(my_fn, "before_model_call")  # direct bus (equivalent)
```

The kernel side emits the same way (inside `AgentRuntime`):

```python
self.hooks.on_agent_init.emit(preset=self.preset.name)     # broadcast
result = self.hooks.before_tool_call.intercept(...)        # mutating dispatch
```

### 27.3 The Address Resolver (AddressResolver)

#### 27.3.1 Positioning

Code location: `src/norpagent/arch/address.py`.

The address resolver does exactly one thing: **turns an "address" into an "object"** —
it does not call factories, does no assembly, does not check protocols. Factory-context
injection and calling rules live in `norpagent.arch.layer.call_factory`. The address
function semantics "empty = default, filled = connected" (3.2) are all implemented by it.

#### 27.3.2 The Four Address Forms

| Form | Meaning |
|---|---|
| `None` | use the slot's default implementation (handled by the caller; the resolver returns None as-is) |
| `"pkg.mod"` | import the module; prefer the module's conventional factory attributes `create` / `build` / `default`, otherwise mount the whole module |
| `"pkg.mod:attr"` | import the module and take the named attribute as the implementation |
| callable / other object | return as-is (factory function / class / instance / value) |

```python
from norpagent.arch.address import resolve_address

resolve_address(None, slot="model")                    # -> None
resolve_address("myapp.models:create", slot="model")   # -> module attribute create
resolve_address("myapp.tools", slot="tools")           # -> one of create/build/default, else the whole module
resolve_address(MyTool(), slot="tools")                # -> the object itself (instance pass-through)
```

Resolution details:

- attribute fallback order `_FACTORY_ATTRS = ("create", "build", "default")`;
- whole-module mounting requires the module itself to implement the slot protocol
  (e.g. a complete LoopRuntime module);
- a missing `:attr` attribute raises `AddressError` (never silently falls back).

#### 27.3.3 Stripping the Extra Config Clause

In `"pkg.mod:create;timeout=5"`, the `key=value` after the semicolon is **not part of
the address** — the resolver strips it first, and ArchLayer resolves it into the
factory's `config` injection parameter. A clause never interferes with module-path /
attribute resolution:

```python
npa(model="myapp.models:create;api_key=sk-xxx;base_url=https://...")
# address  = myapp.models:create
# config   = {"api_key": "sk-xxx", "base_url": "https://..."}
```

#### 27.3.4 is_address_like: Pure Structural Detection

`is_address_like(value)` decides whether a string looks like a "pure address"
(`pkg.mod[:attr]`) — a purely structural check: no import, no side effect, no exception:

- after stripping `;key=value`, the whole string is a dotted identifier containing at
  least one `.` or `:`;
- therefore literals, paths and URLs such as `"high"` / `"./data"` / `"my_tool"` /
  `"https://api.example.com"` are never misclassified as addresses;
- `"myapp.security:high"` / `"myapp.tools"` / `"pkg:attr"` are addresses.

Used for the v0.9.1 "address-first" decision: in literal slots and dict key-value pairs,
strings in address form load by address, everything else keeps its original semantics
(3.3 / 26.3).

#### 27.3.5 Error Semantics

```python
class AddressError(ImportError): ...
```

A module import failure, a missing attribute, or an empty address string all raise
`AddressError` uniformly (inherits ImportError, catchable via `except ImportError`);
the message carries the slot name and the full address for easy location. **Red line:
an address-like string that fails to resolve must raise, never silently fall back to a
literal** (25.2.6 / 25.10.5).

#### 27.3.6 Division of Labor between Resolving and Calling

```
resolve_address("myapp.models:create", slot="model")  # resolve: get the factory object
call_factory(create, {"layer": layer, "slot": "model", "config": {...}})  # call
```

- `call_factory` injects keys such as `layer / slot / config` by signature; keys the
  factory does not declare are ignored automatically, so a factory of any style plugs in;
- a fully parameterless factory is called with zero arguments; non-introspectable
  callables (built-ins) are called with zero arguments;
- non-callables (module / instance / value) are returned as-is, not called.

### 27.4 The Slot Connector (ArchLayer)

#### 27.4.1 Positioning

Code location: `src/norpagent/arch/layer.py`. The module docstring's first sentence is
the definition: "Architecture layer (ArchLayer): the slot connector".

ArchLayer is the "building-block tray":

1. receives a set of slot values (keyword arguments / a config dict);
2. a slot left empty → uses the default implementation (library built-in logic,
   registered via `set_default`);
3. a slot filled with an address → calls the address resolver and assembles; after
   assembly `layer[slot]` hands back the implementation object directly, and
   `layer.describe()` prints the complete assembly manifest (observable).

#### 27.4.2 Data Structures

```python
self.config: Dict[str, Any]                # slot values (config dict merged with kwargs; kwargs win)
self._impls: Dict[str, Any]                # assembly result: slot -> implementation object
self._defaults: Dict[str, factory]         # default-implementation factories (ctx -> impl)
self._subconfigs: Dict[str, Dict]          # ;key=value clauses resolved out of addresses
self._connected: bool                      # whether connect() has run
```

#### 27.4.3 Core API

| Method | Effect |
|---|---|
| `set_default(slot, factory)` | registers the slot's default-implementation factory (the `mount_defaults` assembler calls it before connect) |
| `connect()` | assembles all slots; **idempotent** — calling again only mounts newly registered slots |
| `remount(slot, value=_RAISE)` | runtime hot mount: without a value, re-resolve with the current config (invalidating the module cache first); with None, clear the slot config back to default; any other value replaces the config and rebuilds immediately |
| `layer[slot]` / `get(slot, default)` | fetch the assembly result (`__getitem__` raises RuntimeError when not connected) |
| `subconfig(slot)` | fetch the extra config clause resolved out of that slot's address |
| `describe()` | print the assembly manifest: each slot's source (default / address / direct value) and implementation type |
| `is_connected()` | whether assembly has run |

#### 27.4.4 String Dispatch: The Four string_semantics

`_connect_slot` dispatches string values by the slot's `string_semantics`:

| Semantics | String-value handling |
|---|---|
| `address` | resolved as a module address (default semantics) |
| `name` | passed through as a registry component name (registration decided by the assembler) |
| `name_or_address` | component name first, module address second (the assembler decides in the registry context) |
| `literal` | literal value (level / path / log name) |

Since v0.9.1 every slot supports "address-first":

- name / name_or_address slots: the string is looked up in the registry first; if not
  found, it resolves as a module address;
- literal slots: a string in pure-address form (`is_address_like`) → loaded by address,
  otherwise kept as a literal;
- **dict key-value pairs of any slot**: a value that is a pure-address string →
  uniformly resolved by address into an object (`_resolve_dict_values` recurses to any
  depth; list elements are not resolved and keep literal semantics; the hooks slot's
  values are the callbacks themselves, and callbacks pointed at by an address stay
  as-is, not called).

Special case: for the frontend slot, a string value that is a `.html/.htm` file path
skips address resolution and is passed through to the assembler for "HTML-path direct
mount" (equivalent to `WebFrontend(html=...)`, 5.4).

#### 27.4.5 defer_factory: Deferring Instantiation

A slot with `defer_factory=True` (e.g. agent_runtime) **only resolves the address, does
not instantiate** during connect; the factory call is deferred to the engine-assembly
phase (`NorpEngine._build_agent`), when the registry / preset context is ready and the
full context is injected by signature.

#### 27.4.6 Hot Mount and Module-Cache Invalidation

```python
layer.remount("model", "myapp.models:v2")   # swap the implementation
layer.remount("model")                      # re-resolve with the current config (hot-reload edited code)
layer.remount("model", None)                # clear the config, fall back to the default logic
```

A string address passed to `remount` first runs two-step cache invalidation
(`_invalidate_address_module`):

1. delete the module's bytecode cache (`module.__cached__`'s .pyc) — otherwise, if a
   same-size file is rewritten within the same second, importlib may judge the "cache
   is still fresh" and re-importing would fetch the old code;
2. pop the `sys.modules` entry — the next resolution re-imports from disk.

Thus the hot-reload closed loop "edit code → remount → new code takes effect" holds.
Custom slots (registered via `register_slot`, 3.8) support remount too, resolving per
the spec at registration time; after `replace=True` hot-replaces a spec, remount
resolves per the new spec.

#### 27.4.7 Relationship with the Slot Table

The slot table (`norpagent.arch.slots`, `SLOT_SPECS`) itself is hot-pluggable: after
`register_slot()` registers a custom slot at runtime, connect / remount / describe /
set_default all work against the **live table at call time** — connect idempotently
mounts late-registered slots, and remount applies to new slots as well. SlotSpec fields
(name / protocol / default_address / string_semantics / factory_kwargs / defer_factory /
applier / remount_rebuild_agent) are covered in 25.10.2.

#### 27.4.8 Minimal Usage Example

```python
from norpagent.arch.layer import ArchLayer

layer = ArchLayer(async_loop="myapp.loop:create", preset="standard")
layer.connect()                 # assemble all slots
loop = layer["async_loop"]      # the connected loop system
print(layer.describe())         # assembly manifest (observable)
```

### 27.5 The Registry

#### 27.5.1 Positioning

Code location: `src/norpagent/kernel/registry.py`.

"Everything is a registered item": models / tools / sessions / sandboxes / schedulers /
UIs / plugins / presets / generic components are all registered and resolved by name.
AgentRuntime only interacts with the registry, so replacing any part never requires
kernel-code changes. The registry itself is part of the kernel and knows no concrete
implementation (docstring: "unaware of any concrete implementation").

#### 27.5.2 The 9 Namespaces

| Namespace | Internal dict | Register API | Resolve API |
|---|---|---|---|
| models | `_models` | `register_model(name, provider)` | `resolve_model(name)` (instance) |
| tools | `_tools` | `register_tool(name, tool)` | `resolve_tool(name)` (instance) |
| sessions | `_sessions` | `register_session(name, factory)` | `build_session(name)` (calls factory) |
| sandboxes | `_sandboxes` | `register_sandbox(name, factory)` | `build_sandbox(name)` (calls factory) |
| schedulers | `_schedulers` | `register_scheduler(name, factory)` | `build_scheduler(name)` (calls factory) |
| uis | `_uis` | `register_ui(name, adapter)` | `resolve_ui(name)` (instance) |
| plugins | `_plugins` | `register_plugin(plugin)` | `list_plugins()` (no single-fetch API) |
| presets | `_presets` | `register_preset(preset)` | `resolve_preset(name)` |
| components | `_components[kind]` | `register_component(kind, name, factory)` | `build_component(kind, name, workspace_root=None)` |

Key difference:

- **instances**: models / tools / UIs (resolve and use);
- **factories**: sessions / sandboxes / schedulers / generic components (freshly built on
  every build); `build_component` supports `workspace_root` auto-injection — passed in
  when the factory declares a same-named parameter or `**kwargs` (project management and
  other components locate projects through it).

Side effects of `register_plugin`: tools enter the tool table (same-name overwrite + log
hint); hooks subscribe to the bus (`self.bus.subscribe(fn, hook)`). `unregister_plugin`
does the reverse: unsubscribes hooks and removes the plugin record (tool entries remain —
the name-overwrite semantics mean re-mounting a same-name plugin naturally overwrites;
historical entries are unreachable if not in the preset tool set and do not affect
resolution).

#### 27.5.3 Query and Validation

| Method | Effect |
|---|---|
| `list_models() ... list_uis()` | sorted name lists per namespace |
| `list_components(kind=None)` | component listing: with a kind, the kind's name list; otherwise all groups |
| `tool_schemas(names=None)` | export the tools' OpenAI function schemas (all by default) |
| `validate_preset(preset)` | validate whether the components referenced by a preset are all present, returns `(missing, missing_tools)`; empty lists = usable |

`validate_preset` checks the model / session / sandbox / scheduler / ui / components /
tools branches; missing items are formatted as `"model=openai_compat"`,
`"component=vector_store:pg"`, easy to read and debug; AgentRuntime construction also
runs it first and raises `ComponentError` on any gap (fast fail).

#### 27.5.4 Thread Safety and Error Semantics

- all reads and writes go through `threading.RLock()`; registration and resolution are
  cross-thread safe;
- unregistered / wrong type → `ComponentError` (the message carries the list of
  available names);
- `register_preset` only accepts `Preset` instances, otherwise `ComponentError`.

#### 27.5.5 Relationship with EventBus / ArchLayer

```python
reg = Registry()          # creates an EventBus internally
reg.bus                   # the bus itself (shared with AgentRuntime as the same instance)
reg.hooks                 # lazily creates HookSystem(bus): the 9-layer hook view
reg.security              # security context (installed by norpagent.safe(); wholesale pluggable)
```

Assembly side (`runtime.mount`):

- `build_registry(layer)`: creates the registry + installs the built-in defaults
  (install_defaults);
- `apply_slot_overrides(reg, layer, ...)`: lands slot assembly results into the registry
  (preset-field overrides, component registration, custom-slot applier calls), called
  repeatedly on hot mount — **appliers must be re-entrancy safe** (record objects to
  unsubscribe in `ctx["meta"]`; repeated execution must not stack side effects, 25.10.3).

#### 27.5.6 Usage Example

```python
from norpagent import Registry

reg = Registry()
reg.register_tool("clock", ClockTool())
reg.register_session("memory", lambda: MemorySession())
reg.register_component("context_store", "fts5", lambda: Fts5Store())

sess = reg.build_session("memory")       # freshly built every time
tool = reg.resolve_tool("clock")         # instance fetched directly
store = reg.build_component("context_store", "fts5")
missing, missing_tools = reg.validate_preset(preset)
assert missing == [] and missing_tools == []
```

### 27.6 The Four Working Together: A Walkthrough of One Startup and One Hot Mount

#### 27.6.1 Startup Sequence (Inside npa())

```
1. ArchLayer(**slot_values)           the slot connector receives all slot values
                                      (config + kwargs merged)
2. mount_defaults(layer)              set_default registers each slot's built-in
                                      default logic
3. build_registry(layer)              create the Registry; install_defaults installs
                                      built-in components
4. apply_slot_overrides(reg, layer)   by priority task-level > remount > startup
                                      assembly > preset: override fields; component
                                      registration; custom-slot applier runs
5. layer.connect()                    assemble slot by slot:
                                      - value None   → default factory (ctx injected)
                                      - string       → resolve_address + call_factory
                                        (inject layer / slot / config by signature;
                                        config comes from the ;key=value clause)
                                      - dict         → recursive resolution of
                                        key-value addresses
                                      - defer_factory slots resolve but do not
                                        instantiate
6. NorpEngine._build_agent()          engine-assembly phase: defer_factory factories
                                      are called (registry / preset context ready) →
                                      AgentRuntime(reg, bus, ...)
7. AgentRuntime startup               on construction: self.bus = registry.bus;
                                      UI mounts the bus (bus.subscribe(ui.on_event),
                                      unsubscribed on shutdown); emits on_agent_init;
                                      task execution → emit / intercept
```

Key point: **the slot connector is in charge of "fitting", the registry of "recording",
the event bus of "communicating", the address resolver of "recognizing"** — in order,
the address resolver is called first (during assembly), the registry is filled
mid-assembly, and the bus runs throughout.

#### 27.6.2 Hot-Mount Sequence (npa.remount(slot, value))

```
1. remount(slot, value)               the slot connector: a string address first
                                      invalidates the module cache
                                      (delete .pyc + pop sys.modules)
2. _connect_slot(slot)                re-resolve / re-assemble that slot
3. apply_slot_overrides runs again    called repeatedly on the same registry →
                                      appliers re-entrancy safe
                                      (old subscriptions unsubscribed via ctx["meta"],
                                      preventing stacking)
4. plugin-like slots                  unregister_plugin unsubscribes old hooks →
                                      re-register the new plugin
5. when it takes effect               slots with remount_rebuild_agent=True hot-rebuild
                                      the AgentRuntime; other slots take effect on the
                                      next run() or only update extras, no rebuild
```

#### 27.6.3 Boundaries of the Four Exception Types

| Exception | Raised by | Trigger | Handling advice |
|---|---|---|---|
| `AddressError` | address resolver | address import failure / missing attribute / empty address | check the module path and attribute name; an address-like string never silently falls back |
| `ComponentError` | registry | unregistered / wrong type / wrong preset type | use `list_*()` to see available names; check the namespace is the right one |
| `SlotError` | slot table | illegal slot-table operation (register / unregister / illegal spec) | check SlotSpec fields and the reserved names (prompt / config) |
| `RuntimeError` | slot connector | `layer[slot]` before connect() | `layer.connect()` first; or use `layer.get(slot, default)` |

#### 27.6.4 Replacement Principles and a Checklist

Four principles for replacing any component (echoing 26.8):

1. **Change config first**: anything solvable with `npa(slot=...)` or `remount` needs
   no code change;
2. **Registry first**: register new components with `register_*`, then reference them
   in a preset;
3. **Address form first**: `pkg.mod[:attr]` gets factory injection, `;key=value` clauses
   and code hot reload in one shot; it is the recommended form;
4. **Assembly observable**: run `layer.describe()` before delivery to confirm the source
   is right.

Self-check list:

| # | Check item | Involved component |
|---|---|---|
| 1 | `layer.connect()` succeeds and `layer[slot]` is fetchable | slot connector |
| 2 | `layer.describe()` shows the source (default / address / direct value) | slot connector |
| 3 | address form: `import myapp.xxx` succeeds, the attribute exists | address resolver |
| 4 | an address-like string that fails to resolve raises `AddressError` (red line) | address resolver |
| 5 | `reg.list_*()` sees it and `resolve_*` fetches it | registry |
| 6 | `validate_preset` reports no gaps | registry |
| 7 | after subscribing, `emit` is received; a throwing subscriber does not break the main flow | event bus |
| 8 | mutating-hook return values take effect; HookVeto reaches the kernel | event bus |
| 9 | edit code → remount → new code takes effect | slot connector + address resolver |
| 10 | appliers re-entrancy safe (repeated remount does not stack side effects) | slot connector + registry |

---

## Appendix D Glossary

| Term | Definition |
|---|---|
| Address Function | the framework's core abstraction: filling a slot value with an "address" (module path / factory / instance) mounts it; not filling uses the default |
| Slot | a replaceable component position; `npa(...)`'s keyword-argument names are slot names |
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
| Safe Mode | `npa(safemode="on")`: loads only the minimal kernel, skips all plugins |
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

*NorpAgent Developer Manual · v0.9.5 · Copyright (c) 2026 xingluosama121, MIT Licensed*
