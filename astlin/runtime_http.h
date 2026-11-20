/* Optional native HTTP transport. No shell commands or Python runtime are used. */
static AstJson *ast_http_response(int64_t status, const char *body, const char *error, int line) {
    AstJson *out = ast_json_object();
    out = ast_json_set(out, "status", ast_json_int(status, line), line);
    out = ast_json_set(out, "body", ast_json_string(body), line);
    return ast_json_set(out, "error", ast_json_string(error), line);
}
#ifdef ASTLIN_HTTP
#include <curl/curl.h>
typedef struct { char *data; size_t size, capacity; bool too_large; } AstHttpBuffer;
static size_t ast_http_write(char *data, size_t width, size_t count, void *context) {
    AstHttpBuffer *out = context;
    if (width && count > SIZE_MAX / width) { out->too_large = true; return 0; }
    size_t size = width * count;
    if (size > AST_DATA_LIMIT - 1 - out->size) { out->too_large = true; return 0; }
    size_t needed = out->size + size + 1;
    if (needed > out->capacity) {
        size_t capacity = out->capacity;
        while (capacity < needed) capacity *= 2;
        char *grown = realloc(out->data, capacity);
        if (!grown) return 0;
        out->data = grown; out->capacity = capacity;
    }
    if (size) memcpy(out->data + out->size, data, size);
    out->size += size; out->data[out->size] = 0;
    return size;
}
static bool ast_http_header_name(const char *name) {
    if (!*name) return false;
    for (const unsigned char *p = (const unsigned char *)name; *p; ++p)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-' || *p == '_')) return false;
    return true;
}
static AstJson *ast_http_request(const char *method, const char *url, AstJson *headers,
                                 const char *body, int64_t timeout, int line) {
    if (strncmp(url, "http://", 7) && strncmp(url, "https://", 8)) ast_fail(line, "HTTP URL must use http:// or https://");
    const char *authority = strstr(url, "://") + 3, *end = authority + strcspn(authority, "/?#");
    if (authority == end || memchr(authority, '@', (size_t)(end - authority))) ast_fail(line, "HTTP URL requires a host and must not embed credentials");
    if (strpbrk(url, "\r\n")) ast_fail(line, "HTTP URL must not contain newlines");
    if (!*method || strlen(method) > 16) ast_fail(line, "invalid HTTP method");
    for (const char *p = method; *p; ++p) if (*p < 'A' || *p > 'Z') ast_fail(line, "HTTP method must use uppercase ASCII letters");
    if (timeout <= 0 || timeout > 300000) ast_fail(line, "HTTP timeout must be 1..300000 milliseconds");
    if (strlen(body) >= AST_DATA_LIMIT) ast_fail(line, "HTTP body exceeds 32 MiB");
    ast_json_expect(headers, AST_JOBJECT, line);
    bool authorization = false;
    for (int64_t i = 0; i < headers->as.items.len; ++i) {
        const char *key = headers->as.items.keys[i], *value = ast_json_as_str(headers->as.items.values[i], line);
        if (!ast_http_header_name(key) || strpbrk(value, "\r\n")) ast_fail(line, "invalid HTTP header name or value");
        if (strlen(key) == 13) {
            const char *expected = "authorization";
            bool match = true;
            for (size_t j = 0; j < 13; ++j) if (tolower((unsigned char)key[j]) != expected[j]) match = false;
            if (match) authorization = true;
        }
    }
    if (authorization && !strncmp(url, "http://", 7)) {
        const char *host_end = authority;
        if (*authority == '[') {
            host_end = memchr(authority, ']', (size_t)(end - authority));
            if (host_end) ++host_end; else host_end = end;
        } else {
            host_end = memchr(authority, ':', (size_t)(end - authority));
            if (!host_end) host_end = end;
        }
        size_t length = (size_t)(host_end - authority);
        bool loopback = (length == 9 && (!memcmp(authority, "localhost", 9) || !memcmp(authority, "127.0.0.1", 9))) ||
                        (length == 5 && !memcmp(authority, "[::1]", 5));
        if (!loopback) ast_fail(line, "Authorization requires HTTPS except for loopback model servers");
    }
    static bool initialized = false;
    if (!initialized) {
        if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) return ast_http_response(0, "", "HTTP initialization failed", line);
        if (atexit(curl_global_cleanup) != 0) ast_fail(line, "could not register HTTP cleanup");
        initialized = true;
    }
    CURL *handle = curl_easy_init();
    if (!handle) return ast_http_response(0, "", "HTTP allocation failed", line);
    AstHttpBuffer output = {malloc(64), 0, 64, false};
    if (!output.data) { curl_easy_cleanup(handle); return ast_http_response(0, "", "HTTP allocation failed", line); }
    output.data[0] = 0;
    struct curl_slist *list = NULL;
    CURLcode code = CURLE_OK;
    long status = 0;
    for (int64_t i = 0; i < headers->as.items.len; ++i) {
        AstBuffer header = ast_buffer(line);
        const char *key = headers->as.items.keys[i], *value = headers->as.items.values[i]->as.text;
        ast_buffer_write(&header, key, strlen(key)); ast_buffer_write(&header, ": ", 2); ast_buffer_write(&header, value, strlen(value));
        struct curl_slist *grown = curl_slist_append(list, header.data);
        if (!grown) { code = CURLE_OUT_OF_MEMORY; goto ast_http_finish; }
        list = grown;
    }
