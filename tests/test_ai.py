"""Native AI integration tests. All provider calls use a local mock server."""
import contextlib
import http.server
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import test_astlin as base
from astlin import AstlinError, compile_source
from astlin.compiler import build_native

ROOT, CC = base.ROOT, base.CC


class AiTypeChecks(unittest.TestCase):
    def test_json_and_ai_signatures(self):
        cases = [
            ('fn main() { let value: json = 1 }', 'expected json'),
            ('fn main() { json_parse(1) }', 'expected str'),
            ('fn main() { json_set(json_object(), "x", 1) }', 'expected json'),
            ('fn main() { json_parse("{}")[true] }', 'int or str'),
            ('fn main() { vector_dot([1], [2]) }', 'expected float'),
            ('fn main() { vector_topk([1.0], 1.0) }', 'expected int'),
            ('fn main() { http_request("GET", "http://localhost", "", "", 1) }', 'expected json'),
            ('fn main() { let value: list[json] = [] }', 'list elements'),
            ('fn main() { print(json_null() == json_null()) }', 'cannot compare'),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaises(AstlinError) as caught:
                compile_source(source)
            self.assertIn(message, caught.exception.message)


@unittest.skipUnless(CC, 'a C compiler is required')
class AiNativeTests(unittest.TestCase):
    def execute(self, source, *, sanitize=True, arguments=(), environment=None):
        with tempfile.TemporaryDirectory(prefix='astlin-ai-test-') as directory:
            binary = Path(directory) / 'program'
            build_native(compile_source(source, ROOT / 'tests/ai-test.ad'), binary, CC, sanitize)
            env = dict(os.environ)
            # Never use a user's configured model endpoint or real API key in tests.
            for name in ('ASTLIN_AI_BASE_URL', 'ASTLIN_AI_API_KEY', 'ASTLIN_CHAT_MODEL', 'ASTLIN_EMBED_MODEL'):
                env.pop(name, None)
            env.update(environment or {})
            return subprocess.run([str(binary), *arguments], input='', capture_output=True,
                                  text=True, env=env, timeout=30)

    def test_json_roundtrip_and_value_semantics(self):
        source = r'''struct Payload { data: json }
        fn make() -> json { return json_parse("{\"a\":[1,true,null,\"🌍\"],\"large\":9223372036854775807}") }
        fn main() {
            let original = make()
            let updated_value = json_set(original, "a", json_push(original["a"], json_string("extra")))
            let payload = Payload(updated_value)
            gc_collect()
            print(json_dump(original))
            print(payload.data, json_kind(payload.data), json_as_int(original["large"]))
            print(json_as_bool(original["a"][1]), json_kind(original["missing"]), json_has(original, "missing"))
            print(json_from_floats([0.1, 2.5]), json_from_strings(["a", "🌍"]))
            print(json_to_floats(json_parse("[1,2.5]")))
            print(json_flatten(json_parse("[[1],[2,3],[]]")))
            print(json_is_int(json_parse("1")), json_is_int(json_parse("1.5")))
        }'''
        result = self.execute(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(json.loads(lines[0]), {'a': [1, True, None, '🌍'], 'large': 9223372036854775807})
        self.assertIn('9223372036854775807', lines[1])
        self.assertIn('extra', lines[1])
        self.assertEqual(lines[2], 'true null false')
        self.assertEqual(lines[4:], ['[1, 2.5]', '[1,2,3]', 'true false'])

    def test_json_unicode_escape_and_large_integer_preservation(self):
        documents = [
            {'quote': '"\\\n\r\t\b\f', 'unicode': 'é🌍 e\u0301', 'empty': ''},
            [True, False, None, -9223372036854775808, 9223372036854775808, 1e-120],
            {'outer': [{'nested': [1, 2, 3]}]},
        ]
        literals = ', '.join(json.dumps(json.dumps(doc, ensure_ascii=True)) for doc in documents)
        result = self.execute(f'fn main() {{ for text in [{literals}] {{ print(json_dump(json_parse(text))) }} }}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], documents)

    def test_invalid_json_is_recoverable_and_strict_parse_fails(self):
        bad = ['', '{', '[1,]', '{"x":1,"x":2}', '01', '+1', '1.', '1e', 'NaN', '1e999',
               'null trailing', '"\\u0000"', '"\\ud800"', '"\\udc00"', '"\\u00"',
               '"bad\\q"', '"bad\nline"', '[' * 140 + '0' + ']' * 140]
        texts = ', '.join(json.dumps(text) for text in bad)
        result = self.execute(f'fn main() {{ for text in [{texts}] {{ assert(json_kind(json_try_parse(text)) == "null") }} print("recovered") }}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'recovered\n')
        result = self.execute('fn main() { json_parse("[1,]") }')
        self.assertEqual(result.returncode, 1)
        self.assertIn('runtime error:', result.stderr)
        self.assertNotIn('AddressSanitizer', result.stderr)

    def test_json_collection_stress(self):
        result = self.execute('''struct Box { data: json }
        fn main() {
            let kept = Box(json_parse("{\\\"words\\\":[\\\"hello\\\",\\\"world\\\"]}"))
            var latest = json_null()
            for i in range(15000) {
                latest = json_set(json_object(), "number", json_int(i))
                let unused = json_dump(json_push(json_array(), latest))
                if i % 2 == 0 { continue }
                assert(json_as_int(latest["number"]) == i)
            }
            gc_collect()
            assert(heap_bytes() < 20000)
            print(kept.data["words"][1], latest["number"])
        }''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '"world" 14999\n')

    def test_large_live_json_graph_and_hash_index_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large.json'
            path.write_text(json.dumps(list(range(20000))))
            result = self.execute('''fn check(path: str) {
                let values = json_parse(read_file(path))
                gc_collect()
                assert(json_len(values) == 20000)
                assert(json_as_int(values[19999]) == 19999)
            }
            fn main() {
                for i in range(4) { check(args()[0]); gc_collect() }
                assert(heap_objects() < 100)
                print("large JSON graph preserved; transient allocations reclaimed")
            }''', arguments=[str(path)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('transient allocations reclaimed', result.stdout)

    def test_vector_math_and_matrix_kernels(self):
        result = self.execute('''import "std/ai/tensor.ad" as tensor
        fn main() {
            print(vector_dot([1.0, 2.0], [3.0, 4.0]), vector_norm([3.0, 4.0]))
            print(vector_cosine([1e308, 1e308], [1e308, 1e308]))
            print(vector_cosine([0.0, 0.0], [1.0, 2.0]))
            print(vector_topk([0.5, 0.9, 0.9, -1.0], 3))
            print(vector_topk([1.0], 0), vector_topk([1.0], 10))
            print(vector_softmax([1000.0, 1001.0, 1002.0]))
            let product = tensor.matmul(tensor.matrix(2, 3, [1.0,2.0,3.0,4.0,5.0,6.0]), tensor.matrix(3, 2, [7.0,8.0,9.0,10.0,11.0,12.0]))
            print(product.values, tensor.at(product, 1, 1))
            print(tensor_matmul([], [], 2, 0, 2))
        }''')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:5], ['11 5', '1', '0', '[1, 2, 0]', '[] [0]'])
        softmax = json.loads(lines[5])
        self.assertAlmostEqual(sum(softmax), 1.0)
        self.assertAlmostEqual(softmax[2], 0.6652409557748218)
        self.assertEqual(lines[6:], ['[58, 64, 139, 154] 154', '[0, 0, 0, 0]'])

    def test_vector_topk_matches_reference(self):
        rng = random.Random(42)
        values = [rng.choice([-1.0, 0.0, 0.5, 1.0, rng.random()]) for _ in range(200)]
        expression = ','.join(repr(x) for x in values)
        result = self.execute(f'fn main() {{ print(vector_topk([{expression}], 17)) }}')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = sorted(range(len(values)), key=lambda i: (-values[i], i))[:17]
        self.assertEqual(json.loads(result.stdout), expected)

    def test_unicode_chunks(self):
        cases = [('', 3, 0), ('é🌍abcdef', 3, 1), ('abc', 10, 2), ('abcdef', 2, 0), ('abcdef', 3, 2)]
        statements = '\n'.join(f'print(json_from_strings(text_chunks({json.dumps(text)}, {size}, {overlap})))' for text, size, overlap in cases)
        result = self.execute('fn main() {\n' + statements + '\n}')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = []
        for text, size, overlap in cases:
            chunks = []
            start = 0
            while start < len(text):
                chunks.append(text[start:start + size])
                if start + size >= len(text):
                    break
                start += size - overlap
            expected.append(chunks)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], expected)

    def test_rag_retrieval_against_exact_reference(self):
        rng = random.Random(9)
        vectors = [[rng.uniform(-1, 1) for _ in range(8)] for _ in range(80)]
        query = [rng.uniform(-1, 1) for _ in range(8)]
        ids = [f'doc-{i}' for i in range(len(vectors))]
        strings = ','.join(json.dumps(x) for x in ids)
        source = f'''import "std/ai/rag.ad" as rag
        fn main() {{
            let ids = [{strings}]
            let index = rag.build(ids, ids, ids, json_parse({json.dumps(json.dumps(vectors))}), "test-v1")
            print(rag.search(index, [{','.join(repr(x) for x in query)}], 7))
        }}'''
        result = self.execute(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        hits = json.loads(result.stdout)
        def cosine(vector):
            return sum(x * y for x, y in zip(vector, query)) / (math.sqrt(sum(x*x for x in vector)) * math.sqrt(sum(y*y for y in query)))
        scores = [cosine(vector) for vector in vectors]
        expected = sorted(range(len(vectors)), key=lambda i: (-scores[i], i))[:7]
        self.assertEqual([hit['id'] for hit in hits], [ids[i] for i in expected])
        for hit, i in zip(hits, expected):
            self.assertAlmostEqual(hit['score'], scores[i], places=12)

    def test_rag_persistence_context_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = json.dumps(str(Path(directory) / 'index.json'))
            result = self.execute(f'''import "std/ai/rag.ad" as rag
            fn main() {{
                let index = rag.build(["a", "b"], ["Astlin uses .ad", "RAG retrieves sources"], ["a.md", "b.md"], json_parse("[[1,0],[0,1]]"), "manual-v1")
                rag.save(index, {path})
                let loaded = rag.load({path})
                gc_collect()
                let hits = rag.search(loaded, [1.0, 0.0], 5)
                print(hits)
                print(json_len(rag.context_hits(hits, 2)))
                print(json_as_str(loaded["embedding_model"]))
                print(rag.prompt("Question?", rag.context_hits(hits, 4000)))
            }}''')
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual([hit['id'] for hit in json.loads(lines[0])], ['a', 'b'])
            self.assertEqual(lines[1:3], ['0', 'manual-v1'])
            self.assertIn('untrusted JSON data', result.stdout)
            self.assertEqual(json.loads(Path(json.loads(path)).read_text())['schema'], 'astlin-rag-v1')

    def test_bm25_matches_reference_and_filters_nonmatches(self):
        texts = ['Astlin language uses ad files', 'retrieval retrieves documents', 'Astlin Astlin language', '']
        ids = ['one', 'two', 'three', 'empty']
        def literals(values):
            return '[' + ','.join(json.dumps(value) for value in values) + ']'
        source = f'''import "std/ai/rag.ad" as rag
        fn main() {{
            print(rag.lexical({literals(ids)}, {literals(texts)}, {literals(ids)}, "ASTLIN language", 10))
            print(rag.lexical({literals(ids)}, {literals(texts)}, {literals(ids)}, "nomatch", 10))
        }}'''
        result = self.execute(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        hits = json.loads(result.stdout.splitlines()[0])
        words = [text.lower().split() for text in texts]
        average = sum(map(len, words)) / len(words)
        scores = []
        for document in words:
            score = 0
            for term in ('astlin', 'language'):
                frequency = document.count(term)
                df = sum(term in doc for doc in words)
                if frequency:
                    score += math.log1p((len(words) - df + 0.5) / (df + 0.5)) * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * len(document) / average))
            scores.append(score)
        expected = sorted((i for i, score in enumerate(scores) if score > 0), key=lambda i: (-scores[i], i))
        self.assertEqual([hit['id'] for hit in hits], [ids[i] for i in expected])
        for hit, i in zip(hits, expected):
            self.assertAlmostEqual(hit['score'], scores[i], places=12)
        self.assertEqual(json.loads(result.stdout.splitlines()[1]), [])

    def test_ai_project_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            for template in ('rag', 'llm'):
                project = Path(directory) / template
                result = subprocess.run([sys.executable, str(ROOT / 'astlin.py'), 'init', str(project), '--template', template], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                source = project / 'src/main.ad'
                compile_source(source.read_text(), source)
                env = dict(os.environ)
                for key in ('ASTLIN_CHAT_MODEL', 'ASTLIN_AI_BASE_URL', 'ASTLIN_AI_API_KEY'):
                    env.pop(key, None)
                result = subprocess.run([sys.executable, str(ROOT / 'astlin.py'), 'run'], cwd=project, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Top source:' if template == 'rag' else 'Set ASTLIN_CHAT_MODEL', result.stdout)

    def test_runtime_guards(self):
        expressions = [
            ('json_as_str(json_int(1))', 'unexpected JSON'),
            ('json_as_int(json_parse("1.5"))', 'integer syntax'),
            ('json_as_int(json_parse("9223372036854775808"))', 'integer conversion'),
            ('json_at(json_array(), 0)', 'out of bounds'),
            ('json_float(1e308 * 2.0)', 'finite'),
            ('vector_dot([1.0], [1.0, 2.0])', 'dimension mismatch'),
            ('vector_norm([1e308 * 2.0])', 'finite'),
            ('vector_topk([1.0], -1)', 'nonnegative'),
            ('tensor_matmul([1.0], [1.0], 2, 2, 2)', 'shape'),
            ('text_chunks("abc", 3, 3)', 'overlap'),
            ('rag_index(["a", "a"], ["x", "y"], ["x", "y"], json_parse("[[1],[1]]"))', 'duplicate'),
            ('rag_search(json_parse("{\\\"schema\\\":\\\"wrong\\\"}"), [1.0], 1)', 'unsupported'),
        ]
        for expression, message in expressions:
            with self.subTest(expression=expression):
                result = self.execute('fn main() { ' + expression + ' }')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn('AddressSanitizer', result.stderr)


class ModelHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *arguments):
        pass

    def respond(self, status, value, headers=None):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'application/json')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.server.requests.append(('GET', self.path, {}, dict(self.headers)))
        if self.path == '/redirect':
            self.respond(302, {}, {'Location': '/followed'})
        elif self.path == '/binary':
            self.respond(200, b'bad\xff')
        elif self.path == '/slow':
            time.sleep(0.2)
            self.respond(200, {})
        else:
            self.respond(200, {'hello': '🌍'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.server.requests.append(('POST', self.path, body, dict(self.headers)))
        model = body.get('model', '')
        count = sum(1 for _, path, value, _ in self.server.requests if path == self.path and value.get('model') == model)
        if model == 'retry' and count < 3:
            self.respond(429, {'error': 'retry'})
            return
        if model == 'unauthorized':
            self.respond(401, {'error': 'local-test-secret must not leak'})
            return
        if model == 'bad-json':
            self.respond(200, b'not JSON')
            return
        if self.path == '/v1/chat/completions':
            if model == 'wrong-schema':
                self.respond(200, {'choices': [None]})
            else:
                self.respond(200, {'choices': [{'message': {'content': 'Astlin uses .ad [language]'}}], 'usage': {'total_tokens': 12}})
        elif self.path == '/v1/embeddings':
            rows = []
            for i, text in enumerate(body['input']):
                vector = [1.0, 0.0] if 'Astlin' in text or '.ad' in text else [0.0, 1.0]
                if model == 'bad-dim' and i:
                    vector = [1.0]
                index = 0 if model == 'duplicate' else float(i) if model == 'bad-index' else i
                rows.append({'index': index, 'embedding': vector})
            self.respond(200, {'data': list(reversed(rows))})
        else:
            self.respond(404, {})


@contextlib.contextmanager
def model_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@unittest.skipUnless(CC, 'a C compiler and libcurl are required')
class ProviderIntegration(unittest.TestCase):
    execute = AiNativeTests.execute

    def test_http_preflight_rejects_unsafe_headers_urls_and_credentials(self):
        cases = [
            ('file:///tmp/anything', json.dumps({}), 'must use http'),
            ('http://user:top-secret@localhost', json.dumps({}), 'must not embed credentials'),
            ('http://example.invalid', json.dumps({'aUtHoRiZaTiOn': 'Bearer top-secret'}), 'requires HTTPS'),
            ('http://localhost.evil.invalid', json.dumps({'Authorization': 'Bearer top-secret'}), 'requires HTTPS'),
            ('http://localhost', json.dumps({'X-Test': 'bad\r\nvalue'}), 'invalid HTTP header'),
        ]
        for url, headers, error in cases:
            with self.subTest(url=url):
                source = f'fn main() {{ http_request("GET", {json.dumps(url)}, json_parse({json.dumps(headers)}), "", 100) }}'
                result = self.execute(source)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(error, result.stderr)
                self.assertNotIn('top-secret', result.stderr)

    def test_http_get_redirect_binary_timeout(self):
        with model_server() as (server, url):
            source = f'''import "std/ai/http.ad" as http
            fn main() {{
                let normal = http.get({json.dumps(url)}, 2000)
                print(normal.status, json_try_parse(normal.body)["hello"])
                print(http.get({json.dumps(url + '/redirect')}, 2000).status)
                print(http.get({json.dumps(url + '/binary')}, 2000).error)
                let slow = http.get({json.dumps(url + '/slow')}, 30)
                assert(slow.error != "")
            }}'''
            result = self.execute(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('200 "🌍"', result.stdout)
            self.assertIn('302\n', result.stdout)
            self.assertIn('not valid UTF-8', result.stdout)
            self.assertNotIn('/followed', [request[1] for request in server.requests])

    def test_chat_retry_key_handling_and_error_results(self):
        with model_server() as (server, url):
            source = f'''import "std/ai/llm.ad" as llm
            fn main() {{
                let connection = llm.client({json.dumps(url + '/v1/')}, "retry", "ASTLIN_TEST_KEY")
                print(connection)
                let reply = llm.complete(connection, "system", "hello")
                assert(reply.ok)
                print(reply.text, json_as_int(reply.usage["total_tokens"]))
                for model in ["unauthorized", "bad-json", "wrong-schema"] {{
                    let bad = llm.complete(llm.client({json.dumps(url + '/v1')}, model, "ASTLIN_TEST_KEY"), "", "hello")
                    assert(not bad.ok)
                    print(bad.status, bad.error)
                }}
                let missing = llm.complete(llm.client({json.dumps(url + '/v1')}, "unused", "ASTLIN_MISSING_KEY"), "", "hello")
                assert(not missing.ok)
            }}'''
            result = self.execute(source, environment={'ASTLIN_TEST_KEY': 'local-test-secret'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Astlin uses .ad [language] 12', result.stdout)
            self.assertNotIn('local-test-secret', result.stdout + result.stderr)
            models = [request[2]['model'] for request in server.requests]
            self.assertEqual(models.count('retry'), 3)
            self.assertEqual(models.count('unauthorized'), 1)
            self.assertNotIn('unused', models)
            self.assertEqual(server.requests[0][3].get('Authorization'), 'Bearer local-test-secret')

    def test_embedding_order_batches_and_schema_failures(self):
        with model_server() as (server, url):
            source = f'''import "std/ai/llm.ad" as llm
            fn main() {{
                let connection = llm.client({json.dumps(url + '/v1')}, "embed", "")
                let result = llm.embed_batches(connection, ["Astlin", "weather", "Astlin again"], 2)
                assert(result.ok)
                print(result.vectors)
                for model in ["duplicate", "bad-index", "bad-dim"] {{
                    let bad = llm.embed(llm.client({json.dumps(url + '/v1')}, model, ""), ["Astlin", "weather"])
                    assert(not bad.ok)
                    print(bad.error)
                }}
            }}'''
            result = self.execute(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[0]), [[1, 0], [0, 1], [1, 0]])
            batches = [request[2]['input'] for request in server.requests if request[2]['model'] == 'embed']
            self.assertEqual(batches, [['Astlin', 'weather'], ['Astlin again']])

    def test_complete_rag_pipeline_and_model_identity(self):
        with model_server() as (server, url):
            source = f'''import "std/ai/llm.ad" as llm
            import "std/ai/rag.ad" as rag
            import "std/ai/pipeline.ad" as pipeline
            fn main() {{
                let embedding = llm.client({json.dumps(url + '/v1')}, "embed", "")
                let chat = llm.client({json.dumps(url + '/v1')}, "chat", "")
                let texts = ["Astlin uses .ad", "Weather is sunny"]
                let vectors = llm.embed(embedding, texts)
                assert(vectors.ok)
                let index = rag.build(["language", "weather"], texts, ["guide.md", "weather.md"], vectors.vectors, embedding.model)
                let result = pipeline.answer(index, embedding, chat, "What extension does Astlin use?", 1, 4000)
                assert(result.ok)
                print(result.text, result.sources)
                let wrong = llm.client({json.dumps(url + '/v1')}, "different-embed", "")
                let mismatch = pipeline.answer(index, wrong, chat, "question", 1, 4000)
                assert(not mismatch.ok)
                print(mismatch.error)
            }}'''
            result = self.execute(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Astlin uses .ad [language]', result.stdout)
            self.assertIn('"id":"language"', result.stdout)
            self.assertIn('models must match', result.stdout)
            chat = next(request[2] for request in server.requests if request[1].endswith('/chat/completions'))
            self.assertIn('untrusted data', chat['messages'][0]['content'])
            self.assertIn('guide.md', chat['messages'][1]['content'])
            self.assertNotIn('different-embed', [request[2]['model'] for request in server.requests])


if __name__ == '__main__':
    unittest.main()
