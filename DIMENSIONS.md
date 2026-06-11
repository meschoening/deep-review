# Review Dimensions

The same 9 dimensions are checked on every `/deep-review` run. Dimension agents read this file directly: the severity taxonomy below applies to every finding, and each agent applies the brief and checklist of its own numbered section.

## Severity taxonomy

- **critical** — Security exploit, data loss, or crash on common input. Must fix before merge.
- **high** — Correctness bug or contract violation that will fire in normal use.
- **medium** — Design problem with concrete impact (perf, maintainability, future bugs). Should fix.
- **low** — Maintainability or clarity issue. Nice to fix.
- **nit** — Style or preference. Optional.

Severity rates the impact assuming the finding is real. Confidence (`high` / `medium` / `low`, a separate field on every finding) rates how sure you are it is real. Hedge with confidence, never with severity — don't pad severity to seem useful, and don't downgrade it because you're unsure.

## Dimensions

### 1. Correctness & edge cases

Off-by-one, null/undefined handling, integer overflow, empty collections, unicode, timezone/date arithmetic, floating-point comparisons, default values, branch coverage of conditionals, return-value handling.

Checklist:
- All logical branches reachable; no dead arms or unreachable defaults?
- Boundary values: empty / null / zero / max / negative / single-element?
- Every error path leaves the system in a consistent state?
- Returned types and ranges match the docstring/types/contract?
- Implicit conversions (str↔int, truthy/falsy, NaN, `==` vs `===`) doing the right thing?

### 2. Security

Injection (SQL, shell, path traversal, template), authn/authz, secret handling, deserialization, SSRF, CSRF, XSS, cryptography misuse, dependency CVEs, supply chain, log/error info leakage, file permissions.

Checklist:
- Any user-controlled input reaching a sink (exec, query, fs path, redirect, eval, regex)?
- Secrets hardcoded, logged, in error messages, or committed to the repo?
- Authorization checks present on every privileged path; no IDOR?
- Crypto: secure random, mode/padding, key handling, no homegrown primitives?
- Dependencies pinned and free of known CVEs? Lockfile updated?

### 3. Concurrency & error handling

Race conditions, deadlocks, unhandled rejections/exceptions, error swallowing, retry safety, idempotency, partial failure, cancellation/timeout, resource leaks under failure.

Checklist:
- Shared state accessed without locking, atomicity, or single-owner discipline?
- Errors caught and silently dropped (`except: pass`, ignored Promise, ignored Result)?
- Async paths: every promise/future awaited, every channel drained, every cancel token honored?
- Operations idempotent where they may be retried? Effects bounded under partial failure?
- Resources (locks, file handles, subprocesses, db connections) released on every path?

### 4. Performance & resource use

N+1 queries, accidental quadratic loops, memory leaks, unbounded buffers, missing pagination, hot-path allocation, blocking I/O on async paths, repeated work that could be hoisted.

Checklist:
- Loops over user-sized inputs without bound or pagination?
- Repeated work (re-parsing, re-fetching, re-compiling) that could be hoisted or memoized?
- Connections, file handles, subprocesses always closed; no obvious leaks?
- Hot paths: avoidable allocations, syscalls, or sync I/O in async contexts?
- Data structure choice fits the access pattern (list-vs-set, map-vs-array)?

### 5. API/contract & backwards compatibility

Public function signatures, HTTP/RPC contracts, on-disk formats, env var names, CLI flags, error codes, schema changes, deprecation paths.

Checklist:
- Any public surface changed without a migration path or deprecation?
- New fields optional, old fields preserved, defaults backward-compatible?
- Error/return types still in the documented set?
- Wire/file format readable by the previous version (or a documented migration exists)?
- Behavior changes flagged for callers (semver, changelog, comment)?

### 6. Tests

Coverage of new behavior, brittleness, mocking strategy, missing negative/edge cases, flaky patterns (timeouts, time-based, network, ordering).

Checklist:
- Every new branch / error path has a test?
- Tests assert behavior, not implementation detail (no over-mocking)?
- Negative cases (errors, edge inputs, empty, large) covered?
- Any tests that will go flaky (real time, real network, ordering, race)?
- Test setup/teardown leaves no state on disk or in shared resources?

### 7. Architecture & coupling

Module boundaries, dependency direction, circular imports, layering violations, misplaced logic, tight coupling, abstraction smell, premature generalization.

Checklist:
- Does this code live in the right module? Would a new reader find it where they'd expect?
- Cross-layer reaches (UI touching DB, infra touching domain)?
- New circular imports or tight coupling between previously independent modules?
- Premature abstractions, unused generics, or duplicated abstractions of the same concept?
- Public/private boundary respected (no reaching into internals)?

### 8. Readability & maintainability

Naming, function length, dead code, misleading comments, magic numbers, inconsistency with surrounding code.

Checklist:
- Names self-explain or require comments to understand?
- Functions doing one thing, or many; nesting / cyclomatic complexity reasonable?
- Dead/unused code, commented-out blocks, or "TODO" left dangling?
- New code consistent with neighboring style (formatting, idioms, error patterns)?
- Comments explain *why*, not *what*; no stale or misleading docs?

### 9. Dependencies & build hygiene

New dependencies justified, license-compatible, maintained; lockfile drift; build script changes; CI implications; reproducibility; vendored vs upstream.

Checklist:
- Each new dep necessary, maintained (recent commits), license-compatible?
- Lockfile updated and consistent across package managers if multiple?
- Build/CI scripts changed in expected ways; no skipped checks (`--no-verify`, etc.)?
- Reproducible build preserved (no network in build, pinned versions, deterministic outputs)?
- New binaries / generated files committed appropriately (or correctly gitignored)?
