"""End-to-end compiler tests; programs execute as native machine code."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from astlin import VERSION, AstlinError, compile_source, diagnostic

CC = shutil.which(os.environ.get("ASTLIN_TEST_CC", "cc"))


class StaticChecks(unittest.TestCase):
    def rejects(self, source, message):
        with self.assertRaises(AstlinError) as caught:
            compile_source(source)
        self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_bad_programs(self):
        cases = [
            ("", "entry point"),
            ("fn main(x: int) {}", "no parameters"),
            ("fn main() -> str { return \"x\" }", "return void or int"),
            ("fn main() {} fn main() {}", "already defined"),
            ("fn print() {} fn main() {}", "reserved"),
            ("fn main() { let x = 1; x = 2 }", "immutable"),
            ("fn main() { var x = 1; x = true }", "expected int"),
            ("fn main() { let x: int = 1.5 }", "expected int"),
            ("fn main() { let x = 1; let x = 2 }", "already defined"),
            ("fn main() { print(x) }", "unknown variable"),
            ("fn main() { unknown() }", "unknown function"),
            ("fn main() { if 1 {} }", "expected bool"),
            ("fn main() { while 1 {} }", "expected bool"),
            ("fn main() { break }", "inside a loop"),
            ("fn main() { continue }", "inside a loop"),
            ("fn main() { let x = [] }", "empty list needs a type"),
            ("fn main() { let x = [1, true] }", "expected int"),
            ("fn main() { let x = [[1]] }", "nested lists"),
            ("fn main() { print([1][true]) }", "expected int"),
            ("fn main() { print(1[0]) }", "indexing requires a list"),
            ("fn main() { for n in 1 {} }", "list or range"),
            ("fn main() { range(3) }", "directly in a for"),
            ("fn main() { for x in range() {} }", "one, two, or three"),
            ("fn main() { for x in range(1.0) {} }", "expected int"),
            ("fn main() { print(1 + 1.0) }", "same type"),
            ("fn main() { print(9007199254740993 == 9007199254740992.0) }", "explicit float"),
            ("fn main() { print([1] == [1]) }", "cannot compare"),
            ("fn main() { print(true + false) }", "arithmetic requires"),
            ("fn main() { print(1.0 % 2.0) }", "requires integers"),
            ("fn main() { print(not 1) }", "expected bool"),
            ("fn main() { print(-true) }", "requires a number"),
            ("fn main() { len(1) }", "string or list"),
            ("fn main() { int([1]) }", "scalar"),
            ("fn main() { assert(1) }", "expected bool"),
            ("fn main() { input(1) }", "expected str"),
            ("fn main() { input(\"a\", \"b\") }", "zero arguments or one"),
            ("fn main() { let x = print(1) }", "void value"),
            ("fn main() { return 1 }", "void function"),
            ("fn main() -> int { return }", "expected int"),
            ("fn f() -> int { if true { return 1 } } fn main() {}", "every path"),
            ("fn f() {} fn main() { print(f()) }", "void value"),
            ("fn f(x: int) {} fn main() { f() }", "expects 1 arguments"),
            ("fn f(x: int) {} fn main() { f(true) }", "expected int"),
            ("fn f(x: int, x: int) {} fn main() {}", "already defined"),
            ("fn main() { let x: nope = 1 }", "unknown type"),
            ("fn main() { let x: list[list] = [] }", "list elements"),
            ("fn main() { print(9223372036854775808) }", "64-bit"),
            ("fn main() { print(1e999) }", "finite float64"),
            ("fn main() { print(@) }", "unexpected character"),
            ("fn main() { print(\"bad\\q\") }", "invalid string"),
            ("fn main() { print(\"\\u0000\") }", "NUL"),
            ("fn main() { print(\"\\ud800\") }", "Unicode"),
            ('fn main() { print("unfinished)', "unterminated"),
            ('fn main() { print("a\nb") }', "literal newline"),
            ("fn main() { print(1)", "close the block"),
            ("fn main() { let x = 1 let y = 2 }", "newline or ';'"),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                self.rejects(source, message)

    def test_diagnostic_position(self):
        source = "fn main() {\n    let x: int = true\n}"
        error = self.rejects(source, "expected int")
        rendered = diagnostic(error, source, Path("demo.ad"))
        self.assertIn("demo.ad:2:9: error:", rendered)
        self.assertIn("let x: int = true", rendered)
        self.assertIn("^", rendered)

    def test_large_integer_is_handled(self):
        self.rejects("fn main() { print(" + "9" * 5000 + ") }", "64-bit")

    def test_generated_c_is_self_contained(self):
        code = compile_source('fn main() { print("ok") }', 'a"b.ad')
        self.assertIn("#include <stdint.h>", code)
        self.assertNotIn('#include "runtime.h"', code)
        self.assertIn("static void ast_fn_main(void)", code)


@unittest.skipUnless(CC, "a C compiler is required for native tests")
class NativePrograms(unittest.TestCase):
    def execute(self, source, stdin="", filename="test.ad"):
        with tempfile.TemporaryDirectory(prefix="astlin-test-") as directory:
            path = Path(directory)
            cfile = path / "program.c"
            binary = path / "program"
            cfile.write_text(compile_source(source, filename), encoding="utf-8")
            built = subprocess.run([CC, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                                    "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter",
                                    str(cfile), "-o", str(binary), "-lm"], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            return subprocess.run([str(binary)], input=stdin, capture_output=True, text=True, timeout=10)

    def output(self, source, expected):
        result = self.execute(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)

    def test_arithmetic_and_precedence(self):
        self.output('''fn main() {
            print(2 + 3 * 4, (2 + 3) * 4, -7 / 3, -7 % 3)
            print(0.1, 1.5 + 2.5, 7.0 / 2.0, -2.5)
            print(-9223372036854775808, 9223372036854775807)
            print(-9223372036854775808 % -1)
            print(+10, not false)
        }''', '14 20 -2 -1\n0.1 4 3.5 -2.5\n-9223372036854775808 9223372036854775807\n0\n10 true\n')

    def test_recursion_and_forward_declarations(self):
        self.output('''fn main() { print(fact(10), even(12)) }
        fn fact(n: int) -> int {
            if n <= 1 { return 1 } else { return n * fact(n - 1) }
        }
        fn even(n: int) -> bool {
            if n == 0 { return true } else { return odd(n - 1) }
        }
        fn odd(n: int) -> bool {
            if n == 0 { return false } else { return even(n - 1) }
        }''', '3628800 true\n')

    def test_lists_all_types_and_contextual_empty(self):
        self.output('''fn empty() -> list[int] { return [] }
        fn take(xs: list[float]) -> int { return len(xs) }
        fn main() {
            let a = [1, 2, 3]
            let b = [1.5, 2.5]
            let c = [true, false]
            let d = ["hi", "there"]
            print(a, b, c, d)
            print(a[1], b[0], c[0], d[1])
            print(empty(), take([]))
            var e: list[str] = []
            e = ["x"]
            print(e)
        }''', '[1, 2, 3] [1.5, 2.5] [true, false] ["hi", "there"]\n2 1.5 true there\n[] 0\n["x"]\n')

    def test_ranges_break_continue_and_extremes(self):
        self.output('''fn main() {
            for x in range(5) {
                if x == 1 { continue }
                if x == 4 { break }
                print(x)
            }
            for x in range(5, 0, -2) { print(x) }
            for x in range(9223372036854775806, 9223372036854775807, 2) { print(x) }
            for x in range(-9223372036854775807, -9223372036854775808, -2) { print(x) }
            for x in range(0) { print("wrong") }
            for x in range(0, 10, -1) { print("wrong") }
        }''', '0\n2\n3\n5\n3\n1\n9223372036854775806\n-9223372036854775807\n')

    def test_while_nested_loops_and_shadowing(self):
        self.output('''fn main() {
            let x = 99
            var n = 0
            while n < 3 {
                n = n + 1
                if n == 2 { continue }
                for x in [10, 20] { print(n, x) }
            }
            if true { let x = "inner"; print(x) }
            print(x)
        }''', '1 10\n1 20\n3 10\n3 20\ninner\n99\n')

    def test_iteration_snapshots_list_binding(self):
        self.output('''fn main() {
            var xs = [1, 2, 3]
            for x in xs { print(x); xs = [] }
            print(xs)
        }''', '1\n2\n3\n[]\n')

    def test_short_circuit_and_left_to_right(self):
        self.output('''fn item(n: int) -> int { print(n); return n }
        fn plus(a: int, b: int) -> int { return a + b }
        fn main() {
            print(false and 1 / 0 == 0, true or 1 / 0 == 0)
            print(plus(item(1), item(2)))
            print(item(3) + item(4))
            print([item(5), item(6)])
        }''', 'false true\n1\n2\n3\n3\n4\n7\n5\n6\n[5, 6]\n')

    def test_strings_escapes_unicode_and_delimiter_literals(self):
        self.output(r'''fn main() {
            print(")", "]", "}", "true", "fn", "or", "<end>")
            print("say \"hi\"", "a\\b", "a\nb", "\u03bb")
            print(len("🌍café"), len(""), len("e\u0301"))
            print("a" + "b", "abc" == "abc", "a" != "b", "a" < "b")
        }''', ') ] } true fn or <end>\nsay "hi" a\\b a\nb λ\n5 0 2\nab true true true\n')

    def test_conversions(self):
        self.output('''fn main() {
            print(int("-42"), int(3.9), int(true), float(7), float("2.5"))
            print(str(42) + "!", str(0.1), str(false), int(3), float(2.0), str("ok"))
            print(float(true), int(false), len(str(123)))
        }''', '-42 3 1 7 2.5\n42! 0.1 false 3 2 ok\n1 0 3\n')

    def test_if_else_if_and_main_status(self):
        result = self.execute('''fn main() -> int {
            if false { return 1 }
            else if true { print("selected"); return 7 }
            else { return 2 }
        }''')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'selected\n')

    def test_interactive_input(self):
        result = self.execute('''fn main() {
            let name = input("Name? ")
            print(name, len(name))
            print(int(input()) + 1)
            print(len(input()))
        }''', "José 🌍\r\n41\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Name? José 🌍 6\n42\n0\n')
        result = self.execute('fn main() { print(len(input())) }', "x" * 10000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "10000\n")
        result = self.execute('fn main() { input() }', "a\0b\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("valid UTF-8 without NUL", result.stderr)

    def test_comments_and_multiline_expressions(self):
        self.output('''# leading comment
        fn main()
        {
            let xs = [
                1, # item
                2,
            ]
            print(
                xs,
                (1 +
                2),
            )
            print()
        }''', '[1, 2] 3\n\n')

    def test_runtime_guards(self):
        cases = [
            ("9223372036854775807 + 1", "overflow in addition"),
            ("-9223372036854775808 - 1", "overflow in subtraction"),
            ("9223372036854775807 * 2", "overflow in multiplication"),
            ("-(-9223372036854775808)", "overflow in negation"),
            ("-9223372036854775808 / -1", "overflow in division"),
            ("1 / 0", "division by zero"),
            ("1 % 0", "remainder by zero"),
            ("1.0 / 0.0", "division by zero"),
            ("[1][1]", "out of bounds"),
            ("[1][-1]", "out of bounds"),
            ("int(9223372036854775808.0)", "int64 range"),
            ("int(1e308 * 2.0)", "int64 range"),
            ('int("abc")', "invalid integer"),
            ('int("9223372036854775808")', "invalid integer"),
            ('int(" 1")', "invalid integer"),
            ('float("nan")', "invalid float"),
            ('float("1e999")', "invalid float"),
            ('float("1.2abc")', "invalid float"),
        ]
        for expression, message in cases:
            with self.subTest(expression=expression):
                result = self.execute(f"fn main() {{\n print({expression})\n}}")
                self.assertEqual(result.returncode, 1)
                self.assertIn("test.ad:2: runtime error:", result.stderr)
                self.assertIn(message, result.stderr)
        for source, message in [
            ("fn main() { assert(false) }", "assertion failed"),
            ("fn main() { for n in range(0, 1, 0) {} }", "step cannot be zero"),
            ("fn main() -> int { return 256 }", "exit code"),
        ]:
            with self.subTest(source=source):
                result = self.execute(source)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_examples(self):
        for path in sorted((ROOT / "examples").glob("*.ad")):
            with self.subTest(example=path.name):
                result = self.execute(path.read_text(encoding="utf-8"), "Ada\n36\n", filename=path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout)

    def test_cli(self):
        with tempfile.TemporaryDirectory(prefix="astlin-cli-") as directory:
            path = Path(directory)
            source = path / "hello.ad"
            binary = path / "hello"
            cfile = path / "hello.c"
            source.write_text('fn main() { print("CLI works") }', encoding="utf-8")
            commands = [
                (["check", str(source)], "OK:"),
                (["run", str(source)], "CLI works"),
                (["build", str(source), "-o", str(binary)], "Built"),
                (["emit-c", str(source), "-o", str(cfile)], ""),
                (["--version"], f"Astlin {VERSION}"),
            ]
            for arguments, expected in commands:
                result = subprocess.run([sys.executable, str(ROOT / "astlin.py"), *arguments],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
            self.assertTrue(cfile.is_file())
            self.assertEqual(subprocess.check_output([str(binary)], text=True), "CLI works\n")
            result = subprocess.run([sys.executable, str(ROOT / "astlin.py"), "build", str(source),
                                     "-o", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not overwrite", result.stderr)


if __name__ == "__main__":
    unittest.main()
