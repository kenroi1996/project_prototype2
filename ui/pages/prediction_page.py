from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLineEdit,
    QFileDialog,
    QGraphicsOpacityEffect,
    QProgressBar,
    QScrollArea,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal

from ui.mixins.prediction_mixin import PredictionMixin
from ui.components.loading_overlay import LoadingOverlay
from ui.dialogs.portal_dataset_dialog import PortalDatasetDialog
from ui.dialogs.clean_data_window import CleanDataWindow
from ui.dialogs.confirmation_dialog import ConfirmationDialog, show_error, show_warning
from services.data_store import DataStore
from services.excel_service import (
    read_excel_file,
    dataframe_to_rows,
    rows_to_dataframe,
)

ACCENT = "#4f8cff"

# Portal display config
_PORTAL_CONFIG = {
    "mis":       {"label": "MIS",       "full": "Management Information System",  "icon": "🏫", "color": "#4f8cff"},
    "sao":       {"label": "SAO",       "full": "Student Affairs Office",         "icon": "🎓", "color": "#a78bfa"},
    "guidance":  {"label": "Guidance",  "full": "Guidance Office",                "icon": "🧭", "color": "#34d399"},
    "registrar": {"label": "Registrar", "full": "Registrar's Office",             "icon": "📋", "color": "#f5b335"},
}

_DATASET_CONFIG = {
    "title":  "Prediction Dataset",
    "office": "Incoming First-Year Students",
    "accent": ACCENT,
}


# =============================================================================
# FUSED WORKER  (feature engineering → model scoring in one thread)
# =============================================================================

class _FusedPredictionWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, headers: list, rows: list, name: str, school_year: str):
        super().__init__(parent=None)
        self._headers     = headers
        self._rows        = rows
        self._name        = name
        self._school_year = school_year

    def run(self):
        try:
            from services.feature_engineering import run_prediction_pipeline
            from services.prediction_engine import PredictionEngine
            from services.data_store import DataStore

            # ── Phase 1: Snapshot raw meta BEFORE pipeline strips columns ─────
            self.progress.emit("Snapshotting student demographics…", 5)
            df = rows_to_dataframe(self._headers, self._rows)

            from services.feature_engineering import normalize_columns
            df_norm = normalize_columns(df.copy())

            _SNAPSHOT_COLS = [
                "first_name", "First_Name", "firstname",
                "last_name",  "Last_Name",  "lastname",
                "College", "Final_Avg_GRD", "SecCode", "Year",
                "Home_Address", "Municipality", "Civil_Status",
                "Birthdate", "Year_Enrolled", "Family_Income",
                "Parent_Highest_Education", "HS_GPA", "Year_Graduated",
                "SHS_Strand", "HS_Type", "Graduation_Honors", "HS_School",
                "Scholarship_Applicant", "Scholarship_Type", "Religion",
                "Program", "Sex_code",
            ]

            meta_snapshot: dict = {}
            if "Student_ID" in df_norm.columns:
                available = [c for c in _SNAPSHOT_COLS if c in df_norm.columns]
                for _, row in df_norm.iterrows():
                    sid = str(row["Student_ID"]).strip()
                    if not sid or sid in ("", "nan", "None"):
                        continue
                    meta_snapshot[sid] = {
                        col: (str(row[col]).strip()
                              if row[col] is not None
                              and str(row[col]) not in ("nan", "None", "")
                              else "")
                        for col in available
                    }

            DataStore.get().set_raw_meta_snapshot(meta_snapshot)
            print(f"[FusedWorker] Meta snapshot: {len(meta_snapshot)} students, "
                  f"{len(next(iter(meta_snapshot.values()), {}))} fields each")

            # ── Phase 2: Feature engineering (10 → 45 %) ─────────────────────
            self.progress.emit("Engineering features…", 10)
            engineered = run_prediction_pipeline(df)

            headers, rows = dataframe_to_rows(engineered)
            if not headers or not rows:
                self.error.emit("Feature pipeline produced an empty dataset.")
                return

            self.progress.emit("Pipeline complete — committing to DataStore…", 45)
            store = DataStore.get()
            store.set_prediction_dataset(
                {"headers": headers, "rows": rows},
                name=self._name,
                school_year=self._school_year,
            )

            # ── Phase 3: Model scoring (45 → 100 %) ──────────────────────────
            def _cb(step: str, pct: int):
                self.progress.emit(step, 45 + int(pct * 0.55))

            result = PredictionEngine.run(
                model_data      = store.trained_model,
                unified_dataset = store.get_prediction_dataset(),
                progress_cb     = _cb,
            )
            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


