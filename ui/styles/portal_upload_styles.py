"""
ui/styles/portal_upload_styles.py
====================================
QSS stylesheet builder for PortalUploadPage. The stylesheet depends on the
portal's accent color, so it's exposed as a function rather than a plain
constant.

Extracted verbatim from ui/pages/portal_upload_page.py — no visual changes.
"""
from __future__ import annotations


def build_portal_upload_stylesheet(accent: str) -> str:
    return f"""
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
        """