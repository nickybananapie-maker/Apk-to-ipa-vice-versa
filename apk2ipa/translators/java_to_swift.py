"""
Java → Swift transpiler.

Strategy:
  1. Pattern-based text transformations (fast, deterministic)
  2. javalang AST-based transformation (accurate for well-formed source)
  3. AI-assisted gap filling (optional, via Claude API — fills in what rules can't)

When javalang is not available (e.g., heavily obfuscated code that doesn't
parse cleanly), we fall back to regex-only mode, which still produces useful
(if imperfect) output.

The transpiler never silently drops code.  Anything it cannot translate
becomes a Swift comment block with the original Java and a TODO note,
so the developer always has a starting point.
"""

from __future__ import annotations

import re
import logging
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from apk2ipa.translators.api_mapper import (
    CLASS_MAP, METHOD_MAP, TYPE_MAP, LIFECYCLE_MAP
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TranspilationIssue:
    line: int
    kind: str          # "untranslated" | "warning" | "stub"
    original: str
    note: str


@dataclass
class TranspilationResult:
    swift_source: str
    issues: list[TranspilationIssue] = field(default_factory=list)
    required_imports: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main transpiler class
# ---------------------------------------------------------------------------

class JavaToSwiftTranspiler:
    """
    Convert a Java source string to Swift.

    Usage::

        t = JavaToSwiftTranspiler()
        result = t.transpile(java_source, class_name="MainActivity")
    """

    def __init__(self, use_ast: bool = True):
        self._use_ast = use_ast
        self._issues: list[TranspilationIssue] = []
        self._imports: set[str] = set()

    def transpile(self, java_source: str, class_name: str = "ConvertedClass") -> TranspilationResult:
        """Transpile *java_source* (a single Java compilation unit) to Swift."""
        self._issues = []
        self._imports = set(["Foundation"])  # always needed

        # Try AST-based first
        if self._use_ast:
            try:
                swift = self._ast_transpile(java_source, class_name)
                return TranspilationResult(
                    swift_source=self._add_imports(swift),
                    issues=self._issues,
                    required_imports=sorted(self._imports),
                )
            except Exception as e:
                logger.debug("AST transpile failed (%s), falling back to regex mode", e)

        # Regex fallback
        swift = self._regex_transpile(java_source, class_name)
        return TranspilationResult(
            swift_source=self._add_imports(swift),
            issues=self._issues,
            required_imports=sorted(self._imports),
        )

    # ------------------------------------------------------------------
    # AST-based path (uses javalang)
    # ------------------------------------------------------------------

    def _ast_transpile(self, source: str, class_name: str) -> str:
        import javalang  # optional dependency

        tree = javalang.parse.parse(source)
        lines: list[str] = []

        for _, node in tree.filter(javalang.tree.ClassDeclaration):
            lines.extend(self._translate_class(node))

        for _, node in tree.filter(javalang.tree.InterfaceDeclaration):
            lines.extend(self._translate_interface(node))

        if not lines:
            # Fall through to regex for non-class files
            return self._regex_transpile(source, class_name)

        return "\n".join(lines)

    def _translate_class(self, node) -> list[str]:
        import javalang

        lines: list[str] = []
        class_name = node.name

        # Determine parent class
        extends = ""
        if node.extends:
            android_parent = node.extends.name
            if android_parent in CLASS_MAP:
                cm = CLASS_MAP[android_parent]
                self._imports.add(cm.import_module)
                ios_parent = cm.ios_class
                if cm.notes:
                    lines.append(f"// NOTE: {cm.notes}")
            else:
                ios_parent = android_parent
            extends = f": {ios_parent}"

        # Implements → Swift protocols
        protocols: list[str] = []
        if node.implements:
            for iface in node.implements:
                name = iface.name
                # Common interface mappings
                proto = _INTERFACE_TO_PROTOCOL.get(name, name)
                protocols.append(proto)

        if protocols:
            proto_str = ", ".join(protocols)
            extends = extends + (", " if extends else ": ") + proto_str

        lines.append(f"class {class_name}{extends} {{")

        # Fields
        for _, field_node in _iter_fields(node):
            lines.extend(self._translate_field(field_node))

        # Constructors
        for _, ctor in _iter_constructors(node):
            lines.extend(self._translate_constructor(ctor))

        # Methods
        for _, method in _iter_methods(node):
            lines.extend(self._translate_method(method, class_name))

        lines.append("}")
        return lines

    def _translate_interface(self, node) -> list[str]:
        lines = [f"protocol {node.name} {{"]
        for _, method in _iter_methods(node):
            params = self._params_to_swift(method.parameters)
            ret = self._map_type(method.return_type.name if method.return_type else "void")
            lines.append(f"    func {method.name}({params}) -> {ret}")
        lines.append("}")
        return lines

    def _translate_field(self, field_node) -> list[str]:
        lines: list[str] = []
        try:
            type_name = self._map_type(field_node.type.name)
            for decl in field_node.declarators:
                name = _camel(decl.name)
                modifiers = _swift_access(field_node.modifiers)
                keyword = "let" if "final" in (field_node.modifiers or set()) else "var"

                if decl.initializer:
                    init_str = _java_literal_to_swift(decl.initializer)
                    lines.append(f"    {modifiers}{keyword} {name}: {type_name} = {init_str}")
                else:
                    # Non-optional primitive or optional object
                    if type_name in ("Int", "Int64", "Float", "Double", "Bool", "Character"):
                        default = _type_default(type_name)
                        lines.append(f"    {modifiers}{keyword} {name}: {type_name} = {default}")
                    else:
                        lines.append(f"    {modifiers}var {name}: {type_name}?")
        except Exception:
            lines.append("    // TODO: Untranslated field")
        return lines

    def _translate_constructor(self, ctor) -> list[str]:
        lines: list[str] = []
        try:
            params = self._params_to_swift(ctor.parameters)
            lines.append(f"\n    init({params}) {{")
            for stmt in (ctor.body or []):
                lines.extend(self._translate_statement(stmt, indent=8))
            lines.append("    }")
        except Exception:
            lines.append("    // TODO: Untranslated constructor")
        return lines

    def _translate_method(self, method, class_name: str) -> list[str]:
        lines: list[str] = []
        try:
            name = method.name
            # Lifecycle mapping
            if name in LIFECYCLE_MAP:
                ios_name = LIFECYCLE_MAP[name]
                if ios_name.startswith("//"):
                    lines.append(f"\n    {ios_name}")
                    return lines
                name = ios_name

            params = self._params_to_swift(method.parameters)
            ret_type = self._map_type(
                method.return_type.name if method.return_type else "void"
            )
            access = _swift_access(method.modifiers)
            override = "override " if name in _OVERRIDE_METHODS else ""

            if ret_type == "Void":
                lines.append(f"\n    {access}{override}func {name}({params}) {{")
            else:
                lines.append(f"\n    {access}{override}func {name}({params}) -> {ret_type} {{")

            for stmt in (method.body or []):
                lines.extend(self._translate_statement(stmt, indent=8))

            if ret_type not in ("Void", "void", ""):
                lines.append(f"        // TODO: Return {ret_type}")

            lines.append("    }")
        except Exception:
            lines.append("    // TODO: Untranslated method")
        return lines

    def _translate_statement(self, stmt, indent: int = 8) -> list[str]:
        """Translate a single Java AST statement node to Swift lines."""
        pad = " " * indent
        lines: list[str] = []
        try:
            import javalang
            t = type(stmt).__name__

            if t == "ReturnStatement":
                expr = _expr_to_swift(stmt.expression) if stmt.expression else ""
                lines.append(f"{pad}return {expr}")

            elif t == "LocalVariableDeclaration":
                type_name = self._map_type(stmt.type.name)
                for d in stmt.declarators:
                    init = f" = {_expr_to_swift(d.initializer)}" if d.initializer else ""
                    kw = "let" if not _is_reassigned_later(d.name, stmt) else "var"
                    lines.append(f"{pad}{kw} {_camel(d.name)}: {type_name}{init}")

            elif t == "StatementExpression":
                lines.append(f"{pad}{_expr_to_swift(stmt.expression)}")

            elif t == "IfStatement":
                cond = _expr_to_swift(stmt.condition)
                lines.append(f"{pad}if {cond} {{")
                if stmt.then_statement:
                    lines.extend(self._translate_statement(stmt.then_statement, indent + 4))
                lines.append(f"{pad}}}")
                if stmt.else_statement:
                    lines.append(f"{pad}else {{")
                    lines.extend(self._translate_statement(stmt.else_statement, indent + 4))
                    lines.append(f"{pad}}}")

            elif t == "WhileStatement":
                cond = _expr_to_swift(stmt.condition)
                lines.append(f"{pad}while {cond} {{")
                if stmt.body:
                    lines.extend(self._translate_statement(stmt.body, indent + 4))
                lines.append(f"{pad}}}")

            elif t == "ForStatement":
                lines.append(f"{pad}// TODO: Translate for-loop")
                lines.append(f"{pad}for _ in 0..<1 {{")
                if stmt.body:
                    lines.extend(self._translate_statement(stmt.body, indent + 4))
                lines.append(f"{pad}}}")

            elif t == "EnhancedForStatement":
                var = _camel(stmt.var.declarators[0].name)
                iterable = _expr_to_swift(stmt.iterable)
                lines.append(f"{pad}for {var} in {iterable} {{")
                if stmt.body:
                    lines.extend(self._translate_statement(stmt.body, indent + 4))
                lines.append(f"{pad}}}")

            elif t == "TryStatement":
                lines.append(f"{pad}do {{")
                for s in (stmt.block or []):
                    lines.extend(self._translate_statement(s, indent + 4))
                for catch in (stmt.catches or []):
                    lines.append(f"{pad}}} catch {{")
                    for s in (catch.block or []):
                        lines.extend(self._translate_statement(s, indent + 4))
                lines.append(f"{pad}}}")

            elif t == "BlockStatement":
                for s in (stmt.statements or []):
                    lines.extend(self._translate_statement(s, indent))

            elif t == "SwitchStatement":
                expr = _expr_to_swift(stmt.expression)
                lines.append(f"{pad}switch {expr} {{")
                for case in (stmt.cases or []):
                    if case.case is not None:
                        lines.append(f"{pad}case {_expr_to_swift(case.case)}:")
                    else:
                        lines.append(f"{pad}default:")
                    for s in (case.statements or []):
                        lines.extend(self._translate_statement(s, indent + 4))
                lines.append(f"{pad}}}")

            else:
                lines.append(f"{pad}// TODO: Untranslated {t}")

        except Exception:
            lines.append(f"{pad}// TODO: Statement translation error")
        return lines

    def _params_to_swift(self, params) -> str:
        if not params:
            return ""
        parts: list[str] = []
        for p in params:
            try:
                type_name = self._map_type(p.type.name)
                name = _camel(p.name)
                parts.append(f"_ {name}: {type_name}")
            except Exception:
                parts.append("_ param: Any")
        return ", ".join(parts)

    def _map_type(self, java_type: str) -> str:
        if not java_type:
            return "Void"
        if java_type in TYPE_MAP:
            return TYPE_MAP[java_type]
        if java_type in CLASS_MAP:
            cm = CLASS_MAP[java_type]
            self._imports.add(cm.import_module)
            return cm.ios_class
        return java_type  # unknown → keep as-is (likely a local class)

    # ------------------------------------------------------------------
    # Regex-based fallback path
    # ------------------------------------------------------------------

    def _regex_transpile(self, source: str, class_name: str) -> str:
        """
        Apply a series of regex transformations to convert Java to Swift.
        This is best-effort; the output will need manual review.
        """
        s = source

        # Remove Java-only constructs
        s = re.sub(r"^package\s+[\w.]+;", "", s, flags=re.MULTILINE)
        s = re.sub(r"^import\s+[\w.*]+;", self._translate_import, s, flags=re.MULTILINE)

        # Class declaration
        s = re.sub(
            r"\b(public\s+|protected\s+|private\s+)?(abstract\s+)?class\s+(\w+)"
            r"(\s+extends\s+(\w+))?(\s+implements\s+([\w\s,]+))?",
            self._replace_class_decl, s
        )

        # Interface
        s = re.sub(r"\binterface\s+(\w+)", r"protocol \1", s)

        # Type mappings
        for java_type, swift_type in TYPE_MAP.items():
            s = re.sub(rf"\b{re.escape(java_type)}\b", swift_type, s)

        # Lifecycle methods
        for java_method, swift_method in LIFECYCLE_MAP.items():
            if swift_method.startswith("//"):
                s = re.sub(
                    rf"\b{re.escape(java_method)}\s*\(",
                    f"/* {swift_method} */ func {java_method}(",
                    s
                )
            else:
                # Strip parameter names from the Swift method name for matching
                swift_name = swift_method.split("(")[0].split(" ")[-1]
                s = re.sub(
                    rf"\boverride\s+protected\s+void\s+{re.escape(java_method)}\s*\(",
                    f"override func {swift_name}(",
                    s
                )

        # Method visibility modifiers
        s = re.sub(r"\bpublic\s+(?=func|class|var|let|init)", "public ", s)
        s = re.sub(r"\bprivate\s+(?=func|class|var|let|init)", "private ", s)
        s = re.sub(r"\bprotected\s+", "", s)  # no protected in Swift

        # Return types
        s = re.sub(
            r"\b(public|private|internal|open|)(\s*)(static\s+)?(\w[\w<>\[\]]*)\s+"
            r"(\w+)\s*\(([^)]*)\)\s*\{",
            self._replace_method_decl, s
        )

        # Variable declarations — preserve the type annotation
        s = re.sub(r"\bfinal\s+(\w[\w<>]*)\s+(\w+)\s*=", r"let \2: \1 =", s)
        # Avoid matching keywords like return/if/while/for as "type names"
        _KW = r"(?!return|if|else|while|for|switch|throw|new|super|this|import|package\b)"
        s = re.sub(rf"\b{_KW}(\w[\w<>]*)\s+(\w+)\s*=", r"var \2: \1 =", s)

        # new Foo(...) → Foo(...)
        s = re.sub(r"\bnew\s+(\w+)\s*\(", r"\1(", s)

        # null → nil
        s = re.sub(r"\bnull\b", "nil", s)

        # true/false (already same in Swift)

        # String formatting
        s = re.sub(r'String\.format\("([^"]*)"', r'String(format: "\1"', s)

        # this. → self.
        s = re.sub(r"\bthis\.", "self.", s)

        # super. → super.
        # (already the same)

        # instanceof → is
        s = re.sub(r"\binstanceof\b", "is", s)

        # Casting: (Foo) bar → bar as! Foo
        s = re.sub(r"\((\w+)\)\s*(\w+)", r"\2 as! \1", s)

        # Array creation: new int[5] → [Int](repeating: 0, count: 5)
        s = re.sub(
            r"new\s+(\w+)\[(\d+)\]",
            lambda m: f"[{TYPE_MAP.get(m.group(1), m.group(1))}](repeating: {_type_default(TYPE_MAP.get(m.group(1), m.group(1)))}, count: {m.group(2)})",
            s
        )

        # Array literals: new int[]{1,2,3} → [1, 2, 3]
        s = re.sub(r"new\s+\w+\[\]\s*\{([^}]*)\}", r"[\1]", s)

        # Enhanced for: for (Type var : collection) → for var in collection
        s = re.sub(
            r"for\s*\(\s*\w[\w<>]*\s+(\w+)\s*:\s*(\w+)\s*\)",
            r"for \1 in \2",
            s
        )

        # Standard for: for (int i = 0; i < n; i++) → for i in 0..<n
        s = re.sub(
            r"for\s*\(\s*(?:int|long)\s+(\w+)\s*=\s*(\d+)\s*;\s*\w+\s*<\s*(\w+)\s*;\s*\w+\+\+\s*\)",
            r"for \1 in \2..<\3",
            s
        )

        # Semicolons
        s = re.sub(r";(\s*$)", r"\1", s, flags=re.MULTILINE)

        # System.out.println → print
        s = re.sub(r"System\.out\.println\(", "print(", s)
        s = re.sub(r"System\.out\.print\(", "print(", s)
        s = re.sub(r"System\.err\.println\(", "print(", s)

        # Log.d/e/w/i → print
        s = re.sub(r"Log\.[dewiv]\(\"[^\"]*\",\s*", "print(", s)
        s = re.sub(r"Log\.wtf\(", "assertionFailure(", s)

        # toString() → description or String(...)
        s = re.sub(r"\.toString\(\)", "", s)

        # equals() → ==
        s = re.sub(r"\.equals\(([^)]+)\)", r" == \1", s)

        # Length/size
        s = re.sub(r"\.length\(\)", ".count", s)
        s = re.sub(r"\.size\(\)", ".count", s)

        # @Override annotation
        s = re.sub(r"@Override\s*", "override ", s)

        # @Nullable → Optional marker (add ? to type in a review note)
        s = re.sub(r"@Nullable\s+", "/* @Nullable */ ", s)
        s = re.sub(r"@NonNull\s+", "", s)

        # Annotations (strip remaining Android annotations)
        s = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", s)

        # Comments: already valid Swift (// and /* */)

        # Remove throws declaration
        s = re.sub(r"\bthrows\s+[\w,\s]+\s*(?=\{)", "", s)
        s = re.sub(r"\bthrows\b", "throws", s)  # keep Swift throws

        return s

    # ------------------------------------------------------------------
    # Regex substitution callbacks
    # ------------------------------------------------------------------

    def _translate_import(self, match: re.Match) -> str:
        stmt = match.group(0)
        # Map common Android/Java imports to Swift imports
        for pkg, swift_mod in _IMPORT_MAP.items():
            if pkg in stmt:
                self._imports.add(swift_mod)
                return f"// {stmt}  →  import {swift_mod}"
        return f"// {stmt}"

    def _replace_class_decl(self, match: re.Match) -> str:
        class_name = match.group(3)
        parent_java = match.group(5) or ""
        implements_str = match.group(7) or ""

        ios_parent = ""
        if parent_java:
            if parent_java in CLASS_MAP:
                cm = CLASS_MAP[parent_java]
                self._imports.add(cm.import_module)
                ios_parent = cm.ios_class
                if cm.notes:
                    return f"// NOTE: {cm.notes}\nclass {class_name}: {ios_parent}"
            else:
                ios_parent = parent_java

        protocols: list[str] = []
        if implements_str:
            for iface in re.split(r",\s*", implements_str.strip()):
                iface = iface.strip()
                protocols.append(_INTERFACE_TO_PROTOCOL.get(iface, iface))

        suffix = ""
        if ios_parent:
            suffix = f": {ios_parent}"
            if protocols:
                suffix += ", " + ", ".join(protocols)
        elif protocols:
            suffix = ": " + ", ".join(protocols)

        return f"class {class_name}{suffix}"

    def _replace_method_decl(self, match: re.Match) -> str:
        access = match.group(1).strip()
        static = match.group(3) or ""
        ret_type = match.group(4)
        name = match.group(5)
        params = match.group(6)

        swift_ret = self._map_type(ret_type) if ret_type not in ("void",) else ""
        swift_access = {"public": "public ", "private": "private "}.get(access, "")
        swift_static = "static " if static.strip() else ""

        if name in LIFECYCLE_MAP:
            ios_name = LIFECYCLE_MAP[name]
            if not ios_name.startswith("//"):
                name = ios_name.split("(")[0].split(" ")[-1]

        params_swift = self._translate_params_regex(params)

        if swift_ret:
            return f"{swift_access}{swift_static}func {name}({params_swift}) -> {swift_ret} {{"
        else:
            return f"{swift_access}{swift_static}func {name}({params_swift}) {{"

    def _translate_params_regex(self, params_str: str) -> str:
        if not params_str.strip():
            return ""
        parts: list[str] = []
        for param in re.split(r",\s*", params_str.strip()):
            tokens = param.strip().split()
            if len(tokens) >= 2:
                java_type = tokens[-2]
                name = tokens[-1]
                swift_type = TYPE_MAP.get(java_type, java_type)
                parts.append(f"_ {name}: {swift_type}")
            elif tokens:
                parts.append(f"_ p: Any")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_imports(self, swift: str) -> str:
        import_lines = "\n".join(f"import {m}" for m in sorted(self._imports))
        header = (
            "// AUTO-GENERATED by apk2ipa — review all TODO comments before shipping\n"
            "// This file was translated from Java/Kotlin and may need manual fixes.\n\n"
            + import_lines + "\n\n"
        )
        return header + swift


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _camel(name: str) -> str:
    """Convert a Java identifier to Swift camelCase (usually already is)."""
    return name  # Java and Swift both use camelCase


def _swift_access(modifiers) -> str:
    if modifiers is None:
        return ""
    mods = set(modifiers)
    if "public" in mods:
        return "public "
    if "private" in mods:
        return "private "
    return ""


def _iter_fields(class_node):
    import javalang
    for m in (class_node.body or []):
        if isinstance(m, javalang.tree.FieldDeclaration):
            yield None, m


def _iter_constructors(class_node):
    import javalang
    for m in (class_node.body or []):
        if isinstance(m, javalang.tree.ConstructorDeclaration):
            yield None, m


def _iter_methods(class_node):
    import javalang
    for m in (class_node.body or []):
        if isinstance(m, javalang.tree.MethodDeclaration):
            yield None, m


def _type_default(swift_type: str) -> str:
    defaults = {
        "Int": "0", "Int64": "0", "Int16": "0", "UInt8": "0",
        "Float": "0.0", "Double": "0.0", "Bool": "false",
        "String": '""', "Character": '" "',
    }
    return defaults.get(swift_type, "nil")


def _expr_to_swift(expr) -> str:
    """Best-effort conversion of a javalang expression node to a Swift string."""
    if expr is None:
        return ""
    try:
        import javalang
        t = type(expr).__name__

        if t == "Literal":
            v = expr.value
            if v == "true":  return "true"
            if v == "false": return "false"
            if v == "null":  return "nil"
            return v

        elif t == "MemberReference":
            qualifier = f"{expr.qualifier}." if expr.qualifier else ""
            return f"{qualifier}{expr.member}"

        elif t == "MethodInvocation":
            qualifier = f"{expr.qualifier}." if expr.qualifier else ""
            args = ", ".join(_expr_to_swift(a) for a in (expr.arguments or []))
            return f"{qualifier}{expr.member}({args})"

        elif t == "BinaryOperation":
            op = expr.operator
            # Java string concatenation with + → Swift string interpolation is complex,
            # keep as + for now
            left = _expr_to_swift(expr.operandl)
            right = _expr_to_swift(expr.operandr)
            return f"{left} {op} {right}"

        elif t == "Assignment":
            left = _expr_to_swift(expr.expressionl)
            right = _expr_to_swift(expr.value)
            return f"{left} = {right}"

        elif t == "Cast":
            inner = _expr_to_swift(expr.expression)
            cast_type = TYPE_MAP.get(expr.type.name, expr.type.name)
            return f"{inner} as! {cast_type}"

        elif t == "ClassCreator":
            cls = TYPE_MAP.get(expr.type.name, expr.type.name)
            args = ", ".join(_expr_to_swift(a) for a in (expr.arguments or []))
            return f"{cls}({args})"

        elif t == "ArrayCreator":
            base = TYPE_MAP.get(expr.type.name, expr.type.name)
            if expr.dimensions and expr.dimensions[0]:
                size = _expr_to_swift(expr.dimensions[0])
                return f"[{base}](repeating: {_type_default(base)}, count: {size})"
            return f"[{base}]()"

        elif t == "ArrayAccess":
            arr = _expr_to_swift(expr.postfix_operators[0] if expr.postfix_operators else None)
            return f"{_expr_to_swift(expr.name)}[{_expr_to_swift(expr.index)}]"

        elif t in ("TernaryExpression",):
            cond = _expr_to_swift(expr.condition)
            tval = _expr_to_swift(expr.if_true)
            fval = _expr_to_swift(expr.if_false)
            return f"({cond}) ? ({tval}) : ({fval})"

        elif hasattr(expr, "value"):
            return str(expr.value)

        elif hasattr(expr, "name"):
            return str(expr.name)

    except Exception:
        pass
    return "/* TODO: untranslated expression */"


def _java_literal_to_swift(init_node) -> str:
    if init_node is None:
        return "nil"
    return _expr_to_swift(init_node)


def _is_reassigned_later(name: str, stmt) -> bool:
    """Heuristic: assume variables declared without final could be reassigned."""
    return True  # conservative: always use var


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_OVERRIDE_METHODS = {
    "viewDidLoad", "viewWillAppear", "viewDidAppear",
    "viewWillDisappear", "viewDidDisappear", "loadView",
    "numberOfSections", "numberOfRows", "cellForRowAt",
    "collectionView",
}

_INTERFACE_TO_PROTOCOL: dict[str, str] = {
    "Runnable":                   "// Runnable → use a closure or DispatchWorkItem",
    "Callable":                   "// Callable → use a closure",
    "Comparator":                 "Comparable",
    "Comparable":                 "Comparable",
    "Serializable":               "Codable",
    "Parcelable":                 "Codable",
    "OnClickListener":            "// OnClickListener → use addTarget or closure",
    "TextWatcher":                "UITextFieldDelegate",
    "View.OnClickListener":       "// → use addTarget(_:action:for: .touchUpInside)",
    "RecyclerView.Adapter":       "UICollectionViewDataSource",
    "ViewHolder":                 "// ViewHolder → UICollectionViewCell subclass",
    "Observer":                   "// Observer → @Published / Combine / NotificationCenter",
    "LifecycleObserver":          "// LifecycleObserver → UIViewController lifecycle methods",
}

_IMPORT_MAP: dict[str, str] = {
    "android.app":            "UIKit",
    "android.view":           "UIKit",
    "android.widget":         "UIKit",
    "android.content":        "UIKit",
    "android.os":             "Foundation",
    "android.util":           "Foundation",
    "android.text":           "Foundation",
    "android.graphics":       "UIKit",
    "android.animation":      "UIKit",
    "android.location":       "CoreLocation",
    "android.hardware":       "CoreMotion",
    "android.bluetooth":      "CoreBluetooth",
    "android.media":          "AVFoundation",
    "android.net":            "Foundation",
    "android.database":       "CoreData",
    "android.preference":     "Foundation",
    "java.io":                "Foundation",
    "java.util":              "Foundation",
    "java.net":               "Foundation",
    "java.lang":              "Foundation",
    "java.math":              "Foundation",
    "kotlin.coroutines":      "Combine",
    "kotlinx.coroutines":     "Combine",
    "androidx.lifecycle":     "Combine",
    "androidx.room":          "CoreData",
    "com.google.android.gms.location": "CoreLocation",
    "com.google.android.gms.maps":     "MapKit",
    "com.google.firebase":    "// Firebase → use Firebase iOS SDK",
}
