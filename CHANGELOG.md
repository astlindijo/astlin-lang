# Astlin release history

## 0.2.0

- Added relative `.ad` imports, aliases, shared module loading, bundled-module fallback,
  import cycle diagnostics, and collision-resistant generated names.
- Added immutable nominal structs, positional constructors, nested fields, imported
  struct types, and struct display.
- Replaced exit-only allocation retention with automatic tracing garbage collection,
  compiler-managed roots, explicit collection, and managed-heap statistics.
- Added immutable list append/update/concatenation/slicing, Unicode string indexing
  and slicing, splitting/joining/replacement, whitespace trimming, and substring tests.
- Added UTF-8 file reading/writing, existence checks, program arguments, math, and CPU time.
- Added standard-library modules written in Astlin for math and text helpers.
- Added project initialization/manifests, a native `.ad` test runner with timeouts,
  environment diagnostics, and dependency-aware output protection.
- Added an importable compiler package, `python3 -m astlin`, installable `astlin` command,
  dependency-free wheel/source builds, and VS Code syntax grammar.
- Added integration and sanitizer stress tests, examples, and updated language documentation.

## 0.1.0

- Initial lexer, parser, static checks, C backend, and runtime.
- Introduced functions, scalar types, homogeneous lists, loops, checked arithmetic,
  terminal I/O, and source diagnostics.
- Established the name **Astlin** and source extension **`.ad`**.
