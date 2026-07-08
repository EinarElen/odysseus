---
name: "python-to-rust-rewrite"
description: "Use when the user wants existing Python code rewritten into Rust, ported module-by-module, or re-implemented as a Rust crate, CLI, library, or service while preserving behavior. Trigger for requests about translating Python idioms to Rust ownership, typing, error handling, async, packaging, tests, or performance-oriented rewrites from Python to Rust."
---

# Python To Rust Rewrite

Rewrite Python code into production-quality Rust with behavior preserved unless the user asks for intentional redesign. Favor clear ownership, explicit types, idiomatic error handling, and tests that prove parity with the Python behavior.

## Workflow

1. Read the Python entrypoints, data flow, imports, and tests before writing Rust.
2. Decide the Rust target shape early: single file, Cargo crate, library, CLI, web service, or mixed workspace.
3. Preserve observable behavior first. Improve architecture only when the Python design is clearly unsafe, too dynamic for Rust, or the user asks for a redesign.
4. Translate one boundary at a time:
   - Data models and constants
   - Pure functions and transformations
   - IO and side effects
   - Concurrency or async boundaries
   - CLI, HTTP, or integration layer
5. Add or port tests to lock in parity. If the Python project already has tests, treat them as the executable spec.
6. Explain any unavoidable semantic drift, especially around mutability, numeric behavior, exceptions, iteration order, and concurrency.

## Rewrite Rules

- Prefer safe Rust and standard library types first.
- Map Python exceptions to `Result<T, E>` and use a concrete error type unless the project already standardizes on `anyhow`.
- Replace `dict`/`list`/`set` with the narrowest Rust collection that matches the required semantics.
- Make ownership visible. Use borrowing before cloning.
- Preserve ordering only when Python behavior depends on it.
- For optional values, use `Option<T>` instead of sentinel values.
- For sum-type behavior that Python encoded with strings or shape checks, introduce enums.
- Keep async only when the Python code is IO-bound or already async. Do not introduce async into purely CPU-bound code without a reason.
- If the Python code relies on runtime monkey-patching, dynamic attributes, or reflection, isolate that behavior and redesign explicitly instead of faking Python semantics everywhere.

## Decision Points

### Dynamic Python features

If the source depends on `getattr`, `setattr`, duck typing, decorators with hidden side effects, or heterogenous containers, stop and define a static Rust model before rewriting implementation details.

### Performance rewrites

If the user wants Rust for speed, identify whether the hot path is parsing, allocation, loops, IO, or concurrency. Only optimize the bottleneck; do not turn the whole port into premature micro-optimization.

### FFI vs full rewrite

If preserving Python integration matters more than a clean standalone Rust app, consider a Rust core with Python bindings. Otherwise, produce a normal Rust crate and port tests.

## Output Expectations

- Deliver compilable Rust, not line-by-line transliteration.
- Keep module boundaries obvious and Cargo structure conventional.
- Port representative fixtures and tests whenever possible.
- Call out missing information if exact parity depends on undocumented Python behavior.

## Reference Map

Read [references/rewrite-heuristics.md](references/rewrite-heuristics.md) when you need concrete mappings for:

- Python collections, classes, exceptions, and protocols
- Async and concurrency ports
- Common crate choices
- Test-porting guidance
