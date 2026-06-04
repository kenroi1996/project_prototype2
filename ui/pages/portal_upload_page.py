from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QProgressBar,
    QGraphicsOpacityEffect,
    QFileDialog,
    QGridLayout,
    QMessageBox,
    QTextEdit,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve

from ui.dialogs.preview_dataset import DatasetPreviewDialog
from ui.dialogs.clean_data_window import CleanDataWindow
from ui.dialogs.portal_dataset_dialog import PortalDatasetDialog
from services.data_store import DataStore
from services.excel_service import read_excel_file, dataframe_to_rows, rows_to_dataframe
from services.database_service import DatabaseService, PORTAL_SOURCE_CONFIGS
from services.preprocessing_service import CleaningEngine, compute_issues


PORTAL_CONFIGS = {
    "mis": {
        "title": "MIS Portal",
        "office": "Management Information System",
        "subtitle": "Academic records & enrollment data",
        "description": (
            "Upload semester grades, units earned, failed subjects, "
            "and program enrollment from the MIS office."
        ),
        "accent": "#4f8cff",
        "file_hint": "mis_academic_records_2024.csv",
        "fields": [
            "KEYID", "SYSTEMCODE", "ID_NO", "PROGRAM", "COLLEGE",
            "SECCODE", "YEAR", "SEX_CODE", "HOME_ADDRESS", "CIVIL_STATUS",
            "RELIGION", "FINAL_AVG_GRD",
        ],
    },
    "sao": {
        "title": "SAO Portal",
        "office": "Student Affairs Office",
        "subtitle": "Attendance, conduct & student life data",
        "description": (
            "Upload attendance logs, org membership, violations, "
            "and financial aid status from the SAO office."
        ),
        "accent": "#34d399",
        "file_hint": "sao_student_affairs_2024.csv",
        "fields": [
            "STUDENT_ID", "SCHOLARSHIP_APPLICANT", "SCHOLARSHIP_TYPE",
            "GENDER", "BIRTHDATE", "MUNICIPALITY", "PROGRAM",
        ],
    },
    "guidance": {
        "title": "Guidance Portal",
        "office": "Guidance & Counseling Office",
        "subtitle": "Psychological screening & referral records",
        "description": (
            "Upload psychometric scores, counseling referrals, "
            "and socio-economic background from the Guidance office."
        ),
        "accent": "#f59e0b",
        "file_hint": "guidance_psych_records_2024.csv",
        "fields": [
            "Date", "student_id", "systemcode", "last_name", "first_name",
            "entrance_exam_score", "family_income_bracket",
            "parent_highest_education", "applicant_age",
            "home_municipality", "program_code"
        ],
    },
    "registrar": {
        "title": "Registrar Portal",
        "office": "Office of the Registrar",
        "subtitle": "Student biographical & high school background data",
        "description": (
            "Upload student identity, demographic, and high school "
            "background records for cohort mapping and risk modeling."
        ),
        "accent": "#a78bfa",
        "file_hint": "registrar_student_records_2024.csv",
        "fields": [
            "student_id", "lastname", "firstname", "gender", "hs_gpa",
            "year_graduated", "shs_strand", "hs_type", "graduation_honors",
            "hs_school", "municipality", "home_address", "year_enrolled",
        ],
    },
}


