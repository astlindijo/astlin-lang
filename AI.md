# Astlin 0.3: AI, LLM, and RAG applications

Astlin 0.3 adds native AI application primitives and standard-library workflows. It
can call compatible model servers, construct and persist a retrieval index, retrieve
sources, and ask a model for a cited answer. The executable uses native C and optional
libcurl, with no Python interpreter or provider SDK at runtime.

The compiler still runs on Python. This is an experimental application toolkit, not
a GPU training framework or a demonstrated replacement for Python's AI ecosystem.

## Start offline

```sh
python3 -m astlin run examples/ai_demo.ad
python3 -m astlin init my-rag --template rag
cd my-rag
python3 /path/to/astlin.py run
```

With the `astlin` command installed, use `astlin run` from the project. The RAG
project template uses real BM25 lexical retrieval and needs no model, API key,
network connection, or libcurl. The broader AI demo also shows explicit hand-written
vectors so the vector-search mechanics are reproducible; those vectors are not
learned or semantic embeddings.

```text
import "std/ai/rag.ad" as rag

fn main() {
    let hits = rag.lexical(
        ["language", "retrieval"],
        ["Astlin source files use .ad.", "RAG retrieves documents."],
        ["guide.md", "rag.md"],
        "Which extension does Astlin use?",
        2
    )
    print(hits)
}
```

## Configure a model server

HTTP/LLM imports require **libcurl development headers and libraries**. On Ubuntu:

```sh
sudo apt-get install libcurl4-openssl-dev pkg-config
```

The validated macOS developer toolchain provides system libcurl. Custom installations
can expose flags through `pkg-config libcurl` or `curl-config`. Astlin falls back to
`-lcurl`. Check the toolchain without calling any model:

```sh
python3 -m astlin doctor --ai
```

Configure a compatible local server:

```sh
export ASTLIN_AI_BASE_URL="http://localhost:11434/v1"
export ASTLIN_CHAT_MODEL="your-installed-chat-model"
export ASTLIN_EMBED_MODEL="your-installed-embedding-model"
python3 -m astlin run examples/ai/live/chat.ad
```

Model names above are placeholders. Install/select models on your server separately.
For a compatible hosted provider, set its HTTPS base URL and supply `ASTLIN_AI_API_KEY`
through your shell or secret manager. No `.env` file is loaded automatically. Keys
are read at execution time; clients store the environment variable name rather than
its value. The compiler does not need a key. Configured live examples actually send
user text to the endpoint and may incur provider charges; the automated tests use
only a local mock server with dummy credentials.

