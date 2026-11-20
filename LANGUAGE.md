# Astlin 0.3 language reference

The [AI guide](AI.md) documents JSON, HTTP, chat/embeddings, RAG, and numeric APIs.

## Source and entry point

Astlin source is UTF-8 in files with the **`.ad`** extension. A program begins in
`fn main()`, which takes no parameters and returns `void` or an integer exit status
in `0..255`. Library modules can omit `main`. Only the root module's entry point runs.

Blocks use braces. Statements end at a newline, semicolon, or closing brace.
Newlines inside parentheses and square brackets are ignored. `#` starts a line
comment. Indentation is cosmetic. Identifiers contain ASCII letters, underscores,
and digits and cannot begin with a digit. Names are case-sensitive.

## Types and bindings

| Type | Meaning |
| --- | --- |
| `int` | Signed 64-bit integer, −9,223,372,036,854,775,808 through 9,223,372,036,854,775,807 |
| `float` | C `double`; binary64 on the validated target |
| `bool` | `true` or `false` |
| `str` | Immutable, NUL-free UTF-8 string |
| `list[T]` | Immutable homogeneous list; `T` is `int`, `float`, `bool`, or `str` |
| Named struct | Immutable value with declared fields |
| `json` | Immutable null/bool/number/string/array/object value with checked dynamic access |
| `void` | No function return value; not a parameter, local, or field type |

```text
let answer = 42
let label: str = "hello"
var counter = 0
counter = counter + 1
let empty: list[int] = []
```

`let` prevents reassignment; `var` permits reassignment to the same type. Initializers
are required. Empty lists require an annotation or an expected argument, return,
constructor-field, or assignment type. There are no uninitialized variables, nulls,
implicit numeric conversions, source-level pointers, or manual frees.

Scopes are lexical. An inner block can shadow an outer local, but two bindings in
the same scope cannot share a name. Built-ins, type names, module aliases, and the
current module's function/struct names cannot be reused for local bindings.

## Structs

```text
struct Address {
    city: str
}

struct Person {
    name: str
    age: int
    address: Address
    scores: list[int]
}

fn main() {
    let person = Person("Ada", 36, Address("London"), [10, 20])
    print(person.address.city)
}
```

Constructor arguments are positional, in field declaration order, and must match
their declared types. Fields are read-only, including when the containing binding
uses `var`. To change a value, construct another struct and reassign the binding.
Structs can contain scalars, scalar lists, JSON values, and other structs. Recursive struct
definitions, struct lists, methods, inheritance, and record equality are not supported.
Empty structs are allowed. Types have nominal identity: types declared in different
modules remain distinct even when their fields match.

## Modules

```text
import "geometry.ad" as geometry
import "std/math.ad" as math

fn main() {
    let point: geometry.Point = geometry.Point(3.0, 4.0)
    print(point, math.square(12))
}
```

Imports resolve relative to the importing file. `std/...` falls back to Astlin's
bundled standard-library directory when no relative file exists. Imports require an
explicit alias and `.ad` extension. Each module is loaded once per resolved path.
Cyclic imports fail with an import-chain diagnostic. There are no import-time statements
or initialization effects because top-level declarations are functions, structs, and imports.

A module exports its declared functions and structs through its alias, excluding
the `main` function. Imported aliases are private to that module; there are no
re-exports or wildcard imports. Files with matching declaration names have separate
namespaces. Relative imports can refer to sibling or parent directories. Manifest
entry/test paths, separately, must stay within their project directory.

Compiler and runtime errors in imported code report that code's own filename and line.

## Literals and operators

Integers are decimal; leading zeros do not change their base. Floating literals
include `1.0`, `2.`, and `3e-4`. Negative values use unary `-`. Out-of-range integer
and non-finite float literals are compiler errors.

Strings use double quotes, literal Unicode, and JSON escapes: `\n`, `\r`, `\t`,
`\b`, `\f`, `\"`, `\\`, `\/`, and `\uXXXX`. NUL, unpaired surrogates, and literal
newlines inside strings are rejected.

Precedence, from tightest to loosest:

| Operators | Meaning |
| --- | --- |
| `f(...)`, `value.field`, `value[i]` | Named call, field access, indexing |
| Unary `+`, `-`, `not` | Numeric sign, Boolean negation |
| `*`, `/`, `%` | Multiplication, division, integer remainder |
| `+`, `-` | Numeric addition/subtraction; `+` also joins two strings |
| `<`, `<=`, `>`, `>=` | Number or string ordering |
| `==`, `!=` | Scalar equality |
| `and` | Short-circuit Boolean conjunction |
| `or` | Short-circuit Boolean disjunction |

