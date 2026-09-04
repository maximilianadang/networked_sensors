"""Dashboard asset and UI configuration shared by the HTTP/runtime layers."""

from pathlib import Path


DEFAULT_HISTORY_LIMIT = 600
DEFAULT_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "system_config.json"
DEFAULT_METADATA = {
    "sample_number": "",
    "sub_number": "",
    "dispenser": "",
    "material": "",
    "powder_flow_rate_g_per_s": "",
    "test_duration_s": "",
    "description": "",
    "notes": "",
}

DASHBOARD_ASSET_DIR = Path(__file__).with_name("static")
DASHBOARD_ASSET_TYPES = {
    "/assets/dashboard.css": "text/css; charset=utf-8",
    "/assets/app.js": "text/javascript; charset=utf-8",
    "/assets/api.js": "text/javascript; charset=utf-8",
    "/assets/charts.js": "text/javascript; charset=utf-8",
    "/assets/config.js": "text/javascript; charset=utf-8",
    "/assets/dom.js": "text/javascript; charset=utf-8",
    "/assets/components/metadata.js": "text/javascript; charset=utf-8",
    "/assets/components/metrics.js": "text/javascript; charset=utf-8",
    "/assets/components/sources.js": "text/javascript; charset=utf-8",
    "/assets/components/stepper.js": "text/javascript; charset=utf-8",
    "/assets/components/toolbar.js": "text/javascript; charset=utf-8",
}


def load_dashboard_asset(relative_path: str) -> str:
    """Read a local asset independent of the process working directory."""

    return (DASHBOARD_ASSET_DIR / relative_path).read_text(encoding="utf-8")


INDEX_HTML = load_dashboard_asset("index.html")
