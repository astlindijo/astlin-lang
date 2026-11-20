/* Astlin runtime. Embedded in generated C so executables need no Astlin installation. */
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <math.h>
#include <ctype.h>
#include <time.h>
#include <sys/stat.h>

static const char *ast_current_source = AST_SOURCE;
static _Noreturn void ast_fail(int line, const char *message);
typedef void (*AstTrace)(const void *);
typedef struct { const void *address; AstTrace trace; } AstRoot;
static AstRoot *ast_roots = NULL;
static size_t ast_root_count = 0, ast_root_capacity = 0;
static size_t ast_heap_size = 0, ast_gc_pressure = 0, ast_object_count = 0, ast_gc_limit = 65536;

typedef struct AstAllocation {
    void *data;
    size_t size;
    bool marked;
    struct AstAllocation *next;
} AstAllocation;
static AstAllocation *ast_allocations = NULL;
static AstAllocation **ast_allocation_map = NULL;
static size_t ast_allocation_capacity = 0;

static void ast_cleanup(void) {
    while (ast_allocations) {
        AstAllocation *item = ast_allocations;
        ast_allocations = item->next;
        free(item->data);
        free(item);
    }
    free(ast_roots);
    free(ast_allocation_map);
    ast_roots = NULL;
    ast_allocation_map = NULL;
    ast_allocation_capacity = 0;
    ast_root_count = ast_root_capacity = ast_heap_size = ast_gc_pressure = ast_object_count = 0;
}

static _Noreturn void ast_fail(int line, const char *message) {
    fflush(stdout);
    fprintf(stderr, "%s:%d: runtime error: %s\n", ast_current_source, line, message);
    exit(1);
}

static size_t ast_pointer_hash(const void *pointer) {
    uintptr_t key = (uintptr_t)pointer;
    key ^= key >> 17;
    key *= (uintptr_t)UINT64_C(0xed5ad4bb);
    key ^= key >> 11;
    return (size_t)key;
}
static void ast_map_place(AstAllocation **table, size_t capacity, AstAllocation *item) {
    size_t slot = ast_pointer_hash(item->data) & (capacity - 1);
    while (table[slot]) slot = (slot + 1) & (capacity - 1);
    table[slot] = item;
}
static bool ast_map_resize(size_t capacity) {
    if (capacity > SIZE_MAX / sizeof(AstAllocation *)) return false;
    AstAllocation **table = calloc(capacity, sizeof(*table));
    if (!table) return false;
    for (AstAllocation *item = ast_allocations; item; item = item->next) ast_map_place(table, capacity, item);
    free(ast_allocation_map);
    ast_allocation_map = table;
    ast_allocation_capacity = capacity;
    return true;
}
static void ast_map_add(AstAllocation *item, int line) {
    if (!ast_allocation_capacity || ast_object_count > ast_allocation_capacity / 2) {
        size_t capacity = ast_allocation_capacity ? ast_allocation_capacity : 128;
        while (ast_object_count > capacity / 2) {
            if (capacity > SIZE_MAX / 2) ast_fail(line, "allocation index is too large");
            capacity *= 2;
        }
        if (!ast_map_resize(capacity)) ast_fail(line, "out of memory indexing allocations");
    } else ast_map_place(ast_allocation_map, ast_allocation_capacity, item);
}

/* Collection only runs at compiler-emitted safe points after values are rooted. */
static void *ast_alloc(size_t size, int line) {
    if (size > SIZE_MAX - ast_heap_size) ast_fail(line, "heap size overflow");
    size_t charge = size ? size : 1;
    if (charge > SIZE_MAX - sizeof(AstAllocation)) ast_fail(line, "heap size overflow");
    charge += sizeof(AstAllocation);
    if (charge > SIZE_MAX - ast_gc_pressure) ast_fail(line, "heap size overflow");
    void *data = malloc(size ? size : 1);
    AstAllocation *item = malloc(sizeof(*item));
    if (!data || !item) {
        free(data);
        free(item);
        ast_fail(line, "out of memory");
    }
    item->data = data;
    item->size = size;
    item->marked = false;
    item->next = ast_allocations;
    ast_allocations = item;
    ast_heap_size += size;
    ast_gc_pressure += charge;
    ++ast_object_count;
    ast_map_add(item, line);
    return data;
}