class PortalUploadPage(QWidget):
    """Data upload portal for office record uploads (MIS, SAO, Guidance, Registrar)."""

    # Total expected records across all portals (used for completeness calc)
    def __init__(self, portal_key="mis"):
        super().__init__()
        self._portal_key = portal_key
        self.config = PORTAL_CONFIGS[portal_key]
        self._selected_file = None
        self._cleaned_headers = None
        self._cleaned_rows = None

        # Dynamic state
        self._upload_history = []  # List of (filename, meta, level, timestamp)
        self._last_updated = None
        
        self.setup_ui()
        self._apply_page_styles()
        self._refresh_from_datastore()

    # ── DYNAMIC STATE HELPERS ─────────────────────────────────────

    def _get_records(self):
        """Get current record count from DataStore or cleaned rows."""
        store = DataStore.get()
        portal_data = store.get_portal(self._portal_key)
        if portal_data and "rows" in portal_data:
            return len(portal_data["rows"])
        if self._cleaned_rows is not None:
            return len(self._cleaned_rows)
        return 0

    def _get_total(self):
        """Get total expected records for THIS portal only."""
        store = DataStore.get()
        
        # Use this portal's stored data as the "expected" total
        portal_data = store.get_portal(self._portal_key)
        if portal_data and "rows" in portal_data:
            return len(portal_data["rows"])
        
        # Fallback to local cleaned rows if not yet saved to store
        if self._cleaned_rows:
            return len(self._cleaned_rows)
        
        # No data yet — return 1 to avoid division by zero
        return 1

    def _get_completeness(self):
        """Calculate completeness percentage based on records vs expected total."""
        total = self._get_total()
        records = self._get_records()
        if total == 0:
            return 0
        # If total equals records (same source), show 100%
        # This happens when we only have this portal's own data
        return min(int((records / total) * 100), 100)


    def _get_status(self):
        """Determine status based on record completeness."""
        completeness = self._get_completeness()
        if completeness == 0:
            return "Pending"
        elif completeness >= 95:
            return "Complete"
        else:
            return "Partial"

    def _get_status_detail(self):
        """Generate dynamic status detail text."""
        records = self._get_records()
        status = self._get_status()
        
        if status == "Pending":
            return "Upload pending · No data loaded"
        elif status == "Complete":
            ts = self._last_updated.strftime("%b %d, %Y") if self._last_updated else "Recently"
            return f"Last updated: {ts}"
        else:
            ts = self._last_updated.strftime("%b %d, %Y") if self._last_updated else "Recently"
            return f"Data loaded · Last updated: {ts}"

    def _get_bar_color(self):
        """Get progress bar color based on completeness."""
        comp = self._get_completeness()
        if comp >= 95:
            return "#34d399"
        elif comp >= 80:
            return "#4f8cff"
        else:
            return "#f59e0b"

    def _add_history_entry(self, filename, row_count, level="success"):
        """Add an entry to upload history."""
        from datetime import datetime
        now = datetime.now()
        self._last_updated = now
        meta = f"{now.strftime('%b %d, %Y')} · {row_count:,} rows"
        self._upload_history.insert(0, (filename, meta, level))
        # Keep only last 10 entries
        self._upload_history = self._upload_history[:10]
        self._refresh_history_ui()

    def _refresh_from_datastore(self):
        """Refresh UI state from DataStore on init."""
        store = DataStore.get()
        portal_data = store.get_portal(self._portal_key)
        
        if portal_data:
            self._cleaned_headers = portal_data.get("headers")
            self._cleaned_rows = portal_data.get("rows")
            self._selected_file = portal_data.get("file_path")
            
            if self._selected_file:
                name = self._selected_file.replace("\\", "/").split("/")[-1]
                row_count = len(self._cleaned_rows) if self._cleaned_rows else 0
                self.file_label.setText(f"✓  {name}  ·  {row_count:,} rows cleaned & saved")
                self.file_label.setStyleSheet("color: #34d399; font-size: 12px;")
                self._save_db_btn.setEnabled(True)

        self._refresh_stats_ui()
        self._refresh_history_ui()
        self._update_dataset_action_buttons()

    def _refresh_stats_ui(self):
        """Update all stat widgets with current dynamic values."""
        records = self._get_records()
        total = self._get_total()
        completeness = self._get_completeness()
        status = self._get_status()
        
        # Update stat tiles
        self._records_tile_val.setText(f"{records:,} / {total:,}")
        self._completeness_tile_val.setText(f"{completeness}%")
        self._status_tile_val.setText(status)
        
        # Update progress bar
        self._completeness_bar.setValue(completeness)
        bar_color = self._get_bar_color()
        self._completeness_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {bar_color};
                border-radius: 5px;
            }}
        """)
        
        # Update status note
        self._status_note.setText(self._get_status_detail())
        
        # Update status badge
        self._status_badge.setText(status)
        accent = self.config["accent"]
        if status == "Complete":
            badge_color = "#34d399"
        elif status == "Partial":
            badge_color = accent
        else:
            badge_color = "#f59e0b"
        self._status_badge.setStyleSheet(f"""
            #portalStatusBadge {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid {badge_color};
                border-radius: 12px;
                color: {badge_color};
                font-size: 11px;
                font-weight: 600;
                padding: 5px 12px;
            }}
        """)

    def _refresh_history_ui(self):
        """Rebuild the upload history section dynamically."""
        # Clear existing widgets (except title)
        while self._history_layout.count() > 2:  # title + spacing
            item = self._history_layout.takeAt(2)
            if item.widget():
                item.widget().deleteLater()
        
        if not self._upload_history:
            empty = QLabel("No uploads yet")
            empty.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 12px; padding: 20px;")
            self._history_layout.addWidget(empty)
            return
        
        for filename, meta, level in self._upload_history:
            row = QFrame()
            row.setObjectName("portalHistoryRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 12, 0, 12)

            left = QVBoxLayout()
            left.setSpacing(2)

            name_lbl = QLabel(filename)
            name_lbl.setObjectName("portalHistoryName")

            meta_lbl = QLabel(meta)
            meta_lbl.setObjectName("portalHistoryMeta")

            left.addWidget(name_lbl)
            left.addWidget(meta_lbl)

            status_lbl = QLabel("Uploaded" if level == "success" else "Partial")
            status_lbl.setObjectName(
                "portalHistorySuccess" if level == "success" else "portalHistoryWarning"
            )

            row_layout.addLayout(left, 1)
            row_layout.addWidget(status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            self._history_layout.addWidget(row)

    def _get_loaded_dataset(self) -> tuple[list, list] | None:
        """Return (headers, rows) from memory or DataStore, or None if empty."""
        if self._cleaned_headers and self._cleaned_rows is not None:
            return (
                list(self._cleaned_headers),
                [list(r) for r in self._cleaned_rows],
            )
        portal_data = DataStore.get().get_portal(self._portal_key)
        if portal_data and portal_data.get("headers") and portal_data.get("rows") is not None:
            return (
                list(portal_data["headers"]),
                [list(r) for r in portal_data["rows"]],
            )
        return None

    def _update_dataset_action_buttons(self):
        has_data = self._get_loaded_dataset() is not None
        self._view_dataset_btn.setEnabled(has_data)
        self._edit_dataset_btn.setEnabled(has_data)

    def _sync_dataset_state(self, headers: list, rows: list):
        """Update in-memory state, DataStore, and portal UI labels."""
        self._cleaned_headers = list(headers)
        self._cleaned_rows = [list(r) for r in rows]
        DataStore.get().set_portal(
            self._portal_key,
            self._cleaned_headers,
            self._cleaned_rows,
        )
        row_count = len(self._cleaned_rows)
        if self._selected_file:
            name = self._selected_file.replace("\\", "/").split("/")[-1]
            if name.startswith("database://"):
                self.file_label.setText(f"✓  DB  ·  {row_count:,} rows")
            else:
                self.file_label.setText(
                    f"✓  {name}  ·  {row_count:,} rows"
                )
        else:
            self.file_label.setText(f"✓  {row_count:,} rows loaded")
        self.file_label.setStyleSheet("color: #34d399; font-size: 12px;")
        self._save_db_btn.setEnabled(True)
        self._refresh_stats_ui()
        self._update_dataset_action_buttons()

    def _push_dataset_to_database(self, headers: list, rows: list) -> bool:
        """Upsert headers/rows to the portal PostgreSQL table. Returns success."""
        self._pipeline_log.append(
            f"💾 Saving {self._portal_key.upper()} to PostgreSQL..."
        )
        try:
            with self._get_db_service() as db:
                result = db.push_data(self._portal_key, headers, rows)

                if result["success"]:
                    self._pipeline_log.append(
                        f"✅ Saved to {result['table']}: "
                        f"{result['inserted']}/{result['total']} rows"
                    )
                    if result.get("errors"):
                        self._pipeline_log.append(
                            f"⚠️ {len(result['errors'])} row errors"
                        )
                    stats = db.get_stats(self._portal_key)
                    self._pipeline_log.append(
                        f"📊 Total in {stats['table']}: {stats['total_records']}"
                    )
                    return True

                self._pipeline_log.append(f"❌ {result['error']}")
                QMessageBox.critical(self, "Database Error", result["error"])
                return False
        except Exception as e:
            self._pipeline_log.append(f"❌ {e}")
            QMessageBox.critical(self, "Database Error", str(e))
            return False

    def _on_view_dataset(self):
        data = self._get_loaded_dataset()
        if not data:
            QMessageBox.information(
                self,
                "No Dataset",
                "Upload, pull from DB, or clean a file before viewing.",
            )
            return

        headers, rows = data
        PortalDatasetDialog(
            portal_title=self.config["title"],
            headers=headers,
            rows=rows,
            accent=self.config["accent"],
            readonly=True,
            parent=self,
        ).exec()

    def _on_edit_dataset(self):
        data = self._get_loaded_dataset()
        if not data:
            QMessageBox.information(
                self,
                "No Dataset",
                "Upload, pull from DB, or clean a file before editing.",
            )
            return

        headers, rows = data
        clean = CleanDataWindow(headers, rows, self.config, parent=self)
        if not clean.exec():
            return

        new_headers = clean.cleaned_headers
        new_rows = clean.cleaned_rows

        reply = QMessageBox.question(
            self,
            "Save Changes",
            (
                f"Apply {len(new_rows):,} cleaned rows to this portal and "
                "update PostgreSQL?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        processed_headers, processed_rows = self._preprocess_edited_dataset(
            new_headers, new_rows
        )
        self._sync_dataset_state(processed_headers, processed_rows)
        self._push_dataset_to_database(processed_headers, processed_rows)
        QMessageBox.information(
            self,
            "Dataset Updated",
            f"Saved {len(processed_rows):,} rows to memory and the database.",
        )

    def _preprocess_edited_dataset(
        self, headers: list, rows: list
    ) -> tuple[list, list]:
        """
        Route edited table values through preprocessing_service before save.

        This keeps edit updates aligned with the same cleaning logic used
        elsewhere in the app.
        """
        step_profiles = {
            "mis": {
                "mode_cols": ["PROGRAM", "COLLEGE", "SECCODE", "SEX_CODE"],
                "mean_cols": ["YEAR", "FINAL_AVG_GRD"],
                "required_cols": ["ID_NO"],
            },
            "sao": {
                "mode_cols": [
                    "SCHOLARSHIP_APPLICANT",
                    "SCHOLARSHIP_TYPE",
                    "GENDER",
                    "MUNICIPALITY",
                    "PROGRAM",
                ],
                "required_cols": ["STUDENT_ID"],
            },
            "guidance": {
                "mode_cols": [
                    "systemcode",
                    "family_income_bracket",
                    "parent_highest_education",
                    "home_municipality",
                    "program_code",
                ],
                "mean_cols": ["entrance_exam_score", "applicant_age"],
                "required_cols": ["student_id"],
            },
            "registrar": {
                "mode_cols": [
                    "gender",
                    "shs_strand",
                    "hs_type",
                    "graduation_honors",
                    "municipality",
                ],
                "mean_cols": ["hs_gpa", "year_graduated", "year_enrolled"],
                "required_cols": ["student_id"],
            },
        }

        def _present(cols: list) -> list:
            return [col for col in cols if col in headers]

        profile = step_profiles.get(self._portal_key, {})
        normalized_rows = [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in rows
        ]
        steps = [
            {"op": "remove_empty_rows", "params": {}},
            {"op": "remove_duplicates", "params": {}},
        ]

        for col in _present(profile.get("required_cols", [])):
            steps.append(
                {"op": "fill_missing_value", "params": {"col": col, "value": "UNKNOWN"}}
            )

        for col in _present(profile.get("mean_cols", [])):
            steps.append({"op": "fill_missing_mean", "params": {"col": col}})

        for col in _present(profile.get("mode_cols", [])):
            steps.append({"op": "fill_missing_mode", "params": {"col": col}})

        clean_headers, clean_rows = CleaningEngine.apply(
            list(headers), normalized_rows, steps
        )
        issues = compute_issues(clean_headers, clean_rows)
        self._pipeline_log.append(
            f"🧹 Preprocessed edits: "
            f"{issues['missing']} missing cells, "
            f"{issues['duplicates']} duplicates, "
            f"{issues['empty_rows']} empty rows."
        )
        self._pipeline_log.append(
            "⚙️ Applied steps: "
            + ", ".join(
                [
                    "remove_empty_rows",
                    "remove_duplicates",
                    f"fill_required={len(_present(profile.get('required_cols', [])))}",
                    f"fill_mean={len(_present(profile.get('mean_cols', [])))}",
                    f"fill_mode={len(_present(profile.get('mode_cols', [])))}",
                ]
            )
        )
        return clean_headers, clean_rows

    def _get_db_service(self):
        return DatabaseService(
            host="localhost",
            port=5432,
            database="testDB",
            user="postgres",
            password="admin123",
        )

    def _save_to_database(self):
        data = self._get_loaded_dataset()
        if not data:
            QMessageBox.warning(self, "No Data", "Please clean and save a dataset first.")
            return

        headers, rows = data
        self._save_db_btn.setEnabled(False)
        try:
            if self._push_dataset_to_database(headers, rows):
                with self._get_db_service() as db:
                    stats = db.get_stats(self._portal_key)
                QMessageBox.information(
                    self,
                    "Database Saved",
                    f"Saved {len(rows):,} rows.\n"
                    f"Total in table: {stats.get('total_records', '—')}",
                )
        finally:
            self._save_db_btn.setEnabled(True)

    def _pull_from_database(self):
        self._pull_db_btn.setEnabled(False)
        self._pipeline_log.append(f"📥 Fetching {self._portal_key.upper()} from DB...")

        try:
            with self._get_db_service() as db:
                stats = db.get_stats(self._portal_key)
                if stats["total_records"] == 0:
                    QMessageBox.information(self, "Empty", f"{stats['table']} is empty.")
                    return

                reply = QMessageBox.question(
                    self,
                    "Load Data",
                    f"Load {stats['total_records']:,} records from {stats['table']}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

                result = db.pull_data(self._portal_key)
                if result["success"] and result["rows"]:
                    self._cleaned_headers = result["headers"]
                    self._cleaned_rows = result["rows"]
                    self._selected_file = f"database://{result['table']}"

                    DataStore.get().set_portal(
                        self._portal_key,
                        self._cleaned_headers,
                        self._cleaned_rows,
                    )

                    row_count = len(self._cleaned_rows)
                    self.file_label.setText(f"✓  DB  ·  {row_count:,} rows")
                    self.file_label.setStyleSheet("color: #34d399; font-size: 12px;")
                    self._save_db_btn.setEnabled(True)
                    self._refresh_stats_ui()
                    self._update_dataset_action_buttons()

                    self._pipeline_log.append(f"✅ Loaded {row_count:,} rows")
                else:
                    self._pipeline_log.append("⚠️ No data returned")

        except Exception as e:
            self._pipeline_log.append(f"❌ {e}")
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self._pull_db_btn.setEnabled(True)

    def _run_star_schema_etl(self):
        """Run ETL after all portals are loaded."""
        self._pipeline_log.append("🔄 Running Star Schema ETL...")

        try:
            with self._get_db_service() as db:
                result = db.run_star_schema_etl("2024-2025", "1st")

                if result["success"]:
                    msg = (
                        f"Merge Log Recorded!\n\n"
                        f"run_id: {result.get('run_id', '—')}\n"
                        f"academic_year: {result.get('academic_year', '—')}\n"
                        f"semester: {result.get('semester', '—')}\n"
                        f"total_students: {result.get('total_students', 0)}\n"
                        f"coverage_pct: {result.get('coverage_pct', 0)}%\n"
                        f"fact_student_risk updates: {result.get('facts_inserted', 0)}"
                    )
                    self._pipeline_log.append(msg.replace("\n", " | "))
                    QMessageBox.information(self, "ETL Complete", msg)

                    # Show star schema stats
                    stats = db.get_star_schema_stats()
                    for table, count in stats.items():
                        self._pipeline_log.append(f"  {table}: {count}")
                else:
                    self._pipeline_log.append(f"❌ ETL failed: {result['error']}")
                    QMessageBox.critical(self, "ETL Error", result["error"])

        except Exception as e:
            self._pipeline_log.append(f"❌ ETL error: {e}")
            QMessageBox.critical(self, "Error", str(e))

    # ── EVENT HANDLERS ──────────────────────────────────────────

    def _on_clear_dataset(self):
        reply = QMessageBox.question(
            self,
            "Clear Dataset",
            "Are you sure you want to remove the uploaded dataset?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        store = DataStore.get()
        store.clear_portal(self._portal_key)
        store._notify(self._portal_key)

        self._selected_file = None
        self._cleaned_headers = None
        self._cleaned_rows = None
        self._last_updated = None

        self._upload_lbl.setText("Drag & drop CSV file here")
        self._hint_lbl.setText(f"or browse for {self.config['file_hint']}")
        self.file_label.setText("No file selected")
        self.file_label.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 12px;")

        self._refresh_stats_ui()
        self._save_db_btn.setEnabled(False)
        self._pull_db_btn.setEnabled(True)
        self._update_dataset_action_buttons()

        print(f"[{self._portal_key}] Dataset cleared")

    def _create_stat_tile(self, value, label, value_ref=None):
        """Create a stat tile. value_ref is a QLabel reference to update later."""
        tile = QFrame()
        tile.setObjectName("portalCard")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        val = QLabel(str(value))
        val.setObjectName("portalStatValue")
        if value_ref is not None:
            # Allow external reference for dynamic updates
            pass

        lbl = QLabel(label)
        lbl.setObjectName("portalStatLabel")

        layout.addWidget(val)
        layout.addWidget(lbl)
        return tile, val

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select data file",
            "",
            "Data Files (*.csv *.xlsx *.xls);;All Files (*)"
        )
        if not path:
            return
    
        preview = DatasetPreviewDialog(path, self.config, parent=self)
        if not preview.exec():
            return
    
        self._open_clean_window(preview._headers, preview._rows, path)

    def _open_clean_window(self, headers, rows, file_path):
        clean = CleanDataWindow(headers, rows, self.config, parent=self)
    
        if not clean.exec():
            return
    
        self._cleaned_headers = clean.cleaned_headers
        self._cleaned_rows = clean.cleaned_rows
        self._selected_file = file_path
    
        DataStore.get().set_portal(
            self._portal_key,
            self._cleaned_headers,
            self._cleaned_rows,
        )
    
        name = file_path.replace("\\", "/").split("/")[-1]
        row_count = len(self._cleaned_rows)
        self.file_label.setText(f"✓  {name}  ·  {row_count:,} rows cleaned & saved")
        self.file_label.setStyleSheet("color: #34d399; font-size: 12px;")
    
        self._add_history_entry(name, row_count, "success")

        self._save_db_btn.setEnabled(True)
        self._pull_db_btn.setEnabled(True)
        self._update_dataset_action_buttons()

        self._refresh_stats_ui()
    
        store = DataStore.get()
        readiness = store.get_readiness()
        ready = store.ready_count()
        print(f"[DataStore] {self._portal_key} saved. "
            f"({ready}/4 portals ready)  {readiness}")


    def _apply_page_styles(self):
        accent = self.config["accent"]
        self.setStyleSheet(f"""
            #portalModelCard {{
                background-color: rgba(0, 0, 0, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }}
            #portalModelStatus {{
                color: #2ecc71;
                font-weight: bold;
                font-size: 12px;
            }}
            #portalSemesterPill {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.85);
                font-size: 12px;
                padding: 8px 14px;
            }}
            #portalCard {{
                background-color: rgba(0, 0, 0, 0.22);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }}
            #portalCardTitle {{
                color: rgba(255, 255, 255, 0.4);
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            #portalOfficeName {{
                font-size: 16px;
                font-weight: bold;
                color: white;
            }}
            #portalOfficeDesc {{
                color: rgba(255, 255, 255, 0.45);
                font-size: 12px;
            }}
            #portalStatusBadge {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid {accent};
                border-radius: 12px;
                color: {accent};
                font-size: 11px;
                font-weight: 600;
                padding: 5px 12px;
            }}
            #portalUploadZone {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 2px dashed rgba(255, 255, 255, 0.15);
                border-radius: 12px;
            }}
            #portalUploadIcon {{
                font-size: 32px;
            }}
            #portalUploadTitle {{
                font-size: 14px;
                font-weight: bold;
                color: white;
            }}
            #portalUploadHint {{
                color: rgba(255, 255, 255, 0.4);
                font-size: 12px;
            }}
            #portalBrowseBtn {{
                background-color: {accent};
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 10px 20px;
            }}
            #portalBrowseBtn:hover {{
                background-color: rgba(79, 140, 255, 0.85);
            }}
            #portalClearBtn {{
                background-color: rgba(255,91,91,0.08);
                border: 1px solid rgba(255,91,91,0.25);
                border-radius: 8px;
                color: #ff5b5b;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }}
            #portalClearBtn:hover {{
                background-color: rgba(255,91,91,0.18);
            }}
            #portalViewBtn {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.85);
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }}
            #portalViewBtn:hover:enabled {{
                background-color: rgba(255, 255, 255, 0.1);
            }}
            #portalViewBtn:disabled {{
                color: rgba(255, 255, 255, 0.25);
                border-color: rgba(255, 255, 255, 0.06);
            }}
            #portalEditBtn {{
                background-color: rgba(79, 140, 255, 0.12);
                border: 1px solid rgba(79, 140, 255, 0.35);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }}
            #portalEditBtn:hover:enabled {{
                background-color: rgba(79, 140, 255, 0.22);
            }}
            #portalEditBtn:disabled {{
                color: rgba(255, 255, 255, 0.25);
                border-color: rgba(255, 255, 255, 0.06);
                background-color: rgba(255, 255, 255, 0.03);
            }}
            #portalStatValue {{
                font-size: 22px;
                font-weight: bold;
                color: white;
            }}
            #portalStatLabel {{
                color: rgba(255, 255, 255, 0.4);
                font-size: 11px;
            }}
            #portalFieldPill {{
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 14px;
                color: rgba(255, 255, 255, 0.7);
                font-size: 11px;
                padding: 6px 12px;
            }}
            #portalHistoryRow {{
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }}
            #portalHistoryName {{
                color: white;
                font-size: 13px;
            }}
            #portalHistoryMeta {{
                color: rgba(255, 255, 255, 0.4);
                font-size: 11px;
            }}
            #portalHistorySuccess {{
                color: #34d399;
                font-size: 11px;
            }}
            #portalHistoryWarning {{
                color: #f5b335;
                font-size: 11px;
            }}
            QMessageBox {{
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }}
            QMessageBox QLabel {{
                color: #e8eaf0;
                font-size: 13px;
                background: transparent;
            }}
            QMessageBox QPushButton {{
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                font-weight: 600;
                padding: 8px 20px;
                min-width: 70px;
            }}
            QMessageBox QPushButton:hover {{
                background-color: rgba(255,255,255,0.12);
            }}
            QMessageBox QPushButton[default="true"] {{
                background-color: #ff5b5b;
                border: none;
                color: white;
            }}
            QMessageBox QPushButton[default="true"]:hover {{
                background-color: rgba(255,91,91,0.85);
            }}
        """)



    # ── UI SETUP ────────────────────────────────────────────────

    def setup_ui(self):
        cfg = self.config

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # =====================================
        # FIXED HEADER
        # =====================================

        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(0)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel(cfg["title"])
        header.setObjectName("header")

        subheader = QLabel("Academic Year 2024–2025")
        subheader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subheader)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        model_card = QFrame()
        model_card.setObjectName("portalModelCard")

        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        model_status = QLabel("● Model Active")
        model_status.setObjectName("portalModelStatus")

        opacity_effect = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(opacity_effect)

        status_animation = QPropertyAnimation(opacity_effect, b"opacity")
        status_animation.setDuration(1200)
        status_animation.setStartValue(1.0)
        status_animation.setKeyValueAt(0.5, 0.3)
        status_animation.setEndValue(1.0)
        status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        status_animation.setLoopCount(-1)
        status_animation.start()

        semester_pill = QLabel("1st Semester 2024–25  ▾")
        semester_pill.setObjectName("portalSemesterPill")

        self._save_db_btn = QPushButton("💾 Save to DB")
        self._save_db_btn.setObjectName("runButton")
        self._save_db_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_db_btn.clicked.connect(self._save_to_database)
        self._save_db_btn.setFixedWidth(130)
        self._save_db_btn.setEnabled(False)

        self._pull_db_btn = QPushButton("📥 Pull from DB")
        self._pull_db_btn.setObjectName("runButton")
        self._pull_db_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pull_db_btn.clicked.connect(self._pull_from_database)
        self._pull_db_btn.setFixedWidth(130)

        self._etl_btn = QPushButton("🔄 Run ETL")
        self._etl_btn.setObjectName("runButton")
        self._etl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._etl_btn.clicked.connect(self._run_star_schema_etl)
        self._etl_btn.setFixedWidth(130)

        model_layout.addWidget(model_status)
        model_layout.addWidget(semester_pill)
        model_layout.addWidget(self._save_db_btn)
        model_layout.addWidget(self._pull_db_btn)
        model_layout.addWidget(self._etl_btn)

        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)




        # =====================================
        # OFFICE INFO
        # =====================================

        info_card = QFrame()
        info_card.setObjectName("portalCard")
        info_layout = QHBoxLayout(info_card)
        info_layout.setContentsMargins(24, 20, 24, 20)
        info_layout.setSpacing(16)

        info_left = QVBoxLayout()
        info_left.setSpacing(6)

        office = QLabel(cfg["office"])
        office.setObjectName("portalOfficeName")

        office_sub = QLabel(cfg["subtitle"])
        office_sub.setObjectName("portalOfficeDesc")

        office_desc = QLabel(cfg["description"])
        office_desc.setObjectName("portalOfficeDesc")
        office_desc.setWordWrap(True)

        info_left.addWidget(office)
        info_left.addWidget(office_sub)
        info_left.addWidget(office_desc)

        self._status_badge = QLabel("Pending")  # Will be updated dynamically
        self._status_badge.setObjectName("portalStatusBadge")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_layout.addLayout(info_left, 1)
        info_layout.addWidget(self._status_badge, 0, Qt.AlignmentFlag.AlignTop)

        self.main_layout.addWidget(info_card)

        # =====================================
        # STATS ROW (DYNAMIC)
        # =====================================

        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)

        # Records tile - store reference to value label for updates
        records_tile, self._records_tile_val = self._create_stat_tile("0 / 0", "Records Uploaded")
        stats_row.addWidget(records_tile, 1)
        
        # Completeness tile
        completeness_tile, self._completeness_tile_val = self._create_stat_tile("0%", "Completeness")
        stats_row.addWidget(completeness_tile, 1)
        
        # Status tile
        status_tile, self._status_tile_val = self._create_stat_tile("Pending", "Sync Status")
        stats_row.addWidget(status_tile, 1)

        self.main_layout.addLayout(stats_row)

        # Progress bar
        self._completeness_bar = QProgressBar()
        self._completeness_bar.setValue(0)
        self._completeness_bar.setTextVisible(False)
        self._completeness_bar.setFixedHeight(10)
        self._completeness_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #f59e0b;
                border-radius: 5px;
            }
        """)

        self._status_note = QLabel("Upload pending")
        self._status_note.setStyleSheet("color: rgba(255,255,255,0.45); font-size: 12px;")

        self.main_layout.addWidget(self._completeness_bar)
        self.main_layout.addWidget(self._status_note)

        # =====================================
        # UPLOAD + FIELDS ROW
        # =====================================

        content_row = QHBoxLayout()
        content_row.setSpacing(20)

        # Upload zone
        upload_card = QFrame()
        upload_card.setObjectName("portalCard")
        upload_layout = QVBoxLayout(upload_card)
        upload_layout.setContentsMargins(24, 20, 24, 20)
        upload_layout.setSpacing(16)

        upload_title = QLabel("UPLOAD DATA")
        upload_title.setObjectName("portalCardTitle")
        upload_layout.addWidget(upload_title)

        upload_zone = QFrame()
        upload_zone.setObjectName("portalUploadZone")
        zone_layout = QVBoxLayout(upload_zone)
        zone_layout.setContentsMargins(32, 28, 32, 28)
        zone_layout.setSpacing(10)
        zone_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("📂")
        icon.setObjectName("portalUploadIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._upload_lbl = QLabel("Drag & drop CSV file here")
        self._upload_lbl.setObjectName("portalUploadTitle")
        self._upload_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._hint_lbl = QLabel(f"or browse for {cfg['file_hint']}")
        self._hint_lbl.setObjectName("portalUploadHint")
        self._hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.file_label = QLabel("No file selected")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 12px;")

        zone_layout.addWidget(icon)
        zone_layout.addWidget(self._upload_lbl)
        zone_layout.addWidget(self._hint_lbl)
        zone_layout.addWidget(self.file_label)

        upload_layout.addWidget(upload_zone)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        browse_btn = QPushButton("Browse Files")
        browse_btn.setObjectName("portalBrowseBtn")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_file)

        self._view_dataset_btn = QPushButton("👁  View Dataset")
        self._view_dataset_btn.setObjectName("portalViewBtn")
        self._view_dataset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_dataset_btn.clicked.connect(self._on_view_dataset)
        self._view_dataset_btn.setEnabled(False)

        self._edit_dataset_btn = QPushButton("✎  Edit Dataset")
        self._edit_dataset_btn.setObjectName("portalEditBtn")
        self._edit_dataset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_dataset_btn.clicked.connect(self._on_edit_dataset)
        self._edit_dataset_btn.setEnabled(False)

        clear_btn = QPushButton("🗑  Clear Dataset")
        clear_btn.setObjectName("portalClearBtn")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_dataset)

        btn_row.addWidget(browse_btn)
        btn_row.addWidget(self._view_dataset_btn)
        btn_row.addWidget(self._edit_dataset_btn)
        btn_row.addWidget(clear_btn)
        upload_layout.addLayout(btn_row)

        content_row.addWidget(upload_card, 1)

        # Expected fields
        fields_card = QFrame()
        fields_card.setObjectName("portalCard")
        fields_layout = QVBoxLayout(fields_card)
        fields_layout.setContentsMargins(24, 20, 24, 20)
        fields_layout.setSpacing(14)

        fields_title = QLabel("EXPECTED CSV FIELDS")
        fields_title.setObjectName("portalCardTitle")
        fields_layout.addWidget(fields_title)

        fields_hint = QLabel("Ensure your upload includes the following column headers:")
        fields_hint.setObjectName("portalOfficeDesc")
        fields_hint.setWordWrap(True)
        fields_layout.addWidget(fields_hint)

        fields = cfg["fields"]
        if len(fields) > 6:
            fields_grid_host = QWidget()
            fields_grid = QGridLayout(fields_grid_host)
            fields_grid.setContentsMargins(0, 0, 0, 0)
            fields_grid.setHorizontalSpacing(8)
            fields_grid.setVerticalSpacing(8)
            cols = 2
            for i, field in enumerate(fields):
                pill = QLabel(field)
                pill.setObjectName("portalFieldPill")
                fields_grid.addWidget(pill, i // cols, i % cols)
            fields_layout.addWidget(fields_grid_host)
        else:
            for field in fields:
                pill = QLabel(field)
                pill.setObjectName("portalFieldPill")
                fields_layout.addWidget(pill)

        fields_layout.addStretch()
        content_row.addWidget(fields_card, 1)

        self.main_layout.addLayout(content_row)

        # =====================================
        # ACTIVITY LOG (DB / upload actions)
        # =====================================

        pipeline_card = QFrame()
        pipeline_card.setObjectName("portalCard")
        pipeline_layout = QVBoxLayout(pipeline_card)
        pipeline_layout.setContentsMargins(24, 20, 24, 20)
        pipeline_layout.setSpacing(12)

        pipeline_title = QLabel("ACTIVITY LOG")
        pipeline_title.setObjectName("portalCardTitle")
        pipeline_layout.addWidget(pipeline_title)

        self._pipeline_log = QTextEdit()
        self._pipeline_log.setReadOnly(True)
        self._pipeline_log.setPlaceholderText("Upload and database actions will appear here...")
        self._pipeline_log.setMaximumHeight(150)
        self._pipeline_log.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0,0,0,0.2);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                color: #b8bcc8;
                font-family: 'Consolas', monospace;
                font-size: 12px;
                padding: 10px;
            }
        """)
        pipeline_layout.addWidget(self._pipeline_log)

        self.main_layout.addWidget(pipeline_card)

        # =====================================
        # UPLOAD HISTORY (DYNAMIC)
        # =====================================

        history_card = QFrame()
        history_card.setObjectName("portalCard")
        self._history_layout = QVBoxLayout(history_card)
        self._history_layout.setContentsMargins(24, 20, 24, 12)
        self._history_layout.setSpacing(0)

        history_title = QLabel("UPLOAD HISTORY")
        history_title.setObjectName("portalCardTitle")
        self._history_layout.addWidget(history_title)
        self._history_layout.addSpacing(12)

        # Empty state initially
        empty_history = QLabel("No uploads yet")
        empty_history.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 12px; padding: 20px;")
        self._history_layout.addWidget(empty_history)

        self.main_layout.addWidget(history_card)
        self.main_layout.addStretch()
        self.setLayout(self.main_layout)


class MisPortalPage(PortalUploadPage):
    def __init__(self):
        super().__init__("mis")


class SaoPortalPage(PortalUploadPage):
    def __init__(self):
        super().__init__("sao")


class GuidancePortalPage(PortalUploadPage):
    def __init__(self):
        super().__init__("guidance")


class RegistrarPortalPage(PortalUploadPage):
    def __init__(self):
        super().__init__("registrar")