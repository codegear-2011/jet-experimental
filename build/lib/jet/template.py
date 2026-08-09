"""
jet.template

Jet's built-in template engine.

Rather than interpreting tags at render time, templates are compiled
once into real Python source and executed -- this keeps rendering
fast and gives real Python semantics (any expression, any statement)
instead of a re-invented mini-language.

Syntax:
    <%jet expression %>          -- Python expression output
    <%jet: statement %>          -- Python block (if / for / while / with)
    <%jet: elif condition %>     -- elif branch
    <%jet: else %>               -- else branch
    <%jet: end %>                -- close a block
    {# comment #}                -- template comment (removed from output)

Example (.html file):

    <h1>Hello, <%jet name %>!</h1>

    <%jet: for item in items %>
        <li><%jet item.upper() %></li>
    <%jet: end %>

    <%jet: if user_is_admin %>
        <span>Admin Panel</span>
    <%jet: elif user_is_staff %>
        <span>Staff Area</span>
    <%jet: else %>
        <span>Welcome</span>
    <%jet: end %>
"""

import builtins
import os
import re

TEMPLATES_DIR = "templates"


class TemplateError(Exception):
    """Raised when a template fails to load, compile, or render."""


# ── Regex Patterns ──────────────────────────────────────────────────

_RE_COMMENT = re.compile(r'\{#.*?#\}', re.DOTALL)
_RE_BLOCK = re.compile(r'<%jet:(.*?)%>', re.DOTALL)
_RE_EXPR = re.compile(r'<%jet\s+(.*?)%>', re.DOTALL)
_RE_TAG = re.compile(r'(<%jet:.*?%>|<%jet\s+.*?%>)', re.DOTALL)


# ── Tokeniser ────────────────────────────────────────────────────────

def _tokenise(source: str) -> list:
    """
    Returns a list of (kind, value) tuples.
    kind is one of: 'text' | 'expr' | 'block'
    """
    source = _RE_COMMENT.sub('', source)
    tokens = []

    for part in _RE_TAG.split(source):
        if not part:
            continue

        block_m = _RE_BLOCK.fullmatch(part)
        expr_m = _RE_EXPR.fullmatch(part)

        if block_m:
            tokens.append(('block', block_m.group(1).strip()))
        elif expr_m:
            tokens.append(('expr', expr_m.group(1).strip()))
        else:
            tokens.append(('text', part))

    return tokens


# ── Code Generator ──────────────────────────────────────────────────

_CLOSER = ('end', 'end ')


def _generate(tokens: list) -> str:
    """
    Convert tokens into a Python function source string.
    The function signature is:  _render(__ctx: dict) -> str
    Context variables are available directly by name inside templates.
    """
    lines = [
        "def _render(__ctx):",
        "    __out = []",
    ]

    depth = 1  # current indentation level (1 = inside function)
    pad = lambda d: "    " * d

    for kind, value in tokens:

        if kind == 'text':
            # Use repr() so any special chars are safely escaped
            lines.append(f"{pad(depth)}__out.append({value!r})")

        elif kind == 'expr':
            lines.append(f"{pad(depth)}__out.append(str({value}))")

        elif kind == 'block':
            stmt = value

            if stmt in _CLOSER:
                depth = max(1, depth - 1)

            elif stmt in ('else', 'else:'):
                # step out, write else:, step back in
                depth = max(1, depth - 1)
                lines.append(f"{pad(depth)}else:")
                depth += 1

            elif stmt.startswith('elif '):
                depth = max(1, depth - 1)
                cond = stmt if stmt.endswith(':') else stmt + ':'
                lines.append(f"{pad(depth)}{cond}")
                depth += 1

            else:
                # Normal opener
                opener = stmt if stmt.endswith(':') else stmt + ':'
                lines.append(f"{pad(depth)}{opener}")
                depth += 1

    lines.append("    return ''.join(__out)")
    return '\n'.join(lines)


# ── Executor ─────────────────────────────────────────────────────────

def _execute(py_source: str, context: dict) -> str:
    """
    Compile and run the generated Python function.
    Context variables are injected into the function's global namespace
    so they resolve by bare name (e.g. <%jet name %> not <%jet ctx['name'] %>).
    """
    ns = vars(builtins).copy()
    ns.update(context)

    try:
        exec(compile(py_source, '<jet-template>', 'exec'), ns)
        return ns['_render'](context)
    except TemplateError:
        raise
    except Exception as exc:
        raise TemplateError(f"Error executing template: {exc}") from exc


# ── Public functions ───────────────────────────────────────────────

def render_string(source: str, **context) -> str:
    """Render a template from a raw string."""
    tokens = _tokenise(source)
    py_src = _generate(tokens)
    return _execute(py_src, context)


def debug_compile(source: str) -> str:
    """Return the generated Python source (useful for debugging)."""
    return _generate(_tokenise(source))


# ── Public API (Jet-facing) ──────────────────────────────────────────

class Template:
    """
    Represents a single template file, rendered lazily against a context.

        Template("index.html").render(title="Home")
    """

    def __init__(self, name, directory=None):
        self.name = name
        self.directory = directory or TEMPLATES_DIR

    def path(self):
        return os.path.join(self.directory, self.name)

    def source(self):
        full_path = self.path()
        if not os.path.exists(full_path):
            raise TemplateError(f'Template not found: "{full_path}"')
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def render(self, **context):
        try:
            text = self.source()
            tokens = _tokenise(text)
            py_src = _generate(tokens)
            return _execute(py_src, context)
        except TemplateError as exc:
            raise TemplateError(f'Error rendering "{self.name}": {exc}') from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise TemplateError(f'Error rendering "{self.name}": {exc}') from exc


class TemplateEngine:
    """
    File-based template renderer for Jet.

    Usage:
        engine = TemplateEngine("templates")
        html   = engine.render("index.html", title="Home", items=[1, 2, 3])
    """

    def __init__(self, template_dir: str = TEMPLATES_DIR):
        self.template_dir = template_dir

    def render(self, template_name: str, **context) -> str:
        """Render a template file with the given context variables."""
        return Template(template_name, self.template_dir).render(**context)

    def render_string(self, source: str, **context) -> str:
        return render_string(source, **context)
