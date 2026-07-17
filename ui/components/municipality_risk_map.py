"""
ui/components/municipality_risk_map.py
========================================
Static bubble map showing high-risk student concentration by home municipality.
Rendered with pure Python + QPainter — no WebEngine, no external deps.

Distance features (all three options):
  Option 1 — Hover tooltip includes geodesic distance from CTU-Daanbantayan.
  Option 2 — Dashed lines from campus pin to each bubble; midpoint km label.
  Option 3 — Distance is attached to each bubble dict so the analytics page
              ranked list can display a Distance column (see analytics_page.py).

Bubbles are sized by high-risk count and coloured by risk density.
"""
from __future__ import annotations

import math
from collections import defaultdict

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QRectF, QPointF, QTimer,
    QVariantAnimation, QEasingCurve,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout, QWidget, QToolTip,
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont,
    QFontMetrics, QPainterPath, QCursor,
)

from services.data_store import DataStore


# ── Cebu municipality coordinates (lat, lon) ──────────────────────────────────

_MUNICIPALITY_COORDS: dict[str, tuple[float, float]] = {
    # Northern Cebu
    "Daanbantayan": (11.2522, 124.0054),
    "Medellin":     (11.1285, 124.0520),
    "Bogo":         (11.0510, 124.0055),
    "Tabogon":      (10.9333, 124.0333),
    "Catmon":       (10.9000, 123.9833),
    "Sogod":        (10.7500, 123.9833),
    "Borbon":       (10.8333, 124.0167),
    "Tabuelan":     (10.8167, 123.9500),
    "San Remigio":  (11.0000, 123.9333),
    "Madridejos":   (11.2667, 123.7333),
    "Bantayan":     (11.1667, 123.7167),
    "Santa Fe":     (11.1667, 123.8000),
    "Hagnaya":      (11.0167, 123.9000),
    # Central Cebu
    "Cebu City":    (10.3157, 123.8854),
    "Mandaue":      (10.3236, 123.9223),
    "Lapu-Lapu":    (10.3103, 123.9494),
    "Talisay":      (10.2446, 123.8485),
    "Consolacion":  (10.3742, 123.9600),
    "Liloan":       (10.3978, 123.9994),
    "Compostela":   (10.4569, 124.0050),
    "Danao":        (10.5228, 124.0266),
    "Carmen":       (10.5894, 124.0147),
    "Tuburan":      (10.7333, 123.8333),
    "Asturias":     (10.6667, 123.7167),
    "Balamban":     (10.5000, 123.7167),
    "Toledo":       (10.3775, 123.6383),
    "Naga":         (10.2119, 123.7581),
    "Minglanilla":  (10.2428, 123.7942),
    # Southern Cebu
    "Carcar":       (10.1064, 123.6408),
    "Sibonga":      (10.0333, 123.5833),
    "Argao":        (9.8833,  123.6083),
    "Dalaguete":    (9.7667,  123.5333),
    "Oslob":        (9.5167,  123.4333),
    "Badian":       (9.8667,  123.3833),
    "Moalboal":     (9.9333,  123.4000),
    # Camotes Islands
    "Poro":         (10.6333, 124.0333),
    "Tudela":       (10.6167, 124.1333),
    "Pilar":        (10.6500, 124.3333),
    "San Francisco":(10.6333, 124.3667),
}

_CAMPUS_COORD = (11.2522, 124.0054)   # CTU-Daanbantayan


# ── Known name variants → canonical municipality name ──────────────────────
# Source data (student records, MIS exports) often contains inconsistent
# spellings for the same municipality. This map normalizes them BEFORE
# coordinate lookup and BEFORE aggregation, so "Bogo" and "Bogo City" are
# treated as one municipality everywhere in the app (map, ranked list,
# distance calculations).
_MUNICIPALITY_ALIASES: dict[str, str] = {
    "bogo city":        "Bogo",
    "bogo, cebu":       "Bogo",
    "city of bogo":     "Bogo",
    "lapu-lapu city":   "Lapu-Lapu",
    "city of lapu-lapu":"Lapu-Lapu",
    "cebu city, cebu":  "Cebu City",
    "city of cebu":     "Cebu City",
    "danao city":       "Danao",
    "city of danao":    "Danao",
    "carcar city":      "Carcar",
    "city of carcar":   "Carcar",
    "toledo city":      "Toledo",
    "city of toledo":   "Toledo",
    "naga city":        "Naga",
    "city of naga":     "Naga",
    "talisay city":     "Talisay",
    "city of talisay":  "Talisay",
    "mandaue city":     "Mandaue",
    "city of mandaue":  "Mandaue",
    "sta. fe":          "Santa Fe",
    "sta fe":           "Santa Fe",
    "san francisco, camotes": "San Francisco",
}


