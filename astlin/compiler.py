#!/usr/bin/env python3
"""Astlin: a statically checked language that compiles to C11."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = "0.2.0"
SCALARS = {"int", "float", "bool", "str"}
TYPE_NAMES = SCALARS | {"void", "list"}
RESERVED = {"fn", "let", "var", "if", "else", "while", "for", "in", "return",
            "break", "continue", "true", "false", "and", "or", "not", "import", "as", "struct"}
BUILTINS = {"print", "input", "len", "int", "float", "str", "assert", "range",
            "append", "updated", "concat", "slice", "split", "join", "trim", "contains",
            "starts_with", "ends_with", "replace", "read_file", "write_file", "file_exists",
            "args", "sqrt", "pow", "abs", "min", "max", "clock", "gc_collect", "heap_bytes", "heap_objects"}


@dataclass
class Token:
    kind: str
    text: str
    line: int
    col: int
    source: str = ""


class AstlinError(Exception):
    def __init__(self, token: Token, message: str):
        self.token, self.message = token, message
        super().__init__(message)


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    i, line, col, depth = 0, 1, 1, 0
    pattern = re.compile(r'(?:\d+\.\d*(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+|\d+)')
    while i < len(source):
        c = source[i]
        if c in " \t\r":
            i += 1
            col += 1
            continue
        if c == "#":
            while i < len(source) and source[i] != "\n":
                i += 1
                col += 1
            continue
        t = Token("", c, line, col)
        if c == "\n":
            if depth == 0:
                tokens.append(Token("NL", "\n", line, col))
            i += 1
            line += 1
            col = 1
            continue
        if c == '"':
            start = i
            i += 1
            while i < len(source) and source[i] != '"':
                if source[i] == "\n":
                    raise AstlinError(t, "a string cannot contain a literal newline; use \\n")
                if source[i] == "\\":
                    i += 1
                i += 1
            if i >= len(source):
                raise AstlinError(t, "unterminated string")
            i += 1
            raw = source[start:i]
            try:
                value = json.loads(raw)
                value.encode("utf-8")
            except (ValueError, UnicodeError):
                raise AstlinError(t, "invalid string escape or Unicode character") from None
            if "\0" in value:
                raise AstlinError(t, "strings cannot contain a NUL character")
            tokens.append(Token("STRING", raw, line, col))
            col += len(raw)
            continue
        if c.isascii() and (c.isalpha() or c == "_"):
            start = i
            while i < len(source) and source[i].isascii() and (source[i].isalnum() or source[i] == "_"):
                i += 1
            value = source[start:i]
            tokens.append(Token("NAME", value, line, col))
            col += len(value)
            continue
        if c.isascii() and c.isdigit():
            match = pattern.match(source, i)
            assert match
            value = match.group()
            tokens.append(Token("NUMBER", value, line, col))
            i += len(value)
            col += len(value)
            continue
        pair = source[i:i+2]
        if pair in {"->", "==", "!=", "<=", ">="}:
            tokens.append(Token("OP", pair, line, col))
            i += 2
            col += 2
            continue
        if c in "(){}[],:;.+-*/%<>=":
            tokens.append(Token("OP", c, line, col))
            if c in "([":
                depth += 1
            elif c in ")]":
                depth = max(0, depth - 1)
            i += 1
            col += 1
            continue
        raise AstlinError(t, f"unexpected character {c!r}")
    tokens.append(Token("EOF", "<end>", line, col))
    return tokens


@dataclass
class Node:
    kind: str
    token: Token
    value: object = None
    children: list = field(default_factory=list)


@dataclass
class Function:
    token: Token
    params: list[tuple[Token, str]]
    result: str
    body: list[Node]
    symbol: str = ""
    reserved_names: set[str] = field(default_factory=set)


@dataclass
class Record:
    token: Token
    fields: list[tuple[Token, str]]
    symbol: str = ""


@dataclass
class Import:
    token: Token
    path: str
    alias: Token


class Parser:
    PRECEDENCE = {"or": 1, "and": 2, "==": 3, "!=": 3, "<": 4, "<=": 4,
                  ">": 4, ">=": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}

    def __init__(self, tokens: list[Token]):
        self.tokens, self.i = tokens, 0
        self.imports: list[Import] = []
        self.records: list[Record] = []

    @property
    def current(self):
        return self.tokens[self.i]

    def accept(self, text):
        if self.current.text == text:
            t = self.current
            self.i += 1
            return t
        return None

    def expect(self, text):
        t = self.accept(text)
        if not t:
            raise AstlinError(self.current, f"expected {text!r}, found {self.current.text!r}")
        return t

    def name(self):
        t = self.current
        if t.kind != "NAME" or t.text in RESERVED:
            raise AstlinError(t, "expected an identifier")
        self.i += 1
        return t

    def separators(self):
        while self.current.kind == "NL" or self.current.text == ";":
            self.i += 1

    def end_statement(self):
        if self.current.kind not in {"NL", "EOF"} and self.current.text not in {";", "}"}:
            raise AstlinError(self.current, "expected a newline or ';' after the statement")
        self.separators()

    def type_name(self, allow_void=False):
        t = self.name()
        if t.text == "list":
            self.expect("[")
            elem = self.name()
            if elem.text not in SCALARS:
                raise AstlinError(elem, "list elements must be int, float, bool, or str")
            self.expect("]")
            return f"list[{elem.text}]"
        if t.text == "void" and not allow_void:
            raise AstlinError(t, "void is only allowed as a function return type")
        if self.accept("."):
            return t.text + "." + self.name().text
        return t.text

    def parse(self):
        functions = []
        self.separators()
        while self.current.kind != "EOF":
            if token := self.accept("import"):
                path = self.current
                if path.kind != "STRING":
                    raise AstlinError(path, "expected an .ad module path in double quotes")
                self.i += 1
                self.expect("as")
                self.imports.append(Import(token, json.loads(path.text), self.name()))
                self.end_statement()
                continue
            if self.accept("struct"):
                name = self.name()
                self.separators()
                self.expect("{")
                fields = []
                self.separators()
                while not self.accept("}"):
                    key = self.name()
                    self.expect(":")
                    fields.append((key, self.type_name()))
                    self.end_statement()
                self.records.append(Record(name, fields))
                self.separators()
                continue
            self.expect("fn")
            name = self.name()
            self.expect("(")
            params = []
            if self.current.text != ")":
                while True:
                    param = self.name()
                    self.expect(":")
                    params.append((param, self.type_name()))
                    if not self.accept(",") or self.current.text == ")":
                        break
            self.expect(")")
            result = self.type_name(allow_void=True) if self.accept("->") else "void"
            functions.append(Function(name, params, result, self.block()))
            self.separators()
        return functions

    def block(self):
        self.separators()
        self.expect("{")
        self.separators()
        body = []
        while not self.accept("}"):
            if self.current.kind == "EOF":
                raise AstlinError(self.current, "expected '}' to close the block")
            body.append(self.statement())
            self.separators()
        return body

    def statement(self):
        t = self.current
        if t.text in {"let", "var"}:
            self.i += 1
            name = self.name()
            annotation = self.type_name() if self.accept(":") else None
            self.expect("=")
            node = Node(t.text, name, annotation, [self.expression()])
        elif self.accept("if"):
            cond = self.expression()
            yes = self.block()
            # Newlines may separate a closing brace from `else`.
            self.separators()
            no = []
            if self.accept("else"):
                no = [self.statement()] if self.current.text == "if" else self.block()
            return Node("if", t, children=[cond, yes, no])
        elif self.accept("while"):
            cond = self.expression()
            return Node("while", t, children=[cond, self.block()])
        elif self.accept("for"):
            name = self.name()
            self.expect("in")
            iterable = self.expression()
            return Node("for", name, children=[iterable, self.block()])
        elif self.accept("return"):
            empty = self.current.kind in {"NL", "EOF"} or self.current.text in {";", "}"}
            node = Node("return", t, children=[] if empty else [self.expression()])
        elif t.text in {"break", "continue"}:
            self.i += 1
            node = Node(t.text, t)
        elif t.kind == "NAME" and self.tokens[self.i+1].text == "=":
            self.i += 2
            node = Node("assign", t, children=[self.expression()])
        else:
            node = Node("expr", t, children=[self.expression()])
        self.end_statement()
        return node

    def expression(self, minimum=0):
        t = self.current
        self.i += 1
        if t.text in {"-", "+", "not"}:
            left = Node("unary", t, t.text, [self.expression(7)])
        elif t.kind == "NUMBER":
            left = Node("number", t, t.text)
        elif t.kind == "STRING":
            left = Node("string", t, json.loads(t.text))
        elif t.text in {"true", "false"}:
            left = Node("bool", t, t.text)
        elif t.kind == "NAME" and t.text not in RESERVED:
            left = Node("name", t, t.text)
        elif t.text == "(":
            left = self.expression()
            self.expect(")")
        elif t.text == "[":
            items = []
            if self.current.text != "]":
                while True:
                    items.append(self.expression())
                    if not self.accept(",") or self.current.text == "]":
                        break
            self.expect("]")
            left = Node("list", t, children=items)
        else:
            raise AstlinError(t, f"expected an expression, found {t.text!r}")
        while True:
            if self.accept("("):
                if left.kind not in {"name", "member"}:
                    raise AstlinError(left.token, "only named functions can be called")
                args = []
                if self.current.text != ")":
                    while True:
                        args.append(self.expression())
                        if not self.accept(",") or self.current.text == ")":
                            break
                self.expect(")")
                left = Node("call", left.token, left.value if left.kind == "name" else left, args)
            elif self.accept("."):
                member = self.name()
                left = Node("member", member, member.text, [left])
            elif self.accept("["):
                index = self.expression()
                self.expect("]")
                left = Node("index", left.token, children=[left, index])
            else:
                op = self.current
                precedence = self.PRECEDENCE.get(op.text, -1)
                if precedence < minimum:
                    break
                self.i += 1
                right = self.expression(precedence + 1)
                left = Node("binary", op, op.text, [left, right])
        return left


def is_list(typ):
    return typ.startswith("list[")


def element(typ):
    return typ[5:-1]


def ctype(typ):
    return {"int": "int64_t", "float": "double", "bool": "bool", "str": "const char *",
            "void": "void"}.get(typ, "AstList" if is_list(typ) else f"AstRecord_{c_symbol(typ)}")


def c_symbol(name):
    # Internal module names contain punctuation that source identifiers cannot use.
    return "main" if name == "main" else "s_" + name.encode("utf-8").hex()


def trace_function(typ):
    if typ == "str":
        return "ast_trace_str"
    if is_list(typ):
        return "ast_trace_list_" + element(typ)
    if typ not in SCALARS and typ != "void":
        return "ast_trace_record_" + c_symbol(typ)
    return None


def cstring(text):
    return '"' + ''.join(chr(b) if 32 <= b <= 126 and b not in (34, 92)
                         else f"\\{b:03o}" for b in text.encode("utf-8")) + '"'


class ModuleLoader:
    """Resolve imports once and give each module its own function/type namespace."""
    def __init__(self):
        self.functions: list[Function] = []
        self.records: list[Record] = []
        self.sources: dict[Path, str] = {}
        self.cache: dict[Path, dict[str, str]] = {}
        self.active: list[Path] = []
        self.counter = 0

    def load(self, source, filename, root=True, via=None):
        path = Path(filename).resolve()
        if path in self.active:
            chain = " -> ".join(p.name for p in [*self.active, path])
            raise AstlinError(via, f"cyclic import: {chain}")
        if path in self.cache:
            return self.cache[path]
        self.active.append(path)
        self.sources[path] = source
        prefix = "" if root else f"@{self.counter}:"
        self.counter += 1
        try:
            tokens = lex(source)
        except AstlinError as error:
            error.token.source = str(filename)
            raise
        for token in tokens:
            token.source = str(filename)
        parser = Parser(tokens)
        functions = parser.parse()
        declarations = [*functions, *parser.records]
        local = {}
        for declaration in declarations:
            name = declaration.token.text
            if name in local or name in BUILTINS or name in TYPE_NAMES:
                raise AstlinError(declaration.token, f"name {name!r} is already defined or reserved")
            declaration.symbol = prefix + name
            local[name] = declaration.symbol
        aliases = {}
        for imp in parser.imports:
            alias = imp.alias.text
            if alias in aliases or alias in local or alias in BUILTINS:
                raise AstlinError(imp.alias, f"import alias {alias!r} is already defined or reserved")
            if Path(imp.path).suffix != ".ad":
                raise AstlinError(imp.token, "import paths must use the .ad extension")
            target = path.parent / imp.path
            if not target.is_file() and imp.path.startswith("std/"):
                target = Path(__file__).parent / imp.path
            try:
                imported = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise AstlinError(imp.token, f"cannot import {imp.path!r}: {error}") from None
            aliases[alias] = self.load(imported, str(target.resolve()), root=False, via=imp.token)

        def resolve_type(typ):
            if "." in typ:
                namespace, key = typ.split(".", 1)
                return aliases.get(namespace, {}).get(key, typ)
            return local.get(typ, typ)

        def visit(node):
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if node.kind in {"let", "var"} and node.value:
                node.value = resolve_type(node.value)
            if node.kind == "call":
                if isinstance(node.value, Node):
                    member = node.value
                    base = member.children[0]
                    if base.kind != "name" or base.value not in aliases:
                        raise AstlinError(member.token, "method calls are not supported; use a named function")
                    exports = aliases[base.value]
                    if member.value not in exports or member.value == "main":
                        raise AstlinError(member.token, f"module {base.value!r} has no export {member.value!r}")
                    node.value = exports[member.value]
                else:
                    node.value = local.get(node.value, node.value)
            for child in node.children:
                visit(child)

        for record in parser.records:
            record.fields = [(token, resolve_type(typ)) for token, typ in record.fields]
        for function in functions:
            function.reserved_names = set(local) | set(aliases) | TYPE_NAMES
            function.params = [(token, resolve_type(typ)) for token, typ in function.params]
            function.result = resolve_type(function.result)
            visit(function.body)
        self.records.extend(parser.records)
        self.functions.extend(functions)
        self.cache[path] = local
        self.active.pop()
        return local


@dataclass
class Binding:
    typ: str
    mutable: bool
    c_name: str


class Compiler:
    def __init__(self, functions: list[Function], records=()):
        self.records = {}
        for record in records:
            name = record.symbol or record.token.text
            if name in self.records or name in BUILTINS:
                raise AstlinError(record.token, f"struct {name!r} is already defined or reserved")
            self.records[name] = record
        self.functions = {}
        for f in functions:
            name = f.symbol or f.token.text
            if name in self.functions or name in self.records or name in BUILTINS:
                raise AstlinError(f.token, f"function {name!r} is already defined or reserved")
            self.functions[name] = f
        self.scopes: list[dict[str, Binding]] = []
        self.lines: list[str] = []
        self.indent, self.counter, self.loop_depth = 0, 0, 0
        self.result = "void"
        self.loop_marks = []
        self.function_mark = self.saved_source = ""
        self.name_reserved = set()

    def validate_types(self):
        known = SCALARS | set(self.records)
        def check(typ, token, allow_void=False):
            if typ in known or (allow_void and typ == "void"):
                return
            if is_list(typ) and element(typ) in SCALARS:
                return
            raise AstlinError(token, f"unknown type {typ!r}")
        for record in self.records.values():
            names = set()
            for token, typ in record.fields:
                if token.text in names:
                    raise AstlinError(token, f"duplicate field {token.text!r}")
                names.add(token.text)
                check(typ, token)
        for function in self.functions.values():
            check(function.result, function.token, True)
            for token, typ in function.params:
                check(typ, token)
        order, active, visited = [], set(), set()
        def visit(name):
            if name in active:
                raise AstlinError(self.records[name].token, "recursive struct types are not supported")
            if name in visited:
                return
            active.add(name)
            for _, typ in self.records[name].fields:
                if typ in self.records:
                    visit(typ)
            active.remove(name)
            visited.add(name)
            order.append(name)
        for name in self.records:
            visit(name)
        return order

    def write(self, line):
        self.lines.append("    " * self.indent + line)

    def unique(self, prefix="t"):
        self.counter += 1
        return f"ast_{prefix}_{self.counter}"

    def temp(self, typ, code):
        name = self.unique()
        self.write(f"{ctype(typ)} {name} = {code};")
        self.root(typ, name)
        return typ, name

    def root(self, typ, name):
        if trace := trace_function(typ):
            self.write(f"ast_root_push(&{name}, {trace});")
            self.write("ast_gc_maybe();")

    def root_mark(self):
        mark = self.unique("roots")
        self.write(f"size_t {mark} = ast_roots_mark();")
        return mark

    def return_cleanup(self):
        self.write(f"ast_roots_restore({self.function_mark});")
        self.write(f"ast_current_source = {self.saved_source};")

    def bind(self, token, typ, mutable=False):
        if token.text in self.scopes[-1] or token.text in self.name_reserved or token.text in BUILTINS:
            raise AstlinError(token, f"name {token.text!r} is already defined or reserved")
        binding = Binding(typ, mutable, self.unique("v"))
        self.scopes[-1][token.text] = binding
        return binding

    def lookup(self, token):
        for scope in reversed(self.scopes):
            if token.text in scope:
                return scope[token.text]
        raise AstlinError(token, f"unknown variable {token.text!r}")

    @staticmethod
    def require(actual, expected, token):
        if actual != expected:
            raise AstlinError(token, f"expected {expected}, found {actual}")

    def expr(self, node: Node, expected=None):
        k, t, v, cs = node.kind, node.token, node.value, node.children
        if k == "number":
            if any(c in v for c in ".eE"):
                import math
                try:
                    value = float(v)
                except ValueError:
                    raise AstlinError(t, "invalid float literal") from None
                if not math.isfinite(value):
                    raise AstlinError(t, "float literal is outside the finite float64 range")
                return "float", repr(value)
            # Compare text first to avoid the host's huge-integer conversion limit.
            digits = v.lstrip("0") or "0"
            if len(digits) > 19 or int(digits) > 9223372036854775807:
                raise AstlinError(t, "integer literal is outside the signed 64-bit range")
            return "int", f"INT64_C({int(digits)})"
        if k == "string":
            return "str", cstring(v)
        if k == "bool":
            return "bool", v
        if k == "name":
            b = self.lookup(t)
            return b.typ, b.c_name
        if k == "member":
            typ, code = self.expr(cs[0])
            if typ not in self.records:
                raise AstlinError(t, f"field access requires a struct, found {typ}")
            for token, field_type in self.records[typ].fields:
                if token.text == v:
                    return self.temp(field_type, f"{code}.field_{v}")
            raise AstlinError(t, f"struct {self.records[typ].token.text!r} has no field {v!r}")
        if k == "unary":
            if v == "-" and cs[0].kind == "number" and cs[0].value.lstrip("0") == "9223372036854775808":
                return "int", "INT64_MIN"
            typ, code = self.expr(cs[0])
            if v == "not":
                self.require(typ, "bool", t)
                return self.temp("bool", f"!({code})")
            if typ not in {"int", "float"}:
                raise AstlinError(t, f"unary {v!r} requires a number")
            if v == "+":
                return typ, code
            return self.temp(typ, f"ast_neg({code}, {t.line})" if typ == "int" else f"-({code})")
        if k == "list":
            elem = element(expected) if expected and is_list(expected) else None
            values = []
            for item in cs:
                typ, code = self.expr(item, elem)
                if typ not in SCALARS:
                    raise AstlinError(item.token, "nested lists and void elements are not supported")
                if elem is None:
                    elem = typ
                self.require(typ, elem, item.token)
                values.append(code)
            if elem is None:
                raise AstlinError(t, "an empty list needs a type, e.g. let xs: list[int] = []")
            data = f"({ctype(elem)}[]){{{', '.join(values)}}}" if values else "NULL"
            return self.temp(f"list[{elem}]", f"ast_list({data}, {len(values)}, sizeof({ctype(elem)}), {t.line})")
        if k == "index":
            typ, data = self.expr(cs[0])
            ityp, index = self.expr(cs[1])
            if typ == "str":
                self.require(ityp, "int", cs[1].token)
                return self.temp("str", f"ast_string_index({data}, {index}, {t.line})")
            if not is_list(typ):
                raise AstlinError(t, "indexing requires a list")
            self.require(ityp, "int", cs[1].token)
            elem = element(typ)
            return self.temp(elem, f"(({ctype(elem)} *){data}.data)[ast_index({data}, {index}, {t.line})]")
        if k == "binary":
            lt, left = self.expr(cs[0])
            if v in {"and", "or"}:
                self.require(lt, "bool", t)
                _, output = self.temp("bool", left)
                self.write(f"if ({'!' if v == 'or' else ''}{output}) {{")
                self.indent += 1
                mark = self.root_mark()
                rt, right = self.expr(cs[1])
                self.require(rt, "bool", t)
                self.write(f"{output} = {right};")
                self.write(f"ast_roots_restore({mark});")
                self.indent -= 1
                self.write("}")
                return "bool", output
            rt, right = self.expr(cs[1])
            numeric = lt in {"int", "float"} and rt in {"int", "float"}
            if v in {"==", "!="}:
                if not numeric and (lt != rt or lt not in SCALARS):
                    raise AstlinError(t, f"cannot compare {lt} and {rt}")
                code = f"(strcmp({left}, {right}) {v} 0)" if lt == "str" else f"({left} {v} {right})"
                if numeric and lt != rt:
                    raise AstlinError(t, "mixed numeric comparisons require an explicit float() conversion")
                return self.temp("bool", code)
            if v in {"<", "<=", ">", ">="}:
                if not numeric and not (lt == rt == "str"):
                    raise AstlinError(t, "ordering requires two numbers of the same type or two strings")
                if numeric and lt != rt:
                    raise AstlinError(t, "mixed numeric comparisons require an explicit float() conversion")
                code = f"(strcmp({left}, {right}) {v} 0)" if lt == "str" else f"({left} {v} {right})"
                return self.temp("bool", code)
            if v == "+" and lt == rt == "str":
                return self.temp("str", f"ast_concat({left}, {right}, {t.line})")
            if not numeric or lt != rt:
                raise AstlinError(t, "arithmetic requires numbers of the same type; use int() or float() explicitly")
            if lt == "int":
                helper = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}[v]
                return self.temp("int", f"ast_{helper}({left}, {right}, {t.line})")
            if v == "%":
                raise AstlinError(t, "'%' requires integers")
            code = f"ast_fdiv({left}, {right}, {t.line})" if v == "/" else f"({left} {v} {right})"
            return self.temp("float", code)
        if k == "call":
            return self.call(node)
        raise AssertionError(k)

    def call(self, node):
        name, args, t = node.value, node.children, node.token
        if name in self.records:
            record = self.records[name]
            if len(args) != len(record.fields):
                raise AstlinError(t, f"{record.token.text}() expects {len(record.fields)} fields, found {len(args)}")
            values = []
            for arg, (key, expected) in zip(args, record.fields):
                typ, code = self.expr(arg, expected)
                self.require(typ, expected, arg.token)
                values.append(f".field_{key.text} = {code}")
            return self.temp(name, f"({ctype(name)}){{{', '.join(values) or '0'}}}")
        if name in BUILTINS - {"range", "input", "print", "len", "int", "float", "str", "assert"}:
            return self.library_call(node)
        if name == "range":
            raise AstlinError(t, "range() can only be used directly in a for loop")
        if name == "input":
            if len(args) > 1:
                raise AstlinError(t, "input() expects zero arguments or one string prompt")
            code = '""'
            if args:
                typ, code = self.expr(args[0])
                self.require(typ, "str", args[0].token)
            return self.temp("str", f"ast_input({code}, {t.line})")
        if name == "print":
            values = [self.expr(arg) for arg in args]
            for i, (typ, code) in enumerate(values):
                if typ == "void":
                    raise AstlinError(args[i].token, "cannot print a void value")
                if i:
                    self.write("fputc(' ', stdout);")
                self.print_value(typ, code)
            self.write("fputc('\\n', stdout);")
            return "void", ""
        if name in {"len", "int", "float", "str", "assert"}:
            if len(args) != 1:
                raise AstlinError(t, f"{name}() expects one argument")
            typ, code = self.expr(args[0])
            if name == "len":
                if typ == "str":
                    return self.temp("int", f"ast_strlen({code}, {t.line})")
                if is_list(typ):
                    return self.temp("int", f"{code}.len")
                raise AstlinError(t, "len() requires a string or list")
            if name == "assert":
                self.require(typ, "bool", t)
                self.write(f"if (!({code})) ast_fail({t.line}, \"assertion failed\");")
                return "void", ""
            if typ not in SCALARS:
                raise AstlinError(t, f"{name}() requires a scalar value")
            if name == typ:
                return typ, code
            if name == "str":
                return self.temp("str", f"ast_string_{typ}({code}, {t.line})")
            if name == "int":
                if typ == "str":
                    return self.temp("int", f"ast_parse_int({code}, {t.line})")
                if typ == "float":
                    return self.temp("int", f"ast_float_int({code}, {t.line})")
                return self.temp("int", f"(int64_t){code}")
            if typ == "str":
                return self.temp("float", f"ast_parse_float({code}, {t.line})")
            return self.temp("float", f"(double){code}")
        if name not in self.functions:
            raise AstlinError(t, f"unknown function {name!r}")
        f = self.functions[name]
        if len(args) != len(f.params):
            raise AstlinError(t, f"{name}() expects {len(f.params)} arguments, found {len(args)}")
        values = []
        for arg, (_, expected) in zip(args, f.params):
            typ, code = self.expr(arg, expected)
            self.require(typ, expected, arg.token)
            values.append(code)
        call = f"ast_fn_{c_symbol(name)}({', '.join(values)})"
        if f.result == "void":
            self.write(call + ";")
            return "void", ""
        return self.temp(f.result, call)

    def print_value(self, typ, code):
        if is_list(typ):
            self.write(f"ast_print_list({code}, AST_{element(typ).upper()});")
        elif typ in self.records:
            record = self.records[typ]
            self.write(f"fputs({cstring(record.token.text + '(')}, stdout);")
            for i, (key, field_type) in enumerate(record.fields):
                self.write(f"fputs({cstring((', ' if i else '') + key.text + ': ')}, stdout);")
                self.print_value(field_type, f"{code}.field_{key.text}")
            self.write("fputc(')', stdout);")
        else:
            self.write(f"ast_print_{typ}({code});")

    def library_call(self, node):
        name, args, token = node.value, node.children, node.token
        fixed = {
            "args": ([], "list[str]", "ast_args", False),
            "gc_collect": ([], "void", "ast_gc_collect", False),
            "heap_bytes": ([], "int", "ast_heap_bytes", False),
            "heap_objects": ([], "int", "ast_heap_objects", False),
            "clock": ([], "float", "ast_clock", False),
            "read_file": (["str"], "str", "ast_read_file", True),
            "write_file": (["str", "str"], "void", "ast_write_file", True),
            "file_exists": (["str"], "bool", "ast_file_exists", False),
            "split": (["str", "str"], "list[str]", "ast_split", True),
            "join": (["list[str]", "str"], "str", "ast_join", True),
            "trim": (["str"], "str", "ast_trim", True),
            "replace": (["str", "str", "str"], "str", "ast_replace", True),
            "contains": (["str", "str"], "bool", "ast_contains", False),
            "starts_with": (["str", "str"], "bool", "ast_starts_with", False),
            "ends_with": (["str", "str"], "bool", "ast_ends_with", False),
            "sqrt": (["float"], "float", "ast_sqrt", True),
            "pow": (["float", "float"], "float", "ast_pow", True),
        }
        if name in fixed:
            params, result, helper, line = fixed[name]
            if len(args) != len(params):
                raise AstlinError(token, f"{name}() expects {len(params)} arguments, found {len(args)}")
            values = []
            for arg, expected in zip(args, params):
                typ, code = self.expr(arg, expected)
                self.require(typ, expected, arg.token)
                values.append(code)
            if line:
                values.append(str(token.line))
            code = f"{helper}({', '.join(values)})"
            if result == "void":
                self.write(code + ";")
                return "void", ""
            return self.temp(result, code)
        count = {"append": 2, "updated": 3, "concat": 2, "slice": 3,
                 "abs": 1, "min": 2, "max": 2}[name]
        if len(args) != count:
            raise AstlinError(token, f"{name}() expects {count} arguments, found {len(args)}")
        typ, first = self.expr(args[0])
        if name in {"abs", "min", "max"}:
            if typ not in {"int", "float"}:
                raise AstlinError(token, f"{name}() requires numbers")
            if name == "abs":
                code = f"ast_abs({first}, {token.line})" if typ == "int" else f"fabs({first})"
            else:
                other, second = self.expr(args[1])
                self.require(other, typ, args[1].token)
                op = "<" if name == "min" else ">"
                code = f"({first} {op} {second} ? {first} : {second})"
            return self.temp(typ, code)
        if name == "slice":
            if typ != "str" and not is_list(typ):
                raise AstlinError(token, "slice() requires a string or list")
            values = []
            for arg in args[1:]:
                atype, code = self.expr(arg)
                self.require(atype, "int", arg.token)
                values.append(code)
            if typ == "str":
                code = f"ast_string_slice({first}, {', '.join(values)}, {token.line})"
            else:
                code = f"ast_list_slice({first}, {', '.join(values)}, sizeof({ctype(element(typ))}), {token.line})"
            return self.temp(typ, code)
        if name == "concat" and typ == "str":
            other, second = self.expr(args[1])
            self.require(other, "str", args[1].token)
            return self.temp("str", f"ast_concat({first}, {second}, {token.line})")
        if not is_list(typ):
            raise AstlinError(token, f"{name}() requires a list")
        elem = element(typ)
        if name == "concat":
            other, second = self.expr(args[1], typ)
            self.require(other, typ, args[1].token)
            return self.temp(typ, f"ast_list_concat({first}, {second}, sizeof({ctype(elem)}), {token.line})")
        index = None
        if name == "updated":
            atype, index = self.expr(args[1])
            self.require(atype, "int", args[1].token)
        other, value = self.expr(args[-1])
        self.require(other, elem, args[-1].token)
        item = f"({ctype(elem)}[]){{{value}}}"
        params = f"{first}, " + (f"{index}, " if index else "")
        helper = "ast_list_updated" if name == "updated" else "ast_list_append"
        return self.temp(typ, f"{helper}({params}{item}, sizeof({ctype(elem)}), {token.line})")

    def block(self, body, scoped=True):
        if scoped:
            self.scopes.append({})
            mark = self.root_mark()
        returns = False
        for node in body:
            returns = self.statement(node) or returns
            self.write("ast_gc_maybe();")
        if scoped:
            self.write(f"ast_roots_restore({mark});")
            self.scopes.pop()
        return returns

    def statement(self, n):
        k, t, cs = n.kind, n.token, n.children
        if k in {"let", "var"}:
            if n.value and n.value not in SCALARS and n.value not in self.records and not is_list(n.value):
                raise AstlinError(t, f"unknown type {n.value!r}")
            typ, code = self.expr(cs[0], n.value)
            if typ == "void":
                raise AstlinError(t, "cannot bind a void value")
            if n.value:
                self.require(typ, n.value, t)
            binding = self.bind(t, typ, k == "var")
            self.write(f"{ctype(typ)} {binding.c_name} = {code};")
            self.root(typ, binding.c_name)
        elif k == "assign":
            b = self.lookup(t)
            if not b.mutable:
                raise AstlinError(t, f"{t.text!r} is immutable; declare it with 'var' to allow assignment")
            typ, code = self.expr(cs[0], b.typ)
            self.require(typ, b.typ, t)
            self.write(f"{b.c_name} = {code};")
        elif k == "expr":
            self.expr(cs[0])
        elif k == "return":
            if cs:
                typ, code = self.expr(cs[0], self.result)
                if self.result == "void":
                    raise AstlinError(t, "a void function cannot return a value")
                self.require(typ, self.result, t)
                self.return_cleanup()
                self.write(f"return {code};")
            else:
                self.require("void", self.result, t)
                self.return_cleanup()
                self.write("return;")
            return True
        elif k == "if":
            typ, code = self.expr(cs[0])
            self.require(typ, "bool", cs[0].token)
            self.write(f"if ({code}) {{")
            self.indent += 1
            yes = self.block(cs[1])
            self.indent -= 1
            self.write("} else {")
            self.indent += 1
            no = self.block(cs[2])
            self.indent -= 1
            self.write("}")
            return yes and no
        elif k == "while":
            self.write("while (true) {")
            self.indent += 1
            loop_mark = self.root_mark()
            typ, code = self.expr(cs[0])
            self.require(typ, "bool", cs[0].token)
            self.write(f"if (!({code})) {{ ast_roots_restore({loop_mark}); break; }}")
            self.loop_depth += 1
            self.loop_marks.append(loop_mark)
            self.block(cs[1])
            self.loop_marks.pop()
            self.loop_depth -= 1
            self.write(f"ast_roots_restore({loop_mark});")
            self.write("ast_gc_maybe();")
            self.indent -= 1
            self.write("}")
        elif k == "for":
            iterable = cs[0]
            if iterable.kind == "call" and iterable.value == "range":
                if len(iterable.children) not in {1, 2, 3}:
                    raise AstlinError(iterable.token, "range() expects one, two, or three integer arguments")
                args = []
                for arg in iterable.children:
                    typ, code = self.expr(arg)
                    self.require(typ, "int", arg.token)
                    args.append(code)
                if len(args) == 1:
                    args.insert(0, "0")
                if len(args) == 2:
                    args.append("1")
                iterator = self.unique("range")
                self.write(f"for (AstRange {iterator} = ast_range({', '.join(args)}, {t.line}); "
                           f"ast_range_has({iterator}); ast_range_tick(&{iterator})) {{")
                item_type, item_code = "int", f"{iterator}.current"
            else:
                typ, data = self.expr(iterable)
                if not is_list(typ):
                    raise AstlinError(iterable.token, "for requires a list or range()")
                # Iteration uses the original list even if its binding is reassigned.
                _, data = self.temp(typ, data)
                iterator = self.unique("index")
                self.write(f"for (int64_t {iterator} = 0; {iterator} < {data}.len; ++{iterator}) {{")
                item_type = element(typ)
                item_code = f"(({ctype(item_type)} *){data}.data)[{iterator}]"
            self.indent += 1
            loop_mark = self.root_mark()
            self.scopes.append({})
            b = self.bind(t, item_type)
            self.write(f"{ctype(item_type)} {b.c_name} = {item_code};")
            self.root(item_type, b.c_name)
            self.loop_depth += 1
            self.loop_marks.append(loop_mark)
            self.block(cs[1], scoped=False)
            self.loop_marks.pop()
            self.loop_depth -= 1
            self.scopes.pop()
            self.write(f"ast_roots_restore({loop_mark});")
            self.write("ast_gc_maybe();")
            self.indent -= 1
            self.write("}")
        elif k in {"break", "continue"}:
            if self.loop_depth == 0:
                raise AstlinError(t, f"{k} can only be used inside a loop")
            self.write(f"ast_roots_restore({self.loop_marks[-1]});")
            self.write(k + ";")
        else:
            raise AssertionError(k)
        return False

    def compile(self):
        for name in self.validate_types():
            record = self.records[name]
            self.write("typedef struct {")
            for token, typ in record.fields:
                self.write(f"    {ctype(typ)} field_{token.text};")
            if not record.fields:
                self.write("    char empty;")
            self.write(f"}} {ctype(name)};")
            self.write(f"static void ast_trace_record_{c_symbol(name)}(const void *address) {{")
            self.write(f"    const {ctype(name)} *value = address;")
            self.write("    (void)value;")
            for token, typ in record.fields:
                if trace := trace_function(typ):
                    self.write(f"    {trace}(&value->field_{token.text});")
            self.write("}")
        if "main" not in self.functions:
            raise AstlinError(Token("EOF", "", 1, 1), "program needs a 'fn main()' entry point")
        main = self.functions["main"]
        if main.params or main.result not in {"int", "void"}:
            raise AstlinError(main.token, "main must take no parameters and return void or int")
        for name, f in self.functions.items():
            types = ", ".join(ctype(typ) for _, typ in f.params) or "void"
            self.write(f"static {ctype(f.result)} ast_fn_{c_symbol(name)}({types});")
        for name, f in self.functions.items():
            self.scopes = [{}]
            self.name_reserved = f.reserved_names
            self.result = f.result
            params = []
            for token, typ in f.params:
                b = self.bind(token, typ)
                params.append(f"{ctype(typ)} {b.c_name}")
            self.write(f"static {ctype(f.result)} ast_fn_{c_symbol(name)}({', '.join(params) or 'void'}) {{")
            self.indent += 1
            self.function_mark = self.root_mark()
            self.saved_source = self.unique("source")
            self.write(f"const char *{self.saved_source} = ast_current_source;")
            self.write(f"ast_current_source = {cstring(f.token.source or '<source>')};")
            for binding in self.scopes[-1].values():
                self.root(binding.typ, binding.c_name)
            returns = self.block(f.body, scoped=False)
            if f.result != "void" and not returns:
                raise AstlinError(f.token, f"function {f.token.text!r} must return {f.result} on every path")
            if f.result == "void":
                self.return_cleanup()
            self.indent -= 1
            self.write("}")
        self.write("int main(int argc, char **argv) {")
        self.indent += 1
        self.write("if (atexit(ast_cleanup) != 0) ast_fail(0, \"could not register memory cleanup\");")
        self.write("ast_init_args(argc, argv);")
        if main.result == "int":
            self.write("int64_t result = ast_fn_main();")
            self.write(f"if (result < 0 || result > 255) ast_fail({main.token.line}, \"main exit code must be 0..255\");")
            self.write("return (int)result;")
        else:
            self.write("ast_fn_main();")
            self.write("return 0;")
        self.indent -= 1
        self.write("}")
        return "\n".join(self.lines) + "\n"


def compile_source(source: str, filename="<source>") -> str:
    return compile_unit(source, filename)[0]


def compile_unit(source: str, filename="<source>"):
    loader = ModuleLoader()
    loader.load(source, filename)
    body = Compiler(loader.functions, loader.records).compile()
    runtime = Path(__file__).with_name("runtime.h").read_text(encoding="utf-8")
    code = f"/* Generated by Astlin {VERSION}. */\n#define AST_SOURCE {cstring(str(filename))}\n" + runtime + "\n" + body
    return code, set(loader.sources)


def diagnostic(error: AstlinError, source: str, path: Path):
    t = error.token
    if t.source and not t.source.startswith("<") and t.source != str(path):
        path = Path(t.source)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    lines = source.splitlines()
    snippet = lines[t.line - 1] if t.line <= len(lines) else ""
    return f"{path}:{t.line}:{t.col}: error: {error.message}\n  {snippet}\n  {' ' * (t.col - 1)}^"


def build_native(generated, executable, cc="cc", sanitize=False):
    compiler = shutil.which(cc)
    if not compiler:
        raise ValueError(f"C compiler {cc!r} was not found; install Clang or GCC, or pass --cc")
    cfile = executable.with_suffix(".c")
    cfile.write_text(generated, encoding="utf-8")
    flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Wno-unused-function",
             "-Wno-unused-variable", "-Wno-unused-parameter"]
    if sanitize:
        flags += ["-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    result = subprocess.run([compiler, *flags, str(cfile), "-o", str(executable), "-lm"],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError("C backend failed:\n" + result.stderr)


def project_config(directory):
    manifest = directory / "astlin.json"
    if not manifest.is_file():
        return None
    config = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("astlin.json must contain a JSON object")
    name = config.get("name", directory.name)
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("project name must contain only letters, numbers, underscores, and hyphens")
    for key, default in (("entry", "src/main.ad"), ("test_dir", "tests")):
        value = config.get(key, default)
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError(f"project {key} must be a nonempty relative path")
        resolved = (directory / value).resolve()
        if not resolved.is_relative_to(directory.resolve()):
            raise ValueError(f"project {key} must stay inside the project directory")
        config[key] = value
    config["name"] = name
    return config


def init_project(directory):
    directory = directory.resolve()
    name = re.sub(r"[^A-Za-z0-9_-]", "-", directory.name) or "hello_astlin"
    config = {"name": name, "version": "0.1.0", "entry": "src/main.ad", "test_dir": "tests"}
    files = {
        "astlin.json": json.dumps(config, indent=2) + "\n",
        "src/main.ad": 'fn main() {\n    print("Hello from Astlin!")\n}\n',
        "tests/test_main.ad": 'fn main() {\n    assert(2 + 2 == 4)\n}\n',
    }
    for filename in files:
        if (directory / filename).exists():
            raise ValueError(f"refusing to overwrite {directory / filename}")
    for filename, contents in files.items():
        target = directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    ignore = directory / ".gitignore"
    if not ignore.exists():
        ignore.write_text("build/\n", encoding="utf-8")
    print(f"Created Astlin project: {directory}")
    print("From that directory: astlin run | astlin test | astlin build")


def run_tests(path, cc, sanitize, timeout):
    import math
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("test timeout must be a positive finite number")
    files = [path] if path.is_file() else sorted(path.rglob("test_*.ad"))
    if not files:
        raise ValueError(f"no test_*.ad files found in {path}")
    failed = 0
    for file in files:
        source = ""
        try:
            if file.suffix != ".ad":
                raise ValueError("Astlin test sources must use the .ad extension")
            source = file.read_text(encoding="utf-8")
            code = compile_source(source, file)
            with tempfile.TemporaryDirectory(prefix="astlin-test-") as directory:
                binary = Path(directory) / "test"
                build_native(code, binary, cc, sanitize)
                result = subprocess.run([str(binary)], stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, errors="replace", timeout=timeout)
                if result.returncode:
                    raise ValueError(f"exit status {result.returncode}\n{result.stdout}{result.stderr}")
            print(f"PASS {file}")
        except AstlinError as error:
            failed += 1
            print(f"FAIL {file}\n{diagnostic(error, source, file)}", file=sys.stderr)
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired, RecursionError) as error:
            failed += 1
            print(f"FAIL {file}\n{error}", file=sys.stderr)
    print(f"{len(files) - failed} passed; {failed} failed")
    return 1 if failed else 0


def doctor(cc):
    print(f"Astlin {VERSION}; Python {sys.version.split()[0]}")
    compiler = shutil.which(cc)
    if not compiler:
        raise ValueError(f"C compiler {cc!r} was not found")
    print(f"Compiler: {compiler}")
    with tempfile.TemporaryDirectory(prefix="astlin-doctor-") as directory:
        binary = Path(directory) / "probe"
        build_native(compile_source('fn main() { assert(2 + 2 == 4) }'), binary, cc)
        subprocess.run([str(binary)], check=True)
    print("OK: C11 backend, checked arithmetic, and native execution")


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    program_args = []
    separator = "--" in arguments
    if separator:
        split_at = arguments.index("--")
        program_args, arguments = arguments[split_at + 1:], arguments[:split_at]
    parser = argparse.ArgumentParser(description="Astlin: static types and native executables")
    parser.add_argument("--version", action="version", version=f"Astlin {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "build", "check", "emit-c"):
        sub = commands.add_parser(command)
        sub.add_argument("file", nargs="?", type=Path, help=".ad source; defaults to astlin.json entry")
        if command in {"build", "emit-c"}:
            sub.add_argument("-o", "--output", type=Path)
        if command in {"build", "run"}:
            sub.add_argument("--cc", default="cc")
            sub.add_argument("--sanitize", action="store_true")
        if command == "run":
            sub.epilog = "Pass program arguments after -- (available through args())."
    init = commands.add_parser("init", help="create a new project without overwriting existing files")
    init.add_argument("directory", nargs="?", type=Path, default=Path("."))
    tests = commands.add_parser("test", help="compile and run test_*.ad programs")
    tests.add_argument("path", nargs="?", type=Path)
    tests.add_argument("--cc", default="cc")
    tests.add_argument("--sanitize", action="store_true")
    tests.add_argument("--timeout", type=float, default=10.0, help="seconds per test program")
    environment = commands.add_parser("doctor", help="verify the local compiler toolchain")
    environment.add_argument("--cc", default="cc")
    args = parser.parse_args(arguments)
    if separator and args.command != "run":
        parser.error("program arguments after -- are only supported by run")
    try:
        if args.command == "init":
            init_project(args.directory)
            return 0
        if args.command == "doctor":
            doctor(args.cc)
            return 0
        config = project_config(Path.cwd()) if getattr(args, "file", None) is None and getattr(args, "path", None) is None else None
        if args.command == "test":
            path = args.path or Path(config["test_dir"] if config else "tests")
            return run_tests(path, args.cc, args.sanitize, args.timeout)
        file = args.file
        if file is None:
            file = Path(config["entry"]) if config else Path("main.ad")
            if not file.is_file():
                raise ValueError("pass an .ad file or create a project with 'astlin init'")
        if file.suffix != ".ad":
            raise ValueError("Astlin source files must use the .ad extension")
        source = file.read_text(encoding="utf-8")
        try:
            generated, dependencies = compile_unit(source, file)
        except AstlinError as error:
            print(diagnostic(error, source, file), file=sys.stderr)
            return 1
        if args.command == "check":
            print(f"OK: {file} ({len(dependencies)} modules)")
            return 0
        if args.command == "emit-c":
            if args.output:
                if args.output.resolve() in dependencies:
                    raise ValueError("output must not overwrite the source file or an imported module")
                args.output.write_text(generated, encoding="utf-8")
            else:
                print(generated, end="")
            return 0
        output = None
        if args.command == "build":
            output = args.output or (Path("build") / config["name"] if config else file.with_suffix(""))
            if output.resolve() in dependencies:
                raise ValueError("output must not overwrite the source file or an imported module; choose -o")
        with tempfile.TemporaryDirectory(prefix="astlin-") as directory:
            executable = Path(directory) / "program"
            build_native(generated, executable, args.cc, args.sanitize)
            if args.command == "run":
                try:
                    status = subprocess.run([str(executable), *program_args]).returncode
                    return 128 - status if status < 0 else status
                except KeyboardInterrupt:
                    return 130
            output = output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(executable, output)
            print(f"Built {output}")
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"astlin: {error}", file=sys.stderr)
        return 1
    except RecursionError:
        print("astlin: source nesting exceeds the compiler's limit", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
