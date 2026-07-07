"""
ui/pages/settings/system_config_tab.py
=========================================
Settings page — Tab 3: System Config.
Institution name, default term, risk thresholds, and Ollama AI advisor config.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QSlider,
)

from services.data_store import DataStore
from services.system_config import SystemConfig
from services.auth_service import AuthService
from workers.settings_workers import _ConfigLoader
from ui.helpers.settings_render import (
    _DEFAULT_INSTITUTION, _DEFAULT_AY,
    _section_title, _card, _field_label, _input, _combo,
    _primary_btn, _divider, _feedback,
)


class _SystemConfigTab(QWidget):
    def __init__(self):
        super().__init__()
        self._loader: _ConfigLoader | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        inst_card, inst_lo = _card()
        inst_lo.addWidget(_section_title("INSTITUTION"))
        inst_lo.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        self._inst_name = _input("Institution name")
        self._inst_name.setText(_DEFAULT_INSTITUTION)
        self._def_ay = _combo(
            ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]
        )
        self._def_ay.setCurrentText(_DEFAULT_AY)
        self._def_sem = _combo(["1st Semester", "2nd Semester"])

        for col, (lbl, w) in enumerate([
            ("Institution Name",       self._inst_name),
            ("Default Academic Year",  self._def_ay),
            ("Default Semester",       self._def_sem),
        ]):
            cl = QVBoxLayout()
            cl.setSpacing(4)
            cl.addWidget(_field_label(lbl))
            cl.addWidget(w)
            grid.addLayout(cl, 0, col)

        inst_lo.addLayout(grid)
        self._inst_feedback = _feedback()
        inst_lo.addWidget(self._inst_feedback)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_inst_btn = _primary_btn("💾  Save")
        save_inst_btn.clicked.connect(self._save_institution)
        btn_row.addWidget(save_inst_btn)
        inst_lo.addLayout(btn_row)
        root.addWidget(inst_card)

        risk_card, risk_lo = _card()
        risk_lo.addWidget(_section_title("RISK THRESHOLDS"))
        hint = QLabel(
            "Adjust the probability cutoffs used to classify students into "
            "High Risk and Moderate Risk. Students below the Moderate threshold "
            "are classified as Low Risk."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:11px; background:transparent;"
        )
        risk_lo.addWidget(hint)
        risk_lo.addWidget(_divider())

        self._high_thresh_lbl = QLabel("High Risk threshold:  50%")
        self._high_thresh_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:12px; background:transparent;")
        self._high_slider = QSlider(Qt.Orientation.Horizontal)
        self._high_slider.setRange(30, 90)
        self._high_slider.setValue(50)
        self._high_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:rgba(255,255,255,0.08); height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#ff5b5b; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#ff5b5b; border-radius:3px; }
        """)
        self._high_slider.valueChanged.connect(
            lambda v: self._high_thresh_lbl.setText(
                f"High Risk threshold:  {v}%"))

        self._mod_thresh_lbl = QLabel("Moderate Risk threshold:  25%")
        self._mod_thresh_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:12px; background:transparent;")
        self._mod_slider = QSlider(Qt.Orientation.Horizontal)
        self._mod_slider.setRange(10, 60)
        self._mod_slider.setValue(25)
        self._mod_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:rgba(255,255,255,0.08); height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#f5b335; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#f5b335; border-radius:3px; }
        """)
        self._mod_slider.valueChanged.connect(
            lambda v: self._mod_thresh_lbl.setText(
                f"Moderate Risk threshold:  {v}%"))

        for lbl, slider in [
            (self._high_thresh_lbl, self._high_slider),
            (self._mod_thresh_lbl,  self._mod_slider),
        ]:
            risk_lo.addWidget(lbl)
            risk_lo.addWidget(slider)

        self._thresh_feedback = _feedback()
        risk_lo.addWidget(self._thresh_feedback)

        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        save_thresh_btn = _primary_btn("💾  Save Thresholds", color="#f5b335")
        save_thresh_btn.clicked.connect(self._save_thresholds)
        btn_row2.addWidget(save_thresh_btn)
        risk_lo.addLayout(btn_row2)
        root.addWidget(risk_card)

        ollama_card, ollama_lo = _card()
        ollama_lo.addWidget(_section_title("AI ADVISOR (OLLAMA)"))
        hint_ai = QLabel(
            "Configure the local Ollama server used for AI intervention "
            "recommendations. Ollama must be running on this machine. "
            "Default model: qwen3:4b"
        )
        hint_ai.setWordWrap(True)
        hint_ai.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:11px; background:transparent;"
        )
        ollama_lo.addWidget(hint_ai)
        ollama_lo.addWidget(_divider())

        ai_grid = QGridLayout()
        ai_grid.setSpacing(12)
        ai_grid.setColumnStretch(0, 2)
        ai_grid.setColumnStretch(1, 1)

        self._ollama_url   = _input("http://localhost:11434")
        self._ollama_url.setText("http://localhost:11434")
        self._ollama_model = _input("e.g. qwen3:4b")
        self._ollama_model.setText("qwen3:4b")

        for col, (lbl, w) in enumerate([
            ("Ollama Server URL", self._ollama_url),
            ("Model Name",        self._ollama_model),
        ]):
            cl = QVBoxLayout()
            cl.setSpacing(4)
            cl.addWidget(_field_label(lbl))
            cl.addWidget(w)
            ai_grid.addLayout(cl, 0, col)

        ollama_lo.addLayout(ai_grid)
        self._ollama_feedback = _feedback()
        ollama_lo.addWidget(self._ollama_feedback)

        ai_btn_row = QHBoxLayout()
        ai_btn_row.setSpacing(10)
        test_ollama_btn = _primary_btn("⚡  Test Connection", color="#4f8cff")
        test_ollama_btn.clicked.connect(self._test_ollama)
        ai_btn_row.addWidget(test_ollama_btn)
        ai_btn_row.addStretch()
        save_ai_btn = _primary_btn("💾  Save")
        save_ai_btn.clicked.connect(self._save_ollama)
        ai_btn_row.addWidget(save_ai_btn)
        ollama_lo.addLayout(ai_btn_row)
        root.addWidget(ollama_card)
        root.addStretch()

        self._load_config()

    def _load_config(self):
        self._loader = _ConfigLoader()
        self._loader.finished.connect(self._on_config_loaded)
        self._loader.error.connect(lambda _: None)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_config_loaded(self, cfg: dict):
        if "institution_name"       in cfg: self._inst_name.setText(cfg["institution_name"])
        if "default_academic_year"  in cfg: self._def_ay.setCurrentText(cfg["default_academic_year"])
        if "default_semester"       in cfg:
            self._def_sem.setCurrentIndex(0 if cfg["default_semester"] == "1" else 1)
        if "risk_high_threshold"    in cfg: self._high_slider.setValue(int(cfg["risk_high_threshold"]))
        if "risk_moderate_threshold" in cfg: self._mod_slider.setValue(int(cfg["risk_moderate_threshold"]))
        if "ollama_url"             in cfg: self._ollama_url.setText(cfg["ollama_url"])
        if "ollama_model"           in cfg: self._ollama_model.setText(cfg["ollama_model"])

    def _upsert_config(self, key: str, value: str):
        conn = DataStore.get().db_conn
        user = AuthService.current_username() or "system"
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.system_config (key, value, updated_at, updated_by)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (key) DO UPDATE
                    SET value=EXCLUDED.value,
                        updated_at=NOW(),
                        updated_by=EXCLUDED.updated_by
            """, (key, value, user))
        conn.commit()

    def _save_institution(self):
        name = self._inst_name.text().strip()
        if not name:
            self._set_inst_feedback("Institution name cannot be empty.", error=True)
            return
        ay  = self._def_ay.currentText()
        sem = "1" if self._def_sem.currentIndex() == 0 else "2"
        try:
            self._upsert_config("institution_name",      name)
            self._upsert_config("default_academic_year", ay)
            self._upsert_config("default_semester",      sem)
            SystemConfig.reload(DataStore.get().db_conn)
            self._set_inst_feedback("✓  Settings saved.", error=False)
        except Exception as e:
            self._set_inst_feedback(str(e), error=True)

    def _save_thresholds(self):
        high = self._high_slider.value()
        mod  = self._mod_slider.value()
        if mod >= high:
            self._set_thresh_feedback(
                "Moderate Risk threshold must be below High Risk threshold.",
                error=True)
            return
        try:
            self._upsert_config("risk_high_threshold",     str(high))
            self._upsert_config("risk_moderate_threshold", str(mod))
            self._set_thresh_feedback("✓  Thresholds saved.", error=False)
        except Exception as e:
            self._set_thresh_feedback(str(e), error=True)

    def _save_ollama(self):
        url   = self._ollama_url.text().strip()
        model = self._ollama_model.text().strip()
        if not url:
            self._set_ollama_feedback("Ollama URL cannot be empty.", error=True)
            return
        if not model:
            self._set_ollama_feedback("Model name cannot be empty.", error=True)
            return
        try:
            self._upsert_config("ollama_url",   url)
            self._upsert_config("ollama_model", model)
            SystemConfig.reload(DataStore.get().db_conn)
            self._set_ollama_feedback("✓  Ollama settings saved.", error=False)
        except Exception as e:
            self._set_ollama_feedback(str(e), error=True)

    def _test_ollama(self):
        url   = self._ollama_url.text().strip()
        model = self._ollama_model.text().strip()
        self._set_ollama_feedback("Testing connection…", error=False)
        try:
            import requests
            resp = requests.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "Say OK", "stream": False},
                timeout=10,
            )
            if resp.status_code == 200:
                self._set_ollama_feedback(
                    f"✓  Connected — {model} responded.", error=False)
            else:
                self._set_ollama_feedback(
                    f"⚠ HTTP {resp.status_code}: {resp.text[:80]}", error=True)
        except Exception as e:
            self._set_ollama_feedback(f"⚠ {e}", error=True)

    def _set_ollama_feedback(self, text: str, error: bool = False):
        self._ollama_feedback.setText(text)
        self._ollama_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        if not error:
            QTimer.singleShot(4000, lambda: self._ollama_feedback.setText(""))

    def _set_inst_feedback(self, text: str, error: bool = False):
        self._inst_feedback.setText(text)
        self._inst_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        QTimer.singleShot(4000, lambda: self._inst_feedback.setText(""))

    def _set_thresh_feedback(self, text: str, error: bool = False):
        self._thresh_feedback.setText(text)
        self._thresh_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        QTimer.singleShot(4000, lambda: self._thresh_feedback.setText(""))