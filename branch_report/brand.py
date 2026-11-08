"""CenterWell / CQI branding shared by the branch PDF and the Gradio app.

Colours, assets and footer text match cqi_booklet/src/build_booklet.py and cqi_booklet/config/branding.yaml,
so every Project Alpha output looks like the booklet.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "cqi_booklet", "assets")
BRANDING_YAML = os.environ.get("CQI_BRANDING", os.path.join(ROOT, "cqi_booklet", "config", "branding.yaml"))

# Booklet palette (hex) - same values as build_booklet.py
HEX = {
    "dark_blue": "#003DA5", "light_blue": "#00A3D7", "green": "#78BE20", "panel": "#DCF1F9",
    "dark_gray": "#414042", "navy": "#003057", "plum": "#AF0061", "light_gray": "#D9D9D6",
    "mid_gray": "#6D6E71", "ok_green": "#3E6B0E", "analysis_green": "#4E7D14",
}
TIER_HEX = {"very_high": HEX["plum"], "high": HEX["plum"], "moderate": HEX["light_blue"], "low": HEX["green"]}
TRAJ_HEX = {"deteriorating": HEX["plum"], "insufficient_data": HEX["mid_gray"], "stable": HEX["dark_blue"], "improving": HEX["analysis_green"]}
TRAJ_LABEL = {"deteriorating": "Deteriorating", "insufficient_data": "No recent data", "stable": "Stable", "improving": "Improving"}
WHEN_HEX = {"Within 24 hrs": HEX["plum"], "This conference": HEX["navy"], "Before conference": HEX["navy"],
            "This week": HEX["dark_blue"], "Next SN visit": HEX["navy"], "Before discharge": HEX["analysis_green"]}
GOAL_HEX = {"Achieved": HEX["analysis_green"], "In progress": HEX["dark_blue"], "Delayed": HEX["navy"],
            "Missed": HEX["plum"], "Not started": HEX["mid_gray"]}
TONE_HEX = {"alert": HEX["plum"], "ok": HEX["ok_green"], "neutral": HEX["dark_blue"]}


def asset(name):
    return os.path.join(ASSETS, name)


def branding():
    try:
        with open(BRANDING_YAML, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def font_files():
    """(regular, bold, italic) TTF paths for Arial or a metric-compatible font, or None to fall back to Helvetica."""
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    cands = [(win, ("arial.ttf", "arialbd.ttf", "ariali.ttf")),
             ("/Library/Fonts", ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf")),
             ("/System/Library/Fonts/Supplemental", ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf")),
             ("/usr/share/fonts/truetype/liberation", ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf")),
             ("/usr/share/fonts/liberation", ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"))]
    for d, names in cands:
        paths = tuple(os.path.join(d, n) for n in names)
        if all(os.path.exists(p) for p in paths):
            return paths
    return None