def normalize_municipality(name: str) -> str:
    """
    Return the canonical municipality name for a given raw string.
    Looks up _MUNICIPALITY_ALIASES first (case-insensitive, trimmed).
    If no alias matches, falls back to a title-cased version of the
    trimmed name so pure case variants (e.g. "Bogo" vs "BOGO" vs "bogo")
    collapse into the same bucket even when they're not in the alias
    table. Use this BEFORE grouping/aggregating municipality data
    anywhere in the app so variant spellings AND variant casing both
    collapse into one bucket.
    """
    if not name:
        return name
    cleaned = name.strip()
    key = cleaned.lower()
    if key in _MUNICIPALITY_ALIASES:
        return _MUNICIPALITY_ALIASES[key]
    # No known alias — normalize casing so "Bogo"/"BOGO"/"bogo" merge.
    # Title-case handles multi-word names too (e.g. "san francisco" → "San Francisco"),
    # but preserve hyphenated names like "Lapu-Lapu" correctly.
    return "-".join(part.title() for part in cleaned.split("-"))


# ── Haversine distance ─────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float,
                  lat2: float, lon2: float) -> float:
    """Return geodesic distance in km between two (lat, lon) points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


def distance_from_campus_km(municipality: str) -> float | None:
    """
    Return the straight-line distance (km) from CTU-Daanbantayan to
    *municipality*. Returns None if the municipality is not in the
    coordinate table.  Exported so analytics_page can call it directly.
    """
    c = _coords(municipality)
    if c is None:
        return None
    return _haversine_km(_CAMPUS_COORD[0], _CAMPUS_COORD[1], c[0], c[1])


# ── Coordinate lookup ──────────────────────────────────────────────────────────

def _coords(municipality: str) -> tuple[float, float] | None:
    if not municipality or municipality in ("—", "", "None", "Unknown"):
        return None
    if municipality in _MUNICIPALITY_COORDS:
        return _MUNICIPALITY_COORDS[municipality]
    lc = municipality.strip().lower()
    for name, c in _MUNICIPALITY_COORDS.items():
        if name.lower() == lc:
            return c
    for name, c in _MUNICIPALITY_COORDS.items():
        if name.lower() in lc or lc in name.lower():
            return c
    return None


# ── DB worker ─────────────────────────────────────────────────────────────────

class _MapDataWorker(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            COALESCE(NULLIF(TRIM(ds.home_municipality), ''), 'Unknown')
                                                    AS municipality,
            COUNT(*)                                AS total,
            COUNT(*) FILTER (
                WHERE rl.risk_label ILIKE '%%high%%'
            )                                       AS high_risk,
            COUNT(*) FILTER (
                WHERE rl.risk_label ILIKE '%%moderate%%'
                   OR rl.risk_label ILIKE '%%medium%%'
            )                                       AS moderate_risk,
            COUNT(*) FILTER (
                WHERE rl.risk_label NOT ILIKE '%%high%%'
                  AND rl.risk_label NOT ILIKE '%%moderate%%'
                  AND rl.risk_label NOT ILIKE '%%medium%%'
            )                                       AS low_risk
        FROM  public.fact_student_academic_risk fsr
        JOIN  public.dim_academic_term t
              ON t.term_key       = fsr.term_key
        JOIN  public.dim_student ds
              ON ds.student_key   = fsr.student_key
        LEFT JOIN public.dim_risk_level rl
              ON rl.risk_level_id = fsr.risk_level_id
        WHERE t.academic_year = %s
          AND t.semester      = %s
        GROUP BY municipality
        ORDER BY high_risk DESC, total DESC
    """

    def __init__(self, academic_year: str, semester: int):
        super().__init__()
        self._ay  = academic_year
        self._sem = semester

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self.finished.emit(rows)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Canvas widget ─────────────────────────────────────────────────────────────

