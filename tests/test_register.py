"""register(ctx) registers exactly what plugin.yaml declares (catalog rule 6),
without touching the network or the database."""

import re
from pathlib import Path

import errand_guard

PLUGIN_YAML = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")


def declared(key):
    block = re.search(rf"^{key}:\n((?:  - .+\n)+)", PLUGIN_YAML, re.M)
    return {line.strip()[2:].strip() for line in block.group(1).splitlines()}


class RecordingContext:
    def __init__(self):
        self.tools, self.hooks, self.commands, self.skills = [], [], [], []

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema["name"] == name and callable(handler)
        self.tools.append(name)

    def register_hook(self, name, callback, **kw):
        self.hooks.append(name)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append(name)

    def register_skill(self, name, path, description=""):
        assert Path(path).is_file()
        self.skills.append(name)

    def get_config(self, key, default=None):
        return default


def test_registrations_match_manifest():
    ctx = RecordingContext()
    errand_guard.register(ctx)
    assert set(ctx.tools) == declared("provides_tools")
    assert set(ctx.hooks) == declared("provides_hooks")
    assert ctx.commands == ["errands"]
    assert ctx.skills == ["errand-guard"]


def test_no_api_key_or_secret_in_code():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text or path.name.startswith("test_"), path
        assert not re.search(r"Bearer [A-Za-z0-9]{20,}", text), path
