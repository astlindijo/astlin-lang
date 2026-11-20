/* CPU float64 kernels and exact in-memory vector retrieval. */
static const char *ast_env(const char *name, int line) {
    const char *value = getenv(name);
    if (!value) return "";
    if (!ast_valid_utf8((const unsigned char *)value, strlen(value))) ast_fail(line, "environment value is not valid UTF-8");
    return ast_copy_text(value, strlen(value), line);
}
static void ast_sleep_ms(int64_t milliseconds, int line) {
    if (milliseconds < 0 || milliseconds > 60000) ast_fail(line, "sleep_ms must be between 0 and 60000");
    struct timespec delay = {(time_t)(milliseconds / 1000), (long)(milliseconds % 1000) * 1000000L};
    while (nanosleep(&delay, &delay) != 0) if (errno != EINTR) ast_fail(line, "sleep failed");
}
static void ast_user_fail(const char *message, int line) { ast_fail(line, message); }
static AstList ast_float_list(int64_t count, int line) {
    if (count < 0 || (uint64_t)count > SIZE_MAX / sizeof(double)) ast_fail(line, "tensor is too large");
    return (AstList){ast_alloc((size_t)count * sizeof(double), line), count};
}
static void ast_vector_check(AstList values, int line) {
    for (int64_t i = 0; i < values.len; ++i)
        if (!isfinite(((double *)values.data)[i])) ast_fail(line, "vectors require finite values");
}
static double ast_vector_dot(AstList left, AstList right, int line) {
    if (left.len != right.len) ast_fail(line, "vector dimension mismatch");
    ast_vector_check(left, line); ast_vector_check(right, line);
    long double sum = 0;
    for (int64_t i = 0; i < left.len; ++i) sum += (long double)((double *)left.data)[i] * ((double *)right.data)[i];
    double result = (double)sum;
    if (!isfinite(result)) ast_fail(line, "dot product exceeds float64 range");
    return result;
}
static double ast_vector_norm(AstList values, int line) {
    ast_vector_check(values, line);
    double norm = 0;
    for (int64_t i = 0; i < values.len; ++i) norm = hypot(norm, ((double *)values.data)[i]);
    if (!isfinite(norm)) ast_fail(line, "vector norm exceeds float64 range");
    return norm;
}
static double ast_cosine_values(const double *a, const double *b, int64_t count) {
    double sa = 0, sb = 0;
    for (int64_t i = 0; i < count; ++i) { sa = fmax(sa, fabs(a[i])); sb = fmax(sb, fabs(b[i])); }
    if (sa == 0 || sb == 0) return 0;
    long double dot = 0, aa = 0, bb = 0;
    for (int64_t i = 0; i < count; ++i) {
        long double x = a[i] / sa, y = b[i] / sb;
        dot += x * y; aa += x * x; bb += y * y;
    }
    double value = (double)(dot / sqrtl(aa * bb));
    return fmax(-1.0, fmin(1.0, value));
}
static double ast_vector_cosine(AstList a, AstList b, int line) {
    if (a.len != b.len) ast_fail(line, "vector dimension mismatch");
    ast_vector_check(a, line); ast_vector_check(b, line);
    return ast_cosine_values(a.data, b.data, a.len);
}
typedef struct { int64_t index; double score; } AstRank;
static bool ast_rank_better(AstRank a, AstRank b) { return a.score > b.score || (a.score == b.score && a.index < b.index); }
static int ast_rank_compare(const void *a, const void *b) {
    AstRank left = *(const AstRank *)a, right = *(const AstRank *)b;
    return ast_rank_better(left, right) ? -1 : ast_rank_better(right, left) ? 1 : 0;
}
static void ast_rank_insert(AstRank *heap, int64_t *size, int64_t capacity, AstRank item) {
    if (!capacity) return;
    if (*size < capacity) {
        int64_t i = (*size)++;
        heap[i] = item;
        while (i > 0) {
            int64_t parent = (i - 1) / 2;
            if (!ast_rank_better(heap[parent], heap[i])) break;
            AstRank swap = heap[parent]; heap[parent] = heap[i]; heap[i] = swap; i = parent;
        }
        return;
    }
    if (!ast_rank_better(item, heap[0])) return;
    heap[0] = item;
    int64_t i = 0;
    while (i < *size / 2) {
        int64_t child = 2 * i + 1;
        if (child + 1 < *size && ast_rank_better(heap[child], heap[child + 1])) ++child;
        if (!ast_rank_better(heap[i], heap[child])) break;
        AstRank swap = heap[i]; heap[i] = heap[child]; heap[child] = swap; i = child;
    }
}
static AstList ast_vector_topk(AstList scores, int64_t k, int line) {
    if (k < 0) ast_fail(line, "top-k must be nonnegative");
    ast_vector_check(scores, line);
    if (k > scores.len) k = scores.len;
    if ((uint64_t)k > SIZE_MAX / sizeof(AstRank)) ast_fail(line, "top-k is too large");
    AstRank *heap = ast_alloc((size_t)k * sizeof(*heap), line);
    int64_t size = 0;
    for (int64_t i = 0; i < scores.len; ++i) ast_rank_insert(heap, &size, k, (AstRank){i, ((double *)scores.data)[i]});
    qsort(heap, (size_t)size, sizeof(*heap), ast_rank_compare);
    int64_t *indices = ast_alloc((size_t)size * sizeof(*indices), line);
    for (int64_t i = 0; i < size; ++i) indices[i] = heap[i].index;
    return (AstList){indices, size};
}
static AstList ast_vector_softmax(AstList input, int line) {
    ast_vector_check(input, line);
    AstList output = ast_float_list(input.len, line);
    if (!input.len) return output;
    double maximum = ((double *)input.data)[0];
    for (int64_t i = 1; i < input.len; ++i) maximum = fmax(maximum, ((double *)input.data)[i]);
    long double total = 0;
    for (int64_t i = 0; i < input.len; ++i) {
        double value = exp(((double *)input.data)[i] - maximum);
        ((double *)output.data)[i] = value; total += value;
    }
    for (int64_t i = 0; i < input.len; ++i) ((double *)output.data)[i] /= (double)total;
    return output;
}
static AstList ast_tensor_matmul(AstList a, AstList b, int64_t rows, int64_t inner, int64_t cols, int line) {
    if (rows < 0 || inner < 0 || cols < 0) ast_fail(line, "matrix dimensions must be nonnegative");
    if (a.len != ast_mul(rows, inner, line) || b.len != ast_mul(inner, cols, line)) ast_fail(line, "matrix shape does not match data");
    ast_vector_check(a, line); ast_vector_check(b, line);
    AstList output = ast_float_list(ast_mul(rows, cols, line), line);
    for (int64_t i = 0; i < rows; ++i) for (int64_t j = 0; j < cols; ++j) {
        long double sum = 0;
        for (int64_t k = 0; k < inner; ++k) sum += (long double)((double *)a.data)[i * inner + k] * ((double *)b.data)[k * cols + j];
        double value = (double)sum;
        if (!isfinite(value)) ast_fail(line, "matrix product exceeds float64 range");
        ((double *)output.data)[i * cols + j] = value;
    }
    return output;
}
static AstList ast_text_chunks(const char *text, int64_t size, int64_t overlap, int line) {
    if (size <= 0 || overlap < 0 || overlap >= size) ast_fail(line, "chunk size must be positive and 0 <= overlap < size");
    int64_t length = ast_strlen(text, line), count = 0, step = size - overlap;
    if (length) count = length <= size ? 1 : 1 + (length - size) / step + ((length - size) % step != 0);
    if ((uint64_t)count > SIZE_MAX / sizeof(const char *)) ast_fail(line, "too many chunks");
    const char **chunks = ast_alloc((size_t)count * sizeof(*chunks), line);
    const unsigned char *start = (const unsigned char *)text;
    for (int64_t i = 0; i < count; ++i) {
        const unsigned char *end = start, *next = start;
        for (int64_t j = 0; j < size && *end; ++j) {
            ++end; while ((*end & 0xc0) == 0x80) ++end;
            if (j + 1 == step) next = end;
        }
        chunks[i] = ast_copy_text((const char *)start, (size_t)(end - start), line);
        start = next;
    }
    return (AstList){chunks, count};
}
static int ast_text_compare(const void *a, const void *b) { return strcmp(*(const char *const *)a, *(const char *const *)b); }
static void ast_document_lists(AstList ids, AstList texts, AstList sources, int line) {
    int64_t count = ids.len;
    if (texts.len != count || sources.len != count) ast_fail(line, "RAG document counts must match");
    const char **sorted = ast_alloc((size_t)count * sizeof(*sorted), line);
    if (count) memcpy(sorted, ids.data, (size_t)count * sizeof(*sorted));
    qsort(sorted, (size_t)count, sizeof(*sorted), ast_text_compare);
    for (int64_t i = 0; i < count; ++i) {
        if (!*sorted[i]) ast_fail(line, "RAG document IDs must not be empty");
        if (i && !strcmp(sorted[i - 1], sorted[i])) ast_fail(line, "duplicate RAG document ID");
    }
}
static AstJson *ast_rag_index(AstList ids, AstList texts, AstList sources, AstJson *vectors, int line) {
    ast_json_expect(vectors, AST_JARRAY, line);
    int64_t count = ids.len, dimension = 0;
    ast_document_lists(ids, texts, sources, line);
    if (vectors->as.items.len != count) ast_fail(line, "RAG document/vector counts must match");
    AstJson *documents = ast_json_container(AST_JARRAY, count, line);
    for (int64_t i = 0; i < count; ++i) {
        AstJson *vector = ast_json_at(vectors, i, line);
        ast_json_expect(vector, AST_JARRAY, line);
        if (!i) dimension = vector->as.items.len;
        if (!dimension || vector->as.items.len != dimension) ast_fail(line, "RAG embeddings must have matching nonzero dimensions");
        for (int64_t j = 0; j < dimension; ++j) (void)ast_json_as_float(vector->as.items.values[j], line);
        AstJson *doc = ast_json_object();
        doc = ast_json_set(doc, "id", ast_json_string(((const char **)ids.data)[i]), line);
        doc = ast_json_set(doc, "text", ast_json_string(((const char **)texts.data)[i]), line);
        doc = ast_json_set(doc, "source", ast_json_string(((const char **)sources.data)[i]), line);
        doc = ast_json_set(doc, "embedding", vector, line);
        documents->as.items.values[i] = doc;
        ast_json_child_depth(documents, doc, line);
    }
    AstJson *out = ast_json_object();
    out = ast_json_set(out, "schema", ast_json_string("astlin-rag-v1"), line);
    out = ast_json_set(out, "dimension", ast_json_int(dimension, line), line);
    return ast_json_set(out, "documents", documents, line);
}
static AstJson *ast_rag_search(AstJson *index, AstList query, int64_t k, int line) {
    if (k < 0) ast_fail(line, "top-k must be nonnegative");
    ast_json_expect(index, AST_JOBJECT, line);
    if (strcmp(ast_json_as_str(ast_json_get(index, "schema", line), line), "astlin-rag-v1")) ast_fail(line, "unsupported RAG index schema");
    int64_t dimension = ast_json_as_int(ast_json_get(index, "dimension", line), line);
    AstJson *documents = ast_json_get(index, "documents", line);
    ast_json_expect(documents, AST_JARRAY, line);
    if (dimension < 0 || (documents->as.items.len && dimension == 0)) ast_fail(line, "invalid RAG index dimension");
    if (query.len != dimension) ast_fail(line, "query embedding dimension mismatch");
    ast_vector_check(query, line);
    int64_t count = documents->as.items.len;
    if (k > count) k = count;
    if ((uint64_t)k > SIZE_MAX / sizeof(AstRank)) ast_fail(line, "top-k is too large");
    AstRank *heap = ast_alloc((size_t)k * sizeof(*heap), line);
    AstList scratch = ast_float_list(dimension, line);
    int64_t size = 0;
    for (int64_t i = 0; i < count; ++i) {
        AstJson *doc = ast_json_at(documents, i, line);
        AstJson *embedding = ast_json_get(doc, "embedding", line);
        ast_json_expect(embedding, AST_JARRAY, line);
        if (embedding->as.items.len != dimension) ast_fail(line, "corrupt RAG embedding dimension");
        for (int64_t j = 0; j < dimension; ++j) ((double *)scratch.data)[j] = ast_json_as_float(embedding->as.items.values[j], line);
        double score = ast_cosine_values(query.data, scratch.data, dimension);
        ast_rank_insert(heap, &size, k, (AstRank){i, score});
    }
    qsort(heap, (size_t)size, sizeof(*heap), ast_rank_compare);
    AstJson *hits = ast_json_container(AST_JARRAY, size, line);
    for (int64_t i = 0; i < size; ++i) {
        AstJson *doc = ast_json_at(documents, heap[i].index, line), *hit = ast_json_object();
        const char *keys[] = {"id", "text", "source"};
        for (int j = 0; j < 3; ++j) {
            AstJson *value = ast_json_get(doc, keys[j], line);
            ast_json_expect(value, AST_JSTRING, line);
            hit = ast_json_set(hit, keys[j], value, line);
        }
        hit = ast_json_set(hit, "score", ast_json_float(heap[i].score, line), line);
        hits->as.items.values[i] = hit;
        ast_json_child_depth(hits, hit, line);
    }
    return hits;
}