#define AST_CURL_SET(option, value) do { code = curl_easy_setopt(handle, option, value); if (code != CURLE_OK) goto ast_http_finish; } while (0)
    AST_CURL_SET(CURLOPT_URL, url);
    AST_CURL_SET(CURLOPT_CUSTOMREQUEST, method);
    AST_CURL_SET(CURLOPT_HTTPHEADER, list);
    AST_CURL_SET(CURLOPT_TIMEOUT_MS, (long)timeout);
    AST_CURL_SET(CURLOPT_CONNECTTIMEOUT_MS, (long)(timeout < 10000 ? timeout : 10000));
    AST_CURL_SET(CURLOPT_NOSIGNAL, 1L);
    AST_CURL_SET(CURLOPT_FOLLOWLOCATION, 0L);
    AST_CURL_SET(CURLOPT_SSL_VERIFYPEER, 1L);
    AST_CURL_SET(CURLOPT_SSL_VERIFYHOST, 2L);
    AST_CURL_SET(CURLOPT_ACCEPT_ENCODING, "");
    AST_CURL_SET(CURLOPT_WRITEFUNCTION, ast_http_write);
    AST_CURL_SET(CURLOPT_WRITEDATA, &output);
    AST_CURL_SET(CURLOPT_MAXFILESIZE_LARGE, (curl_off_t)(AST_DATA_LIMIT - 1));
#if LIBCURL_VERSION_NUM >= 0x075500
    AST_CURL_SET(CURLOPT_PROTOCOLS_STR, "http,https");
#else
    AST_CURL_SET(CURLOPT_PROTOCOLS, (long)(CURLPROTO_HTTP | CURLPROTO_HTTPS));
#endif
    if (!strcmp(method, "HEAD")) { AST_CURL_SET(CURLOPT_NOBODY, 1L); }
    else if (*body || !strcmp(method, "POST") || !strcmp(method, "PUT") || !strcmp(method, "PATCH")) {
        AST_CURL_SET(CURLOPT_POSTFIELDS, body);
        AST_CURL_SET(CURLOPT_POSTFIELDSIZE_LARGE, (curl_off_t)strlen(body));
    }
    code = curl_easy_perform(handle);
    (void)curl_easy_getinfo(handle, CURLINFO_RESPONSE_CODE, &status);
ast_http_finish:
#undef AST_CURL_SET
    curl_slist_free_all(list);
    curl_easy_cleanup(handle);
    bool valid = ast_valid_utf8((const unsigned char *)output.data, output.size);
    const char *error = output.too_large ? "HTTP response exceeds 32 MiB" :
        code != CURLE_OK ? curl_easy_strerror(code) : !valid ? "HTTP response is not valid UTF-8 text" : "";
    const char *text = code == CURLE_OK && valid ? ast_copy_text(output.data, output.size, line) : "";
    free(output.data);
    return ast_http_response((int64_t)status, text, error, line);
}
#else
static AstJson *ast_http_request(const char *method, const char *url, AstJson *headers,
                                 const char *body, int64_t timeout, int line) {
    (void)method; (void)url; (void)headers; (void)body; (void)timeout;
    ast_fail(line, "HTTP support requires an Astlin build with libcurl");
}
#endif