The client targets the documented [Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)
and [Embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
JSON protocols. Compatible local servers may implement subsets; see
[Ollama's compatibility documentation](https://docs.ollama.com/api/openai-compatibility).
No actual hosted provider or downloaded model weights were used to validate this release.

## Chat and embeddings

```text
import "std/ai/llm.ad" as llm

fn main() {
    let chat = llm.from_env("ASTLIN_CHAT_MODEL")
    let reply = llm.complete(chat, "You are a helpful assistant.", "Explain retrieval augmented generation.")
    if reply.ok { print(reply.text) } else { print(reply.error) }

    let embedding = llm.from_env("ASTLIN_EMBED_MODEL")
    let vectors = llm.embed_batches(embedding, ["First document", "Second document"], 32)
    if vectors.ok { print(vectors.vectors) } else { print(vectors.error) }
}
```

`llm.client(base_url, model, key_env)` constructs a client without reading the key.
Use `key_env = ""` for a local server without authentication. Defaults are a
30,000 ms timeout per attempt and three attempts. Construct a `llm.Client` directly
to select `timeout_ms` and `attempts`; valid attempt counts are 1–5. `from_env`
defaults only the base URL to localhost and requires the selected model variable.
It uses `ASTLIN_AI_API_KEY` if that variable is nonempty.

The native HTTP transport verifies TLS certificates/hostnames, accepts only HTTP(S),
refuses embedded URL credentials and newline header injection, and does not follow
redirects. Authorization headers require HTTPS except for canonical loopback hosts
`localhost`, `127.0.0.1`, and `[::1]`. Text response bodies, after decompression, are
limited to less than 32 MiB. Request bodies use the same limit. Timeouts must be
1–300,000 ms. Binary/NUL-containing/invalid-UTF-8 responses are returned as errors.

`llm.request` retries transport/text-decoding errors, HTTP 429, and HTTP 5xx with
100 ms incremental delays. Other HTTP failures return immediately. Retries can
repeat billable work; there is no idempotency-key policy, Retry-After scheduling,
provider quota manager, or global request deadline. A client timeout applies to each
attempt. Provider error bodies are not copied into high-level error messages.

`Completion` has `ok`, `text`, `status`, `error`, and `usage: json` fields. `Embeddings`
has `ok`, `vectors: json`, `status`, and `error`. Invalid provider JSON/schema produces
an unsuccessful result. Invalid native API arguments, malformed persistent data,
allocation failures, or language runtime guards still terminate with a runtime error.
These result records are not a general exception mechanism.

`embed` accepts 1–64 texts per request; an empty list succeeds without a request.
`embed_batches` accepts a batch size of 1–64 and preserves input order. Responses are
reordered using their integer `index` fields; duplicate/missing indexes, empty vectors,
and inconsistent dimensions fail. The same dimension is checked across batches.
There is no model-specific token counting or automatic truncation.

## Embedding RAG workflow

```sh
python3 -m astlin run examples/ai/live/rag.ad -- ./knowledge.txt "What does the document say?"
```

The example chunks UTF-8 text, obtains embeddings in batches, builds an index, embeds
the question, retrieves four chunks, selects source context, and calls the chat model.
The model server and any remote charges belong to the configured provider.

The same pieces can be composed in `.ad` code:

```text
import "std/ai/llm.ad" as llm
import "std/ai/rag.ad" as rag
import "std/ai/pipeline.ad" as pipeline

fn main() {
    let embedding = llm.from_env("ASTLIN_EMBED_MODEL")
    let chat = llm.from_env("ASTLIN_CHAT_MODEL")
    let texts = ["Astlin programs use .ad files.", "Astlin has static type checking."]
    let vectors = llm.embed_batches(embedding, texts, 32)
    if not vectors.ok { print(vectors.error); return }
    let index = rag.build(["extension", "types"], texts, ["guide.md#1", "guide.md#2"], vectors.vectors, embedding.model)
    rag.save(index, "knowledge-index.json")
    let result = pipeline.answer(rag.load("knowledge-index.json"), embedding, chat,
                                 "What extension does Astlin use?", 2, 12000)
    if result.ok { print(result.text); print(result.sources) } else { print(result.error) }
}
```

The `Answer` record contains `ok`, `text`, `sources: json`, and `error`. `sources`
contains the selected source IDs, source strings, chunk text, and retrieval scores.
The pipeline rejects a query client whose model name differs from the saved index's
embedding model. This name check does not fingerprint model weights or tokenizer
versions: use a new model identifier/rebuild the index when the embedding system changes.

`rag.chunks(text, size, overlap)` splits by Unicode scalar values, not bytes, tokens,
words, sentences, or grapheme clusters. Require `size > 0` and `0 <= overlap < size`.
Empty text yields no chunks. Character-sized chunks can exceed model token limits;
choose sizes for your provider and data. Whole-document text is loaded into memory.

`rag.build(ids, texts, sources, vectors, embedding_model)` validates matching counts,
nonempty unique IDs, finite numeric vectors, and a common nonzero dimension. It builds
a versioned JSON index with documents, vectors, dimension, and model identifier.
`rag.save` writes the index directly; writes are not atomic or encrypted. `rag.load`
parses the schema/model metadata; search validates dimensions and values. Index files
contain original document text, so store them according to the sensitivity of your data.

`rag.search(index, query_vector, k)` performs exact cosine search. Top-k uses a bounded
heap, with stable ties in original document order. Query cost is O(ND + N log k),
with O(D + k) query scratch storage, excluding the resident index and result objects.
JSON numbers are decoded during search; there is no ANN engine, compressed vector
format, vector database, or GPU acceleration. Zero vectors have similarity zero.

`rag.lexical(ids, texts, sources, query, k)` uses BM25 with k1=1.2 and b=0.75. It returns
only positive-scoring matches. Tokens use ASCII letters/digits/underscores plus
non-ASCII UTF-8 bytes, with ASCII case folding and ASCII punctuation separators.
There is no stemming, stop-word removal, Unicode case folding, or language-specific
segmentation. Queries support up to 256 distinct terms. It scans corpus tokens and
uses a document-by-query-term count table; it is not a persistent inverted index.

`rag.context_hits(hits, max_characters)` includes whole source records in rank order
until the JSON-serialized character budget would be exceeded. A single oversized
first record yields no selected context; choose suitable chunk sizes. Budgets are
characters, not model tokens. `rag.prompt` places questions and retrieved content in
JSON data. The pipeline's separate system message requests source-ID citations and
treats retrieved material as untrusted. These instructions do not guarantee factuality,
faithful citations, or protection against prompt injection. This version does not
validate model-generated citation IDs or apply similarity thresholds automatically.

## JSON and numeric primitives

`json` is a first-class immutable type usable in variables, function signatures, and
struct fields. JSON arrays/objects can contain nested JSON values. Native `list[json]`
is not supported; use a JSON array. Values are traced by the language collector.

```text
fn main() {
    let original: json = json_parse("{\"name\":\"Astlin\",\"count\":2}")
    let changed = json_set(original, "count", json_int(3))
    print(original["count"], changed["count"])
    print(json_dump(changed))
}
```

Constructors: `json_null()`, `json_object()`, `json_array()`, `json_int(int)`,
`json_float(float)`, `json_bool(bool)`, and `json_string(str)`.
Use `json_set(object, key, value)` and `json_push(array, value)` for immutable copies;
repeated append/update can be quadratic. `json_from_strings`, `json_from_floats`, and
`json_flatten` provide bulk construction; `json_to_floats` creates a native float list.

`json_parse` is strict. `json_try_parse` returns JSON null on parse failure, so invalid
input and a legitimate null are indistinguishable. Access with `json_get`/`json_at` or
`value["key"]`/`value[index]`. Missing object keys yield JSON null; incorrect base types
and out-of-bounds array indexes fail. `json_kind`, `json_len`, and `json_has` inspect
values. `json_as_str`, `json_as_bool`, `json_as_int`, and `json_as_float` check types.
`json_is_int` checks integer syntax and int64 range without failing. `str(json)` and
`json_dump` serialize JSON; `print(json)` emits JSON text.

The parser rejects duplicate keys, NUL, invalid Unicode escapes, nonfinite numbers,
trailing content, and nesting beyond its 128-edge limit. Input/serialized output is
limited to less than 32 MiB and parsed input to one million values. Parsed number
text is preserved, including large integer digits; int64 extraction still checks its
range and requires integer syntax. Floating extraction can round and underflow.
Constructed floats serialize to a short finite representation. JSON null is a value,
not a native nullable pointer. Object insertion order is preserved; no JSON equality
operator or JSON Schema validator is provided.

Numeric operations use finite CPU float64 values and return new immutable buffers:

| Function | Result |
| --- | --- |
| `vector_dot(a, b)` | Dot product; matching dimensions required |
| `vector_norm(a)` | Euclidean norm |
| `vector_cosine(a, b)` | Scaled cosine similarity, zero for zero vectors |
| `vector_topk(scores, k)` | Indices in descending score order, stable input-index ties |
| `vector_softmax(values)` | Stable softmax using maximum subtraction |
| `tensor_matmul(a, b, rows, inner, cols)` | Checked row-major matrix multiplication |

`std/ai/tensor.ad` supplies `Matrix`, `matrix`, `matmul`, and `at` wrappers. Empty
vectors/shapes have defined empty or zero-filled results; negative sizes, mismatched
shapes, nonfinite inputs, and overflowing numeric results fail. The kernels use
ordinary C loops and are not BLAS, PyTorch, CUDA, Metal, autograd, a tensor compiler,
or a native transformer implementation. No relative speed claim is established.

## Validation and remaining work

Run `make test` and `make test-native`. The integration suite exercises JSON Unicode
and malformed input, collection stress, numeric reference results, BM25/cosine ranking,
index round trips, templates, HTTP timeouts/redirects/invalid UTF-8, retry counts,
credential handling, embedding response order, and an end-to-end mock-provider RAG flow.
Tests need local listening sockets; a restrictive execution sandbox may require an
approved local-server test run. Hosted-provider behavior and answer quality still
need evaluation on your own models and documents.

Future work includes async/concurrent I/O, SSE streaming, provider tool calls, tokenizer
integration, model-specific limits, persistent/ANN indexes, hybrid reranking, structured
output schemas, grounded-answer evaluations, Python/ONNX interoperability, GPU backends,
autograd, mature debugging/editor tools, and larger real application benchmarks.