typedef struct { const unsigned char *text; size_t size; } AstTerm;
static bool ast_word_byte(unsigned char c) {
    return c >= 128 || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
}
static bool ast_next_term(const unsigned char **cursor, AstTerm *term) {
    const unsigned char *p = *cursor;
    while (*p && !ast_word_byte(*p)) ++p;
    if (!*p) { *cursor = p; return false; }
    term->text = p;
    while (*p && ast_word_byte(*p)) ++p;
    term->size = (size_t)(p - term->text); *cursor = p;
    return true;
}
static bool ast_term_equal(AstTerm a, AstTerm b) {
    if (a.size != b.size) return false;
    for (size_t i = 0; i < a.size; ++i) if (tolower(a.text[i]) != tolower(b.text[i])) return false;
    return true;
}
static AstJson *ast_rag_lexical(AstList ids, AstList texts, AstList sources, const char *query, int64_t k, int line) {
    ast_document_lists(ids, texts, sources, line);
    if (k < 0) ast_fail(line, "top-k must be nonnegative");
    AstTerm terms[256], term;
    int64_t term_count = 0;
    const unsigned char *cursor = (const unsigned char *)query;
    while (ast_next_term(&cursor, &term)) {
        bool duplicate = false;
        for (int64_t i = 0; i < term_count; ++i) if (ast_term_equal(term, terms[i])) { duplicate = true; break; }
        if (!duplicate) {
            if (term_count == 256) ast_fail(line, "lexical query exceeds 256 distinct terms");
            terms[term_count++] = term;
        }
    }
    int64_t count = ids.len;
    if (!term_count || !count || !k) return ast_json_array();
    if (k > count) k = count;
    int64_t cells = ast_mul(count, term_count, line);
    if ((uint64_t)cells > SIZE_MAX / sizeof(int64_t) || (uint64_t)count > SIZE_MAX / sizeof(int64_t) || (uint64_t)k > SIZE_MAX / sizeof(AstRank)) ast_fail(line, "lexical corpus is too large");
    int64_t *frequency = ast_alloc((size_t)cells * sizeof(*frequency), line);
    memset(frequency, 0, (size_t)cells * sizeof(*frequency));
    int64_t *lengths = ast_alloc((size_t)count * sizeof(*lengths), line);
    int64_t document_frequency[256] = {0}, total = 0;
    for (int64_t d = 0; d < count; ++d) {
        cursor = (const unsigned char *)((const char **)texts.data)[d];
        lengths[d] = 0;
        while (ast_next_term(&cursor, &term)) {
            lengths[d] = ast_add(lengths[d], 1, line);
            for (int64_t i = 0; i < term_count; ++i) if (ast_term_equal(term, terms[i])) { ++frequency[d * term_count + i]; break; }
        }
        total = ast_add(total, lengths[d], line);
        for (int64_t i = 0; i < term_count; ++i) if (frequency[d * term_count + i]) ++document_frequency[i];
    }
    if (!total) return ast_json_array();
    double average = (double)total / (double)count;
    AstRank *heap = ast_alloc((size_t)k * sizeof(*heap), line);
    int64_t size = 0;
    for (int64_t d = 0; d < count; ++d) {
        double score = 0;
        for (int64_t i = 0; i < term_count; ++i) {
            double f = (double)frequency[d * term_count + i];
            if (!f) continue;
            double df = (double)document_frequency[i];
            double idf = log1p(((double)count - df + 0.5) / (df + 0.5));
            score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * (double)lengths[d] / average));
        }
        if (score > 0) ast_rank_insert(heap, &size, k, (AstRank){d, score});
    }
    qsort(heap, (size_t)size, sizeof(*heap), ast_rank_compare);
    AstJson *hits = ast_json_container(AST_JARRAY, size, line);
    for (int64_t i = 0; i < size; ++i) {
        int64_t d = heap[i].index;
        AstJson *hit = ast_json_object();
        hit = ast_json_set(hit, "id", ast_json_string(((const char **)ids.data)[d]), line);
        hit = ast_json_set(hit, "text", ast_json_string(((const char **)texts.data)[d]), line);
        hit = ast_json_set(hit, "source", ast_json_string(((const char **)sources.data)[d]), line);
        hit = ast_json_set(hit, "score", ast_json_float(heap[i].score, line), line);
        hits->as.items.values[i] = hit;
        ast_json_child_depth(hits, hit, line);
    }
    return hits;
}