Binary operators associate left to right. Use `a < b and b < c`, not chained
comparisons. `not` has unary precedence: use `not (a == b)` to negate a comparison.
Operands, constructor fields, and function arguments evaluate left to right.
`and`/`or` evaluate the right operand only when needed. Conditions require `bool`;
there is no truthiness.

Arithmetic and numeric comparisons require matching numeric types. Convert explicitly
with `int()` or `float()` to avoid implicit precision loss in large-integer comparisons.
Integer overflow and zero division/remainder fail at runtime in all builds.
Integer `/` truncates toward zero; `%` has the dividend's sign. Thus `-7 / 3` is
`-2` and `-7 % 3` is `-1`. The minimum integer modulo `-1` is zero; division by `-1`
overflows. Floating division by either sign of zero also fails. Other floating
arithmetic follows the target and may produce infinity or NaN. `%` is integer-only.

Strings compare by content; ordering compares UTF-8 bytes without locale collation
or normalization. Lists and structs cannot be compared.

JSON values also cannot be compared with equality operators. `value["key"]` checks
object access; missing keys yield JSON null. `value[index]` checks array access and
bounds. JSON null is distinct from a native nullable pointer; use `json_kind` to inspect it.

## Functions and control flow

```text
fn factorial(n: int) -> int {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}

fn main() {
    var total = 0
    for n in range(1, 11) {
        if n == 3 { continue }
        total = total + n
    }
    while total > 0 {
        total = total - 1
        if total == 5 { break }
    }
    if total == 5 { print("five") } else { print(total) }
}
```

Parameters are typed, immutable local values. Omitting `-> T` means `-> void`.
Non-void functions must return on every statically proven path. A loop alone does
not prove a return. Void functions may use bare `return` or fall through.
Forward calls and mutual recursion are allowed. There are no overloads, closures,
nested functions, or function values. Structs copy by value; their immutable list
and string storage can be shared. Returning lists, strings, and structs is supported.

`if`/`else if`/`else` and `while` require Boolean conditions. `break` and `continue`
affect the nearest loop. `for x in list` snapshots the original list, even if its
binding is reassigned in the body. The loop variable is immutable and local to the body.

`range(stop)`, `range(start, stop)`, and `range(start, stop, step)` are supported only
directly in `for ... in`. Arguments are integers evaluated once. Start is inclusive,
stop exclusive; defaults are start `0` and step `1`. Negative steps work; zero fails.
A step that would exceed int64 ends the range safely.

## Lists and Unicode

Lists contain one scalar type and support bounds-checked indexing from `0` through
`len(xs)-1`. Strings support indexing by Unicode scalar value; `"é🌍"[1]` is `"🌍"`.
Negative indexes fail. There are no nested lists or item assignment.

`len(str)` counts Unicode scalar values, not bytes or grapheme clusters. `len("🌍")`
is `1` while `len("e\u0301")` is `2`. No normalization is performed.

All list operations below return immutable copies and leave their inputs unchanged.
They take linear time in the size copied. Repeated appends to a growing list can take
quadratic total time. List concatenation uses `concat`, not `+`.

## Built-in API

`T` below means a scalar list element type; the compiler checks consistency.