static size_t ast_roots_mark(void) { return ast_root_count; }
static void ast_roots_restore(size_t mark) { ast_root_count = mark; }
static void ast_root_push(const void *address, AstTrace trace) {
    if (ast_root_count == ast_root_capacity) {
        size_t capacity = ast_root_capacity ? ast_root_capacity * 2 : 64;
        if (capacity < ast_root_capacity || capacity > SIZE_MAX / sizeof(AstRoot))
            ast_fail(0, "too many live values");
        AstRoot *grown = realloc(ast_roots, capacity * sizeof(*grown));
        if (!grown) ast_fail(0, "out of memory");
        ast_roots = grown;
        ast_root_capacity = capacity;
    }
    ast_roots[ast_root_count++] = (AstRoot){address, trace};
}
static bool ast_mark(const void *data) {
    /* Expected O(1) lookup; literal/external pointers are never header-probed. */
    if (!data || !ast_allocation_capacity) return false;
    size_t slot = ast_pointer_hash(data) & (ast_allocation_capacity - 1);
    while (ast_allocation_map[slot] && ast_allocation_map[slot]->data != data)
        slot = (slot + 1) & (ast_allocation_capacity - 1);
    AstAllocation *item = ast_allocation_map[slot];
    if (!item || item->marked) return false;
    item->marked = true;
    return true;
}
static void ast_trace_str(const void *address) { ast_mark(*(const char *const *)address); }
static void ast_gc_collect(void) {
    for (AstAllocation *item = ast_allocations; item; item = item->next) item->marked = false;
    for (size_t i = 0; i < ast_root_count; ++i) ast_roots[i].trace(ast_roots[i].address);
    AstAllocation **link = &ast_allocations;
    while (*link) {
        AstAllocation *item = *link;
        if (item->marked) { link = &item->next; continue; }
        *link = item->next;
        ast_heap_size -= item->size;
        ast_gc_pressure -= (item->size ? item->size : 1) + sizeof(AstAllocation);
        --ast_object_count;
        free(item->data);
        free(item);
    }
    /* Rebuild after sweeping, so the index never retains freed allocation headers. */
    size_t capacity = 128;
    while (ast_object_count > capacity / 2) capacity *= 2;
    bool resized = capacity < ast_allocation_capacity / 4 && ast_map_resize(capacity);
    if (!resized && ast_allocation_capacity) {
        memset(ast_allocation_map, 0, ast_allocation_capacity * sizeof(*ast_allocation_map));
        for (AstAllocation *item = ast_allocations; item; item = item->next)
            ast_map_place(ast_allocation_map, ast_allocation_capacity, item);
    }
    ast_gc_limit = ast_gc_pressure > SIZE_MAX / 2 ? SIZE_MAX : ast_gc_pressure * 2;
    if (ast_gc_limit < 65536) ast_gc_limit = 65536;
}
static void ast_gc_maybe(void) { if (ast_gc_pressure >= ast_gc_limit) ast_gc_collect(); }
static int64_t ast_heap_bytes(void) {
    if (ast_heap_size > INT64_MAX) ast_fail(0, "heap size exceeds int64");
    return (int64_t)ast_heap_size;
}
static int64_t ast_heap_objects(void) {
    if (ast_object_count > INT64_MAX) ast_fail(0, "object count exceeds int64");
    return (int64_t)ast_object_count;
}

