"""
Run this script once from your project root to patch portal_upload_page.py:
    python patch_portal_upload.py
"""
import ast, sys

path = "ui/pages/portal_upload_page.py"

with open(path, encoding="utf-8") as f:
    content = f.read()

original = content

# 1. Add SystemConfig import
if "from services.system_config import SystemConfig" not in content:
    content = content.replace(
        "from services.data_store import DataStore",
        "from services.data_store import DataStore\nfrom services.system_config import SystemConfig",
        1,
    )

# 2. Fix subheader label (Academic Year)
content = content.replace(
    '        subheader = QLabel("Academic Year 2024\u20132025")\n'
    '        subheader.setObjectName("subHeader")',
    '        self._ay_sub_lbl = QLabel(f"Academic Year {SystemConfig.academic_year()}")\n'
    '        self._ay_sub_lbl.setObjectName("subHeader")',
)
content = content.replace(
    '        header_text_layout.addWidget(subheader)',
    '        header_text_layout.addWidget(self._ay_sub_lbl)',
)

# 3. Fix semester pill label
content = content.replace(
    '        semester_pill = QLabel("1st Semester 2024\u201325  \u25be")\n'
    '        semester_pill.setObjectName("portalSemesterPill")',
    '        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  \u25be")\n'
    '        self._sem_pill_lbl.setObjectName("portalSemesterPill")',
)
content = content.replace(
    '        model_layout.addWidget(semester_pill)',
    '        model_layout.addWidget(self._sem_pill_lbl)',
)

# 4. Add DataStore listener call in __init__
if "_on_system_config_updated" not in content:
    content = content.replace(
        '        self.setup_ui()\n'
        '        self._apply_page_styles()\n'
        '        self._refresh_from_datastore()',
        '        self.setup_ui()\n'
        '        self._apply_page_styles()\n'
        '        self._refresh_from_datastore()\n'
        '        DataStore.get().add_listener(self._on_system_config_updated)',
    )

    # 5. Add the listener method before _apply_page_styles
    content = content.replace(
        '    def _apply_page_styles(self):',
        '    def _on_system_config_updated(self, key: str):\n'
        '        """Update header labels when system config changes in Settings."""\n'
        '        if key in ("system_config", "all"):\n'
        '            if hasattr(self, "_ay_sub_lbl"):\n'
        '                self._ay_sub_lbl.setText(\n'
        '                    f"Academic Year {SystemConfig.academic_year()}"\n'
        '                )\n'
        '            if hasattr(self, "_sem_pill_lbl"):\n'
        '                self._sem_pill_lbl.setText(\n'
        '                    f"{SystemConfig.term_label()}  \u25be"\n'
        '                )\n\n'
        '    def _apply_page_styles(self):',
        1,
    )

if content == original:
    print("WARNING: no changes made — strings may already be patched or use different quotes.")
    sys.exit(1)

try:
    ast.parse(content)
except SyntaxError as e:
    print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
    sys.exit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Patched: {path}")
print("  ✓ SystemConfig import added")
print("  ✓ subheader → self._ay_sub_lbl")
print("  ✓ semester_pill → self._sem_pill_lbl")
print("  ✓ DataStore listener added")