| Function | Behavior |
| --- | --- |
| `print(...) -> void` | Evaluate all arguments, print values separated by spaces, then newline. No arguments prints a blank line. |
| `input([prompt: str]) -> str` | Print/flush an optional prompt, read a UTF-8 line, remove LF/CRLF; return `""` at EOF. |
| `args() -> list[str]` | Program arguments, excluding the executable name. |
| `len(str or list[T]) -> int` | Unicode scalar count or list length. |
| `int(scalar) -> int` | Identity; truncate checked float; bool to 0/1; parse signed decimal string. |
| `float(scalar) -> float` | Identity; convert int/bool; parse a finite numeric string. |
| `str(scalar) -> str` | Scalar text, with `true`/`false` for bool. |
| `assert(bool) -> void` | Fail if false, including optimized builds. |
| `append(list[T], T) -> list[T]` | Copy with one extra element. |
| `updated(list[T], index: int, T) -> list[T]` | Copy with a replacement at a checked index. |
| `concat(list[T], list[T]) -> list[T]` | Concatenate two same-type lists. Also accepts two strings. |
| `slice(value, start: int, stop: int)` | Copy a string/list interval, inclusive start, exclusive stop; require `0 <= start <= stop <= len(value)`. |
| `split(str, separator: str) -> list[str]` | Split on literal, nonempty separator; preserve empty parts. |
| `join(list[str], separator: str) -> str` | Join strings; empty list yields empty string. |
| `trim(str) -> str` | Strip ASCII whitespace at both ends. |
| `replace(str, old: str, replacement: str) -> str` | Replace nonoverlapping literal matches; `old` must be nonempty. |
| `contains(str, part: str) -> bool` | Literal substring test. |
| `starts_with(str, prefix: str) -> bool` | Prefix test. |
| `ends_with(str, suffix: str) -> bool` | Suffix test. |
| `read_file(path: str) -> str` | Read complete UTF-8 text; fail on I/O errors, invalid UTF-8, or NUL. |
| `write_file(path: str, text: str) -> void` | Create/truncate a file and write text bytes; fail on I/O errors. |
| `file_exists(path: str) -> bool` | Whether filesystem metadata can be obtained; includes directories. |
| `abs(int or float)` | Same-type absolute value; minimum int overflows. |
| `min(number, number)`, `max(number, number)` | Matching numeric types; select via `<` or `>` respectively. |
| `sqrt(float) -> float` | Square root; negative arguments fail. |
| `pow(float, float) -> float` | Power; reported domain/range errors and nonfinite results fail. |
| `clock() -> float` | C process CPU time in seconds, not wall-clock time. |
| `gc_collect() -> void` | Collect unreachable managed allocations at this safe call site. |
| `heap_bytes() -> int` | Currently allocated managed payload bytes, including uncollected garbage. |
| `heap_objects() -> int` | Count of allocated managed blocks, including zero-length lists and uncollected garbage. |

File paths and ordinary filesystem effects follow the operating system. File writes
are not atomic; they overwrite existing contents. Text I/O does not provide a binary
API. Files do not undergo newline conversion. `file_exists` returns false on metadata
errors, including some permission failures. Empty input and EOF are indistinguishable.

String-to-number conversions reject whitespace, trailing junk, empty strings, and
out-of-range input. Float parsing uses C `strtod` syntax, including hexadecimal
floats; NaN/infinity and reported range errors (including some subnormal/underflow
inputs) are rejected. Large int-to-float conversions can round.

Float display chooses a short round-tripping decimal from candidates of 1–17
significant digits. Lists use brackets; string elements have surrounding quotes.
Structs display their name and fields. Display output is for humans and is not a
serialization format; quoted list elements are not escaped.

## Bundled modules

`import "std/math.ad" as math` provides `square(int) -> int`,
`clamp(int, lower: int, upper: int) -> int`, and `factorial(int) -> int`.
Clamp asserts ordered bounds; factorial asserts nonnegative input. Arithmetic remains
checked, so excessively large results fail.

`import "std/text.ad" as text` provides `lines(str) -> list[str]` (literal LF split,
preserving empty parts), `is_blank(str) -> bool` (ASCII whitespace), and
`repeat(str, count: int) -> str` (asserts nonnegative count). Repetition currently
uses repeated concatenation and can be quadratic.

## Collection and errors

The runtime uses a nonmoving, single-threaded mark-and-sweep collector. Compiler-emitted
roots trace strings, list payloads, string-list elements, and managed struct fields.
Roots are removed on scope exit, function return, `break`, and `continue`.
Generated temporaries can keep values alive until block exit. Automatic collections
run after rooting managed values and at statement/iteration safe points when allocation
exceeds a threshold. The threshold includes block metadata and a minimum payload
charge, so zero-length lists also trigger collection. It is at least 64 KiB and is
updated relative to live allocation cost after collection.
`gc_collect()` forces collection. JSON trees, container storage, and string/number
payloads are traced as well. Other remaining allocations are freed at process exit.

Allocation lookup during tracing uses an open-addressed hash index with expected
constant-time lookup. Sweeps rebuild it and can shrink it after a large transient heap.
Collection still pauses execution and grows with live data and allocation count.
Root tables, allocation/hash metadata, C stack space, and temporary I/O buffers are
excluded from `heap_bytes()`. There is no hard allocation budget, stack-overflow
interception, thread support, or real-time pause guarantee.

Compiler errors show filename, line, column, source excerpt, and caret. Runtime errors
show filename and line, then exit status `1`; there are no language stack traces or
exception recovery yet. Compiler/build failures also return `1`. Native signals are
forwarded as conventional `128 + signal` statuses by `run`.