# =============================================================================
# PREDICTION PAGE
# =============================================================================

class PredictionPage(PredictionMixin, QWidget):
    """
    Prediction page — upload four portal datasets, merge them, then
    run feature engineering and model scoring in one fused step.

    Workflow (4 steps)
    ------------------
    1. Enter Dataset Name + School Year.
    2. Upload each portal file (MIS, SAO, Guidance, Registrar).
    3. Merge portals → optionally Clean → confirm merge report.
    4. Run Pipeline & Predict → results saved to DB automatically.
    """

    def __init__(self):
        super().__init__()

        # Per-portal state: {key: {"headers": [...], "rows": [...]} | None}
        self._portal_data: dict = {k: None for k in _PORTAL_CONFIG}

        self._merged_headers: list | None = None
        self._merged_rows:    list | None = None
        self._merged          = False
        self._cleaned         = False
        self._fused_worker: _FusedPredictionWorker | None = None

        self.setup_ui()
        self._apply_page_styles()
        self.init_prediction(
            overlay_message="Running Pipeline & Prediction…",
            overlay_sub="Preparing features",
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.setObjectName("page")
        self.overlay = LoadingOverlay(self)

        # Outer scroll area so the page works on smaller screens
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # ── Header ────────────────────────────────────────────────────
        self.main_layout.addWidget(self._build_header())

        # ── Step 1: Dataset details ────────────────────────────────────
        self.main_layout.addWidget(self._build_details_card())

        # ── Step 2: Portal uploads ─────────────────────────────────────
        self.main_layout.addWidget(self._build_portals_card())

        # ── Step 3: Merge & Clean ──────────────────────────────────────
        self.main_layout.addWidget(self._build_merge_card())

        # ── Step 4: Run Pipeline & Predict ────────────────────────────
        self.main_layout.addWidget(self._build_fused_card())

        self.main_layout.addStretch()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> QFrame:
        container = QFrame()
        container.setObjectName("fixedHeaderContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(15)

        text_col = QVBoxLayout()
        text_col.setSpacing(5)
        header = QLabel("PREDICTION")
        header.setObjectName("header")
        subheader = QLabel(
            "Upload portal datasets, merge, and score incoming first-year students"
        )
        subheader.setObjectName("subHeader")
        text_col.addWidget(header)
        text_col.addWidget(subheader)
        row.addLayout(text_col)
        row.addStretch()

        # Model status pill
        model_card = QFrame()
        model_card.setObjectName("predictionModelCard")
        pill_layout = QHBoxLayout(model_card)
        pill_layout.setContentsMargins(20, 15, 20, 15)
        model_status = QLabel("● Model Active")
        model_status.setObjectName("predictionModelStatus")
        opacity_effect = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(opacity_effect)
        self._status_anim = QPropertyAnimation(opacity_effect, b"opacity")
        self._status_anim.setDuration(1200)
        self._status_anim.setStartValue(1.0)
        self._status_anim.setKeyValueAt(0.5, 0.3)
        self._status_anim.setEndValue(1.0)
        self._status_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._status_anim.setLoopCount(-1)
        self._status_anim.start()
        pill_layout.addWidget(model_status)
        row.addWidget(model_card)

        layout.addLayout(row)
        return container

    # ------------------------------------------------------------------
    # Step 1 — Dataset details
    # ------------------------------------------------------------------

    def _build_details_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predictionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._card_title("STEP 1 · DATASET DETAILS"))
        hint = QLabel("Enter the dataset name and school year before uploading.")
        hint.setObjectName("predictionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(18)

        name_col = QVBoxLayout()
        name_col.setSpacing(6)
        name_col.addWidget(self._input_label("Dataset Name"))
        self._name_input = QLineEdit()
        self._name_input.setObjectName("predictionInput")
        self._name_input.setPlaceholderText("e.g. Incoming First-Year Cohort 2025")
        self._name_input.textChanged.connect(self._refresh_button_states)
        name_col.addWidget(self._name_input)

        year_col = QVBoxLayout()
        year_col.setSpacing(6)
        year_col.addWidget(self._input_label("School Year"))
        self._year_input = QLineEdit()
        self._year_input.setObjectName("predictionInput")
        self._year_input.setPlaceholderText("e.g. 2025–2026")
        self._year_input.textChanged.connect(self._refresh_button_states)
        year_col.addWidget(self._year_input)

        row.addLayout(name_col, 1)
        row.addLayout(year_col, 1)
        layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # Step 2 — Portal upload cards
    # ------------------------------------------------------------------

    def _build_portals_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predictionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(self._card_title("STEP 2 · UPLOAD PORTAL DATASETS"))
        hint = QLabel(
            "Upload the incoming student export from each office. "
            "All four are required before merging."
        )
        hint.setObjectName("predictionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 2×2 grid of portal cards
        self._portal_cards: dict = {}
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        bot_row = QHBoxLayout()
        bot_row.setSpacing(14)

        keys = list(_PORTAL_CONFIG.keys())
        for i, key in enumerate(keys):
            pcard = self._build_portal_upload_card(key)
            self._portal_cards[key] = pcard
            (top_row if i < 2 else bot_row).addWidget(pcard, 1)

        layout.addLayout(top_row)
        layout.addLayout(bot_row)
        return card

    def _build_portal_upload_card(self, key: str) -> QFrame:
        cfg = _PORTAL_CONFIG[key]
        color = cfg["color"]

        card = QFrame()
        card.setObjectName(f"portalCard_{key}")
        card.setStyleSheet(f"""
            QFrame#portalCard_{key} {{
                background-color: rgba(0,0,0,0.18);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 12px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header row: icon + label + status dot
        hrow = QHBoxLayout()
        icon_lbl = QLabel(cfg["icon"])
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent;")
        hrow.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        name_lbl = QLabel(cfg["label"])
        name_lbl.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        full_lbl = QLabel(cfg["full"])
        full_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.4); font-size: 10px; background: transparent;"
        )
        title_col.addWidget(name_lbl)
        title_col.addWidget(full_lbl)
        hrow.addLayout(title_col, 1)

        # Status dot
        status_dot = QLabel("●")
        status_dot.setObjectName(f"portalDot_{key}")
        status_dot.setStyleSheet(
            "color: rgba(255,255,255,0.2); font-size: 10px; background: transparent;"
        )
        hrow.addWidget(status_dot)
        layout.addLayout(hrow)

        # Status label
        status_lbl = QLabel("No file uploaded")
        status_lbl.setObjectName(f"portalStatus_{key}")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 11px; background: transparent;"
        )
        layout.addWidget(status_lbl)

        # Browse button
        browse_btn = QPushButton("Browse File")
        browse_btn.setObjectName(f"portalBrowseBtn_{key}")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setFixedHeight(32)
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {color}66;
                border-radius: 6px;
                color: {color};
                font-size: 11px;
                font-weight: 600;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: {color}18;
            }}
        """)
        browse_btn.clicked.connect(lambda _, k=key: self._browse_portal(k))
        layout.addWidget(browse_btn)

        return card

    # ------------------------------------------------------------------
    # Step 3 — Merge & Clean
    # ------------------------------------------------------------------

    def _build_merge_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predictionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._card_title("STEP 3 · MERGE & CLEAN"))

        self._merge_hint = QLabel("Upload all four portal datasets to enable merging.")
        self._merge_hint.setObjectName("predictionHint")
        self._merge_hint.setWordWrap(True)
        layout.addWidget(self._merge_hint)

        # Merge report area (hidden until merge runs)
        self._merge_report_lbl = QLabel("")
        self._merge_report_lbl.setObjectName("predictionHint")
        self._merge_report_lbl.setWordWrap(True)
        self._merge_report_lbl.hide()
        layout.addWidget(self._merge_report_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._merge_btn = QPushButton("⚙  Merge Portals")
        self._merge_btn.setObjectName("predictionPipelineBtn")
        self._merge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._merge_btn.setFixedHeight(38)
        self._merge_btn.setFixedWidth(160)
        self._merge_btn.clicked.connect(self._run_merge)

        self._view_merged_btn = QPushButton("👁  View Merged")
        self._view_merged_btn.setObjectName("predictionSecondaryBtn")
        self._view_merged_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_merged_btn.setFixedHeight(38)
        self._view_merged_btn.clicked.connect(self._view_merged)

        self._clean_btn = QPushButton("🧹  Clean Data")
        self._clean_btn.setObjectName("predictionSecondaryBtn")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setFixedHeight(38)
        self._clean_btn.clicked.connect(self._clean_data)

        btn_row.addWidget(self._merge_btn)
        btn_row.addWidget(self._view_merged_btn)
        btn_row.addWidget(self._clean_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return card

    # ------------------------------------------------------------------
    # Step 4 — Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _build_fused_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predictionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._card_title("STEP 4 · RUN PIPELINE & PREDICT"))

        header_row = QHBoxLayout()
        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        self._fused_hint = QLabel("Merge the portal datasets to enable prediction.")
        self._fused_hint.setObjectName("predictionHint")
        self._fused_hint.setWordWrap(True)
        text_col.addWidget(self._fused_hint)

        self._fused_btn = QPushButton("⚡  Run Pipeline & Predict")
        self._fused_btn.setObjectName("predictionPredictBtn")
        self._fused_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fused_btn.setFixedWidth(210)
        self._fused_btn.setFixedHeight(42)
        self._fused_btn.clicked.connect(self._on_fused_clicked)

        header_row.addLayout(text_col, 1)
        header_row.addWidget(self._fused_btn)
        layout.addLayout(header_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255,255,255,0.08);
                border-radius: 3px; border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f8cff, stop:1 #34d399);
                border-radius: 3px;
            }
        """)
        self._progress_bar.hide()

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("predictionHint")
        self._progress_label.hide()

        layout.addWidget(self._progress_bar)
        layout.addWidget(self._progress_label)
        return card

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _card_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("predictionCardTitle")
        return lbl

    def _input_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("predictionInputLabel")
        return lbl

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _all_portals_uploaded(self) -> bool:
        return all(v is not None for v in self._portal_data.values())

    def _inputs_filled(self) -> bool:
        return (bool(self._name_input.text().strip())
                and bool(self._year_input.text().strip()))

    def _refresh_button_states(self):
        all_up  = self._all_portals_uploaded()
        merged  = self._merged and self._merged_rows is not None

        self._merge_btn.setEnabled(all_up)
        self._view_merged_btn.setEnabled(merged)
        self._clean_btn.setEnabled(merged)
        self._fused_btn.setEnabled(merged)

        if not all_up:
            missing = [_PORTAL_CONFIG[k]["label"]
                       for k, v in self._portal_data.items() if v is None]
            self._merge_hint.setText(
                f"Still needed: {', '.join(missing)}"
            )
        elif not merged:
            self._merge_hint.setText(
                "All portals ready — click Merge Portals to unify the datasets."
            )
        elif not self._cleaned:
            self._merge_hint.setText(
                f"Merged  ·  {len(self._merged_rows):,} students  ·  "
                "Optionally clean the data before predicting."
            )
        else:
            self._merge_hint.setText(
                f"✓  Merged & cleaned  ·  {len(self._merged_rows):,} students ready."
            )

        if merged:
            self._fused_hint.setText(
                f"Ready — {len(self._merged_rows):,} students will be scored "
                "and results saved to the database."
            )
        else:
            self._fused_hint.setText(
                "Merge the portal datasets to enable prediction."
            )

    def _update_portal_card_ui(self, key: str):
        """Refresh the status dot + label for a single portal card."""
        cfg   = _PORTAL_CONFIG[key]
        color = cfg["color"]
        data  = self._portal_data[key]

        dot   = self._portal_cards[key].findChild(QLabel, f"portalDot_{key}")
        lbl   = self._portal_cards[key].findChild(QLabel, f"portalStatus_{key}")

        if data is None:
            if dot: dot.setStyleSheet(
                "color: rgba(255,255,255,0.2); font-size: 10px; background: transparent;"
            )
            if lbl:
                lbl.setText("No file uploaded")
                lbl.setStyleSheet(
                    "color: rgba(255,255,255,0.35); font-size: 11px; background: transparent;"
                )
        else:
            if dot: dot.setStyleSheet(
                f"color: {color}; font-size: 10px; background: transparent;"
            )
            if lbl:
                rc = data["row_count"]
                lbl.setText(f"✓  {rc:,} rows · {len(data['headers'])} columns")
                lbl.setStyleSheet(
                    f"color: {color}; font-size: 11px; background: transparent;"
                )

    # ------------------------------------------------------------------
    # Portal file upload
    # ------------------------------------------------------------------

    def _browse_portal(self, key: str):
        cfg = _PORTAL_CONFIG[key]
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {cfg['full']} dataset",
            "",
            "Data Files (*.xlsx *.xls *.csv);;All Files (*)",
        )
        if not path:
            return

        try:
            df = read_excel_file(path)
        except Exception as e:
            show_error(
                self, "Upload Failed",
                f"Could not read the {cfg['label']} file.",
                str(e),
            )
            return

        headers, rows = dataframe_to_rows(df)
        if not headers or not rows:
            show_warning(
                self, "Empty File",
                f"The {cfg['label']} file contains no usable rows.",
            )
            return

        # Reject files that are clearly training outputs
        wrong = self._detect_engineered_columns(headers)
        if wrong:
            show_error(
                self, "Wrong File Type",
                f"The {cfg['label']} file contains processed feature columns.",
                f"Unexpected columns: {', '.join(wrong[:5])}"
                + (" and more…" if len(wrong) > 5 else ""),
            )
            return

        self._portal_data[key] = {
            "headers":   headers,
            "rows":      rows,
            "row_count": len(rows),
        }

        # Reset merge state whenever any portal changes
        self._merged         = False
        self._cleaned        = False
        self._merged_headers = None
        self._merged_rows    = None
        self._merge_report_lbl.hide()

        self._update_portal_card_ui(key)
        self._refresh_button_states()

        DataStore.get().add_activity(
            f"{cfg['label']} dataset uploaded — {len(rows):,} rows",
            icon=cfg["icon"],
            color=cfg["color"],
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _run_merge(self):
        if not self._all_portals_uploaded():
            return

        from services.merge_engine import MergeEngine

        # Build portals dict in the format MergeEngine expects
        portals = {
            k: {"headers": v["headers"], "rows": v["rows"]}
            for k, v in self._portal_data.items()
        }

        result = MergeEngine.merge(portals)

        if not result.success:
            show_error(
                self,
                "Merge Failed",
                "The portal datasets could not be merged.",
                "\n".join(result.report.errors),
            )
            return

        self._merged_headers = result.headers
        self._merged_rows    = result.rows
        self._merged         = True
        self._cleaned        = False

        r = result.report
        report_text = (
            f"✓  Merged  ·  {r.total_merged:,} students  ·  "
            f"{len(result.headers)} unified columns  ·  "
            f"Coverage: {r.coverage_pct:.0f}%"
        )
        if any(r.unmatched.values()):
            unmatched_parts = [
                f"{_PORTAL_CONFIG[k]['label']}: {v:,} unmatched"
                for k, v in r.unmatched.items() if v
            ]
            report_text += f"  ·  {',  '.join(unmatched_parts)}"

        self._merge_report_lbl.setText(report_text)
        self._merge_report_lbl.setStyleSheet(
            "color: #34d399; font-size: 12px; background: transparent;"
        )
        self._merge_report_lbl.show()

        name        = self._name_input.text().strip()
        school_year = self._year_input.text().strip()
        DataStore.get().add_activity(
            f"Portals merged for prediction — \"{name}\" ({school_year}), "
            f"{r.total_merged:,} students",
            icon="⚙",
            color=ACCENT,
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # View / Clean merged dataset
    # ------------------------------------------------------------------

    def _view_merged(self):
        if not self._merged or not self._merged_rows:
            return
        PortalDatasetDialog(
            portal_title="Merged Prediction Dataset",
            headers=self._merged_headers,
            rows=self._merged_rows,
            accent=ACCENT,
            readonly=True,
            parent=self,
        ).exec()

    def _clean_data(self):
        if not self._merged or not self._merged_rows:
            return

        clean = CleanDataWindow(
            self._merged_headers,
            self._merged_rows,
            _DATASET_CONFIG,
            parent=self,
        )
        if not clean.exec():
            return

        self._merged_headers = list(clean.cleaned_headers)
        self._merged_rows    = [list(r) for r in clean.cleaned_rows]
        self._cleaned        = True

        self._merge_report_lbl.setText(
            f"✓  Merged & cleaned  ·  {len(self._merged_rows):,} rows  ·  "
            f"{len(self._merged_headers)} columns — ready to predict"
        )
        self._merge_report_lbl.setStyleSheet(
            "color: #34d399; font-size: 12px; background: transparent;"
        )

        DataStore.get().add_activity(
            f"Merged dataset cleaned — {len(self._merged_rows):,} rows",
            icon="🧹",
            color="#34d399",
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # Fused Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _on_fused_clicked(self):
        if not self._merged or not self._merged_rows:
            return

        # Validate required fields before launching worker
        name        = self._name_input.text().strip()
        school_year = self._year_input.text().strip()
        if not name or not school_year:
            show_warning(
                self,
                "Missing Details",
                "Please fill in the Dataset Name and School Year (Step 1) "
                "before running prediction.",
            )
            # Scroll to top so user sees Step 1
            self._name_input.setFocus()
            return

        store = DataStore.get()

        # Ensure model is loaded
        if not store.trained_model:
            from services.model_registry import ModelRegistry
            pkg = ModelRegistry.load_latest_model()
            if pkg:
                store.trained_model = {
                    "model":         pkg["model"],
                    "model_id":      pkg["model_id"],
                    "feature_names": pkg["feature_names"],
                    "metadata":      pkg["metadata"],
                    "target_col":    "risk_label",
                }
                store.model_ready = True
            else:
                show_warning(
                    self,
                    "No Trained Model",
                    "No trained model was found.",
                    "Complete Model Training first, then return here to run prediction.",
                )
                return

        dialog = ConfirmationDialog(
            "Run Pipeline & Predict",
            f"Score {len(self._merged_rows):,} students in \"{name}\" ({school_year})?",
            detail="Feature engineering runs first, then the trained model scores "
                   "each student. Results are saved to the database automatically.",
            parent=self,
        )
        if not dialog.exec():
            return

        self._fused_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._progress_label.setText("Starting…")
        self._progress_label.show()
        self.overlay.set_message("Running Pipeline & Prediction…", "Engineering features")
        self.overlay.show()

        self._fused_worker = _FusedPredictionWorker(
            headers     = self._merged_headers,
            rows        = self._merged_rows,
            name        = name,
            school_year = school_year,
        )
        self._fused_worker.progress.connect(self._on_fused_progress)
        self._fused_worker.finished.connect(self._on_fused_finished)
        self._fused_worker.error.connect(self._on_fused_error)
        self._fused_worker.finished.connect(
            self._fused_worker.deleteLater, Qt.ConnectionType.QueuedConnection
        )
        self._fused_worker.error.connect(
            self._fused_worker.deleteLater, Qt.ConnectionType.QueuedConnection
        )
        self._fused_worker.start()

    def _on_fused_progress(self, step: str, pct: int):
        self._progress_bar.setValue(pct)
        self._progress_label.setText(step)
        self.overlay.set_message("Running Pipeline & Prediction…", step)

    def _on_fused_finished(self, result):
        self._progress_bar.setValue(100)
        self._fused_worker = None
        self._on_prediction_complete(result)
        self._progress_bar.hide()
        self._progress_label.hide()
        self._fused_btn.setEnabled(True)

    def _on_fused_error(self, error_msg: str):
        self.overlay.hide()
        self._progress_bar.hide()
        self._progress_label.hide()
        self._fused_btn.setEnabled(True)
        self._fused_worker = None
        show_error(
            self,
            "Pipeline & Prediction Failed",
            "The operation could not complete.",
            error_msg,
        )

    # ------------------------------------------------------------------
    # PredictionMixin hook
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        if not result or not result.success:
            return
        s    = result.summary
        name = self._name_input.text().strip()
        yr   = self._year_input.text().strip()
        self._fused_hint.setText(
            f"✓  Scored {s.total:,} students  ·  {s.high_risk:,} high-risk  ·  "
            f"\"{name}\" ({yr})  ·  Saved to database."
        )

    # ------------------------------------------------------------------
    # Validation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_engineered_columns(headers: list) -> list[str]:
        _ENGINEERED_MARKERS = {
            "risk_label", "Entrance_Exam_Tier", "HS_Performance_Tier",
            "Strand_Program_Match", "Financial_Stress", "First_Gen_Student",
            "Has_Scholarship", "Gap_Years", "Private_HS", "Has_HS_Honors",
            "Age_At_Enrollment", "Age_Group", "Distance_Bucket", "Distance_KM",
            "Program_Risk_Index", "Municipality_Risk_Index",
            "GPA_Tier", "Has_College_Grade", "Year_Level",
        }
        return sorted(_ENGINEERED_MARKERS & {h.strip() for h in headers})

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_page_styles(self):
        self.setStyleSheet(f"""
            #predictionModelCard {{
                background-color: rgba(0,0,0,0.2);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }}
            #predictionModelStatus {{
                color: #2ecc71; font-weight: bold; font-size: 12px;
            }}
            #predictionCard {{
                background-color: rgba(0,0,0,0.22);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
            }}
            #predictionCardTitle {{
                color: rgba(255,255,255,0.45);
                font-size: 11px; font-weight: bold; letter-spacing: 1px;
            }}
            #predictionHint {{
                color: rgba(255,255,255,0.5); font-size: 12px;
            }}
            #predictionInputLabel {{
                color: rgba(255,255,255,0.75);
                font-size: 12px; font-weight: 600;
            }}
            #predictionInput {{
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px; color: white;
                font-size: 13px; padding: 9px 12px;
            }}
            #predictionInput:focus {{ border: 1px solid {ACCENT}; }}
            #predictionSecondaryBtn {{
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px; color: rgba(255,255,255,0.85);
                font-size: 12px; font-weight: 600; padding: 0 18px;
            }}
            #predictionSecondaryBtn:hover {{
                background-color: rgba(255,255,255,0.12);
            }}
            #predictionSecondaryBtn:disabled {{
                background-color: rgba(255,255,255,0.03);
                color: rgba(255,255,255,0.3);
                border-color: rgba(255,255,255,0.06);
            }}
            #predictionPredictBtn {{
                background-color: #2ecc71; border: none;
                border-radius: 8px; color: white;
                font-size: 13px; font-weight: 700; padding: 0 20px;
            }}
            #predictionPredictBtn:hover {{ background-color: #29b765; }}
            #predictionPredictBtn:disabled {{
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.35);
            }}
            #predictionPipelineBtn {{
                background-color: {ACCENT}; border: none;
                border-radius: 8px; color: white;
                font-size: 12px; font-weight: 700; padding: 0 20px;
            }}
            #predictionPipelineBtn:hover {{
                background-color: rgba(79,140,255,0.85);
            }}
            #predictionPipelineBtn:disabled {{
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.35);
            }}
        """)