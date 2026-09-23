"""
«Identifier 'AI_KIND_FA' has already been declared».

app.js and the five js/ai/*.js files are plain scripts, not modules, so every
top-level const, let, function and class lands in the same global scope. Two
files each declared AI_KIND_FA — one mapping how an agent runs, one mapping
what a property is, entirely unrelated — and because a redeclared const is a
SyntaxError rather than a warning, the second file to load did not merely lose
its constant: none of it ran at all. The listing reader was dead on production
and the panel looked fine.

Nothing about that failure is visible in either file on its own, which is why
it is a test and not a convention.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"
INDEX = (FRONT / "index.html").read_text(encoding="utf-8")

# the panel's own scripts, in the order the page loads them
SCRIPTS = [FRONT / m for m in re.findall(r'<script src="((?:js|vendor)/[^"?]+)', INDEX)
           if m.startswith("js/")]

_DECL = re.compile(r"^(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", re.M)


def _top_level_names(path):
    """Declarations at column zero — anything indented is inside something."""
    return {m.group(1): src[:m.start()].count("\n") + 1
            for src in [path.read_text(encoding="utf-8")]
            for m in _DECL.finditer(src)}


class TestOneScope:

    def test_the_scripts_are_actually_found(self):
        assert len(SCRIPTS) >= 5, f"expected app.js and the ai/* files, got {SCRIPTS}"
        assert all(p.exists() for p in SCRIPTS)

    def test_they_are_plain_scripts_sharing_one_scope(self):
        """If these ever become modules this test can go — until then the
        global scope is shared and collisions are fatal."""
        assert 'type="module"' not in INDEX

    def test_no_two_files_declare_the_same_name(self):
        seen, clashes = {}, []
        for path in SCRIPTS:
            for name, line in _top_level_names(path).items():
                if name in seen:
                    clashes.append(f"{name}: {seen[name]} and {path.name}:{line}")
                else:
                    seen[name] = f"{path.name}:{line}"
        assert not clashes, (
            "a redeclared const is a SyntaxError and the whole file stops "
            "running:\n  " + "\n  ".join(clashes))

    def test_the_two_kind_maps_are_named_for_what_they_hold(self):
        app = (FRONT / "js/app.js").read_text(encoding="utf-8")
        reader = (FRONT / "js/ai/reader.js").read_text(encoding="utf-8")
        assert "AI_AGENT_KIND_FA" in app and "AI_PROPERTY_KIND_FA" in reader
        # in code, not in the comment that explains why the name changed
        for src in (app, reader):
            bare = re.sub(r"//.*", "", src)
            assert not re.search(r"(?<![A-Z_])AI_KIND_FA", bare)
