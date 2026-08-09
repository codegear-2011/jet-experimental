"""
jet.cli

The Jet command line interface.

    jet newapp myapp
    jet run
    jet build
    jet shell

Everything goes through the single `jet` command. No extra executables.
"""

import os
import sys
import code

from . import __version__

APP_PY = '''from jet import *

app = Jet()


def login(request):
    return Response.html("<h1>Login page</h1>")


app.page("/", "index.html")
app.route("/login", login)

if __name__ == "__main__":
    serve(app)
'''

CONFIG_PY = '''"""
config.py

Application configuration only.
Route and application logic belong in app.py.
"""

CONFIG = {
    "STATIC_DIR": "static",
    "TEMPLATES_DIR": "templates",
    "UPLOADS_DIR": "uploads",

    # Future batteries (v0.2+):
    # "DATABASE": {},
    # "AUTH": {},
    # "CACHE": {},
    # "MAIL": {},
    # "STORAGE": {},
}
'''

INDEX_HTML = '''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Jet App</title>
</head>
<body>
  <h1>Welcome to Jet</h1>
  <p>Edit <code>templates/index.html</code> to get started.</p>

  {# example of Jet's template syntax #}
  <%jet: if request %>
    <p>Request path: <%jet request.path %></p>
  <%jet: end %>
</body>
</html>
'''


def cmd_newapp(args):
    if not args:
        print("Usage: jet newapp <name>")
        return

    name = args[0]
    if os.path.exists(name):
        print(f"Error: '{name}' already exists.")
        return

    os.makedirs(os.path.join(name, "templates"))
    os.makedirs(os.path.join(name, "static"))
    os.makedirs(os.path.join(name, "uploads"))

    with open(os.path.join(name, "app.py"), "w") as f:
        f.write(APP_PY)

    with open(os.path.join(name, "config.py"), "w") as f:
        f.write(CONFIG_PY)

    with open(os.path.join(name, "templates", "index.html"), "w") as f:
        f.write(INDEX_HTML)

    print(f"Created new Jet app: {name}/")
    print(f"\n  cd {name}")
    print("  jet run\n")


def cmd_run(args):
    if not os.path.exists("app.py"):
        print("Error: app.py not found. Run 'jet newapp <name>' first.")
        return

    sys.path.insert(0, os.getcwd())
    namespace = {"__name__": "__main__", "__file__": "app.py"}
    with open("app.py") as f:
        source = f.read()

    exec(compile(source, "app.py", "exec"), namespace)


def cmd_build(args):
    print("jet build: nothing to build yet in v0.1 (framework core only).")


def cmd_shell(args):
    if not os.path.exists("app.py"):
        print("Error: app.py not found in this directory.")
        return

    sys.path.insert(0, os.getcwd())
    namespace = {}
    with open("app.py") as f:
        source = f.read()
    exec(compile(source, "app.py", "exec"), namespace)

    banner = f"Jet {__version__} interactive shell — 'app' is available."
    code.interact(banner=banner, local=namespace)


COMMANDS = {
    "newapp": cmd_newapp,
    "run": cmd_run,
    "build": cmd_build,
    "shell": cmd_shell,
}


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(f"Jet {__version__} — usage: jet <command> [args]")
        print("\nCommands:")
        for name in COMMANDS:
            print(f"  jet {name}")
        return

    if args[0] in ("-v", "--version"):
        print(f"Jet {__version__}")
        return

    command, rest = args[0], args[1:]
    handler = COMMANDS.get(command)

    if not handler:
        print(f"Unknown command: {command}")
        print("Run 'jet --help' to see available commands.")
        return

    handler(rest)


if __name__ == "__main__":
    main()
