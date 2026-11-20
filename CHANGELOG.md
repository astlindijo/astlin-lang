# Astlin release history

## 0.3.0 — AI toolkit (development)

- Added a first-class immutable `json` type, object/array access, strict parsing,
  recoverable parsing, serialization, scalar conversions, and collector support.
- Replaced linear managed-pointer lookup with a resizing hash index so tracing large
  JSON/vector object graphs avoids scanning every allocation for each reference.
- Added optional libcurl-backed native HTTP with deadlines, verified TLS, decoded
  response-size limits, no automatic redirects, and header validation.
- Added environment configuration, bounded sleeps, and friendly explicit failures.
- Added finite float64 vector dot/norm/cosine, stable softmax/top-k, and CPU matrix multiplication.
- Added Unicode-aware overlapping character chunks, exact cosine retrieval, BM25 lexical
  retrieval, model-tagged JSON indexes, persistence, and bounded source context.
- Added `std/ai/http`, `llm`, `rag`, `pipeline`, and `tensor` modules, including chat,
  indexed/batched embeddings, bounded retries, structured results, and source-bearing answers.
- Added offline RAG and configured LLM project templates, `doctor --ai`, runnable examples,
  and native/sanitizer tests against local mock model servers.
- Added [AI.md](AI.md) documenting contracts, provider protocols, cost/network behavior,
  retrieval limits, and the distinction between orchestration and GPU model training.

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
