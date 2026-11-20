/* Immutable JSON values, traced by Astlin's collector. */
#define AST_DATA_LIMIT ((size_t)32 * 1024 * 1024)
#define AST_JSON_DEPTH 128
typedef enum { AST_JNULL, AST_JBOOL, AST_JNUMBER, AST_JSTRING, AST_JARRAY, AST_JOBJECT } AstJsonKind;
typedef struct AstJson AstJson;
struct AstJson {
    AstJsonKind kind;
    int depth;
    union {
        bool boolean;
        const char *text;
        struct { int64_t len; AstJson **values; const char **keys; } items;
    } as;
};
typedef struct { char *data; size_t len, capacity; int line; } AstBuffer;
static AstBuffer ast_buffer(int line) {
    return (AstBuffer){ast_alloc(64, line), 0, 64, line};
}
static void ast_buffer_write(AstBuffer *buffer, const char *text, size_t size) {
    size_t needed = ast_size_add(ast_size_add(buffer->len, size, buffer->line), 1, buffer->line);
    if (needed > AST_DATA_LIMIT) ast_fail(buffer->line, "text/JSON exceeds the 32 MiB limit");
    if (needed > buffer->capacity) {
        size_t capacity = buffer->capacity;
        while (capacity < needed) capacity *= 2;
        char *data = ast_alloc(capacity, buffer->line);
        if (buffer->len) memcpy(data, buffer->data, buffer->len);
        buffer->data = data;
        buffer->capacity = capacity;
    }
    if (size) memcpy(buffer->data + buffer->len, text, size);
    buffer->len += size;
    buffer->data[buffer->len] = 0;
}
static void ast_buffer_char(AstBuffer *buffer, char c) { ast_buffer_write(buffer, &c, 1); }
static AstJson *ast_json_new(AstJsonKind kind) {
    AstJson *value = ast_alloc(sizeof(*value), 0);
    *value = (AstJson){.kind = kind, .depth = 0, .as = {.items = {0, NULL, NULL}}};
    return value;
}
static AstJson *ast_json_null(void) { return ast_json_new(AST_JNULL); }
static AstJson *ast_json_object(void) { return ast_json_new(AST_JOBJECT); }
static AstJson *ast_json_array(void) { return ast_json_new(AST_JARRAY); }
static AstJson *ast_json_bool(bool value) {
    AstJson *out = ast_json_new(AST_JBOOL); out->as.boolean = value; return out;
}
static AstJson *ast_json_string(const char *value) {
    AstJson *out = ast_json_new(AST_JSTRING); out->as.text = value; return out;
}
static AstJson *ast_json_int(int64_t value, int line) {
    AstJson *out = ast_json_new(AST_JNUMBER); out->as.text = ast_string_int(value, line); return out;
}
static AstJson *ast_json_float(double value, int line) {
    if (!isfinite(value)) ast_fail(line, "JSON numbers must be finite");
    AstJson *out = ast_json_new(AST_JNUMBER); out->as.text = ast_string_float(value, line); return out;
}
static void ast_trace_json(const void *address) {
    AstJson *value = *(AstJson *const *)address;
    if (!value || !ast_mark(value)) return;
    if (value->kind == AST_JSTRING || value->kind == AST_JNUMBER) ast_mark(value->as.text);
    if (value->kind == AST_JARRAY || value->kind == AST_JOBJECT) {
        ast_mark(value->as.items.values);
        if (value->kind == AST_JOBJECT) ast_mark(value->as.items.keys);
        for (int64_t i = 0; i < value->as.items.len; ++i) {
            ast_trace_json(&value->as.items.values[i]);
            if (value->kind == AST_JOBJECT) ast_mark(value->as.items.keys[i]);
        }
    }
}
static void ast_json_expect(AstJson *value, AstJsonKind kind, int line) {
    if (value->kind != kind) ast_fail(line, "unexpected JSON value type");
}
static const char *ast_json_kind(AstJson *value) {
    static const char *names[] = {"null", "bool", "number", "string", "array", "object"};
    return names[value->kind];
}
static const char *ast_json_as_str(AstJson *value, int line) {
    ast_json_expect(value, AST_JSTRING, line); return value->as.text;
}
static bool ast_json_as_bool(AstJson *value, int line) {
    ast_json_expect(value, AST_JBOOL, line); return value->as.boolean;
}
static double ast_json_as_float(AstJson *value, int line) {
    ast_json_expect(value, AST_JNUMBER, line);
    double result = strtod(value->as.text, NULL);
    if (!isfinite(result)) ast_fail(line, "JSON number is outside float64 range");
    return result;
}
static int64_t ast_json_as_int(AstJson *value, int line) {
    ast_json_expect(value, AST_JNUMBER, line);
    if (strpbrk(value->as.text, ".eE")) ast_fail(line, "JSON integer conversion requires integer syntax");
    return ast_parse_int(value->as.text, line);
}
static bool ast_json_is_int(AstJson *value) {
    if (value->kind != AST_JNUMBER || strpbrk(value->as.text, ".eE")) return false;
    errno = 0;
    intmax_t number = strtoimax(value->as.text, NULL, 10);
    return errno != ERANGE && number >= INT64_MIN && number <= INT64_MAX;
}
static int64_t ast_json_len(AstJson *value, int line) {
    if (value->kind == AST_JSTRING) return ast_strlen(value->as.text, line);
    if (value->kind != AST_JARRAY && value->kind != AST_JOBJECT) ast_fail(line, "JSON len requires a string, array, or object");
    return value->as.items.len;
}
static int64_t ast_json_find(AstJson *object, const char *key) {
    for (int64_t i = 0; i < object->as.items.len; ++i)
        if (!strcmp(object->as.items.keys[i], key)) return i;
    return -1;
}
static bool ast_json_has(AstJson *object, const char *key, int line) {
    ast_json_expect(object, AST_JOBJECT, line); return ast_json_find(object, key) >= 0;
}
static AstJson *ast_json_get(AstJson *object, const char *key, int line) {
    ast_json_expect(object, AST_JOBJECT, line);
    int64_t i = ast_json_find(object, key);
    return i < 0 ? ast_json_null() : object->as.items.values[i];
}
static AstJson *ast_json_at(AstJson *array, int64_t index, int line) {
    ast_json_expect(array, AST_JARRAY, line);
    if (index < 0 || index >= array->as.items.len) ast_fail(line, "JSON index out of bounds");
    return array->as.items.values[index];
}
static AstJson *ast_json_container(AstJsonKind kind, int64_t count, int line) {
    if (count < 0 || (uint64_t)count > SIZE_MAX / sizeof(AstJson *)) ast_fail(line, "JSON container is too large");
    AstJson *value = ast_json_new(kind);
    value->as.items.len = count;
    value->as.items.values = ast_alloc((size_t)count * sizeof(AstJson *), line);
    if (kind == AST_JOBJECT) value->as.items.keys = ast_alloc((size_t)count * sizeof(const char *), line);
    return value;
}
static void ast_json_child_depth(AstJson *parent, AstJson *child, int line) {
    if (child->depth >= AST_JSON_DEPTH) ast_fail(line, "JSON nesting exceeds 128 levels");
    if (parent->depth < child->depth + 1) parent->depth = child->depth + 1;
}
static AstJson *ast_json_set(AstJson *object, const char *key, AstJson *item, int line) {
    ast_json_expect(object, AST_JOBJECT, line);
    int64_t index = ast_json_find(object, key), old_count = object->as.items.len;
    int64_t count = index < 0 ? ast_add(old_count, 1, line) : old_count;
    AstJson *copy = ast_json_container(AST_JOBJECT, count, line);
    for (int64_t i = 0; i < old_count; ++i) {
        copy->as.items.keys[i] = object->as.items.keys[i];
        copy->as.items.values[i] = i == index ? item : object->as.items.values[i];
    }
    if (index < 0) { copy->as.items.keys[old_count] = key; copy->as.items.values[old_count] = item; }
    for (int64_t i = 0; i < count; ++i) ast_json_child_depth(copy, copy->as.items.values[i], line);
    return copy;
}
static AstJson *ast_json_push(AstJson *array, AstJson *item, int line) {
    ast_json_expect(array, AST_JARRAY, line);
    AstJson *copy = ast_json_container(AST_JARRAY, ast_add(array->as.items.len, 1, line), line);
    if (array->as.items.len) memcpy(copy->as.items.values, array->as.items.values, (size_t)array->as.items.len * sizeof(AstJson *));
    copy->as.items.values[array->as.items.len] = item;
    copy->depth = array->depth;
    ast_json_child_depth(copy, item, line);
    return copy;
}
static AstJson *ast_json_from_strings(AstList list, int line) {
    AstJson *out = ast_json_container(AST_JARRAY, list.len, line);
    out->depth = list.len ? 1 : 0;
    for (int64_t i = 0; i < list.len; ++i) out->as.items.values[i] = ast_json_string(((const char **)list.data)[i]);
    return out;
}
static AstJson *ast_json_from_floats(AstList list, int line) {
    AstJson *out = ast_json_container(AST_JARRAY, list.len, line);
    out->depth = list.len ? 1 : 0;
    for (int64_t i = 0; i < list.len; ++i) out->as.items.values[i] = ast_json_float(((double *)list.data)[i], line);
    return out;
}
static AstList ast_json_to_floats(AstJson *array, int line) {
    ast_json_expect(array, AST_JARRAY, line);
    int64_t count = array->as.items.len;
    if ((uint64_t)count > SIZE_MAX / sizeof(double)) ast_fail(line, "vector is too large");
    double *data = ast_alloc((size_t)count * sizeof(double), line);
    for (int64_t i = 0; i < count; ++i) data[i] = ast_json_as_float(array->as.items.values[i], line);
    return (AstList){data, count};
}
static AstJson *ast_json_flatten(AstJson *arrays, int line) {
    ast_json_expect(arrays, AST_JARRAY, line);
    int64_t count = 0;
    for (int64_t i = 0; i < arrays->as.items.len; ++i) {
        ast_json_expect(arrays->as.items.values[i], AST_JARRAY, line);
        count = ast_add(count, arrays->as.items.values[i]->as.items.len, line);
    }
    AstJson *out = ast_json_container(AST_JARRAY, count, line);
    int64_t offset = 0;
    for (int64_t i = 0; i < arrays->as.items.len; ++i) {
        AstJson *part = arrays->as.items.values[i];
        for (int64_t j = 0; j < part->as.items.len; ++j) {
            AstJson *value = part->as.items.values[j];
            out->as.items.values[offset++] = value;
            ast_json_child_depth(out, value, line);
        }
    }
    return out;
}
static void ast_json_quote(AstBuffer *out, const char *text) {
    ast_buffer_char(out, '"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        unsigned char c = *p;
        if (c == '"' || c == '\\') { ast_buffer_char(out, '\\'); ast_buffer_char(out, (char)c); }
        else if (c < 32) { char escape[7]; snprintf(escape, sizeof(escape), "\\u%04x", c); ast_buffer_write(out, escape, 6); }
        else ast_buffer_char(out, (char)c);
    }
    ast_buffer_char(out, '"');
}
static void ast_json_encode(AstBuffer *out, AstJson *value) {
    switch (value->kind) {
    case AST_JNULL: ast_buffer_write(out, "null", 4); break;
    case AST_JBOOL: ast_buffer_write(out, value->as.boolean ? "true" : "false", value->as.boolean ? 4 : 5); break;
    case AST_JNUMBER: ast_buffer_write(out, value->as.text, strlen(value->as.text)); break;
    case AST_JSTRING: ast_json_quote(out, value->as.text); break;
    case AST_JARRAY: case AST_JOBJECT:
        ast_buffer_char(out, value->kind == AST_JARRAY ? '[' : '{');
        for (int64_t i = 0; i < value->as.items.len; ++i) {
            if (i) ast_buffer_char(out, ',');
            if (value->kind == AST_JOBJECT) { ast_json_quote(out, value->as.items.keys[i]); ast_buffer_char(out, ':'); }
            ast_json_encode(out, value->as.items.values[i]);
        }
        ast_buffer_char(out, value->kind == AST_JARRAY ? ']' : '}');
        break;
    }
}
static const char *ast_json_dump(AstJson *value, int line) {
    AstBuffer out = ast_buffer(line);
    ast_json_encode(&out, value);
    return out.data;
}
static void ast_print_json(AstJson *value) { fputs(ast_json_dump(value, 0), stdout); }