static int64_t ast_add(int64_t a, int64_t b, int line) {
    int64_t result;
    if (__builtin_add_overflow(a, b, &result)) ast_fail(line, "integer overflow in addition");
    return result;
}
static int64_t ast_sub(int64_t a, int64_t b, int line) {
    int64_t result;
    if (__builtin_sub_overflow(a, b, &result)) ast_fail(line, "integer overflow in subtraction");
    return result;
}
static int64_t ast_mul(int64_t a, int64_t b, int line) {
    int64_t result;
    if (__builtin_mul_overflow(a, b, &result)) ast_fail(line, "integer overflow in multiplication");
    return result;
}
static int64_t ast_neg(int64_t a, int line) {
    if (a == INT64_MIN) ast_fail(line, "integer overflow in negation");
    return -a;
}
static int64_t ast_div(int64_t a, int64_t b, int line) {
    if (!b) ast_fail(line, "division by zero");
    if (a == INT64_MIN && b == -1) ast_fail(line, "integer overflow in division");
    return a / b;
}
static int64_t ast_mod(int64_t a, int64_t b, int line) {
    if (!b) ast_fail(line, "remainder by zero");
    if (a == INT64_MIN && b == -1) return 0;
    return a % b;
}
static double ast_fdiv(double a, double b, int line) {
    if (b == 0.0) ast_fail(line, "division by zero");
    return a / b;
}

