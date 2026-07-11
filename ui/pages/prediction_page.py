"""
ui/pages/prediction_page.py
==============================
4-step wizard: Dataset Details -> Upload Portals -> Merge & Clean ->
Run Pipeline & Predict. Each step is a full-width page inside a
_SlideStack; Back/Next slide between them.

Extracted into their own modules to keep this file focused on page
orchestration:
  workers/prediction_workers.py            -> _FusedPredictionWorker
  ui/widgets/slide_stack.py                -> _SlideStack
  ui/widgets/step_indicator.py             -> StepIndicatorBar (shared with
                                               data_merge_pipeline_page.py)
  ui/widgets/wizard_nav.py                 -> WizardNavBar (shared with
                                               data_merge_pipeline_page.py)
  ui/pages/prediction/constants.py         -> ACCENT, PORTAL_CONFIG,
                                               DATASET_CONFIG, STEP_META
  ui/pages/prediction/model_status_panel.py-> ModelStatusPanel
  ui/pages/prediction/header_card.py       -> HeaderCard
  ui/helpers/prediction_render.py          -> step_label, input_label

What stays here: the four step-card builders and all business logic
(portal upload + validation, merge, clean, the fused predict flow, the
duplicate-term DB check, and the wizard's Back/Next state machine). These
methods read and write self._portal_data, self._merged_headers/rows,
self._name_input, DataStore, and dialogs directly — splitting them further
would mean introducing a controller/presenter layer, which is a design
change, not a relocation (same reasoning as portal_upload_page.py's
refactor earlier in this project).
"""
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QFileDialog,
    QProgressBar, QScrollArea, QGridLayout, QComboBox,
)
from PyQt6.QtCore import Qt

from ui.mixins.prediction_mixin import PredictionMixin
from ui.components.loading_overlay import LoadingOverlay
from ui.dialogs.portal_dataset_dialog import PortalDatasetDialog
from ui.dialogs.clean_data_window import CleanDataWindow
from ui.dialogs.confirmation_dialog import ConfirmationDialog, show_error, show_warning
from services.data_store import DataStore
from services.excel_service import read_excel_file, dataframe_to_rows
from services.system_config import SystemConfig

from workers.prediction_workers import _FusedPredictionWorker
from ui.widgets.slide_stack import _SlideStack
from ui.widgets.step_indicator import StepIndicatorBar
from ui.widgets.wizard_nav import WizardNavBar
from ui.pages.prediction.constants import ACCENT, PORTAL_CONFIG, DATASET_CONFIG, STEP_META
from ui.pages.prediction.model_status_panel import ModelStatusPanel
from ui.pages.prediction.header_card import HeaderCard
from ui.helpers.prediction_render import step_label, input_label


# =============================================================================
# PREDICTION PAGE
# =============================================================================