class _MapCanvas(QWidget):
    """
    Custom QPainter canvas that draws municipality bubbles directly.
    No WebEngine, no SVG file — pure Qt painting.

    Each bubble dict now carries:
        dist_km  — geodesic distance from CTU campus (float | None)
    """

    # Colour ramp for distance lines: close → far
    _LINE_PALETTE = [
        QColor("#4f8cff"),   # ≤ 20 km  — blue (local)
        QColor("#34d399"),   # ≤ 60 km  — green (moderate)
        QColor("#f5b335"),   # ≤ 120 km — amber (distant)
        QColor("#ff5b5b"),   # > 120 km — red (very far)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data:       list[dict] = []
        self._bubbles:    list[dict] = []    # pre-computed screen positions
        self._show_lines: bool       = True  # Option 2 toggle
        self._reveal:     float      = 1.0   # 0→1 grow-in progress for bubbles
        self._reveal_anim: QVariantAnimation | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(360)

    def set_data(self, data: list[dict]):
        self._data = data
        self._reproject()
        self._start_reveal_animation()
        self.update()

    def _start_reveal_animation(self):
        """Grow the bubbles in from nothing with a soft overshoot, each
        time fresh data loads onto the map."""
        if self._reveal_anim is not None:
            self._reveal_anim.stop()

        self._reveal = 0.0
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(550)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)

        def _step(value):
            # OutBack can overshoot past 1.0; clamp only the alpha use,
            # let radius keep the slight bounce for a springy feel.
            self._reveal = value
            self.update()

        anim.valueChanged.connect(_step)
        self._reveal_anim = anim
        anim.start()

    def clear(self):
        if self._reveal_anim is not None:
            self._reveal_anim.stop()
        self._data    = []
        self._bubbles = []
        self._reveal  = 1.0
        self.update()

    def set_show_lines(self, show: bool):
        """Toggle Option 2 (distance lines) at runtime."""
        self._show_lines = show
        self.update()

    # ── Projection ────────────────────────────────────────────────────

    def _reproject(self):
        """Convert lat/lon to pixel positions for the current widget size."""
        self._bubbles = []
        mapped = []
        for row in self._data:
            c = _coords(row["municipality"])
            if c:
                mapped.append({**row, "lat": c[0], "lon": c[1]})

        if not mapped:
            return

        lats = [r["lat"] for r in mapped]
        lons = [r["lon"] for r in mapped]
        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)

        # Keep campus always in view
        lat_min = min(lat_min, _CAMPUS_COORD[0])
        lat_max = max(lat_max, _CAMPUS_COORD[0])
        lon_min = min(lon_min, _CAMPUS_COORD[1])
        lon_max = max(lon_max, _CAMPUS_COORD[1])

        pad      = 0.08
        lat_span = (lat_max - lat_min) or 0.5
        lon_span = (lon_max - lon_min) or 0.5
        lat_min -= lat_span * pad
        lat_max += lat_span * pad
        lon_min -= lon_span * pad
        lon_max += lon_span * pad

        W = self.width()  or 600
        H = self.height() or 380

        def _px(lat, lon):
            x = (lon - lon_min) / (lon_max - lon_min) * W
            y = (1 - (lat - lat_min) / (lat_max - lat_min)) * H
            return x, y

        max_high = max((r["high_risk"] for r in mapped), default=1) or 1

        for row in mapped:
            high     = int(row["high_risk"])
            moderate = int(row["moderate_risk"])
            low      = int(row["low_risk"])
            total    = int(row["total"])
            muni     = row["municipality"]

            if total == 0:
                continue

            c        = _coords(muni)
            dist_km  = (_haversine_km(_CAMPUS_COORD[0], _CAMPUS_COORD[1],
                                      c[0], c[1])
                        if c else None)

            risk_pct = high / total * 100
            radius   = max(8, math.sqrt(high / max_high) * 36)
            x, y     = _px(row["lat"], row["lon"])

            if risk_pct >= 50:
                color = QColor("#ff5b5b")
            elif risk_pct >= 25:
                color = QColor("#f5b335")
            else:
                color = QColor("#34d399")

            self._bubbles.append({
                "x": x, "y": y, "r": radius,
                "color":    color,
                "muni":     muni,
                "high":     high,
                "moderate": moderate,
                "low":      low,
                "total":    total,
                "risk_pct": risk_pct,
                "dist_km":  dist_km,      # ← Option 1 & 3
            })

        # Campus pin pixel
        cx, cy = _px(_CAMPUS_COORD[0], _CAMPUS_COORD[1])
        self._campus_px = (cx, cy)

    # ── Line colour by distance ────────────────────────────────────────

    @staticmethod
    def _line_color(dist_km: float | None) -> QColor:
        if dist_km is None:
            return QColor(255, 255, 255, 50)
        if dist_km <= 20:
            return QColor("#4f8cff")
        if dist_km <= 60:
            return QColor("#34d399")
        if dist_km <= 120:
            return QColor("#f5b335")
        return QColor("#ff5b5b")

    # ── Paint ─────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self._bubbles:
            self._paint_empty()
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        p.fillRect(self.rect(), QColor("#0e1120"))

        # Grid lines (subtle)
        p.setPen(QPen(QColor(255, 255, 255, 12), 1))
        step_x = self.width()  // 6
        step_y = self.height() // 5
        for i in range(1, 6):
            p.drawLine(i * step_x, 0, i * step_x, self.height())
        for i in range(1, 5):
            p.drawLine(0, i * step_y, self.width(), i * step_y)

        # ── Option 2: Distance lines ───────────────────────────────────
        if self._show_lines and hasattr(self, "_campus_px"):
            self._paint_distance_lines(p)

        # ── Bubbles ───────────────────────────────────────────────────
        # Draw shadow ring first, then fill
        reveal = max(0.0, self._reveal)          # clamp negative overshoot only
        alpha_reveal = min(1.0, reveal)           # alpha must stay in [0,1]
        for b in self._bubbles:
            x, y, r = b["x"], b["y"], b["r"] * reveal
            col: QColor = b["color"]

            # Outer glow ring
            glow = QColor(col)
            glow.setAlpha(int(40 * alpha_reveal))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QRectF(x - r*1.6, y - r*1.6, r*3.2, r*3.2))

            # Fill
            fill = QColor(col)
            fill.setAlpha(int(160 * alpha_reveal))
            p.setBrush(QBrush(fill))
            pen_col = QColor(col)
            pen_col.setAlpha(int(255 * alpha_reveal))
            p.setPen(QPen(pen_col, 1.5))
            p.drawEllipse(QRectF(x - r, y - r, r*2, r*2))

            # Label (only for larger bubbles, once mostly grown in)
            if r >= 14 and reveal >= 0.85:
                p.setPen(QPen(QColor("#ffffff"), 1))
                f = QFont("Segoe UI", 8, QFont.Weight.Bold)
                p.setFont(f)
                lbl = b["muni"]
                fm  = QFontMetrics(f)
                tw  = fm.horizontalAdvance(lbl)
                p.drawText(int(x - tw / 2), int(y + r + 14), lbl)

                # High-risk count inside bubble
                cnt = str(b["high"])
                tw2 = fm.horizontalAdvance(cnt)
                p.setPen(QPen(QColor("#ffffff"), 1))
                p.drawText(int(x - tw2/2), int(y + 4), cnt)

        # ── Campus pin ────────────────────────────────────────────────
        if hasattr(self, "_campus_px"):
            cx, cy = self._campus_px
            p.setPen(QPen(QColor("#4f8cff"), 2))
            p.setBrush(QBrush(QColor("#4f8cff")))
            p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            p.setPen(QPen(QColor("#4f8cff"), 1))
            f2 = QFont("Segoe UI", 8, QFont.Weight.Bold)
            p.setFont(f2)
            p.drawText(int(cx + 8), int(cy + 4), "CTU-Daanbantayan")

        # ── Legend ────────────────────────────────────────────────────
        self._paint_legend(p)
        p.end()

    # ── Option 2: dashed lines + midpoint km label ────────────────────

    def _paint_distance_lines(self, p: QPainter):
        cx, cy = self._campus_px

        # Sort ascending by dist so shorter (darker) lines paint on top
        sorted_bubbles = sorted(
            self._bubbles,
            key=lambda b: b["dist_km"] if b["dist_km"] is not None else 9999,
            reverse=True,
        )

        lbl_font = QFont("Segoe UI", 7)
        lbl_font_bold = QFont("Segoe UI", 7, QFont.Weight.Bold)

        for b in sorted_bubbles:
            bx, by = b["x"], b["y"]
            dist   = b["dist_km"]

            line_col = self._line_color(dist)
            line_col_t = QColor(line_col)
            line_col_t.setAlpha(80)

            # Dashed line
            pen = QPen(line_col_t, 1.2, Qt.PenStyle.DashLine)
            pen.setDashPattern([4, 4])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(cx, cy), QPointF(bx, by))

            # Midpoint distance label
            if dist is not None:
                mx = (cx + bx) / 2
                my = (cy + by) / 2

                km_txt = f"{dist:.1f} km"
                p.setFont(lbl_font)
                fm = QFontMetrics(lbl_font)
                tw = fm.horizontalAdvance(km_txt)
                th = fm.height()

                # Pill background
                pad = 3
                bg  = QColor(14, 17, 32, 210)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(bg))
                p.drawRoundedRect(
                    QRectF(mx - tw/2 - pad, my - th/2 - pad + 1,
                           tw + pad*2, th + pad*2 - 2),
                    4, 4
                )

                # Text
                label_col = QColor(line_col)
                label_col.setAlpha(220)
                p.setPen(QPen(label_col))
                p.setFont(lbl_font_bold)
                p.drawText(
                    int(mx - tw/2),
                    int(my + th/2 - 1),
                    km_txt,
                )

    def _paint_empty(self):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0e1120"))
        p.setPen(QPen(QColor(255, 255, 255, 80)))
        p.setFont(QFont("Segoe UI", 12))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "🗺  No municipality data available\n\n"
            "Ensure home_municipality is populated in dim_student."
        )
        p.end()

    def _paint_legend(self, p: QPainter):
        bubble_entries = [
            (QColor("#ff5b5b"), "High ≥ 50%"),
            (QColor("#f5b335"), "Moderate 25–50%"),
            (QColor("#34d399"), "Low < 25%"),
        ]
        line_entries = [
            (QColor("#4f8cff"), "≤ 20 km"),
            (QColor("#34d399"), "21–60 km"),
            (QColor("#f5b335"), "61–120 km"),
            (QColor("#ff5b5b"), "> 120 km"),
        ]

        W = self.width()
        H = self.height()

        # ── Bubble legend (bottom-right) ──────────────────────────────
        lx, ly = W - 160, H - 90
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(QBrush(QColor(19, 23, 42, 210)))
        p.drawRoundedRect(QRectF(lx - 10, ly - 24, 158, 88), 8, 8)

        p.setPen(QPen(QColor(255, 255, 255, 100)))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(int(lx), int(ly - 8), "RISK DENSITY")

        p.setFont(QFont("Segoe UI", 8))
        for i, (col, lbl) in enumerate(bubble_entries):
            ey = ly + i * 22
            p.setBrush(QBrush(col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(lx, ey, 12, 12))
            p.setPen(QPen(QColor("#c9d0e0")))
            p.drawText(int(lx + 18), int(ey + 10), lbl)

        p.setPen(QPen(QColor(255, 255, 255, 60)))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(int(lx), int(ly + 70), "Bubble size = high-risk count")

        # ── Distance line legend (bottom-left, only when lines shown) ─
        if not self._show_lines:
            return

        dx, dy = 14, H - 116
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(QBrush(QColor(19, 23, 42, 210)))
        p.drawRoundedRect(QRectF(dx - 6, dy - 24, 148, 108), 8, 8)

        p.setPen(QPen(QColor(255, 255, 255, 100)))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(int(dx), int(dy - 8), "DISTANCE FROM CTU")

        dash_pen_base = QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.DashLine)
        dash_pen_base.setDashPattern([4, 4])

        p.setFont(QFont("Segoe UI", 8))
        for i, (col, lbl) in enumerate(line_entries):
            ey = dy + i * 22
            lc = QColor(col); lc.setAlpha(200)
            dash_pen = QPen(lc, 1.5, Qt.PenStyle.DashLine)
            dash_pen.setDashPattern([4, 4])
            p.setPen(dash_pen)
            p.drawLine(int(dx), int(ey + 6), int(dx + 22), int(ey + 6))
            p.setPen(QPen(QColor("#c9d0e0")))
            p.drawText(int(dx + 28), int(ey + 10), lbl)

    # ── Mouse hover tooltip (Option 1) ────────────────────────────────

    def mouseMoveEvent(self, event):
        mx, my = event.position().x(), event.position().y()
        for b in reversed(self._bubbles):
            dx = mx - b["x"]
            dy = my - b["y"]
            if math.sqrt(dx*dx + dy*dy) <= b["r"] + 4:
                dist    = b["dist_km"]
                dist_s  = (f"{dist:.1f} km from CTU-Daanbantayan"
                           if dist is not None else "Distance unknown")
                risk_pct = b["risk_pct"]
                tip = (
                    f"<b>{b['muni']}</b><br>"
                    f"Total: {b['total']:,} students<br>"
                    f"<span style='color:#ff5b5b;'>● High: {b['high']:,}</span><br>"
                    f"<span style='color:#f5b335;'>● Moderate: {b['moderate']:,}</span><br>"
                    f"<span style='color:#34d399;'>● Low: {b['low']:,}</span><br>"
                    f"Risk density: <b>{risk_pct:.1f}%</b><br>"
                    f"📍 <b>{dist_s}</b>"          # ← Option 1
                )
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                return
        QToolTip.hideText()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._data:
            self._reproject()
            self.update()