typedef struct { void *data; int64_t len; } AstList;
static void ast_trace_list_int(const void *address) { ast_mark(((const AstList *)address)->data); }
static void ast_trace_list_float(const void *address) { ast_trace_list_int(address); }
static void ast_trace_list_bool(const void *address) { ast_trace_list_int(address); }
static void ast_trace_list_str(const void *address) {
    const AstList *list = address;
    if (ast_mark(list->data)) {
        for (int64_t i = 0; i < list->len; ++i) ast_trace_str(&((const char **)list->data)[i]);
    }
}
static AstList ast_list(const void *data, size_t count, size_t width, int line) {
    if (count > INT64_MAX || count > SIZE_MAX / width) ast_fail(line, "list is too large");
    void *copy = ast_alloc(count * width, line);
    if (count) memcpy(copy, data, count * width);
    return (AstList){copy, (int64_t)count};
}
static int64_t ast_index(AstList list, int64_t index, int line) {
    if (index < 0 || index >= list.len) ast_fail(line, "list index out of bounds");
    return index;
}
static const char *ast_concat(const char *a, const char *b, int line) {
    size_t x = strlen(a), y = strlen(b);
    if (y == SIZE_MAX || x > SIZE_MAX - y - 1) ast_fail(line, "string is too large");
    char *out = ast_alloc(x + y + 1, line);
    memcpy(out, a, x);
    memcpy(out + x, b, y + 1);
    return out;
}
static int64_t ast_strlen(const char *text, int line) {
    size_t count = 0;
    /* Strings originate from validated UTF-8 source/input or ASCII conversions. */
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if ((*p & 0xc0) != 0x80) {
            if (count == INT64_MAX) ast_fail(line, "string is too large");
            ++count;
        }
    }
    return (int64_t)count;
}
static bool ast_valid_utf8(const unsigned char *text, size_t size) {
    for (size_t i = 0; i < size; ++i) {
        uint32_t c = text[i], value, minimum;
        size_t trailing;
        if (!c) return false;
        if (c < 0x80) continue;
        if (c >= 0xc2 && c <= 0xdf) { value = c & 0x1f; minimum = 0x80; trailing = 1; }
        else if (c >= 0xe0 && c <= 0xef) { value = c & 0x0f; minimum = 0x800; trailing = 2; }
        else if (c >= 0xf0 && c <= 0xf4) { value = c & 0x07; minimum = 0x10000; trailing = 3; }
        else return false;
        if (trailing > size - i - 1) return false;
        for (size_t j = 0; j < trailing; ++j) {
            uint32_t next = text[++i];
            if ((next & 0xc0) != 0x80) return false;
            value = (value << 6) | (next & 0x3f);
        }
        if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) return false;
    }
    return true;
}
static const char *ast_input(const char *prompt, int line) {
    fputs(prompt, stdout);
    fflush(stdout);
    size_t size = 0, capacity = 64;
    char *buffer = malloc(capacity);
    if (!buffer) ast_fail(line, "out of memory");
    int c;
    while ((c = fgetc(stdin)) != EOF && c != '\n') {
        if (size == capacity - 1) {
            if (capacity > SIZE_MAX / 2) { free(buffer); ast_fail(line, "input is too large"); }
            capacity *= 2;
            char *grown = realloc(buffer, capacity);
            if (!grown) { free(buffer); ast_fail(line, "out of memory"); }
            buffer = grown;
        }
        buffer[size++] = (char)c;
    }
    if (ferror(stdin)) { free(buffer); ast_fail(line, "could not read input"); }
    if (size && buffer[size - 1] == '\r' && c == '\n') --size;
    if (!ast_valid_utf8((const unsigned char *)buffer, size)) {
        free(buffer);
        ast_fail(line, "input must be valid UTF-8 without NUL characters");
    }
    char *result = ast_alloc(size + 1, line);
    memcpy(result, buffer, size);
    result[size] = 0;
    free(buffer);
    return result;
}
static int64_t ast_float_int(double value, int line) {
    if (!isfinite(value) || value < -0x1p63 || value >= 0x1p63)
        ast_fail(line, "float is outside the int64 range");
    return (int64_t)value;
}
static int64_t ast_parse_int(const char *text, int line) {
    char *end;
    errno = 0;
    intmax_t value = strtoimax(text, &end, 10);
    if (!*text || isspace((unsigned char)*text) || *end || errno == ERANGE || value < INT64_MIN || value > INT64_MAX)
        ast_fail(line, "invalid integer conversion");
    return (int64_t)value;
}
static double ast_parse_float(const char *text, int line) {
    char *end;
    errno = 0;
    double value = strtod(text, &end);
    if (!*text || isspace((unsigned char)*text) || *end || errno == ERANGE || !isfinite(value))
        ast_fail(line, "invalid float conversion");
    return value;
}
static const char *ast_string_int(int64_t value, int line) {
    char *out = ast_alloc(32, line);
    snprintf(out, 32, "%" PRId64, value);
    return out;
}
static void ast_format_float(char *out, double value) {
    snprintf(out, 64, "%.17g", value);
    /* Prefer a shorter representation when it preserves the exact double. */
    for (int precision = 1; precision < 17; ++precision) {
        char candidate[64];
        snprintf(candidate, sizeof(candidate), "%.*g", precision, value);
        if (strlen(candidate) < strlen(out) && strtod(candidate, NULL) == value)
            memcpy(out, candidate, strlen(candidate) + 1);
    }
}
static const char *ast_string_float(double value, int line) {
    char *out = ast_alloc(64, line);
    ast_format_float(out, value);
    return out;
}
static const char *ast_string_bool(bool value, int line) {
    (void)line;
    return value ? "true" : "false";
}
static void ast_print_int(int64_t value) { printf("%" PRId64, value); }
static void ast_print_float(double value) {
    char out[64];
    ast_format_float(out, value);
    fputs(out, stdout);
}
static void ast_print_bool(bool value) { fputs(value ? "true" : "false", stdout); }
static void ast_print_str(const char *value) { fputs(value, stdout); }
enum { AST_INT, AST_FLOAT, AST_BOOL, AST_STR };
static void ast_print_list(AstList list, int kind) {
    fputc('[', stdout);
    for (int64_t i = 0; i < list.len; ++i) {
        if (i) fputs(", ", stdout);
        switch (kind) {
        case AST_INT: ast_print_int(((int64_t *)list.data)[i]); break;
        case AST_FLOAT: ast_print_float(((double *)list.data)[i]); break;
        case AST_BOOL: ast_print_bool(((bool *)list.data)[i]); break;
        case AST_STR:
            fputc('"', stdout);
            ast_print_str(((const char **)list.data)[i]);
            fputc('"', stdout);
            break;
        }
    }
    fputc(']', stdout);
}

typedef struct { int64_t current, stop, step; bool live; } AstRange;
static AstRange ast_range(int64_t start, int64_t stop, int64_t step, int line) {
    if (!step) ast_fail(line, "range step cannot be zero");
    return (AstRange){start, stop, step, true};
}
static bool ast_range_has(AstRange range) {
    return range.live && (range.step > 0 ? range.current < range.stop : range.current > range.stop);
}
static void ast_range_tick(AstRange *range) {
    int64_t next;
    if (__builtin_add_overflow(range->current, range->step, &next)) range->live = false;
    else range->current = next;
}

