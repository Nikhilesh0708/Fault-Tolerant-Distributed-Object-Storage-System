"""Presentation only. No scripts, external fonts, or extra frontend frameworks."""
CSS = """
<style>
.stApp { background: #f5f7f5; color: #183b32; }
.block-container { max-width: 1400px; padding-top: 4.6rem; padding-bottom: 3rem; }
[data-testid="stSidebar"] { background: #153c32; }
[data-testid="stSidebar"] * { color: #f1f6f2; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * { color: #c2d8cf; }
[data-testid="stSidebar"] hr { border-color: #396052; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding: .35rem 0; }
[data-testid="stSidebar"] button { background: #285647; border-color: #547767; color: white; }
h1, h2, h3 { color: #173e31; letter-spacing: -.035em; }
h1 { font-size: 2.7rem !important; font-weight: 650 !important; }
h3 { font-size: 1.15rem !important; }
[data-testid="stMetric"] { background: white; border: 1px solid #dce6df; border-radius: 12px; padding: 1.15rem 1.35rem; }
[data-testid="stMetricValue"] { color: #173e31; }
[data-testid="stMetricLabel"] { color: #6a7b72; }
[data-testid="stVerticalBlockBorderWrapper"] > div { border-radius: 12px; }
.eyebrow { font-size: .7rem; font-weight: 700; letter-spacing: .17em; color: #6f8277; text-transform: uppercase; margin-bottom: .45rem; }
.brand { font-size: 2.1rem; font-weight: 750; letter-spacing: -.06em; color: #eff8ed; }
.brand-mark { display: inline-block; border: 2px solid #aed4a5; border-radius: 9px; padding: 0 9px; font-size: 1.7rem; margin-right: 10px; color: #c3e8b3; }
.brand-subtitle { color: #bfd4c9; font-size: .8rem; margin: .35rem 0 2rem; }
.status-band { background: #e7f0e5; border: 1px solid #c9ddc4; border-radius: 12px; padding: 1rem 1.3rem; margin: .5rem 0 1.6rem; display:flex; gap: 1rem; align-items:center; }
.status-band.warn { background:#fff3df; border-color:#efd6aa; }
.status-band.neutral { background:#eaf0f2; border-color:#d4dfe2; }
.status-dot { height:10px; width:10px; background:#488746; border-radius:50%; flex-shrink:0; }
.warn .status-dot { background:#b17b25; }
.neutral .status-dot { background:#83959a; }
.status-title { font-size:.95rem; font-weight:650; margin-bottom:.18rem; }
.status-note { font-size:.8rem; color:#526959; }
.pill { display:inline-block; padding:3px 9px; border-radius:20px; background:#e9f1e7; color:#3a703d; font-size:.7rem; font-weight:700; letter-spacing:.03em; }
.pill.off { background:#f8e9e4; color:#ad573e; }
.pill.wait { background:#fff0d6; color:#a46e20; }
.node-title { font-weight:650; font-size:1rem; margin:.45rem 0 .7rem; }
.node-note { color:#748179; font-size:.75rem; line-height:1.5; padding-bottom:.3rem; }
.footer-note { color:#809085; font-size:.75rem; margin-top:2rem; border-top:1px solid #dee6df; padding-top:1rem; }
div[data-testid="stFileUploader"] { border-radius:10px; }
@media (max-width: 700px) { .block-container { padding:4.6rem 1.2rem 1.2rem; } h1 { font-size:2rem !important; } }
</style>
"""