typedef struct { const char *cursor; const char *error; int line; size_t nodes; } AstJsonParser;
static void ast_json_space(AstJsonParser *p) {
    while (*p->cursor == ' ' || *p->cursor == '\n' || *p->cursor == '\r' || *p->cursor == '\t') ++p->cursor;
}
static AstJson *ast_json_bad(AstJsonParser *p, const char *error) { p->error = error; return NULL; }
static int ast_json_hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static bool ast_json_codepoint(AstJsonParser *p, uint32_t *out) {
    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
        int digit = ast_json_hex(*p->cursor);
        if (digit < 0) { p->error = "invalid JSON Unicode escape"; return false; }
        ++p->cursor;
        value = (value << 4) | (uint32_t)digit;
    }
    *out = value;
    return true;
}
static const char *ast_json_parse_string(AstJsonParser *p) {
    if (*p->cursor++ != '"') { p->error = "expected JSON string"; return NULL; }
    AstBuffer out = ast_buffer(p->line);
    while (*p->cursor && *p->cursor != '"') {
        unsigned char c = (unsigned char)*p->cursor++;
        if (c < 32) { p->error = "unescaped control character in JSON"; return NULL; }
        if (c != '\\') { ast_buffer_char(&out, (char)c); continue; }
        c = (unsigned char)*p->cursor;
        if (!c) { p->error = "unterminated JSON escape"; return NULL; }
        ++p->cursor;
        switch (c) {
        case '"': case '\\': case '/': ast_buffer_char(&out, (char)c); break;
        case 'b': ast_buffer_char(&out, '\b'); break;
        case 'f': ast_buffer_char(&out, '\f'); break;
        case 'n': ast_buffer_char(&out, '\n'); break;
        case 'r': ast_buffer_char(&out, '\r'); break;
        case 't': ast_buffer_char(&out, '\t'); break;
        case 'u': {
            uint32_t value;
            if (!ast_json_codepoint(p, &value)) return NULL;
            if (value >= 0xd800 && value <= 0xdbff) {
                if (p->cursor[0] != '\\' || p->cursor[1] != 'u') { p->error = "missing JSON low surrogate"; return NULL; }
                p->cursor += 2;
                uint32_t low;
                if (!ast_json_codepoint(p, &low)) return NULL;
                if (low < 0xdc00 || low > 0xdfff) { p->error = "invalid JSON low surrogate"; return NULL; }
                value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
            } else if (value >= 0xdc00 && value <= 0xdfff) { p->error = "unpaired JSON surrogate"; return NULL; }
            if (!value) { p->error = "JSON strings cannot contain NUL"; return NULL; }
            if (value < 0x80) ast_buffer_char(&out, (char)value);
            else if (value < 0x800) {
                ast_buffer_char(&out, (char)(0xc0 | (value >> 6)));
                ast_buffer_char(&out, (char)(0x80 | (value & 0x3f)));
            } else if (value < 0x10000) {
                ast_buffer_char(&out, (char)(0xe0 | (value >> 12)));
                ast_buffer_char(&out, (char)(0x80 | ((value >> 6) & 0x3f)));
                ast_buffer_char(&out, (char)(0x80 | (value & 0x3f)));
            } else {
                ast_buffer_char(&out, (char)(0xf0 | (value >> 18)));
                ast_buffer_char(&out, (char)(0x80 | ((value >> 12) & 0x3f)));
                ast_buffer_char(&out, (char)(0x80 | ((value >> 6) & 0x3f)));
                ast_buffer_char(&out, (char)(0x80 | (value & 0x3f)));
            }
            break;
        }
        default: p->error = "invalid JSON escape"; return NULL;
        }
    }
    if (*p->cursor != '"') { p->error = "unterminated JSON string"; return NULL; }
    ++p->cursor;
    ast_buffer_write(&out, "", 0);
    return out.data;
}
static int ast_json_key_compare(const void *left, const void *right) {
    return strcmp(*(const char *const *)left, *(const char *const *)right);
}
static AstJson *ast_json_parse_value(AstJsonParser *p, int depth) {
    if (depth > AST_JSON_DEPTH) return ast_json_bad(p, "JSON nesting exceeds 128 levels");
    if (++p->nodes > 1000000) return ast_json_bad(p, "JSON exceeds one million values");
    ast_json_space(p);
    char c = *p->cursor;
    if (c == '"') {
        const char *text = ast_json_parse_string(p);
        return text ? ast_json_string(text) : NULL;
    }
    if (c == '[' || c == '{') {
        ++p->cursor;
        bool object = c == '{';
        char close = object ? '}' : ']';
        AstJson *out = ast_json_container(object ? AST_JOBJECT : AST_JARRAY, 0, p->line);
        size_t capacity = 0;
        ast_json_space(p);
        if (*p->cursor == close) { ++p->cursor; return out; }
        while (true) {
            const char *key = NULL;
            ast_json_space(p);
            if (object) {
                if (*p->cursor != '"') return ast_json_bad(p, "expected JSON object key");
                key = ast_json_parse_string(p);
                if (!key) return NULL;
                ast_json_space(p);
                if (*p->cursor != ':') return ast_json_bad(p, "expected ':' after JSON key");
                ++p->cursor;
            }
            AstJson *item = ast_json_parse_value(p, depth + 1);
            if (!item) return NULL;
            if ((size_t)out->as.items.len == capacity) {
                capacity = capacity ? capacity * 2 : 8;
                AstJson **values = ast_alloc(capacity * sizeof(*values), p->line);
                if (out->as.items.len) memcpy(values, out->as.items.values, (size_t)out->as.items.len * sizeof(*values));
                out->as.items.values = values;
                if (object) {
                    const char **keys = ast_alloc(capacity * sizeof(*keys), p->line);
                    if (out->as.items.len) memcpy(keys, out->as.items.keys, (size_t)out->as.items.len * sizeof(*keys));
                    out->as.items.keys = keys;
                }
            }
            int64_t i = out->as.items.len++;
            out->as.items.values[i] = item;
            if (object) out->as.items.keys[i] = key;
            ast_json_child_depth(out, item, p->line);
            ast_json_space(p);
            if (*p->cursor == close) {
                if (object && out->as.items.len > 1) {
                    size_t bytes = (size_t)out->as.items.len * sizeof(const char *);
                    const char **keys = ast_alloc(bytes, p->line);
                    memcpy(keys, out->as.items.keys, bytes);
                    qsort(keys, (size_t)out->as.items.len, sizeof(*keys), ast_json_key_compare);
                    for (int64_t j = 1; j < out->as.items.len; ++j)
                        if (!strcmp(keys[j - 1], keys[j])) return ast_json_bad(p, "duplicate JSON object key");
                }
                ++p->cursor; return out;
            }
            if (*p->cursor != ',') return ast_json_bad(p, "expected ',' or closing JSON delimiter");
            ++p->cursor;
        }
    }
    if (!strncmp(p->cursor, "null", 4)) { p->cursor += 4; return ast_json_null(); }
    if (!strncmp(p->cursor, "true", 4)) { p->cursor += 4; return ast_json_bool(true); }
    if (!strncmp(p->cursor, "false", 5)) { p->cursor += 5; return ast_json_bool(false); }
    const char *start = p->cursor;
    if (*p->cursor == '-') ++p->cursor;
    if (*p->cursor == '0') ++p->cursor;
    else if (*p->cursor >= '1' && *p->cursor <= '9') while (*p->cursor >= '0' && *p->cursor <= '9') ++p->cursor;
    else return ast_json_bad(p, "expected JSON value");
    if (*p->cursor == '.') {
        ++p->cursor;
        if (*p->cursor < '0' || *p->cursor > '9') return ast_json_bad(p, "invalid JSON fraction");
        while (*p->cursor >= '0' && *p->cursor <= '9') ++p->cursor;
    }
    if (*p->cursor == 'e' || *p->cursor == 'E') {
        ++p->cursor;
        if (*p->cursor == '-' || *p->cursor == '+') ++p->cursor;
        if (*p->cursor < '0' || *p->cursor > '9') return ast_json_bad(p, "invalid JSON exponent");
        while (*p->cursor >= '0' && *p->cursor <= '9') ++p->cursor;
    }
    const char *number = ast_copy_text(start, (size_t)(p->cursor - start), p->line);
    if (!isfinite(strtod(number, NULL))) return ast_json_bad(p, "JSON number exceeds float64 range");
    AstJson *out = ast_json_new(AST_JNUMBER); out->as.text = number; return out;
}
static AstJson *ast_json_parse_internal(const char *text, int line, const char **error) {
    if (strlen(text) >= AST_DATA_LIMIT) { *error = "JSON exceeds the 32 MiB limit"; return NULL; }
    AstJsonParser parser = {text, NULL, line, 0};
    AstJson *value = ast_json_parse_value(&parser, 0);
    ast_json_space(&parser);
    if (value && *parser.cursor) { value = NULL; parser.error = "trailing content after JSON value"; }
    *error = parser.error;
    return value;
}
static AstJson *ast_json_parse(const char *text, int line) {
    const char *error = NULL;
    AstJson *value = ast_json_parse_internal(text, line, &error);
    if (!value) ast_fail(line, error ? error : "invalid JSON");
    return value;
}
static AstJson *ast_json_try_parse(const char *text, int line) {
    const char *error = NULL;
    AstJson *value = ast_json_parse_internal(text, line, &error);
    return value ? value : ast_json_null();
}