# ── Public widget ─────────────────────────────────────────────────────────────

class MunicipalityRiskMap(QFrame):
    """
    Drop-in municipality bubble map widget.
    Uses pure QPainter — no WebEngine required.

    Exposes:
        load_predictions(predictions)  — aggregate from in-memory preds
        load_from_db(ay, sem)          — load direct from DB
        show_empty()                   — reset to empty state
        cleanup()                      — stop worker (call from closeEvent)
        set_show_distance_lines(bool)  — toggle Option 2 lines at runtime
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("municipalityMapFrame")
        self.setMinimumHeight(420)
        self._worker: _MapDataWorker | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Status bar
        hdr    = QWidget()
        hdr.setStyleSheet("background:transparent;")
        hdr_lo = QHBoxLayout(hdr)
        hdr_lo.setContentsMargins(0, 4, 4, 0)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35);font-size:10px;background:transparent;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        hdr_lo.addStretch()
        hdr_lo.addWidget(self._status_lbl)
        root.addWidget(hdr)

        self._canvas = _MapCanvas()
        root.addWidget(self._canvas, 1)

    # ── Public API ─────────────────────────────────────────────────────

    def load_predictions(self, predictions: list[dict]):
        """Aggregate home_municipality from predictions and render."""
        agg: dict[str, dict] = defaultdict(
            lambda: {"total": 0, "high_risk": 0,
                     "moderate_risk": 0, "low_risk": 0}
        )
        for pred in predictions:
            muni = str(
                pred.get("home_municipality") or
                pred.get("municipality")      or
                pred.get("home_address")      or
                "Unknown"
            ).strip()
            cat = pred.get("category", "low_risk")
            agg[muni]["total"] += 1
            if cat == "high_risk":
                agg[muni]["high_risk"] += 1
            elif cat == "moderate_risk":
                agg[muni]["moderate_risk"] += 1
            else:
                agg[muni]["low_risk"] += 1

        data = sorted(
            [{"municipality": m, **v} for m, v in agg.items()],
            key=lambda x: x["high_risk"], reverse=True,
        )
        self._render(data)

    def load_from_db(self, academic_year: str, semester: int):
        """Load municipality risk aggregates directly from the DB."""
        if self._worker is not None:
            return
        self._status_lbl.setText("Loading map…")
        self._worker = _MapDataWorker(academic_year, semester)
        self._worker.finished.connect(self._on_db_data)
        self._worker.error.connect(self._on_db_error)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(self._worker.deleteLater)
        self._worker.start()

    def show_empty(self):
        """Reset to the empty state."""
        self._canvas.clear()
        self._status_lbl.setText("")

    def cleanup(self):
        """Stop any running worker. Call from parent closeEvent."""
        if self._worker is None:
            return
        try:
            self._worker.finished.disconnect()
            self._worker.error.disconnect()
            if self._worker.isRunning():
                self._worker.quit()
                self._worker.wait(2000)
        except Exception:
            pass
        self._worker = None

    def set_show_distance_lines(self, show: bool):
        """Toggle Option 2 distance lines."""
        self._canvas.set_show_lines(show)

    # ── Internal ───────────────────────────────────────────────────────

    def _render(self, data: list[dict]):
        mapped_count = sum(1 for r in data if _coords(r["municipality"]))
        total_high   = sum(r["high_risk"] for r in data)
        self._status_lbl.setText(
            f"{mapped_count}/{len(data)} municipalities mapped  ·  "
            f"{total_high:,} high-risk students"
        )
        self._canvas.set_data(data)

    def _on_db_data(self, data: list[dict]):
        self._worker = None
        self._render(data)

    def _on_db_error(self, msg: str):
        self._worker = None
        self._status_lbl.setText(f"⚠ {msg[:60]}")