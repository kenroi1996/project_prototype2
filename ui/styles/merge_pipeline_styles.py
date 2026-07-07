"""
ui/styles/merge_pipeline_styles.py
=====================================
QSS stylesheet for DataMergePipelinePage, extracted verbatim from
ui/pages/data_merge_pipeline_page.py — no visual changes.
"""

MERGE_PIPELINE_STYLESHEET = """
            #mergeHeaderCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #mergeCard, #pipelineCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #mergeCardTitleBar { background: transparent; }
            #mergeCardTitle {
                color: #e8eaf0;
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
            #mergeCardSubtitle {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #mergeTitle {
                color: #e8eaf0;
                font-size: 30px;
                font-weight: bold;
                background: transparent;
            }
            #mergeSubtitle {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #mergeModelPill {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
            #pipelineSemesterPill {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }
            #sectionTag {
                color: rgba(255,255,255,0.25);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.4px;
                background: transparent;
            }
            #sectionDividerTitle {
                color: #e8eaf0;
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }
            #pipelineGateBanner {
                background-color: rgba(245,179,53,0.07);
                border: 1px solid rgba(245,179,53,0.25);
                border-radius: 12px;
            }
            #pipelineGateMsg {
                color: rgba(255,255,255,0.65);
                font-size: 13px;
                background: transparent;
            }
            #mergeSourceRow {
                background-color: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 10px;
                padding: 12px 16px;
            }
            #mergeSourceMeta {
                color: rgba(255,255,255,0.4);
                font-size: 11px;
                background: transparent;
                min-width: 80px;
            }
            #mergeSourceProgress {
                background-color: rgba(255,255,255,0.08);
                border-radius: 3px;
                border: none;
            }
            #mergeSourceProgress::chunk {
                background-color: #4f8cff;
                border-radius: 3px;
            }
            #mergeConfigTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #mergeExpectedLabel {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #mergeRunPredBtn {
                background-color: rgba(79,140,255,0.15);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }
            #mergeRunPredBtn:hover { background-color: rgba(79,140,255,0.25); }
            #mergeQualityBar {
                background-color: rgba(0,0,0,0.15);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #mergeQualityLabel {
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }
            #mergeQualityPct {
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
            #mergeQualityProgress {
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px;
                border: none;
            }
            #mergeQualityProgress::chunk { border-radius: 4px; }
            #mergeTableContainer { background: transparent; }
            #mergeTable {
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(79,140,255,0.18);
                selection-color: white;
            }
            #mergeTable QHeaderView::section {
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.45);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 8px 10px;
            }
            #mergeTable QHeaderView::section:last { border-right: none; }
            #mergeTable QScrollBar:vertical { background: transparent; width: 8px; }
            #mergeTable QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
                min-height: 30px;
            }
            #mergeTable QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.28); }
            #mergeTable QScrollBar:horizontal { background: transparent; height: 8px; }
            #mergeTable QScrollBar::handle:horizontal {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
            }
            #mergeTable QScrollBar::add-line:vertical,
            #mergeTable QScrollBar::sub-line:vertical,
            #mergeTable QScrollBar::add-line:horizontal,
            #mergeTable QScrollBar::sub-line:horizontal { height: 0; width: 0; }
            #mergeLogContainer { background: transparent; }
            #mergeLog {
                background-color: rgba(0,0,0,0.25);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
                color: #b8bcc8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 10px;
            }
            #mergeUnmatchedLabel {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }
            #mergeResultFooter {
                background-color: rgba(0,0,0,0.12);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #mergeResultNote {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }
            #mergeSaveBtn {
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.35);
                border-radius: 8px;
                color: #34d399;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #mergeSaveBtn:hover { background-color: rgba(52,211,153,0.20); }
            #mergeProceedBtn {
                background-color: #4f8cff;
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #mergeProceedBtn:hover { background-color: rgba(79,140,255,0.85); }
            #pipelineDownloadBtn {
                background-color: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #pipelineDownloadBtn:hover { background-color: rgba(79,140,255,0.20); }
            #pipelineDownloadBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.2);
            }
            #pipelineViewDatasetBtn {
                background-color: rgba(167,139,250,0.10);
                border: 1px solid rgba(167,139,250,0.30);
                border-radius: 8px;
                color: #a78bfa;
                font-size: 11px;
                font-weight: 600;
                padding: 0 14px;
            }
            #pipelineViewDatasetBtn:hover { background-color: rgba(167,139,250,0.20); }
            #pipelineViewDatasetBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.2);
            }
            #mergeStatTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #pipelineSectionTitle {
                color: #e8eaf0;
                font-size: 15px;
                font-weight: bold;
                background: transparent;
            }
            #pipelineSectionDesc {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #pipelineCardTitle {
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.2px;
                background: transparent;
            }
            #pipelineStage {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #pipelineStageIcon {
                font-size: 24px;
                color: rgba(255,255,255,0.4);
                background: transparent;
            }
            #pipelineStageLabel {
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                background: transparent;
            }
            #pipelineStageArrow {
                color: rgba(255,255,255,0.2);
                font-size: 20px;
                background: transparent;
            }
            #pipelineMetricLabel {
                color: rgba(255,255,255,0.55);
                font-size: 12px;
                background: transparent;
            }
            #pipelineMetricValue {
                color: rgba(255,255,255,0.7);
                font-size: 12px;
                background: transparent;
            }
            #pipelineQualityFooter {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #pipelinePreviewMeta {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #pipelineFeatureBlue {
                color: #4f8cff;
                background-color: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeatureGreen {
                color: #34d399;
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeatureOrange {
                color: #f59e0b;
                background-color: rgba(245,158,11,0.10);
                border: 1px solid rgba(245,158,11,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeaturePurple {
                color: #a78bfa;
                background-color: rgba(167,139,250,0.10);
                border: 1px solid rgba(167,139,250,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.10);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.20); }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """