"""Integration tests for modules, records, collection, libraries, and project tools."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_astlin as base
from astlin import AstlinError, compile_source, diagnostic

ROOT, CC = base.ROOT, base.CC


class FeatureDiagnostics(unittest.TestCase):
    def test_struct_and_library_errors(self):
        cases = [
            ('struct P { x: int; x: str } fn main() {}', "duplicate field"),
            ('struct P { child: P } fn main() {}', "recursive struct"),
            ('struct A { b: B } struct B { a: A } fn main() {}', "recursive struct"),
            ('struct bool {} fn main() {}', "reserved"),
            ('struct P { x: missing } fn main() {}', "unknown type"),
            ('struct P { x: int } fn main() { P(true) }', "expected int"),
            ('struct P { x: int } fn main() { P() }', "expects 1 fields"),
            ('struct P { x: int } fn main() { print(P(1).y) }', "has no field"),
            ('fn main() { let n = 1; print(n.x) }', "requires a struct"),
            ('fn main() { append([1], true) }', "expected int"),
            ('fn main() { append(1, 2) }', "requires a list"),
            ('fn main() { updated([1], false, 2) }', "expected int"),
            ('fn main() { concat([1], [true]) }', "expected int"),
            ('fn main() { slice(1, 0, 1) }', "string or list"),
            ('fn main() { slice("x", 0.0, 1) }', "expected int"),
            ('fn main() { join([1], ",") }', "expected str"),
            ('fn main() { read_file(1) }', "expected str"),
            ('fn main() { sqrt(4) }', "expected float"),
            ('fn main() { min(1, 2.0) }', "expected int"),
            ('fn main() { abs(true) }', "requires numbers"),
            ('fn main() { args(1) }', "expects 0 arguments"),
            ('fn main() { gc_collect(1) }', "expects 0 arguments"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaises(AstlinError) as caught:
                compile_source(source)
            self.assertIn(message, caught.exception.message)

    def test_import_errors_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.ad"
            helper = root / "helper.ad"
            cases = [
                ('import "missing.ad" as m\nfn main() {}', "cannot import"),
                ('import "helper.txt" as m\nfn main() {}', ".ad extension"),
                ('import "helper.ad" as m\nimport "helper.ad" as m\nfn main() {}', "alias"),
                ('import "helper.ad" as m\nfn main() { m.nope() }', "has no export"),
                ('import "helper.ad" as m\nfn main() { let p: m.Missing = 1 }', "unknown type"),
            ]
            helper.write_text('fn helper() -> int { return 1 }')
            for source, message in cases:
                with self.subTest(source=source), self.assertRaises(AstlinError) as caught:
                    compile_source(source, main)
                self.assertIn(message, caught.exception.message)
            helper.write_text('import "main.ad" as main\nfn helper() {}')
            main.write_text('import "helper.ad" as h\nfn main() {}')
            with self.assertRaises(AstlinError) as caught:
                compile_source('import "helper.ad" as h\nfn main() {}', main)
            self.assertIn("cyclic import", caught.exception.message)
            helper.write_text('fn helper() {\n let n: int = false\n}')
            with self.assertRaises(AstlinError) as caught:
                compile_source('import "helper.ad" as h\nfn main() {}', main)
            rendered = diagnostic(caught.exception, '', main)
            self.assertIn(str(helper) + ':2:', rendered)
            self.assertIn('let n: int = false', rendered)
            helper.write_text('fn helper() -> int { return 1 }')
            with self.assertRaises(AstlinError) as caught:
                compile_source('import "helper.ad" as h\nfn main() { let h = 1 }', main)
            self.assertIn('reserved', caught.exception.message)


@unittest.skipUnless(CC, "a C compiler is required")
class FeaturePrograms(unittest.TestCase):
    def execute(self, source, filename="test.ad", arguments=(), sanitize=False):
        with tempfile.TemporaryDirectory(prefix="astlin-features-") as directory:
            path = Path(directory)
            cfile, binary = path / "main.c", path / "main"
            cfile.write_text(compile_source(source, filename), encoding="utf-8")
            flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                     "-Wno-unused-variable", "-Wno-unused-parameter"]
            if sanitize:
                flags += ["-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            result = subprocess.run([CC, *flags, str(cfile), '-o', str(binary), '-lm'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=20)

    def test_records_and_nested_fields(self):
        result = self.execute('''struct Name { text: str }
        struct Person { name: Name; age: int; tags: list[str] }
        struct Empty {}
        fn make(n: int) -> Person { return Person(Name("user " + str(n)), n, [str(n)]) }
        fn main() {
            let person = make(42)
            gc_collect()
            print(person, person.name.text, person.tags[0], Empty())
        }''', sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Person(name: Name(text: user 42), age: 42, tags: ["42"]) user 42 42 Empty()\n')

    def test_list_operations_preserve_values(self):
        result = self.execute('''fn main() {
            let a = [1, 2]
            print(a, append(a, 3), updated(a, 0, 9), concat(a, [3, 4]), slice(a, 1, 2))
            let s = [str(10)]
            let b = append(s, str(20))
            gc_collect()
            print(b, updated(b, 1, "x"), concat(b, []), slice(b, 0, 0))
            print(append([true], false), updated([1.5], 0, 2.5))
        }''', sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '[1, 2] [1, 2, 3] [9, 2] [1, 2, 3, 4] [2]\n["10", "20"] ["10", "x"] ["10", "20"] []\n[true, false] [2.5]\n')

    def test_text_math_and_arguments(self):
        result = self.execute(r'''fn main() {
            print(split("::a::::b::", "::"))
            print(join([], ","), join(["a", "b"], "--"))
            print(trim(" \t🌍 \n"), replace("a-b-a", "a", "xyz"))
            print(contains("abc", "b"), starts_with("abc", "ab"), ends_with("abc", "bc"))
            print(slice("é🌍x", 0, 2), "é🌍x"[1], slice("abc", 3, 3))
            print(sqrt(9.0), pow(2.0, 10.0), abs(-7), abs(-2.5), min(3, 4), max(3.0, 4.0))
            print(args(), clock() >= 0.0)
        }''', arguments=['hello', '🌍', '--flag'], sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '["", "a", "", "b", ""]\n a--b\n🌍 xyz-b-xyz\ntrue true true\né🌍 🌍 \n3 1024 7 2.5 3 4\n["hello", "🌍", "--flag"] true\n')

    def test_file_io_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.txt"
            literal = json.dumps(str(path))
            source = f'''fn main() {{
                let path = {literal}
                assert(not file_exists(path))
                write_file(path, "Hello 🌍\\n")
                assert(file_exists(path))
                print(read_file(path))
            }}'''
            result = self.execute(source, sanitize=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_text(), 'Hello 🌍\n')
            path.write_bytes(b'bad\xff\n')
            result = self.execute(f'fn main() {{ read_file({literal}) }}', sanitize=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('valid UTF-8', result.stderr)
            path.unlink()
            result = self.execute(f'fn main() {{ read_file({literal}) }}')
            self.assertEqual(result.returncode, 1)
            self.assertIn('could not open', result.stderr)

    def test_module_namespaces_and_shared_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'one.ad').write_text('struct P { n: int }\nfn value() -> P { return P(1) }')
            (root / 'two.ad').write_text('import "one.ad" as one\nfn value() -> int { return one.value().n + 1 }')
            result = self.execute('''import "one.ad" as a
            import "one.ad" as alias
            import "two.ad" as b
            fn value() -> int { return 99 }
            fn m1_value() -> int { return 100 }
            struct m1_P { text: str }
            fn main() {
                let p: a.P = alias.P(7)
                print(a.value().n, b.value(), value(), p.n, m1_value(), m1_P("ok").text)
            }''', root / 'main.ad', sanitize=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '1 2 99 7 100 ok\n')

    def test_runtime_diagnostics_keep_module_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper.ad'
            helper.write_text('fn value() -> int { return 1 / 0 }')
            source = 'import "helper.ad" as h\nfn main() { print(h.value()) }'
            result = self.execute(source, root / 'main.ad')
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(helper) + ':1: runtime error:', result.stderr)
            helper.write_text('fn value() -> int { return 1 }')
            source = 'import "helper.ad" as h\nfn main() { print(h.value()); print(1 / 0) }'
            result = self.execute(source, root / 'main.ad')
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(root / 'main.ad') + ':2: runtime error:', result.stderr)

    def test_gc_nested_calls_loop_exits_and_live_values(self):
        result = self.execute('''struct Box { s: str; values: list[str] }
        fn keep(s: str, n: int) -> str {
            gc_collect()
            if n == 0 { return s }
            return keep(s, n - 1)
        }
        fn main() {
            let box = Box(str(123), [str(10), str(20)])
            var current = [str(1)]
            for i in range(30000) {
                current = [str(i)]
                if i % 2 == 0 { continue }
                assert(true and len(trim(str(i))) > 0)
                if false or len(str(i)) > 0 { let discarded = split(str(i), "0") }
            }
            var n = 0
            while len(str(n)) < 6 {
                n = n + 1
                if n < 10 { continue }
                if n == 1000 { break }
            }
            gc_collect()
            assert(heap_bytes() < 10000)
            print(box.s, join(box.values, ","), current[0], keep(str(42), 10), n)
        }''', sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '123 10,20 29999 42 1000\n')

    def test_new_runtime_guards(self):
        cases = [
            ('"abc"[3]', 'string index out of bounds'),
            ('slice("🌍", 0, 2)', 'string slice out of bounds'),
            ('slice([1], 1, 0)', 'list slice out of bounds'),
            ('updated([1], -1, 2)', 'out of bounds'),
            ('split("abc", "")', 'separator cannot be empty'),
            ('replace("abc", "", "x")', 'separator cannot be empty'),
            ('sqrt(-1.0)', 'nonnegative'),
            ('pow(-1.0, 0.5)', 'domain or range'),
            ('abs(-9223372036854775808)', 'overflow'),
        ]
        for expression, message in cases:
            with self.subTest(expression=expression):
                result = self.execute(f'fn main() {{ print({expression}) }}', sanitize=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn('AddressSanitizer', result.stderr)

    def test_zero_size_lists_trigger_automatic_collection(self):
        result = self.execute('''fn main() {
            var values: list[int] = []
            for i in range(100000) { values = [] }
            assert(heap_objects() < 2500)
            gc_collect()
            assert(heap_objects() < 10)
            print(len(values))
        }''', sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '0\n')

    def test_project_lifecycle_and_test_failures(self):
        def cli(arguments, cwd):
            return subprocess.run([sys.executable, str(ROOT / 'astlin.py'), *arguments],
                                  cwd=cwd, capture_output=True, text=True, timeout=30)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'my-app'
            result = cli(['init', str(project)], directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            for arguments, output in [(['run'], 'Hello from Astlin!'), (['test'], '1 passed; 0 failed'),
                                      (['build'], 'Built'), (['doctor'], 'OK: C11')]:
                result = cli(arguments, project)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(output, result.stdout)
            self.assertTrue((project / 'build/my-app').is_file())
            main = project / 'src/main.ad'
            original = main.read_text()
            result = cli(['init', '.'], project)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(main.read_text(), original)
            main.write_text('fn main() { print(args()) }')
            result = cli(['run', '--', 'a', '--flag'], project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('["a", "--flag"]', result.stdout)
            (project / 'tests/test_main.ad').write_text('fn main() { assert(false) }')
            result = cli(['test'], project)
            self.assertEqual(result.returncode, 1)
            self.assertIn('1 failed', result.stdout)
            self.assertIn('assertion failed', result.stderr)
            (project / 'tests/test_main.ad').write_text('fn main() { while true {} }')
            result = cli(['test', '--timeout', '0.05'], project)
            self.assertEqual(result.returncode, 1)
            self.assertIn('timed out', result.stderr)
            config = json.loads((project / 'astlin.json').read_text())
            config['entry'] = '../outside.ad'
            (project / 'astlin.json').write_text(json.dumps(config))
            result = cli(['run'], project)
            self.assertEqual(result.returncode, 1)
            self.assertIn('inside the project', result.stderr)

    def test_output_cannot_overwrite_imported_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper.ad'
            main = root / 'main.ad'
            helper.write_text('fn value() -> int { return 42 }')
            original = helper.read_text()
            main.write_text('import "helper.ad" as h\nfn main() { print(h.value()) }')
            for command in ('build', 'emit-c'):
                result = subprocess.run([sys.executable, str(ROOT / 'astlin.py'), command,
                                         str(main), '-o', str(helper)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn('must not overwrite', result.stderr)
                self.assertEqual(helper.read_text(), original)

    @unittest.skipUnless(importlib.util.find_spec('pip'), 'pip required for distribution installation test')
    def test_offline_wheel_and_source_installation(self):
        from tools import build_backend
        with tempfile.TemporaryDirectory(prefix='astlin-distribution-') as directory:
            root = Path(directory)
            wheel = root / build_backend.build_wheel(root)
            sdist = root / build_backend.build_sdist(root)
            for i, distribution in enumerate((wheel, sdist)):
                with self.subTest(distribution=distribution.name):
                    installed = root / f'installed-{i}'
                    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                                             '--no-cache-dir', '--target', str(installed), str(distribution)],
                                            cwd=root, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    program = root / 'main.ad'
                    program.write_text('import "std/math.ad" as math\nfn main() { print(math.square(12)) }')
                    env = dict(os.environ, PYTHONPATH=str(installed))
                    result = subprocess.run([sys.executable, str(installed / 'bin/astlin'), 'run', str(program)],
                                            cwd=root, env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, '144\n')


if __name__ == '__main__':
    unittest.main()
