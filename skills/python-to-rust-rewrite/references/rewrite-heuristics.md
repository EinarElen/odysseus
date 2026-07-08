# Python To Rust Rewrite Heuristics

Use this file only when the rewrite needs concrete translation patterns.

## Type Mapping

| Python | Rust |
| --- | --- |
| `str` | `String` or `&str` |
| `bytes` | `Vec<u8>` or `&[u8]` |
| `int` | `i64`, `u64`, `isize`, or domain-specific integer type |
| `float` | `f64` |
| `bool` | `bool` |
| `list[T]` | `Vec<T>` |
| `tuple[...]` | tuple or struct |
| `dict[K, V]` | `HashMap<K, V>` or `BTreeMap<K, V>` |
| `set[T]` | `HashSet<T>` or `BTreeSet<T>` |
| `None` | `Option<T>` |

Choose narrower types when the domain allows it.

## Classes And Objects

- Python data containers usually become `struct`s.
- Behavior with distinct variants usually becomes `enum` plus `impl`.
- Use traits only when polymorphism is genuinely needed. Do not replace every duck-typed function with trait objects by default.
- Class methods that mutate shared state often need `&mut self`, interior mutability, or a redesigned ownership boundary.

## Errors

- Replace raised exceptions with `Result`.
- Keep library errors typed. Use `thiserror` for structured errors when the codebase benefits from explicit variants.
- Use `anyhow` mainly in binaries, glue code, or quick migrations where ergonomic propagation matters more than public API precision.

## Iteration And Functional Patterns

- Python comprehensions usually become iterator chains or explicit loops. Choose the version that remains readable.
- Generator behavior can map to iterators, channels, or collected vectors depending on lifetime and API needs.
- Avoid deeply nested iterator chains when a loop is clearer.

## Async And Concurrency

- `asyncio` code usually maps to `tokio`.
- `asyncio.gather` often becomes `try_join!`, `join!`, or spawned tasks.
- `threading` may map to `std::thread`, `rayon`, channels, or async tasks depending on workload.
- Shared mutable state should be redesigned carefully; do not default blindly to `Arc<Mutex<_>>`.

## Serialization And External Interfaces

- JSON, YAML, and TOML models usually benefit from `serde` derives.
- HTTP clients often map cleanly to `reqwest`.
- CLI tools usually fit `clap`.
- Filesystem-heavy utilities often need `std::fs`, `camino` only if path ergonomics are painful, and `tempfile` for tests.

## Testing

- Port Python unit tests into `#[test]` functions or integration tests.
- Preserve table-driven cases from `pytest.mark.parametrize` as arrays of fixtures iterated in Rust tests.
- If the Python behavior is loosely specified, add golden inputs and outputs before rewriting.

## Red Flags

- Implicit coercions
- Reliance on insertion order without tests proving it
- Mutation during iteration
- Floating-point equality assumptions
- Broad `except Exception` blocks masking behavior
- Hidden global state