static size_t ast_size_add(size_t a, size_t b, int line) {
    if (b > SIZE_MAX - a) ast_fail(line, "value is too large");
    return a + b;
}
static const char *ast_copy_text(const char *text, size_t count, int line) {
    char *copy = ast_alloc(ast_size_add(count, 1, line), line);
    if (count) memcpy(copy, text, count);
    copy[count] = 0;
    return copy;
}
static const char *ast_string_slice(const char *text, int64_t start, int64_t stop, int line) {
    int64_t length = ast_strlen(text, line);
    if (start < 0 || stop < start || stop > length) ast_fail(line, "string slice out of bounds");
    const unsigned char *p = (const unsigned char *)text;
    const unsigned char *begin = p;
    int64_t index = 0;
    while (*p && index < stop) {
        if (index == start) begin = p;
        ++p;
        while ((*p & 0xc0) == 0x80) ++p;
        ++index;
    }
    if (start == stop) begin = p;
    return ast_copy_text((const char *)begin, (size_t)(p - begin), line);
}
static const char *ast_string_index(const char *text, int64_t index, int line) {
    int64_t length = ast_strlen(text, line);
    if (index < 0 || index >= length) ast_fail(line, "string index out of bounds");
    return ast_string_slice(text, index, index + 1, line);
}
static AstList ast_list_slice(AstList list, int64_t start, int64_t stop, size_t width, int line) {
    if (start < 0 || stop < start || stop > list.len) ast_fail(line, "list slice out of bounds");
    return ast_list((const char *)list.data + (size_t)start * width, (size_t)(stop - start), width, line);
}
static AstList ast_list_append(AstList list, const void *value, size_t width, int line) {
    int64_t count = ast_add(list.len, 1, line);
    if ((uint64_t)count > SIZE_MAX / width) ast_fail(line, "list is too large");
    char *data = ast_alloc((size_t)count * width, line);
    if (list.len) memcpy(data, list.data, (size_t)list.len * width);
    memcpy(data + (size_t)list.len * width, value, width);
    return (AstList){data, count};
}
static AstList ast_list_updated(AstList list, int64_t index, const void *value, size_t width, int line) {
    ast_index(list, index, line);
    AstList copy = ast_list(list.data, (size_t)list.len, width, line);
    memcpy((char *)copy.data + (size_t)index * width, value, width);
    return copy;
}
static AstList ast_list_concat(AstList a, AstList b, size_t width, int line) {
    int64_t count = ast_add(a.len, b.len, line);
    if ((uint64_t)count > SIZE_MAX / width) ast_fail(line, "list is too large");
    char *data = ast_alloc((size_t)count * width, line);
    if (a.len) memcpy(data, a.data, (size_t)a.len * width);
    if (b.len) memcpy(data + (size_t)a.len * width, b.data, (size_t)b.len * width);
    return (AstList){data, count};
}
static AstList ast_split(const char *text, const char *separator, int line) {
    size_t width = strlen(separator), count = 1;
    if (!width) ast_fail(line, "split separator cannot be empty");
    const char *p = text, *match;
    while ((match = strstr(p, separator))) {
        count = ast_size_add(count, 1, line);
        p = match + width;
    }
    if (count > INT64_MAX || count > SIZE_MAX / sizeof(const char *)) ast_fail(line, "too many split parts");
    const char **data = ast_alloc(count * sizeof(*data), line);
    p = text;
    size_t i = 0;
    while ((match = strstr(p, separator))) {
        data[i++] = ast_copy_text(p, (size_t)(match - p), line);
        p = match + width;
    }
    data[i] = ast_copy_text(p, strlen(p), line);
    return (AstList){data, (int64_t)count};
}
static const char *ast_join(AstList parts, const char *separator, int line) {
    size_t total = 0, width = strlen(separator);
    const char **data = parts.data;
    for (int64_t i = 0; i < parts.len; ++i) {
        if (i) total = ast_size_add(total, width, line);
        total = ast_size_add(total, strlen(data[i]), line);
    }
    char *out = ast_alloc(ast_size_add(total, 1, line), line);
    size_t offset = 0;
    for (int64_t i = 0; i < parts.len; ++i) {
        if (i) { memcpy(out + offset, separator, width); offset += width; }
        size_t size = strlen(data[i]);
        memcpy(out + offset, data[i], size);
        offset += size;
    }
    out[offset] = 0;
    return out;
}
static const char *ast_replace(const char *text, const char *old, const char *replacement, int line) {
    return ast_join(ast_split(text, old, line), replacement, line);
}
static const char *ast_trim(const char *text, int line) {
    while (*text && isspace((unsigned char)*text)) ++text;
    size_t length = strlen(text);
    while (length && isspace((unsigned char)text[length - 1])) --length;
    return ast_copy_text(text, length, line);
}
static bool ast_contains(const char *text, const char *part) { return strstr(text, part) != NULL; }
static bool ast_starts_with(const char *text, const char *prefix) { return strncmp(text, prefix, strlen(prefix)) == 0; }
static bool ast_ends_with(const char *text, const char *suffix) {
    size_t a = strlen(text), b = strlen(suffix);
    return b <= a && strcmp(text + a - b, suffix) == 0;
}
static const char *ast_read_file(const char *path, int line) {
    FILE *file = fopen(path, "rb");
    if (!file) ast_fail(line, "could not open file for reading");
    size_t size = 0, capacity = 4096;
    char *buffer = malloc(capacity);
    if (!buffer) { fclose(file); ast_fail(line, "out of memory"); }
    int c;
    while ((c = fgetc(file)) != EOF) {
        if (size == capacity - 1) {
            if (capacity > SIZE_MAX / 2) { free(buffer); fclose(file); ast_fail(line, "file is too large"); }
            capacity *= 2;
            char *grown = realloc(buffer, capacity);
            if (!grown) { free(buffer); fclose(file); ast_fail(line, "out of memory"); }
            buffer = grown;
        }
        buffer[size++] = (char)c;
    }
    bool failed = ferror(file) != 0;
    if (fclose(file) != 0) failed = true;
    if (failed) { free(buffer); ast_fail(line, "could not read file"); }
    if (!ast_valid_utf8((const unsigned char *)buffer, size)) {
        free(buffer);
        ast_fail(line, "file must contain valid UTF-8 without NUL characters");
    }
    const char *result = ast_copy_text(buffer, size, line);
    free(buffer);
    return result;
}
static void ast_write_file(const char *path, const char *text, int line) {
    FILE *file = fopen(path, "wb");
    if (!file) ast_fail(line, "could not open file for writing");
    size_t size = strlen(text);
    bool failed = fwrite(text, 1, size, file) != size;
    if (fclose(file) != 0) failed = true;
    if (failed) ast_fail(line, "could not write file");
}
static bool ast_file_exists(const char *path) { struct stat info; return stat(path, &info) == 0; }
static int64_t ast_abs(int64_t value, int line) { return value < 0 ? ast_neg(value, line) : value; }
static double ast_sqrt(double value, int line) {
    if (value < 0.0) ast_fail(line, "sqrt requires a nonnegative value");
    return sqrt(value);
}
static double ast_pow(double a, double b, int line) {
    errno = 0;
    double value = pow(a, b);
    if (errno == EDOM || errno == ERANGE || !isfinite(value)) ast_fail(line, "pow domain or range error");
    return value;
}
static double ast_clock(void) { return (double)clock() / CLOCKS_PER_SEC; }
static AstList ast_cli_args;
static void ast_init_args(int argc, char **argv) {
    size_t count = argc > 0 ? (size_t)argc - 1 : 0;
    if (count > INT64_MAX || count > SIZE_MAX / sizeof(const char *)) ast_fail(0, "too many arguments");
    const char **data = ast_alloc(count * sizeof(*data), 0);
    for (size_t i = 0; i < count; ++i) {
        const char *text = argv[i + 1];
        if (!ast_valid_utf8((const unsigned char *)text, strlen(text))) ast_fail(0, "arguments must be valid UTF-8");
        data[i] = text;
    }
    ast_cli_args = (AstList){data, (int64_t)count};
    ast_root_push(&ast_cli_args, ast_trace_list_str);
}
static AstList ast_args(void) { return ast_cli_args; }
