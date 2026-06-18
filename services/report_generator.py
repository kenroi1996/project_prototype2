"""
EarlyAlert — Cohort Risk Summary Report Generator
==================================================
Uses reportlab (pure Python, no Qt print module needed).

Install on your machine:
    pip install reportlab

Usage
-----
    from services.report_generator import CohortReportGenerator
    gen  = CohortReportGenerator(rows, term_label, academic_year, semester)
    path = gen.build("outputs/reports")
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import Flowable

# ── Palette ───────────────────────────────────────────────────────────────────
C_NAVY     = colors.HexColor("#1e2540")
C_ACCENT   = colors.HexColor("#4f8cff")
C_HIGH     = colors.HexColor("#ff5b5b")
C_MODERATE = colors.HexColor("#f5b335")
C_LOW      = colors.HexColor("#34d399")
C_TEXT     = colors.HexColor("#1a1f35")
C_MUTED    = colors.HexColor("#6b7a99")
C_WHITE    = colors.white
C_LIGHT    = colors.HexColor("#f0f2f8")
C_DIVIDER  = colors.HexColor("#dde2ee")
C_CARD     = colors.HexColor("#f7f9ff")

# ── Risk helpers ──────────────────────────────────────────────────────────────
def _cat(label: str) -> str:
    lc = label.lower()
    if "high"     in lc: return "high_risk"
    if "moderate" in lc or "medium" in lc: return "moderate_risk"
    return "low_risk"

def _risk_color(label: str) -> colors.Color:
    return {"high_risk": C_HIGH, "moderate_risk": C_MODERATE,
            "low_risk": C_LOW}.get(_cat(label), C_MUTED)

# ── Custom Flowables ──────────────────────────────────────────────────────────
class _AccentBar(Flowable):
    """Full-width coloured rule."""
    def __init__(self, color, height=3):
        super().__init__()
        self._color = color
        self.height = height

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        self.canv.setFillColor(self._color)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)


class _StatRow(Flowable):
    """Four side-by-side stat cards in one Flowable."""
    def __init__(self, stats: list[tuple]):
        """stats: list of (value_str, label_str, color)"""
        super().__init__()
        self._stats = stats
        self.height = 58

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        n   = len(self._stats)
        gap = 6
        w   = (self.width - gap * (n - 1)) / n
        for i, (val, lbl, col) in enumerate(self._stats):
            x = i * (w + gap)
            # Card background
            self.canv.setFillColor(C_CARD)
            self.canv.roundRect(x, 0, w, self.height, 4, stroke=0, fill=1)
            # Left stripe
            self.canv.setFillColor(col)
            self.canv.rect(x, 0, 3, self.height, stroke=0, fill=1)
            # Value
            self.canv.setFillColor(col)
            self.canv.setFont("Helvetica-Bold", 20)
            self.canv.drawString(x + 10, self.height - 26, val)
            # Label
            self.canv.setFillColor(C_MUTED)
            self.canv.setFont("Helvetica", 8)
            self.canv.drawString(x + 10, 8, lbl)


class _StackedBar(Flowable):
    """Proportional horizontal High | Moderate | Low bar."""
    def __init__(self, high, moderate, low, height=18):
        super().__init__()
        self._high, self._moderate, self._low = high, moderate, low
        self.height = height

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        total = max(self._high + self._moderate + self._low, 1)
        w = self.width
        h = self.height
        self.canv.setFillColor(C_LIGHT)
        self.canv.roundRect(0, 0, w, h, 4, stroke=0, fill=1)
        x = 0
        for count, col in [
            (self._high,     C_HIGH),
            (self._moderate, C_MODERATE),
            (self._low,      C_LOW),
        ]:
            seg = w * count / total
            if seg > 0:
                self.canv.setFillColor(col)
                self.canv.rect(x, 0, seg, h, stroke=0, fill=1)
                x += seg


# ── Styles ────────────────────────────────────────────────────────────────────
_BASE = getSampleStyleSheet()["Normal"]

def _s(name: str) -> ParagraphStyle:
    defs = {
        "title": ParagraphStyle("title", parent=_BASE,
            fontName="Helvetica-Bold", fontSize=13,
            textColor=C_TEXT, leading=18),
        "term": ParagraphStyle("term", parent=_BASE,
            fontName="Helvetica", fontSize=9,
            textColor=C_MUTED, leading=14),
        "section": ParagraphStyle("section", parent=_BASE,
            fontName="Helvetica-Bold", fontSize=7,
            textColor=C_MUTED, leading=10, spaceBefore=4),
        "cell": ParagraphStyle("cell", parent=_BASE,
            fontName="Helvetica", fontSize=8,
            textColor=C_TEXT, leading=11),
        "cell_bold": ParagraphStyle("cell_bold", parent=_BASE,
            fontName="Helvetica-Bold", fontSize=8,
            textColor=C_TEXT, leading=11),
        "legend": ParagraphStyle("legend", parent=_BASE,
            fontName="Helvetica", fontSize=8,
            textColor=C_TEXT, leading=12),
        "note": ParagraphStyle("note", parent=_BASE,
            fontName="Helvetica-Oblique", fontSize=7,
            textColor=C_MUTED, leading=10),
    }
    return defs.get(name, _BASE)


def _cell(text: str, bold=False, color=None, align=TA_LEFT) -> Paragraph:
    style = ParagraphStyle(
        "c", parent=_BASE,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=8, textColor=color or C_TEXT,
        leading=11, alignment=align,
    )
    return Paragraph(str(text), style)


def _header_cell(text: str) -> Paragraph:
    style = ParagraphStyle(
        "h", parent=_BASE,
        fontName="Helvetica-Bold", fontSize=8,
        textColor=C_WHITE, leading=11,
    )
    return Paragraph(str(text), style)


def _make_table(
    headers: list,
    rows: list,
    col_widths_mm: list,
    risk_col: int = -1,
) -> Table:
    col_w = [w * mm for w in col_widths_mm]

    data = [[_header_cell(h) for h in headers]]
    for row in rows:
        styled = []
        for i, cell in enumerate(row):
            if i == risk_col:
                styled.append(_cell(cell, bold=True,
                                    color=_risk_color(str(cell))))
            else:
                align = TA_CENTER if i > 0 else TA_LEFT
                styled.append(_cell(cell, align=align))
        data.append(styled)

    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
        ("LINEBELOW",     (0, 0), (-1, 0),  1, C_ACCENT),
        # Alternating rows
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),
         [colors.white, C_CARD]),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.3, C_DIVIDER),
        # Padding
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


# ── Page templates ────────────────────────────────────────────────────────────
def _on_cover(canvas, doc, term_label, generated_at, institution):
    W, H = A4
    canvas.saveState()
    # Navy banner
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, H - 75*mm, W, 75*mm, stroke=0, fill=1)
    # Top accent strip
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, H - 3, W, 3, stroke=0, fill=1)
    # System name
    canvas.setFillColor(C_WHITE)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(20*mm, H - 28*mm, "EarlyAlert")
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#8b9dc3"))
    canvas.drawString(20*mm, H - 36*mm,
                      "AI-Powered Student Risk Prediction System")
    # Report type
    canvas.setFont("Helvetica-Bold", 13)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(20*mm, H - 52*mm, "Cohort Risk Summary Report")
    # Right-aligned term + date
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#8b9dc3"))
    canvas.drawRightString(W - 20*mm, H - 52*mm, term_label)
    canvas.drawRightString(W - 20*mm, H - 60*mm,
                           f"Generated: {generated_at}")
    # Footer
    canvas.setFillColor(C_DIVIDER)
    canvas.rect(0, 12*mm, W, 0.5, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(20*mm, 8*mm, "EarlyAlert — Confidential")
    canvas.drawRightString(W - 20*mm, 8*mm, "Page 1")
    canvas.restoreState()


def _on_inner(canvas, doc, term_label):
    W, H = A4
    canvas.saveState()
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, H - 2, W, 2, stroke=0, fill=1)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(C_NAVY)
    canvas.drawString(20*mm, H - 9*mm,
                      "EarlyAlert — Cohort Risk Summary")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawRightString(W - 20*mm, H - 9*mm, term_label)
    canvas.setFillColor(C_DIVIDER)
    canvas.rect(0, 12*mm, W, 0.5, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(20*mm, 8*mm, "EarlyAlert — Confidential")
    canvas.drawRightString(W - 20*mm, 8*mm, f"Page {doc.page}")
    canvas.restoreState()


# ── Main class ────────────────────────────────────────────────────────────────
class CohortReportGenerator:
    def __init__(
        self,
        rows:          list[dict],
        term_label:    str,
        academic_year: str,
        semester:      int,
        institution:   str = "University",
    ):
        self._rows          = rows
        self._term_label    = term_label
        self._academic_year = academic_year
        self._semester      = semester
        self._institution   = institution
        self._generated_at  = datetime.now().strftime("%B %d, %Y  %H:%M")
        self._stats         = self._compute_stats()

    # ── Public ────────────────────────────────────────────────────────
    def build(self, save_dir: str = "outputs/reports") -> str:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        ay  = self._academic_year.replace("-","_").replace(" ","")
        fn  = f"CohortRiskSummary_{ay}_Sem{self._semester}.pdf"
        path = str(Path(save_dir) / fn)
        buf = self._render()
        Path(path).write_bytes(buf.getvalue())
        return path

    def build_bytes(self) -> io.BytesIO:
        return self._render()

    # ── Stats ─────────────────────────────────────────────────────────
    def _compute_stats(self) -> dict:
        rows  = self._rows
        total = len(rows)
        if not total:
            return {}

        high = [r for r in rows if _cat(r.get("risk_label","")) == "high_risk"]
        mod  = [r for r in rows if _cat(r.get("risk_label","")) == "moderate_risk"]
        low  = [r for r in rows if _cat(r.get("risk_label","")) == "low_risk"]

        def avg(subset, key, mult=1):
            v = [float(r[key])*mult for r in subset
                 if r.get(key) is not None]
            return round(sum(v)/len(v), 2) if v else None

        scores = [float(r["predicted_risk_score"])*100
                  for r in rows if r.get("predicted_risk_score") is not None]
        avg_score = round(sum(scores)/len(scores), 1) if scores else 0.0

        by_col  = defaultdict(lambda: {"total":0,"high":0,"moderate":0,"low":0})
        by_prog = defaultdict(lambda: {"total":0,"high":0,"moderate":0,"low":0,"college":"—"})

        for r in rows:
            col  = str(r.get("college") or "—").strip()
            prog = str(r.get("program") or "Unknown").strip()
            cat  = _cat(r.get("risk_label",""))
            k    = cat.replace("_risk","")
            by_col[col]["total"]  += 1
            by_col[col][k]        += 1
            by_prog[prog]["total"] += 1
            by_prog[prog][k]       += 1
            by_prog[prog]["college"] = col

        top10 = sorted(by_prog.items(),
                       key=lambda x: x[1]["high"]+x[1]["moderate"],
                       reverse=True)[:10]

        def tier_vals(subset):
            sc = [float(r["predicted_risk_score"])*100
                  for r in subset if r.get("predicted_risk_score") is not None]
            ex = [float(r["entrance_exam_score"])
                  for r in subset if r.get("entrance_exam_score") is not None]
            gp = [float(r["high_school_gpa"])
                  for r in subset if r.get("high_school_gpa") is not None]
            return {
                "score": f"{round(sum(sc)/len(sc),1)}%" if sc else "—",
                "exam":  str(round(sum(ex)/len(ex),1)) if ex else "—",
                "gpa":   str(round(sum(gp)/len(gp),2)) if gp else "—",
                "count": len(subset),
            }

        return {
            "total":    total,
            "high":     len(high),
            "moderate": len(mod),
            "low":      len(low),
            "high_pct":     round(len(high)/total*100, 1),
            "moderate_pct": round(len(mod)/total*100,  1),
            "low_pct":      round(len(low)/total*100,  1),
            "avg_score":    avg_score,
            "by_college":   dict(by_col),
            "top_programs": top10,
            "tier": {
                "High Risk":     tier_vals(high),
                "Moderate Risk": tier_vals(mod),
                "Low Risk":      tier_vals(low),
            },
        }

    # ── Render ────────────────────────────────────────────────────────
    def _render(self) -> io.BytesIO:
        buf = io.BytesIO()
        W, H = A4
        M = 20*mm

        doc = BaseDocTemplate(
            buf, pagesize=A4,
            leftMargin=M, rightMargin=M,
            topMargin=M, bottomMargin=18*mm,
            title=f"Cohort Risk Summary — {self._term_label}",
            author="EarlyAlert",
        )

        tl = self._term_label
        ga = self._generated_at
        inst = self._institution

        cover_frame = Frame(M, 18*mm, W-2*M, H-80*mm-18*mm, id="cover")
        inner_frame = Frame(M, 18*mm, W-2*M, H-22*mm-18*mm, id="inner")

        doc.addPageTemplates([
            PageTemplate("Cover", [cover_frame],
                onPage=lambda c,d: _on_cover(c, d, tl, ga, inst)),
            PageTemplate("Inner", [inner_frame],
                onPage=lambda c,d: _on_inner(c, d, tl)),
        ])

        doc.build(self._story())
        buf.seek(0)
        return buf

    def _story(self) -> list:
        s      = self._stats
        M      = 20*mm
        W, _H  = A4
        TW     = W - 2*M   # text width
        story  = []

        story.append(NextPageTemplate("Cover"))

        # ── Institution + term ────────────────────────────────────────
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph(self._institution, _s("title")))
        story.append(Spacer(1, 1*mm))
        sem = "1st" if self._semester == 1 else "2nd"
        story.append(Paragraph(
            f"Academic Year {self._academic_year}  ·  {sem} Semester",
            _s("term")))
        story.append(Spacer(1, 5*mm))
        story.append(_AccentBar(C_ACCENT, height=2))
        story.append(Spacer(1, 8*mm))

        # ── Stat boxes ────────────────────────────────────────────────
        story.append(Paragraph("EXECUTIVE SUMMARY", _s("section")))
        story.append(Spacer(1, 3*mm))
        story.append(_StatRow([
            (f"{s['total']:,}",    "Total Students",  C_ACCENT),
            (f"{s['high']:,}",     "High Risk",       C_HIGH),
            (f"{s['moderate']:,}", "Moderate Risk",   C_MODERATE),
            (f"{s['low']:,}",      "Low Risk",        C_LOW),
        ]))
        story.append(Spacer(1, 7*mm))

        # ── Stacked bar ───────────────────────────────────────────────
        story.append(Paragraph("RISK DISTRIBUTION", _s("section")))
        story.append(Spacer(1, 3*mm))
        story.append(_StackedBar(s["high"], s["moderate"], s["low"]))
        story.append(Spacer(1, 3*mm))

        # Legend row
        legend_data = [[
            Paragraph(
                f'<font color="#ff5b5b">■</font>  '
                f'High Risk: {s["high"]:,} ({s["high_pct"]}%)',
                _s("legend")),
            Paragraph(
                f'<font color="#f5b335">■</font>  '
                f'Moderate Risk: {s["moderate"]:,} ({s["moderate_pct"]}%)',
                _s("legend")),
            Paragraph(
                f'<font color="#34d399">■</font>  '
                f'Low Risk: {s["low"]:,} ({s["low_pct"]}%)',
                _s("legend")),
        ]]
        lt = Table(legend_data, colWidths=[TW/3]*3)
        lt.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
        story.append(lt)
        story.append(Spacer(1, 8*mm))

        # ── Tier averages table ───────────────────────────────────────
        story.append(Paragraph("INDICATOR AVERAGES BY RISK TIER",
                                _s("section")))
        story.append(Spacer(1, 3*mm))
        tier_rows = []
        for lbl in ["High Risk", "Moderate Risk", "Low Risk"]:
            td = s["tier"][lbl]
            tier_rows.append([lbl, f"{td['count']:,}",
                               td["score"], td["exam"], td["gpa"]])
        story.append(_make_table(
            ["Risk Tier", "Count", "Avg Risk Score",
             "Avg Entrance Exam", "Avg HS GPA"],
            tier_rows,
            col_widths_mm=[44, 20, 36, 38, 30],
            risk_col=0,
        ))

        # ── Page 2 ────────────────────────────────────────────────────
        story.append(NextPageTemplate("Inner"))
        story.append(PageBreak())

        # By college
        story.append(Paragraph("RISK BREAKDOWN BY COLLEGE", _s("section")))
        story.append(Spacer(1, 3*mm))
        college_rows = []
        for col_name, data in sorted(
            s["by_college"].items(),
            key=lambda x: x[1]["high"]+x[1]["moderate"], reverse=True
        ):
            tot = max(data["total"], 1)
            college_rows.append([
                col_name,
                str(data["total"]),
                str(data.get("high",0)),
                f"{data.get('high',0)/tot*100:.1f}%",
                str(data.get("moderate",0)),
                f"{data.get('moderate',0)/tot*100:.1f}%",
                str(data.get("low",0)),
            ])
        story.append(_make_table(
            ["College","Total","High","High %",
             "Moderate","Mod %","Low"],
            college_rows,
            col_widths_mm=[45, 16, 18, 18, 22, 18, 16],
        ))
        story.append(Spacer(1, 8*mm))

        # Top 10 programs
        story.append(Paragraph("TOP 10 PROGRAMS BY AT-RISK COUNT",
                                _s("section")))
        story.append(Spacer(1, 3*mm))
        prog_rows = []
        for rank, (prog, data) in enumerate(s["top_programs"], 1):
            at_risk = data.get("high",0)+data.get("moderate",0)
            tot_p   = max(data["total"], 1)
            prog_rows.append([
                str(rank), prog, data.get("college","—"),
                str(data["total"]),
                str(data.get("high",0)),
                str(data.get("moderate",0)),
                str(data.get("low",0)),
                f"{at_risk/tot_p*100:.1f}%",
            ])
        story.append(_make_table(
            ["#","Program","College","Total",
             "High","Mod","Low","At-Risk %"],
            prog_rows,
            col_widths_mm=[8, 40, 34, 15, 16, 16, 16, 20],
        ))
        story.append(Spacer(1, 8*mm))

        # Footer note
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=C_DIVIDER))
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(
            f"This report was automatically generated by EarlyAlert on "
            f"{self._generated_at}. Risk scores are produced by a Random "
            f"Forest classifier trained on pre-enrollment student features. "
            f"Results should be interpreted alongside academic and counseling "
            f"records. Total students in this report: {s['total']:,}.",
            _s("note"),
        ))

        return story


# ═════════════════════════════════════════════════════════════════════════════
# Intervention Report Generator
# ═════════════════════════════════════════════════════════════════════════════

def _on_interv_cover(canvas, doc, term_label, generated_at, institution):
    """Cover page for intervention reports — green accent instead of blue."""
    W, H = A4
    C_GREEN = colors.HexColor("#34d399")
    canvas.saveState()
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, H - 75*mm, W, 75*mm, stroke=0, fill=1)
    canvas.setFillColor(C_GREEN)
    canvas.rect(0, H - 3, W, 3, stroke=0, fill=1)
    canvas.setFillColor(C_WHITE)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(20*mm, H - 28*mm, "EarlyAlert")
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#8b9dc3"))
    canvas.drawString(20*mm, H - 36*mm,
                      "AI-Powered Student Risk Prediction System")
    canvas.setFont("Helvetica-Bold", 13)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(20*mm, H - 52*mm, "AI Intervention Recommendations Report")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#8b9dc3"))
    canvas.drawRightString(W - 20*mm, H - 52*mm, term_label)
    canvas.drawRightString(W - 20*mm, H - 60*mm,
                           f"Generated: {generated_at}")
    canvas.setFillColor(C_DIVIDER)
    canvas.rect(0, 12*mm, W, 0.5, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(20*mm, 8*mm, institution + " — Confidential")
    canvas.drawRightString(W - 20*mm, 8*mm, "Page 1")
    canvas.restoreState()


def _on_interv_inner(canvas, doc, term_label):
    W, H = A4
    C_GREEN = colors.HexColor("#34d399")
    canvas.saveState()
    canvas.setFillColor(C_GREEN)
    canvas.rect(0, H - 2, W, 2, stroke=0, fill=1)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(C_NAVY)
    canvas.drawString(20*mm, H - 9*mm,
                      "EarlyAlert — AI Intervention Recommendations")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawRightString(W - 20*mm, H - 9*mm, term_label)
    canvas.setFillColor(C_DIVIDER)
    canvas.rect(0, 12*mm, W, 0.5, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(20*mm, 8*mm, "EarlyAlert — Confidential")
    canvas.drawRightString(W - 20*mm, 8*mm, f"Page {doc.page}")
    canvas.restoreState()


class InterventionReportGenerator:
    """
    Generates a PDF report of all AI intervention records for a given term.

    Each intervention record contains:
      - mode          'per_student' or 'cohort'
      - student_id    (per_student only)
      - risk_score / risk_label
      - recommendations  JSONB list
      - logged_at

    Usage
    -----
        gen  = InterventionReportGenerator(records, term_label, ay, sem)
        buf  = gen.build_bytes()   # → io.BytesIO
        path = gen.build(save_dir) # → str path
    """

    def __init__(
        self,
        records:       list[dict],
        term_label:    str,
        academic_year: str,
        semester:      int,
        institution:   str = "University",
    ):
        self._records       = records
        self._term_label    = term_label
        self._academic_year = academic_year
        self._semester      = semester
        self._institution   = institution
        self._generated_at  = datetime.now().strftime("%B %d, %Y  %H:%M")

    # ── Public ────────────────────────────────────────────────────────────────

    def build(self, save_dir: str = "outputs/reports") -> str:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        ay  = self._academic_year.replace("-", "_").replace(" ", "")
        fn  = f"InterventionReport_{ay}_Sem{self._semester}.pdf"
        path = str(Path(save_dir) / fn)
        buf  = self._render()
        Path(path).write_bytes(buf.getvalue())
        return path

    def build_bytes(self) -> io.BytesIO:
        return self._render()

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self) -> io.BytesIO:
        buf = io.BytesIO()
        W, H = A4
        M    = 20*mm

        doc = BaseDocTemplate(
            buf, pagesize=A4,
            leftMargin=M, rightMargin=M,
            topMargin=M, bottomMargin=18*mm,
            title=f"AI Intervention Report — {self._term_label}",
            author="EarlyAlert",
        )

        tl   = self._term_label
        ga   = self._generated_at
        inst = self._institution

        cover_frame = Frame(M, 18*mm, W-2*M, H-80*mm-18*mm, id="cover")
        inner_frame = Frame(M, 18*mm, W-2*M, H-22*mm-18*mm, id="inner")

        doc.addPageTemplates([
            PageTemplate("Cover", [cover_frame],
                onPage=lambda c, d: _on_interv_cover(c, d, tl, ga, inst)),
            PageTemplate("Inner", [inner_frame],
                onPage=lambda c, d: _on_interv_inner(c, d, tl)),
        ])

        doc.build(self._story())
        buf.seek(0)
        return buf

    def _story(self) -> list:
        C_GREEN = colors.HexColor("#34d399")
        story   = []

        # ── Cover summary ─────────────────────────────────────────────────────
        per_student = [r for r in self._records if r.get("mode") == "per_student"]
        cohort      = [r for r in self._records if r.get("mode") == "cohort"]
        total_recs  = sum(
            len(r.get("recommendations") or []) for r in self._records
        )

        story.append(Paragraph(self._institution, _s("term")))
        story.append(Spacer(1, 2*mm))
        story.append(Paragraph(
            f"AI Intervention Recommendations Report", _s("title")
        ))
        story.append(Spacer(1, 1*mm))
        story.append(Paragraph(self._term_label, _s("term")))
        story.append(Spacer(1, 6*mm))
        story.append(_AccentBar(C_GREEN))
        story.append(Spacer(1, 6*mm))

        # Summary stat cards
        story.append(_StatRow([
            (str(len(self._records)), "Total Interventions Logged", C_GREEN),
            (str(len(per_student)),   "Per-Student Sessions",       C_ACCENT),
            (str(len(cohort)),        "Cohort Summaries",           C_MODERATE),
            (str(total_recs),         "Total Recommendations",      C_HIGH),
        ]))
        story.append(Spacer(1, 8*mm))

        story.append(Paragraph(
            f"Generated {self._generated_at} · EarlyAlert AI Advisor",
            _s("note")
        ))
        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=C_DIVIDER, spaceAfter=4*mm))

        # ── Per-student interventions ──────────────────────────────────────────
        if per_student:
            story.append(NextPageTemplate("Inner"))
            story.append(PageBreak())
            story.append(Paragraph(
                "PER-STUDENT INTERVENTIONS", _s("section")
            ))
            story.append(Spacer(1, 3*mm))

            for rec in per_student:
                story += self._student_block(rec, C_GREEN)
                story.append(Spacer(1, 4*mm))
                story.append(HRFlowable(width="100%", thickness=0.3,
                                         color=C_DIVIDER, spaceAfter=2*mm))

        # ── Cohort summaries ───────────────────────────────────────────────────
        if cohort:
            story.append(NextPageTemplate("Inner"))
            story.append(PageBreak())
            story.append(Paragraph("COHORT SYSTEMIC ISSUES", _s("section")))
            story.append(Spacer(1, 3*mm))

            for rec in cohort:
                story += self._cohort_block(rec, C_GREEN)
                story.append(Spacer(1, 4*mm))
                story.append(HRFlowable(width="100%", thickness=0.3,
                                         color=C_DIVIDER, spaceAfter=2*mm))

        return story

    # ── Per-student block ──────────────────────────────────────────────────────

    def _student_block(self, rec: dict, accent) -> list:
        items    = []
        recs     = rec.get("recommendations") or []
        sid      = rec.get("student_id") or "—"
        score    = rec.get("risk_score")
        label    = rec.get("risk_label") or "—"
        factors  = rec.get("risk_factors") or "—"
        logged   = rec.get("logged_at")
        logged_s = (logged.strftime("%b %d, %Y %H:%M")
                    if hasattr(logged, "strftime") else str(logged or "—")[:16])

        risk_col = _risk_color(label)
        score_s  = f"{float(score):.1f}%" if score else "—"

        # Student header row
        hdr_data = [[
            _cell(f"Student ID: {sid}", bold=True),
            _cell(f"Risk: {score_s}", bold=True, color=risk_col, align=TA_CENTER),
            _cell(f"Label: {label}", bold=True, color=risk_col, align=TA_CENTER),
            _cell(f"Logged: {logged_s}", align=TA_RIGHT),
        ]]
        hdr_t = Table(hdr_data, colWidths=[45*mm, 35*mm, 45*mm, 45*mm])
        hdr_t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), C_CARD),
            ("LINEBELOW",    (0, 0), (-1, 0), 1.5, accent),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        items.append(hdr_t)
        items.append(Spacer(1, 1*mm))

        if factors and factors != "—":
            items.append(Paragraph(
                f"Top risk factor: {factors}", _s("note")
            ))
            items.append(Spacer(1, 2*mm))

        if not recs:
            items.append(Paragraph("No recommendations recorded.", _s("note")))
            return items

        # Recommendations table
        type_colors = {
            "Academic Support": C_ACCENT,
            "Financial Aid":    C_MODERATE,
            "Counseling":       colors.HexColor("#a78bfa"),
            "Program Guidance": C_LOW,
            "Peer Support":     colors.HexColor("#f59e0b"),
        }

        tbl_data = [[
            _header_cell("#"),
            _header_cell("Type"),
            _header_cell("Action"),
            _header_cell("Rationale"),
            _header_cell("Timeline"),
        ]]
        for i, r in enumerate(recs):
            rtype   = str(r.get("type",     "—"))
            action  = str(r.get("action",   "—"))
            rat     = str(r.get("rationale","—"))
            tl      = str(r.get("timeline", "—"))
            tc      = type_colors.get(rtype, C_MUTED)
            tbl_data.append([
                _cell(str(i+1), align=TA_CENTER),
                _cell(rtype, bold=True, color=tc),
                _cell(action),
                _cell(rat),
                _cell(tl, align=TA_CENTER),
            ])

        rec_t = _make_table(
            [], tbl_data[1:],    # pass pre-built data
            [8, 30, 50, 55, 27],
        )
        # Build manually to use custom header row
        rec_t2 = Table(tbl_data, colWidths=[8*mm, 30*mm, 50*mm, 55*mm, 27*mm],
                        repeatRows=1)
        rec_t2.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
            ("LINEBELOW",     (0, 0), (-1, 0),  1, accent),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_CARD]),
            ("LINEBELOW",     (0, 1), (-1, -1), 0.3, C_DIVIDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        items.append(rec_t2)
        return items

    # ── Cohort block ───────────────────────────────────────────────────────────

    def _cohort_block(self, rec: dict, accent) -> list:
        items   = []
        issues  = rec.get("recommendations") or []
        scope   = rec.get("risk_label") or "Cohort"
        factors = rec.get("risk_factors") or "—"
        logged  = rec.get("logged_at")
        logged_s = (logged.strftime("%b %d, %Y %H:%M")
                    if hasattr(logged, "strftime") else str(logged or "—")[:16])

        hdr_data = [[
            _cell(f"Scope: {scope}", bold=True),
            _cell(f"Term: {factors}", bold=True),
            _cell(f"Logged: {logged_s}", align=TA_RIGHT),
        ]]
        hdr_t = Table(hdr_data, colWidths=[60*mm, 80*mm, 30*mm])
        hdr_t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), C_CARD),
            ("LINEBELOW",    (0, 0), (-1, 0), 1.5, accent),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        items.append(hdr_t)
        items.append(Spacer(1, 2*mm))

        if not issues:
            items.append(Paragraph("No issues recorded.", _s("note")))
            return items

        tbl_data = [[
            _header_cell("#"),
            _header_cell("Systemic Issue"),
            _header_cell("Affected"),
            _header_cell("Description"),
            _header_cell("Recommended Action"),
        ]]
        priority_colors = [C_HIGH, C_MODERATE, C_ACCENT, C_LOW, C_MUTED]
        for i, iss in enumerate(issues):
            col = priority_colors[min(i, len(priority_colors)-1)]
            tbl_data.append([
                _cell(str(i+1), align=TA_CENTER),
                _cell(str(iss.get("issue", "—")), bold=True, color=col),
                _cell(str(iss.get("affected_count", "—")), align=TA_CENTER),
                _cell(str(iss.get("description", "—"))),
                _cell(str(iss.get("recommended_action", "—"))),
            ])

        tbl = Table(tbl_data, colWidths=[8*mm, 38*mm, 18*mm, 55*mm, 51*mm],
                    repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
            ("LINEBELOW",     (0, 0), (-1, 0),  1, accent),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_CARD]),
            ("LINEBELOW",     (0, 1), (-1, -1), 0.3, C_DIVIDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        items.append(tbl)
        return items