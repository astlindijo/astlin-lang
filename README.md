# Astlin

**Astlin is a statically typed programming language with `.ad` source files and native executables.**

Version **0.2.0** includes a working compiler, tracing garbage collector, multi-file
imports, immutable structs, a small standard library, project tools, tests, offline
installation, and VS Code syntax support. The compiler uses Python's standard library;
the programs it builds run independently of Python.

```text
import "std/math.ad" as math

struct Person {
    name: str
    age: int
}

fn main() {
    let person = Person("Astlin", 1)
    var scores = [10, 20]
    scores = append(scores, math.square(5))
    print(person.name, scores)
}
```

## Get started

Requirements: **Python 3.10+** and **Clang or GCC** available as `cc`.
The validated platform is macOS ARM64. Other targets have not yet been validated.

From this checkout, no installation is needed:

```sh
python3 -m astlin doctor
python3 -m astlin run
python3 -m astlin test
python3 -m astlin build
./build/astlin-demo
```

`python3 astlin.py` is also supported. To install the `astlin` command in your Python
environment, including offline:

```sh
python3 -m pip install --no-index .
astlin --version
```

The build backend needs no third-party build packages. A C compiler is needed only
to build or run Astlin programs, not to install the compiler or check source types.

## Create your own project

After installing the command:

```sh
astlin init my-app
cd my-app
astlin run
astlin test
astlin build
./build/my-app
```

`init` creates `astlin.json`, `src/main.ad`, and `tests/test_main.ad`, and refuses to
overwrite existing project files. The manifest selects the entry point and test directory:

```json
{
  "name": "my-app",
  "version": "0.1.0",
  "entry": "src/main.ad",
  "test_dir": "tests"
}
```

With no source argument, commands use the manifest in the current directory.
Without a manifest, the entry defaults to `main.ad`. Manifest paths must stay inside
the project directory. The manifest does not declare or download dependencies.

## Compiler commands

```sh
astlin run examples/features.ad -- hello --flag
astlin run examples/greet.ad
astlin check examples/features.ad
astlin build examples/features.ad -o build/features
astlin emit-c examples/features.ad -o build/features.c
astlin run examples/features.ad --sanitize
astlin test tests/ad --sanitize --timeout 10
astlin doctor --cc clang
```

`args()` returns program arguments after `--`, excluding the executable name.
`run` uses a temporary executable and forwards standard input/output and the exit status.
`build` creates output directories as needed and produces a standalone binary. It
defaults to `build/<project-name>` when using a manifest entry, or the source basename
when a file is supplied. Build and C emission refuse to overwrite any imported source.
`--cc` and `--sanitize` are available for `run`, `build`, and `test`.

`test` compiles and runs each `test_*.ad` file as a separate program. Use `assert` for
checks. Failed assertions, compilation errors, nonzero exit statuses, and timeouts
fail the test run; tests receive EOF on standard input. Native test executables must
define their own `main` functions.

## Language features

| Area | Implemented |
| --- | --- |
| Types | `int`, `float`, `bool`, `str`, homogeneous scalar lists, immutable structs |
| Bindings | Inferred locals, `let` for immutable bindings, `var` for reassignment |
| Functions | Typed parameters/returns, recursion, forward calls, lexical scopes |
| Control flow | `if`/`else`, `while`, list/range loops, `break`, `continue` |
| Modules | Relative `.ad` imports with aliases, shared-import deduplication, cycle errors |
| Runtime checks | Integer overflow, division by zero, list/string bounds, conversions, UTF-8 |
| Memory | Compiler-tracked roots and automatic mark-and-sweep collection |
| Library | Terminal/file I/O, command-line arguments, strings, list operations, math |
| Tooling | Project initialization, native test runner, environment diagnostics, source diagnostics |

The language prioritizes small syntax, explicit mutation, checked operations, and
native distribution. Its performance and capabilities have **not** been shown to
outperform Python, C, C++, Java, or C# across workloads.

See [LANGUAGE.md](LANGUAGE.md) for exact semantics and the built-in API, and
[CHANGELOG.md](CHANGELOG.md) for the release changes.

Release notes and publication steps are in [releases/v0.2.0.md](releases/v0.2.0.md)
and [RELEASING.md](RELEASING.md). `make release` builds the distributions, verifies
their contents, and writes `dist/SHA256SUMS`; public publication is a separate step.

## Imports and data types

```text
# geometry.ad
struct Point {
    x: float
    y: float
}

fn distance(a: Point, b: Point) -> float {
    let dx = a.x - b.x
    let dy = a.y - b.y
    return sqrt(dx * dx + dy * dy)
}
```

```text
# main.ad
import "geometry.ad" as geometry

fn main() {
    let origin = geometry.Point(0.0, 0.0)
    let target: geometry.Point = geometry.Point(3.0, 4.0)
    print(geometry.distance(origin, target))
}
```

## Memory and safety

The compiler registers live strings, lists, and struct fields as roots. It inserts
garbage-collection safe points after rooting values, between statements, and at loop iterations. Unreachable
managed allocations can be reclaimed while a program is running, and remaining
allocations are freed at exit. Generated temporary values may remain rooted until
their enclosing block ends. `gc_collect()` requests a collection; `heap_bytes()`
reports managed payload bytes, excluding runtime metadata and the native stack.

The collector is single-threaded and uses linear allocation lookup. Large live heaps
can cause pauses. There is no hard memory limit or stack-overflow recovery. Runtime
checks and sanitizer tests provide evidence for the implemented behavior; this is
not a formal safety proof or a security audit.

List operations such as `append` and `updated` return copies. Repeatedly appending
to a growing list can be quadratic. Strings are UTF-8; indexing and slicing count
Unicode scalar values, with no grapheme segmentation or normalization.

## Examples and development

- `examples/features.ad`: imports, structs, lists, strings, arguments.
- `examples/tour.ad`: original syntax and numeric features.
- `examples/greet.ad`: interactive input.
- `examples/file_tool.ad`: read a UTF-8 text file supplied as an argument.
- `examples/primes.ad`, `examples/fibonacci.ad`: numeric programs.
- `tests/ad/`: tests written in Astlin, including a garbage-collection stress test.

```sh
make test          # compiler and CLI integration tests
make test-native   # Astlin tests with address/undefined-behavior sanitizers
make package       # local wheel and source archive in dist/
```

VS Code syntax support is in [editors/vscode](editors/vscode/README.md). It supplies
highlighting, bracket pairing, and comments; semantic completion and editor diagnostics
are future work. The extension has not been published or installed automatically.

## Implementation and remaining work

```text
.ad modules → lexer/parser → namespace/type checks → C generation → C compiler → executable
```

- `astlin/compiler.py`: source loading, syntax, checking, code generation, CLI.
- `astlin/runtime.h`: collector, checked operations, strings, lists, I/O, math.
- `astlin/std/`: reusable library modules written in Astlin.
- `tools/build_backend.py`: dependency-free distribution builder.
- `tests/`: static checks, native integration, CLI, and runtime tests.

Current limits include one-dimensional scalar lists, immutable positional structs,
no recursive struct types, no module re-exports, no closures, exceptions, interfaces,
user generics, async, threads, dictionaries, binary-file API, or foreign-function API.
There is no language package registry, formatter, language server, or debugger
integration yet. Integer values are fixed-width and numeric mixing is explicit.
The syntax and APIs remain experimental; compatibility between releases is not guaranteed.
