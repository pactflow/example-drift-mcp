# Example Drift MCP Provider

[![Build](https://github.com/pactflow/example-drift-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/pactflow/example-drift-mcp/actions/workflows/build.yml)

An example of using [Drift](https://docs.pactflow.io/docs/drift) to verify that a
[Model Context Protocol](https://modelcontextprotocol.io) server still offers the
tools it promised, and that those tools still behave as documented — then
publishing that proof to [PactFlow](https://pactflow.io).

The server is a small notes service, implemented twice — once over Streamable
HTTP and once over stdio — using only the Python standard library.

## What problem does this solve?

An MCP server's tool surface *is* its API. Agents read `tools/list`, decide what
to call from the descriptions and schemas they find, and act. But allow-lists,
routing rules, approval policies and audit mappings all hardcode tool names and
safety annotations — and those break silently when the server moves.

The failure mode is quiet, which is what makes it dangerous. Remove a tool and
the agent doesn't crash; it just does something else. Flip a `destructiveHint`
from `true` to `false` and every approval policy built on it silently stops
asking. Narrow an `inputSchema` and calls that used to work start getting
rejected.

Drift compares a declared MCP definition against a **live server** and tells you
what moved. The question it answers is *"can the agents built on this server
still do what they were authorised to do, at the risk level that was
approved?"* — not *"is this byte-identical to last week?"*

## Two kinds of checking

**Surface drift** — compare the declaration against what the server advertises,
invoking nothing:

```yaml
drift_all:
  target: notes:drift/all
```

**Behavioural conformance** — actually call the tools, and check results against
the declared `outputSchema`, arguments against `inputSchema`, and everything
against MCP's own content-block types:

```yaml
createNote_Success:
  target: notes:tool/create_note
  parameters:
    arguments: { title: "Shopping", body: "milk, eggs" }
  expected:
    result:
      structuredContent: { title: "Shopping" }
```

### The warning/failure split is deliberately asymmetric

A server offering **less** than it declared fails. A server offering **more**
warns.

That asymmetry exists because MCP consumers are agents that re-read `tools/list`
every session and adapt. Adding a tool doesn't break them. Editing a description
is how you *improve* them — in MCP the description is part of the interface,
since it's what the model reads to decide. But a removed tool, a narrowed
schema, or a changed safety annotation breaks the things built *around* the
agent.

Teams that need the warnings to fail can opt in — see [Stricter policies](#stricter-policies).

## Layout

```
├── notes.mcp.yaml                # The MCP definition — the contract
├── drift/
│   └── notes.testcases.yaml      # Drift test cases (both transports)
├── server/
│   ├── server.py                 # Notes server, Streamable HTTP
│   └── stdio_server.py           # Same server, stdio transport
├── scripts/
│   └── assert_drift_detected.py  # Checks `make test_drift` failed for the right reason
├── Makefile
└── .github/workflows/build.yml
```

`notes.mcp.yaml` is the artefact you'd author in
[SmartBear Swagger Studio](https://swagger.io/tools/swagger-studio/). Drift
verifies the running server against it.

The same testcase file drives both transports — only the provider URL differs.

## Running it locally

### Prerequisites

- Python 3.9+ (no third-party packages — the servers are standard library only)
- [Drift](https://docs.pactflow.io/docs/drift/getting-started/installation) **2608.3.0 or later**, which is when the `mcp` plugin shipped
- A PactFlow account with Drift enabled, and `PACT_BROKER_BASE_URL` / `PACT_BROKER_TOKEN` set
- Docker (only for publishing to PactFlow)

### Verify the server

```sh
make test
```

Starts the server, runs `drift verify` against it, shuts it down. Results land in
`output/`:

- `output/results/verification.*.result` — the bundle published to PactFlow
- `output/reports/junit/verification-result.xml` — a JUnit report for CI

### The other modes

| Command | What it does |
|---|---|
| `make test` | Streamable HTTP, current protocol era — the run CI publishes |
| `make test_legacy` | Same tests against a server using the older `initialize` handshake |
| `make test_stdio` | Same tests over the stdio transport |
| `make test_safe` | Skips operations tagged `destructive` |
| `make test_drift` | Runs a deliberately-drifted server; **passes only when Drift flags the expected findings** |
| `make fake_ci` | Full CI flow locally, including publishing |
| `make server` | Runs the HTTP server in the foreground |

### Watch it catch drift

```sh
make test_drift
```

This starts the server with `--drift`, which makes four changes to the surface,
and asserts that Drift notices:

| Severity | What changed | Why |
|---|---|---|
| **Fail** | `read_note` `readOnlyHint` flipped `true` → `false` | A safety annotation changed — approval policies built on it are now wrong |
| **Fail** | `create_note` newly requires `author` | Narrows the contract; calls that worked now fail |
| **Fail** | `delete_note` removed | Capability withdrawn |
| **Warn** | `create_note` gained an undeclared `author` property | Widening, not breaking |

Plus `createNote_Success` fails its `outputSchema` check, because the drifted
server returns `body` as a number where the schema declares a string.

Everything else still passes — only the tool surface moved.

Note what this target asserts. `drift verify` exiting non-zero proves nothing on
its own: it also exits non-zero when it can't authenticate or can't reach the
server. So the exit code is ignored, and
[`scripts/assert_drift_detected.py`](scripts/assert_drift_detected.py) checks the
JUnit report to confirm Drift actually ran and flagged `drift_all` and
`createNote_Success` specifically. Without that, the CI job would pass while
testing nothing.

## Transports

### Streamable HTTP

```sh
python3 server/server.py --port 8099 &
drift verify --server-url http://127.0.0.1:8099/mcp --test-files drift/notes.testcases.yaml
```

### stdio

Most MCP servers today are launched as a local subprocess speaking
newline-delimited JSON-RPC over stdin/stdout. Drift supports this with a
`stdio:///<command>?arg=…&arg=…` provider URL:

```sh
drift verify \
  --server-url "stdio:///python3?arg=server/stdio_server.py" \
  --test-files drift/notes.testcases.yaml
```

There's no server to start — Drift spawns it, **once per run**, and holds it
open for every operation. The command and each `arg` are percent-decoded
independently, and repeated `arg` parameters are passed in order.

### Protocol eras

Drift negotiates the protocol era itself. Against a modern server it uses
`server/discover`; against an older one it falls back to the `initialize`
handshake and carries the `Mcp-Session-Id` through the run. Your testcases don't
change:

```sh
python3 server/server.py --legacy --port 8098 &
drift verify --server-url http://127.0.0.1:8098/mcp --test-files drift/notes.testcases.yaml
```

## The two error channels

MCP has two distinct failure modes, and conflating them is the commonest testing
mistake. This example contrasts them directly:

| Testcase | Channel | Wire shape | Asserted with |
|---|---|---|---|
| `createNote_MissingArguments` | **Protocol** — the request was rejected | JSON-RPC `error`, code `-32602` | `expected.error.code` |
| `readNote_Unknown` | **Tool** — the tool ran and reported failure | HTTP 200 + `result.isError: true` | `expected.result.isError` |

## Safety gating

`delete_note` declares `annotations.destructiveHint: true`, so the plugin
automatically tags its operation `destructive`. Skip it with:

```sh
drift verify ... --tags '!destructive'   # or: make test_safe
```

## Operations reference

**Surface drift** — `drift/all` is the union of the four scoped checks, so use
one style or the other, never both:

| Target | Checks |
|---|---|
| `notes:drift/discover` | Server is reachable and an era can be negotiated |
| `notes:drift/all` | The whole declared surface matches the server |
| `notes:drift/capabilities` | Advertised capabilities match what's actually served |
| `notes:drift/tools` | Declared tools match `tools/list` |
| `notes:drift/resources` | Declared resources match `resources/list` |
| `notes:drift/resourceTemplates` | Declared templates match `resources/templates/list` |
| `notes:drift/prompts` | Declared prompts match `prompts/list` |

**Invocation:**

| Target | Checks |
|---|---|
| `notes:tool/create_note` | Result matches the declared `outputSchema`; missing arguments produce a protocol error |
| `notes:tool/read_note` | A known note returns its body; an unknown one produces a *tool* error |
| `notes:tool/delete_note` | Tagged `destructive`; skipped with `--tags '!destructive'` |
| `notes:resource/notes://index` | The index resource is readable |
| `notes:prompt/summarise_note` | The prompt renders with an argument |

## Stricter policies

Description changes and undeclared tools warn by default. To make them fail, add
a `drift.config.toml`:

```toml
[plugin_config.mcp]
# Fail rather than warn when a tool/prompt description differs from the declaration.
fail-on-description-change = true
# Fail rather than warn when the server advertises a tool, resource, resource
# template or prompt the definition does not declare.
fail-on-undeclared = true
# Fail rather than warn when a tool result carries a field its outputSchema does
# not declare, or arguments carry a field an inputSchema does not declare.
fail-on-undocumented-output = true
fail-on-undocumented-input = true
```

Two things to watch:

- The table is `[plugin_config.mcp]`, not `[plugins.mcp]`.
- The file must live somewhere Drift searches — the OS config directory,
  `~/.drift`, or the Drift home directory (`--drift-home-dir`). **Not** the
  working directory you run the tests from. `--plugin-value` does not deliver
  plugin config.

All four default to `false`. Capability and safety findings — missing tools,
narrowed `required`/`type`, changed annotations — always fail and aren't
configurable.

## Publishing to PactFlow

The definition is published as the provider contract, with the Drift results
attached as evidence:

```sh
pactflow publish-provider-contract \
  notes.mcp.yaml \
  --provider pactflow-example-drift-mcp \
  --provider-app-version "$(git rev-parse --short HEAD)" \
  --branch "$(git rev-parse --abbrev-ref HEAD)" \
  --specification mcp \
  --content-type application/yaml \
  --verification-exit-code $EXIT_CODE \
  --verification-results output/results/verification.*.result \
  --verification-results-content-type application/vnd.smartbear.drift.result \
  --verifier drift
```

The contract publishes whether verification passed or failed —
`--verification-exit-code` records which — so a failing build shows the provider
as unverified rather than leaving stale results in place.

> **Note:** PactFlow stores `mcp` provider contracts but does not compare them
> against consumer contracts, so this example doesn't use `can-i-deploy`. It is
> provider-side verification only.

## CI

[`.github/workflows/build.yml`](.github/workflows/build.yml) runs three jobs on
every push and pull request:

1. **Verify and publish** — the main run, published to PactFlow
2. **Compatibility** — the same testcases against the legacy era and the stdio transport
3. **Detects a drifted server** — asserts Drift catches the deliberately-drifted server, so the other jobs can't be vacuously green

It needs two repository settings:

| Setting | Type | Value |
|---|---|---|
| `PACT_BROKER_BASE_URL` | Variable | Your PactFlow URL, e.g. `https://you.pactflow.io` |
| `PACT_BROKER_TOKEN` | Secret | A PactFlow API token with write access |

## Known limitations

- **Surface schema comparison is shallow.** Only the top-level `required` set,
  the `properties` key set, and each property's `type` are compared — not full
  JSON Schema subsumption. Strict invocation validation catches the behavioural
  consequence of most deeper drift, but only for tools a testcase actually
  exercises. A server that narrows its advertised schema without yet returning
  data that violates the old one is invisible to both checks.
- **Name definitions `*.mcp.yaml` / `*.mcp.yml` / `*.mcp.json`.** MCP documents
  carry no in-band version marker like OpenAPI's `openapi: 3.0.0`, so the
  filename is what identifies them. Remote sources served as
  `application/mcp+yaml` / `application/mcp+json` work too.
- **This compares the definition to a live server, not to a previous version of
  itself.** Drop a tool from both the contract and the server and this passes.
- **A never-closing SSE stream will hang the run** — response bodies are buffered whole.
- **One spawned server per provider URL per run**; a server that dies mid-run isn't restarted.
- **Resource templates aren't directly invocable.** Target a concrete URI with
  `parameters.uri` instead.

## Related examples

- [example-drift-grpc](https://github.com/pactflow/example-drift-grpc) — the same idea for a gRPC provider
- [example-bi-directional-provider-drift](https://github.com/pactflow/example-bi-directional-provider-drift) — Drift verifying an OpenAPI provider, wired into bi-directional contract testing
- [Drift documentation](https://docs.pactflow.io/docs/drift)

## License

[MIT](LICENSE)