class PredictionPage(PredictionMixin, QWidget):
    """
    Upload four portal datasets -> merge -> run feature engineering
    and model scoring -> results saved to DB automatically.
    """

    _WIZARD_HEIGHT = 460   # fixed height for the slide area; tallest step
                           # (portal grid) sets the floor, shorter steps use
                           # addStretch() to pin content to the top instead
                           # of stretching to fill the space.

    def __init__(self):
        super().__init__()
        self._portal_data: dict = {k: None for k in PORTAL_CONFIG}
        self._merged_headers: list | None = None
        self._merged_rows:    list | None = None
        self._merged  = False
        self._cleaned = False
        self._fused_worker: _FusedPredictionWorker | None = None
        self._available_terms: list = []

        # Wizard navigation state
        self._current_step  = 0
        self._furthest_step = 0

        self.setup_ui()
        self._apply_styles()
        self.init_prediction(
            overlay_message="Running Pipeline & Prediction…",
            overlay_sub="Preparing features",
        )
        self._refresh_button_states()
        DataStore.get().add_listener(self._on_store_updated)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_model_status_panel"):
            self._model_status_panel.refresh()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.setObjectName("page")
        self.overlay = LoadingOverlay(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("predScroll")

        container = QWidget()
        container.setObjectName("predPageContainer")
        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._header_card = HeaderCard()
        self.main_layout.addWidget(self._header_card)

        self._model_status_panel = ModelStatusPanel()
        self.main_layout.addWidget(self._model_status_panel)

        self.main_layout.addWidget(self._build_wizard())
        self.main_layout.addStretch()

        self._init_wizard_state()

    def _on_store_updated(self, key: str):
        if key in ("system_config", "all"):
            if hasattr(self, "_header_card"):
                self._header_card.refresh_term_label()
            if hasattr(self, "_academic_year_combo"):
                self._academic_year_combo.setCurrentText(SystemConfig.academic_year())
            if hasattr(self, "_semester_combo"):
                self._semester_combo.setCurrentIndex(SystemConfig.semester() - 1)
        if key in ("trained_model", "all"):
            if hasattr(self, "_model_status_panel"):
                self._model_status_panel.refresh()

    # ------------------------------------------------------------------
    # Wizard shell: step indicator + slide stack + nav
    # ------------------------------------------------------------------

    def _build_wizard(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predWizardCard")
        card.setStyleSheet("""
            QFrame#predWizardCard {
                background-color: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._step_indicator = StepIndicatorBar(steps=STEP_META, accent=ACCENT)
        self._step_indicator.step_clicked.connect(self._on_step_chip_clicked)
        layout.addWidget(self._step_indicator)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.07); border: none;")
        layout.addWidget(sep)

        self._slide_stack = _SlideStack()
        self._slide_stack.setObjectName("predSlideStack")
        self._slide_stack.setFixedHeight(self._WIZARD_HEIGHT)

        self._step_pages = [
            self._build_details_card(),
            self._build_portals_card(),
            self._build_merge_card(),
            self._build_fused_card(),
        ]
        for page in self._step_pages:
            self._slide_stack.add_page(page)

        layout.addWidget(self._slide_stack)

        self._wizard_nav = WizardNavBar()
        self._wizard_nav.back_clicked.connect(self._go_back)
        self._wizard_nav.next_clicked.connect(self._go_next)
        layout.addWidget(self._wizard_nav)

        return card

    # ------------------------------------------------------------------
    # Wizard navigation state machine
    # ------------------------------------------------------------------

    def _init_wizard_state(self):
        self._current_step  = 0
        self._furthest_step = 0
        self._update_wizard_nav()
        self._update_step_indicator()

    def _step_is_complete(self, idx: int) -> bool:
        if idx == 0:
            return bool(self._name_input.text().strip())
        if idx == 1:
            return self._all_portals_uploaded()
        if idx == 2:
            return self._merged and self._merged_rows is not None
        return False   # last step is terminal — no "next"

    def _go_next(self):
        if self._current_step >= len(self._step_pages) - 1:
            return
        if not self._step_is_complete(self._current_step):
            if self._current_step == 0:
                self._name_input.setFocus()
            return
        self._go_to_step(self._current_step + 1)

    def _go_back(self):
        if self._current_step <= 0:
            return
        self._go_to_step(self._current_step - 1)

    def _go_to_step(self, idx: int):
        idx = max(0, min(idx, len(self._step_pages) - 1))
        if idx > self._current_step and not self._step_is_complete(self._current_step):
            return
        self._current_step  = idx
        self._furthest_step = max(self._furthest_step, idx)
        self._slide_stack.slide_to(idx)
        self._update_wizard_nav()
        self._update_step_indicator()

    def _on_step_chip_clicked(self, idx: int):
        if idx <= self._furthest_step:
            self._go_to_step(idx)

    def _update_wizard_nav(self):
        is_last = self._current_step == len(self._step_pages) - 1
        self._wizard_nav.set_state(
            current      = self._current_step,
            total        = len(self._step_pages),
            back_visible = self._current_step > 0,
            next_visible = not is_last,
            next_enabled = self._step_is_complete(self._current_step),
        )

    def _update_step_indicator(self):
        completed = [self._step_is_complete(i) for i in range(len(self._step_pages))]
        self._step_indicator.set_state(self._current_step, self._furthest_step, completed)

    # ------------------------------------------------------------------
    # Step 01 — Dataset Details
    # ------------------------------------------------------------------

    def _build_details_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardDetails")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(step_label("01", "Dataset Details"))

        hint = QLabel(
            "Name this cohort, select the academic term, then continue to "
            "upload the portal files."
        )
        hint.setObjectName("predHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(input_label("Dataset Name"))
        self._name_input = QLineEdit()
        self._name_input.setObjectName("predInput")
        self._name_input.setPlaceholderText("e.g. Incoming First-Year Cohort 2025")
        self._name_input.textChanged.connect(self._refresh_button_states)
        layout.addWidget(self._name_input)

        term_row = QHBoxLayout()
        term_row.setSpacing(10)

        ay_col = QVBoxLayout()
        ay_col.setSpacing(6)
        ay_col.addWidget(input_label("Academic Year"))
        self._academic_year_combo = QComboBox()
        self._academic_year_combo.setObjectName("predTermCombo")
        for ay in ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]:
            self._academic_year_combo.addItem(ay)
        self._academic_year_combo.setCurrentText(SystemConfig.academic_year())
        ay_col.addWidget(self._academic_year_combo)

        sem_col = QVBoxLayout()
        sem_col.setSpacing(6)
        sem_col.addWidget(input_label("Semester"))
        self._semester_combo = QComboBox()
        self._semester_combo.setObjectName("predTermCombo")
        self._semester_combo.addItem("1st Semester", userData=1)
        self._semester_combo.addItem("2nd Semester", userData=2)
        self._semester_combo.setCurrentIndex(SystemConfig.semester() - 1)
        sem_col.addWidget(self._semester_combo)

        term_row.addLayout(ay_col, 2)
        term_row.addLayout(sem_col, 1)
        layout.addLayout(term_row)

        self._term_preview = QLabel()
        self._term_preview.setObjectName("predTermPreview")
        self._update_term_preview()
        self._academic_year_combo.currentTextChanged.connect(
            lambda _: self._update_term_preview())
        self._semester_combo.currentIndexChanged.connect(
            lambda _: self._update_term_preview())
        layout.addWidget(self._term_preview)

        layout.addStretch()
        return card

    def _update_term_preview(self):
        ay  = self._academic_year_combo.currentText()
        sem = self._semester_combo.currentText()
        self._term_preview.setText(f"Term: {ay} — {sem}")

    def _get_selected_term(self) -> tuple[str, int]:
        ay  = self._academic_year_combo.currentText()
        sem = self._semester_combo.currentData() or 1
        return ay, int(sem)

    # ------------------------------------------------------------------
    # Step 02 — Upload Portal Datasets
    # ------------------------------------------------------------------

    def _build_portals_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardPortals")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(step_label("02", "Upload Portal Datasets"))

        hint = QLabel(
            "Upload the incoming student export from each office. "
            "All four are required before merging."
        )
        hint.setObjectName("predHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._portal_cards: dict = {}
        grid = QGridLayout()
        grid.setSpacing(12)

        for i, key in enumerate(list(PORTAL_CONFIG.keys())):
            pcard = self._build_portal_tile(key)
            self._portal_cards[key] = pcard
            grid.addWidget(pcard, i // 2, i % 2)

        layout.addLayout(grid)
        layout.addStretch()
        return card

    def _build_portal_tile(self, key: str) -> QFrame:
        cfg   = PORTAL_CONFIG[key]
        color = cfg["color"]

        tile = QFrame()
        tile.setObjectName("predPortalTile")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        icon_lbl = QLabel(cfg["icon"])
        icon_lbl.setStyleSheet("font-size: 18px; background: transparent;")
        top.addWidget(icon_lbl)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_lbl = QLabel(cfg["label"])
        name_lbl.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        full_lbl = QLabel(cfg["full"])
        full_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 10px; background: transparent;"
        )
        name_col.addWidget(name_lbl)
        name_col.addWidget(full_lbl)
        top.addLayout(name_col, 1)

        dot = QLabel("●")
        dot.setObjectName(f"portalDot_{key}")
        dot.setStyleSheet(
            "color: rgba(255,255,255,0.18); font-size: 9px; background: transparent;"
        )
        top.addWidget(dot)
        layout.addLayout(top)

        status_lbl = QLabel("No file uploaded")
        status_lbl.setObjectName(f"portalStatus_{key}")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.3); font-size: 11px; background: transparent;"
        )
        layout.addWidget(status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(0)

        btn = QPushButton("  Browse File")
        btn.setObjectName("predPortalBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setFixedWidth(120)
        btn.clicked.connect(lambda _, k=key: self._browse_portal(k))
        btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return tile

    # ------------------------------------------------------------------
    # Step 03 — Merge & Clean
    # ------------------------------------------------------------------

    def _build_merge_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardMerge")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(step_label("03", "Merge & Clean"))

        self._merge_hint = QLabel("Upload all four portal datasets to enable merging.")
        self._merge_hint.setObjectName("predHint")
        self._merge_hint.setWordWrap(True)
        layout.addWidget(self._merge_hint)

        self._merge_report_lbl = QLabel("")
        self._merge_report_lbl.setObjectName("predSuccess")
        self._merge_report_lbl.setWordWrap(True)
        self._merge_report_lbl.hide()
        layout.addWidget(self._merge_report_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._merge_btn = QPushButton("⚙  Merge Portals")
        self._merge_btn.setObjectName("predPrimaryBtn")
        self._merge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._merge_btn.setFixedHeight(36)
        self._merge_btn.clicked.connect(self._run_merge)

        self._view_merged_btn = QPushButton("View Merged")
        self._view_merged_btn.setObjectName("predSecondaryBtn")
        self._view_merged_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_merged_btn.setFixedHeight(36)
        self._view_merged_btn.clicked.connect(self._view_merged)

        self._clean_btn = QPushButton("Clean Data")
        self._clean_btn.setObjectName("predSecondaryBtn")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setFixedHeight(36)
        self._clean_btn.clicked.connect(self._clean_data)

        btn_row.addWidget(self._merge_btn)
        btn_row.addWidget(self._view_merged_btn)
        btn_row.addWidget(self._clean_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return card

    # ------------------------------------------------------------------
    # Step 04 — Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _build_fused_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardPredict")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.setSpacing(20)

        label_col = QVBoxLayout()
        label_col.setSpacing(6)
        label_col.addWidget(step_label("04", "Run Pipeline & Predict"))
        self._fused_hint = QLabel("Merge the portal datasets to enable prediction.")
        self._fused_hint.setObjectName("predHint")
        self._fused_hint.setWordWrap(True)
        label_col.addWidget(self._fused_hint)

        self._fused_btn = QPushButton("Run Pipeline & Predict")
        self._fused_btn.setObjectName("predRunBtn")
        self._fused_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fused_btn.setFixedWidth(220)
        self._fused_btn.setFixedHeight(42)
        self._fused_btn.clicked.connect(self._on_fused_clicked)

        top_row.addLayout(label_col, 1)
        top_row.addWidget(self._fused_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setObjectName("predProgressBar")
        layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("predHint")
        layout.addWidget(self._progress_label)

        layout.addStretch()
        return card

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _all_portals_uploaded(self) -> bool:
        return all(v is not None for v in self._portal_data.values())

    def _refresh_button_states(self):
        all_up = self._all_portals_uploaded()
        merged = self._merged and self._merged_rows is not None

        self._merge_btn.setEnabled(all_up)
        self._view_merged_btn.setEnabled(merged)
        self._clean_btn.setEnabled(merged)
        self._fused_btn.setEnabled(merged)

        if not all_up:
            missing = [PORTAL_CONFIG[k]["label"]
                       for k, v in self._portal_data.items() if v is None]
            self._merge_hint.setText(f"Still needed: {', '.join(missing)}")
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
            self._fused_hint.setText("Merge the portal datasets to enable prediction.")

        # Keep wizard nav / step indicator in sync with whatever changed
        # (portal upload, merge, clean, or dataset-name edits all funnel
        # through this one method already).
        if hasattr(self, "_wizard_nav"):
            self._update_wizard_nav()
            self._update_step_indicator()

    def _update_portal_card_ui(self, key: str):
        cfg   = PORTAL_CONFIG[key]
        color = cfg["color"]
        data  = self._portal_data[key]
        tile  = self._portal_cards[key]

        dot = tile.findChild(QLabel, f"portalDot_{key}")
        lbl = tile.findChild(QLabel, f"portalStatus_{key}")

        if data is None:
            if dot:
                dot.setStyleSheet(
                    "color: rgba(255,255,255,0.18); font-size: 9px; background: transparent;"
                )
            if lbl:
                lbl.setText("No file uploaded")
                lbl.setStyleSheet(
                    "color: rgba(255,255,255,0.3); font-size: 11px; background: transparent;"
                )
        else:
            if dot:
                dot.setStyleSheet(
                    f"color: {color}; font-size: 9px; background: transparent;"
                )
            if lbl:
                lbl.setText(f"✓  {data['row_count']:,} rows · {len(data['headers'])} columns")
                lbl.setStyleSheet(
                    f"color: {color}; font-size: 11px; background: transparent;"
                )

    # ------------------------------------------------------------------
    # Portal file upload
    # ------------------------------------------------------------------

    def _browse_portal(self, key: str):
        cfg  = PORTAL_CONFIG[key]
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {cfg['full']} dataset", "",
            "Data Files (*.xlsx *.xls *.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            df = read_excel_file(path)
        except Exception as e:
            show_error(self, "Upload Failed",
                       f"Could not read the {cfg['label']} file.", str(e))
            return

        headers, rows = dataframe_to_rows(df)
        if not headers or not rows:
            show_warning(self, "Empty File",
                         f"The {cfg['label']} file contains no usable rows.")
            return

        wrong = self._detect_engineered_columns(headers)
        if wrong:
            show_error(self, "Wrong File Type",
                       f"The {cfg['label']} file contains processed feature columns.",
                       f"Unexpected columns: {', '.join(wrong[:5])}"
                       + (" and more…" if len(wrong) > 5 else ""))
            return

        self._portal_data[key] = {"headers": headers, "rows": rows, "row_count": len(rows)}
        self._merged  = False
        self._cleaned = False
        self._merged_headers = None
        self._merged_rows    = None
        self._merge_report_lbl.hide()
        self._update_portal_card_ui(key)
        self._refresh_button_states()

        DataStore.get().add_activity(
            f"{cfg['label']} dataset uploaded — {len(rows):,} rows",
            icon=cfg["icon"], color=cfg["color"],
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _run_merge(self):
        if not self._all_portals_uploaded():
            return
        from services.merge_engine import MergeEngine

        portals = {k: {"headers": v["headers"], "rows": v["rows"]}
                   for k, v in self._portal_data.items()}
        result = MergeEngine.merge(portals)

        if not result.success:
            show_error(self, "Merge Failed",
                       "The portal datasets could not be merged.",
                       "\n".join(result.report.errors))
            return

        self._merged_headers = result.headers
        self._merged_rows    = result.rows
        self._merged  = True
        self._cleaned = False

        r = result.report
        report_text = (
            f"✓  {r.total_merged:,} students  ·  {len(result.headers)} columns  ·  "
            f"Coverage {r.coverage_pct:.0f}%"
        )
        if any(r.unmatched.values()):
            parts = [f"{PORTAL_CONFIG[k]['label']}: {v:,} unmatched"
                     for k, v in r.unmatched.items() if v]
            report_text += f"  ·  {',  '.join(parts)}"

        self._merge_report_lbl.setText(report_text)
        self._merge_report_lbl.show()

        DataStore.get().add_activity(
            f"Portals merged — {r.total_merged:,} students  ·  "
            f"\"{self._name_input.text().strip()}\" "
            f"({self._academic_year_combo.currentText()})",
            icon="⚙", color=ACCENT,
        )

        try:
            from services.activity_logger import ActivityLogger
            _conn = DataStore.get().db_conn
            if _conn:
                ActivityLogger.log_merge(
                    _conn,
                    total_merged = r.total_merged,
                    coverage_pct = float(r.coverage_pct),
                    dataset_name = self._name_input.text().strip(),
                )
                _conn.commit()
        except Exception as _e:
            print(f"[PredictionPage] Merge log error: {_e}")

        self._refresh_button_states()

    # ------------------------------------------------------------------
    # View / Clean
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
            self._merged_headers, self._merged_rows, DATASET_CONFIG, parent=self,
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
        DataStore.get().add_activity(
            f"Merged dataset cleaned — {len(self._merged_rows):,} rows",
            icon="🧹", color="#34d399",
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # Duplicate term check
    # ------------------------------------------------------------------

    def _check_existing_prediction(self, academic_year: str, semester: int) -> int:
        """
        Return the count of rows already saved in fact_student_academic_risk
        for the given (academic_year, semester).
        Returns 0 if the DB is unavailable or the term has no records.
        """
        conn = DataStore.get().db_conn
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    WHERE  t.academic_year = %s
                      AND  t.semester      = %s
                    """,
                    (academic_year, semester),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            print(f"[PredictionPage] Term existence check failed: {exc}")
            return 0   # fail open — don't block the user on a DB error

    # ------------------------------------------------------------------
    # Fused Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _on_fused_clicked(self):
        if not self._merged or not self._merged_rows:
            return

        name        = self._name_input.text().strip()
        school_year = self._academic_year_combo.currentText()
        if not name or not school_year:
            show_warning(self, "Missing Details",
                         "Fill in the Dataset Name and School Year (Step 01) "
                         "before running prediction.")
            self._go_to_step(0)
            self._name_input.setFocus()
            return

        store = DataStore.get()
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
                show_warning(self, "No Trained Model",
                             "No trained model was found.",
                             "Complete Model Training first, then return here.")
                return

        # ── Duplicate term check ──────────────────────────────────────
        # Query the DB before showing the confirmation dialog.
        # If this (academic_year, semester) already has saved predictions,
        # warn the user and require explicit confirmation to overwrite.
        academic_year, semester = self._get_selected_term()
        existing_count = self._check_existing_prediction(academic_year, semester)
        if existing_count > 0:
            sem_label = "1st" if semester == 1 else "2nd"
            overwrite_dlg = ConfirmationDialog(
                "Prediction Already Exists",
                f"{academic_year} — {sem_label} Semester already has "
                f"{existing_count:,} saved prediction records.",
                detail="Running again will overwrite the existing results for "
                       "this term. Do you want to continue?",
                confirm_label="Overwrite",
                parent=self,
            )
            if not overwrite_dlg.exec():
                return

        # ── Normal confirmation ───────────────────────────────────────
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
        self._progress_label.setText("Starting…")
        self.overlay.set_message("Running Pipeline & Prediction…", "Engineering features")
        self.overlay.show()

        self._fused_worker = _FusedPredictionWorker(
            headers       = self._merged_headers,
            rows          = self._merged_rows,
            name          = name,
            school_year   = school_year,
            academic_year = academic_year,
            semester      = semester,
        )
        self._fused_worker.progress.connect(self._on_fused_progress)
        self._fused_worker.finished.connect(self._on_fused_finished)
        self._fused_worker.error.connect(self._on_fused_error)
        self._fused_worker.finished.connect(
            self._fused_worker.deleteLater,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fused_worker.error.connect(
            self._fused_worker.deleteLater,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fused_worker.start()

    def _on_fused_progress(self, step: str, pct: int):
        self._progress_bar.setValue(pct)
        self._progress_label.setText(step)
        self.overlay.set_message("Running Pipeline & Prediction…", step)

    def _on_fused_finished(self, result):
        self._progress_bar.setValue(100)
        self._progress_label.setText("Done ✅")
        self._fused_btn.setEnabled(True)
        self._on_prediction_complete(result)
        self._fused_worker = None

    def _on_fused_error(self, error_msg: str):
        self.overlay.hide()
        self._progress_bar.setValue(0)
        self._progress_label.setText("")
        self._fused_btn.setEnabled(True)
        self._fused_worker = None
        show_error(self, "Pipeline & Prediction Failed",
                   "The operation could not complete.", error_msg)

    # ------------------------------------------------------------------
    # PredictionMixin hook
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        if not result or not result.success:
            return
        s    = result.summary
        name = self._name_input.text().strip()
        yr   = self._academic_year_combo.currentText()
        self._fused_hint.setText(
            f"✓  Scored {s.total:,} students  ·  {s.high_risk:,} high-risk  ·  "
            f"\"{name}\" ({yr})  ·  Saved to database."
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_engineered_columns(headers: list) -> list[str]:
        _MARKERS = {
            "risk_label", "Entrance_Exam_Tier", "HS_Performance_Tier",
            "Strand_Program_Match", "Financial_Stress", "First_Gen_Student",
            "Has_Scholarship", "Gap_Years", "Private_HS", "Has_HS_Honors",
            "Age_At_Enrollment", "Age_Group", "Distance_Bucket", "Distance_KM",
            "Program_Risk_Index", "Municipality_Risk_Index",
            "GPA_Tier", "Has_College_Grade", "Year_Level",
        }
        return sorted(_MARKERS & {h.strip() for h in headers})

    def _apply_styles(self):
        pass  # Styles defined in theme.qss

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)