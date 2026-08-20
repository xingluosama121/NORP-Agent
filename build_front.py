"""
build_front.py — Build front.html from modular source files in front_src/

Usage: python build_front.py

This reads:
  front_src/index.html  — HTML skeleton (with <link> to styles.css but no inline CSS)
  front_src/styles.css  — All CSS
  front_src/i18n.js     — I18N data + t()/tf()/setLanguage()
  front_src/tabs.js     — Multi-tab support
  front_src/core.js     — Global state, rendering, event handling, streaming, polling
  front_src/ui.js       — Modals, settings, plugins, NORP, jailbreak, message center
  front_src/wizard.js   — Onboarding wizard
  front_src/main.js     — Entry point: pywebviewready, event bindings, initialization

And produces:
  front.html — Single self-contained file, ready for pywebview
"""

import os

SRC = "front_src"
OUT = "front.html"

def read_src(filename):
    path = os.path.join(SRC, filename)
    if not os.path.exists(path):
        print(f"  WARNING: {path} not found, skipping")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

print("=== Building front.html from front_src/ ===\n")

# Read all source files
index_html = read_src("index.html")
styles_css = read_src("styles.css")
i18n_js   = read_src("i18n.js")
tabs_js   = read_src("tabs.js")
core_js   = read_src("core.js")
ui_js     = read_src("ui.js")
wizard_js = read_src("wizard.js")
main_js   = read_src("main.js")

# ── Assemble ──
# 1. Inline CSS into the <style> tag (replacing <link rel="stylesheet" href="styles.css">)
if '<link rel="stylesheet" href="styles.css">' in index_html:
    index_html = index_html.replace(
        '<link rel="stylesheet" href="styles.css">',
        f'<style>\n{styles_css}\n</style>'
    )
    print(f"  Inlined styles.css ({len(styles_css)} chars)")
else:
    print("  WARNING: styles.css link not found in index.html")

# 2. Find the SCRIPTS placeholder and inject all JS modules
scripts_block = f"""<script>
// ═══════════════════════════════════════════════════════════════
//  I18N — Multi-language support (5 languages)
// ═══════════════════════════════════════════════════════════════
{i18n_js}

// ═══════════════════════════════════════════════════════════════
//  MULTI-TAB SUPPORT — like browser tabs
// ═══════════════════════════════════════════════════════════════
{tabs_js}

// ═══════════════════════════════════════════════════════════════
//  CORE — Global state, rendering, streaming, polling
// ═══════════════════════════════════════════════════════════════
{core_js}

// ═══════════════════════════════════════════════════════════════
//  UI — Modals, settings, plugins, NORP, message center
// ═══════════════════════════════════════════════════════════════
{ui_js}

// ═══════════════════════════════════════════════════════════════
//  WIZARD — Onboarding wizard
// ═══════════════════════════════════════════════════════════════
{wizard_js}

// ═══════════════════════════════════════════════════════════════
//  MAIN — Entry point & initialization
// ═══════════════════════════════════════════════════════════════
{main_js}
</script>"""

if '<!-- SCRIPTS injected by build tool -->' in index_html:
    index_html = index_html.replace(
        '<!-- SCRIPTS injected by build tool -->',
        scripts_block
    )
elif '<!-- SCRIPTS' in index_html:
    # Fallback: find any SCRIPTS comment
    import re
    index_html = re.sub(r'<!-- SCRIPTS.*?-->', scripts_block, index_html, count=1)
    print("  Used regex fallback for scripts injection")
else:
    # Last resort: inject before </body>
    index_html = index_html.replace('</body>', scripts_block + '\n</body>')
    print("  Injected scripts before </body>")

# ── Write output ──
with open(OUT, "w", encoding="utf-8") as f:
    f.write(index_html)

print(f"\n  Output: {OUT} ({len(index_html)} chars)")

# ── Quick validation ──
checks = [
    ('<script>', '<script> tags'),
    ('<style>', '<style> tags'),
    ('var I18N', 'I18N'),
    ('function createTabState', 'Tabs'),
    ('function renderContent', 'Core/rendering'),
    ('function openSettings', 'UI/settings'),
    ('function startWizParticles', 'Wizard'),
    ('pywebviewready', 'Main/init'),
]
all_ok = True
for marker, label in checks:
    ok = marker in index_html
    status = "[OK]" if ok else "[MISSING!]"
    if not ok:
        all_ok = False
    print(f"  {status} {label}")

if all_ok:
    print("\n  All checks passed!")
else:
    print("\n  WARNING: Some components missing!")

print("\nDone!")
