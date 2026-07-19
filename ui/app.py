from __future__ import annotations

import json
import re
import sys
import tempfile
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, get_args

import streamlit as st
from pydantic import ValidationError

# ui/app.py lives one level below the project root; the infra package is only
# importable once that root is on sys.path (Streamlit can be launched from
# any working directory, so we can't rely on the caller's cwd for this).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from infra.load_env import load_project_env

load_project_env()

from infra.use_case_service.builder import UseCaseBuilder
from infra.use_case_service.repositories import run_repository as run_repo
from infra.use_case_service.repositories import use_case_repository as repo
from infra.use_case_service.schemas import DEFAULT_LLM_MODEL, LlmModel, UseCaseContract

# get_args() reads the literal string values out of the LlmModel type at
# runtime, so this dropdown always matches whatever the schema currently
# allows instead of drifting out of sync with it.
LLM_MODEL_OPTIONS: list[str] = list(get_args(LlmModel))
PLATFORMS = ["ios", "android"]
IN_APP_EVENT_METHODS = ["log_event", "validate_payload", "custom"]

# Mirrors the extension check in UseCaseContract.validate_platform_policies —
# kept here too so the dropdown never even offers a choice the schema would reject.
_PLATFORM_APP_EXTENSIONS = {
    "ios": (".ipa", ".app", ".zip"),
    "android": (".apk", ".aab", ".zip"),
}

# ---------------------------------------------------------------------------
# Visual design only — no business logic lives below this point.
# ---------------------------------------------------------------------------

_CUSTOM_CSS = """
<style>
    /* ---- Global look & feel — mirrors data/reports/templates/run_report.html.j2:
       same body gradient, font stack, card radius/shadow and heading color as
       the generated reports, so the builder and the reports it produces read
       as one consistent product instead of two different tools. ------------ */
    .stApp {
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        background: linear-gradient(160deg, #f0f7ff 0%, #ffffff 45%, #e0f2fe 100%);
    }
    .block-container {
        padding-top: 2.25rem;
        padding-bottom: 3rem;
        max-width: 900px;
    }
    h1 {
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 0.15rem !important;
        color: #0369A1;
    }
    h2, h3 {
        font-weight: 600;
        letter-spacing: -0.01em;
        color: #0369A1;
    }
    p, .stCaption, [data-testid="stCaptionContainer"] {
        color: #64748B;
        line-height: 1.6;
    }

    /* ---- Segmented top-level navigation ---------------------------------- */
    div[role="radiogroup"][aria-label="__nav__"] {
        display: flex;
        gap: 0.35rem;
        background: #F0F7FF;
        padding: 0.3rem;
        border-radius: 14px;
        margin-bottom: 1.5rem;
        border: 1px solid #DBEAFE;
    }
    div[role="radiogroup"][aria-label="__nav__"] label {
        flex: 1;
        justify-content: center;
        border-radius: 10px;
        padding: 0.45rem 0.75rem !important;
        margin: 0 !important;
        transition: background 0.15s ease, color 0.15s ease;
        font-weight: 500;
    }
    div[role="radiogroup"][aria-label="__nav__"] label:has(input:checked) {
        background: #FFFFFF;
        box-shadow: 0 1px 3px rgba(2, 132, 199, 0.18);
    }
    div[role="radiogroup"][aria-label="__nav__"] label div[data-testid="stMarkdownContainer"] p {
        font-size: 0.95rem;
    }
    div[role="radiogroup"][aria-label="__nav__"] input {
        display: none;
    }

    /* ---- Platform sub-tabs (Existing use cases) --------------------------- */
    div[role="radiogroup"][aria-label="__platform_tabs__"] {
        display: flex;
        gap: 1.25rem;
        border-bottom: 1px solid #DBEAFE;
        margin-bottom: 0.75rem;
    }
    div[role="radiogroup"][aria-label="__platform_tabs__"] label {
        margin: 0 !important;
        padding: 0 0 0.6rem 0 !important;
        border-bottom: 2px solid transparent;
        font-weight: 500;
        color: #64748B;
        transition: color 0.15s ease, border-color 0.15s ease;
    }
    div[role="radiogroup"][aria-label="__platform_tabs__"] label:has(input:checked) {
        color: #1E3A5F;
        border-bottom-color: #0284C7;
    }
    div[role="radiogroup"][aria-label="__platform_tabs__"] input {
        display: none;
    }

    /* ---- Cards / containers ------------------------------------------------ */
    /* radius/shadow match the report's --radius (14px) and --shadow
       (0 4px 24px rgba(2,132,199,.08)) — same "card" language as .verdict
       and details.panel there. */
    div[data-testid="stExpander"] {
        border: 1px solid #DBEAFE !important;
        border-radius: 14px !important;
        background: #FFFFFF;
        box-shadow: 0 4px 24px rgba(2, 132, 199, 0.08);
        margin-bottom: 0.6rem;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 600;
        padding: 0.65rem 0.9rem !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important;
    }

    /* ---- Badges (Custom / Seed) ------------------------------------------- */
    .badge {
        display: inline-block;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        padding: 0.12rem 0.55rem;
        border-radius: 999px;
        text-transform: uppercase;
        vertical-align: middle;
    }
    .badge-custom {
        background: #E0F2FE;
        color: #0369A1;
    }
    .badge-seed {
        background: #F1F5F9;
        color: #64748B;
    }

    /* ---- Section headers --------------------------------------------------- */
    .section-eyebrow {
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #0369A1;
        margin-bottom: 0.15rem;
    }

    /* ---- Buttons -------------------------------------------------------------- */
    /* Pill-shaped, like the report's .back-link/.theme-toggle chips. */
    .stButton button {
        border-radius: 999px;
        font-weight: 600;
    }

    /* ---- Previous-report links (green monospace, like a clickable file) ----- */
    /* Tertiary buttons are only used in the "Previous reports" list, so styling
       them here doesn't touch the primary/secondary buttons elsewhere. The
       `kind` and `data-testid` selectors cover both older and newer Streamlit
       button markup. */
    .stButton button[kind="tertiary"],
    .stButton button[data-testid="stBaseButton-tertiary"] {
        color: #0369A1 !important;
        font-family: "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace !important;
        font-weight: 600;
        display: flex !important;
        justify-content: flex-start !important;
        text-align: left !important;
        padding-left: 0 !important;
    }
    /* Force every layer of the label (the flex child div, the markdown
       container, and the <p>/<span> inside it) hard left, so the run name
       sits at the row's left edge instead of floating in the centre. The
       universal selector covers whatever wrapper Streamlit's version uses. */
    .stButton button[kind="tertiary"] > div,
    .stButton button[data-testid="stBaseButton-tertiary"] > div {
        width: 100%;
        justify-content: flex-start !important;
    }
    .stButton button[kind="tertiary"] *,
    .stButton button[data-testid="stBaseButton-tertiary"] * {
        text-align: left !important;
        margin-left: 0 !important;
        margin-right: auto !important;
    }
    .stButton button[kind="tertiary"]:hover,
    .stButton button[data-testid="stBaseButton-tertiary"]:hover {
        color: #0284C7 !important;
        text-decoration: underline;
    }

    /* ---- History: per-run meta (use-case count + finish time) --------------- */
    .history-meta {
        display: flex;
        justify-content: flex-end;
        align-items: center;
        gap: 12px;
    }
    .uc-chip {
        background: #E0F2FE;
        color: #0369A1;
        border: 1px solid #93C5FD;
        border-radius: 999px;
        padding: 2px 11px;
        font-size: 0.78rem;
        font-weight: 600;
        white-space: nowrap;
    }
    .history-time {
        color: #64748B;
        font-size: 0.85rem;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
    }
    .history-sep {
        border: none;
        border-top: 1px solid #DBEAFE;
        margin: 0.4rem 0 !important;
    }

    /* ---- Divider breathing room --------------------------------------------- */
    hr {
        margin: 1.75rem 0 !important;
        border-color: #DBEAFE !important;
    }
</style>
"""


def _inject_base_styles() -> None:
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)


def _badge_html(is_editable: bool) -> str:
    if is_editable:
        return '<span class="badge badge-custom">Custom</span>'
    return '<span class="badge badge-seed">Seed · read-only</span>'


def _restore_scroll_position() -> None:
    """
    Best-effort scroll-position memory across reruns.

    Streamlit's rerun mechanism repaints the app in place rather than doing a
    full browser navigation, but it still resets viewport scroll to the top
    each time. This keeps the last known scroll offset in the browser's
    sessionStorage and re-applies it a moment after each rerun completes, so
    clicking a button deep in the "Existing use cases" list doesn't bounce the
    user back to the page title.
    """
    st.components.v1.html(
        """
        <script>
        (function() {
            const KEY = "usecase_builder_scroll_y";
            const doc = window.parent.document;
            const scroller = doc.scrollingElement || doc.documentElement;

            const saved = window.parent.sessionStorage.getItem(KEY);
            if (saved !== null) {
                setTimeout(() => scroller.scrollTo(0, parseInt(saved, 10)), 60);
            }
            window.parent.addEventListener("scroll", () => {
                window.parent.sessionStorage.setItem(KEY, scroller.scrollTop);
            }, { passive: true });
        })();
        </script>
        """,
        height=0,
    )


def _apps_for_platform(platform: str) -> list[str]:
    """Apps under data/application whose extension is valid for this platform."""
    extensions = _PLATFORM_APP_EXTENSIONS.get(platform, ())
    return [app for app in repo.list_available_apps() if app.lower().endswith(extensions)]


def _selected_map() -> dict[str, dict]:
    """
    id -> {"contract": UseCaseContract, "catalog_platform": str} for every use
    case currently selected for this run.

    catalog_platform is the *catalog's* platform tag ("common"/"ios"/"android"),
    not the contract's own platform field. Those two can disagree: every seed
    "common" use case still has to declare a concrete platform inside its file
    to satisfy the schema (there's no "common" option there), so relying on
    contract.platform here would wrongly treat a "common" pick as e.g. "ios"
    and block Android from being selected alongside it.
    """
    return st.session_state.setdefault("selected_use_cases", {})


def _session_id() -> str:
    """
    A stable id for this browser session, created once and reused for every
    rerun of it.

    This is what makes a saved run selection map onto "one session" instead
    of "one click of the Save button": every save this session ever performs
    is written to run_repository.py's storage keyed by this same id, so
    saving again always overwrites the same file rather than creating a new
    one alongside it.
    """
    return st.session_state.setdefault("session_id", uuid.uuid4().hex)


def _selected_concrete_platforms(exclude_id: Optional[str] = None) -> set[str]:
    """The distinct non-'common' platforms currently represented in the selection."""
    return {
        info["catalog_platform"]
        for entry_id, info in _selected_map().items()
        if info["catalog_platform"] != "common" and entry_id != exclude_id
    }


def _platform_conflict(catalog_platform: str, entry_id: str) -> Optional[str]:
    """
    None if adding/updating entry_id with this catalog_platform is fine,
    otherwise a human-readable reason it's blocked.

    Rule: 'common' is always compatible with anything. Two concrete platforms
    (ios + android) can never coexist in one run's selection.
    """
    if catalog_platform == "common":
        return None
    conflicting = _selected_concrete_platforms(exclude_id=entry_id) - {catalog_platform}
    if conflicting:
        other = next(iter(conflicting))
        return (
            f"You already have a '{other}' use case selected for this run — "
            f"remove it first to add a '{catalog_platform}' one."
        )
    return None


def _stamp_run_platform(contract: UseCaseContract, run_platform: str) -> UseCaseContract:
    """
    Tag a resolved contract with the concrete platform ('ios'/'android') the
    user had chosen in the platform selector when they picked it.

    contract.platform stays whatever it already was ('common' included) —
    it drives schema validation (e.g. which answer_policy sub-object is
    required) and must not be overwritten. run_platform is a plain extra
    field (UseCaseContract allows them) that survives model_dump()/JSON
    round-trips, so it's still there once the workflow reads this use case
    back from disk — even for a run made up of 'common' use cases only,
    where contract.platform alone would just say "common" and never reveal
    which concrete platform the run was actually for.
    """
    return contract.model_copy(update={"run_platform": run_platform})


def credentials_section(context_key: str) -> tuple[str, str]:
    """
    Render the App ID / Dev Key inputs shared by the create and 'use existing'
    flows. These are per-user credentials, not part of a reusable use case
    template, so they are always collected here rather than trusted from a
    stored file. Pre-filling from session_state means the user only has to
    type them once per session, not once per use case.
    """
    col1, col2 = st.columns(2)
    with col1:
        app_id = st.text_input(
            "App ID *",
            value=st.session_state.get("session_app_id", ""),
            key=f"{context_key}_app_id",
        )
    with col2:
        dev_key = st.text_input(
            "Dev Key *",
            value=st.session_state.get("session_dev_key", ""),
            type="password",
            key=f"{context_key}_dev_key",
        )
    # Sync back into the shared session keys so the *next* place this section
    # is rendered (a different tab/entry) starts pre-filled with this value.
    st.session_state["session_app_id"] = app_id
    st.session_state["session_dev_key"] = dev_key
    return app_id.strip(), dev_key.strip()


def _flash(kind: str, message: str) -> None:
    """
    Queue a message to render right after the *next* rerun.

    Needed because st.rerun() immediately abandons the current script pass —
    calling st.success() and then st.rerun() in the same handler would render
    the message and instantly discard it before anyone sees it.
    """
    st.session_state["_flash"] = (kind, message)


def _show_flash() -> None:
    """Show and clear a message queued by _flash(), if any."""
    flash = st.session_state.pop("_flash", None)
    if flash:
        kind, message = flash
        getattr(st, kind)(message)


def _display_validation_errors(exc: ValidationError) -> None:
    """Turn a Pydantic ValidationError into readable per-field error messages."""
    st.error("This use case is not valid:")
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"]) or "(top level)"
        st.error(f"- **{field}**: {err['msg']}")


def _streamlit_home_url() -> str:
    """
    Absolute URL of this Streamlit entry page, with ?goto=history so landing
    back here scrolls straight to the "Previous reports" section. Shared with
    the report templates (build_report.STREAMLIT_HOME_URL) so the in-report
    back link and the injected one below always point to the same place.
    """
    try:
        from data.reports.build_report import STREAMLIT_HOME_URL

        return STREAMLIT_HOME_URL
    except Exception:  # noqa: BLE001 - fall back to the well-known local default
        return "http://localhost:8501/?goto=history"


# Matches any in-report "🏠 Back to use cases" anchor (as emitted by the report
# templates), so it can be stripped before injecting the single uniform bar —
# leaving any "← All use cases" (index) link untouched.
_IN_REPORT_HOME_LINK_RE = re.compile(r"<a\b[^>]*>[^<]*Back to use cases[^<]*</a>", re.IGNORECASE)


def _report_date_from_path(report_path: Path) -> Optional[str]:
    """The data/reports/<YYYY-MM-DD>/ folder name in a report's path, if any."""
    for part in report_path.parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            return part
    return None


def _inject_back_bar(html: str, report_path: Path, report_date: Optional[str] = None) -> str:
    """
    Give every report the exact same "🏠 Back to use cases" bar.

    To keep the design uniform across all reports (old and new), any back link
    the report already baked in is stripped first, then one identical sticky
    bar is injected at the very top. The bar's link carries ?goto=history (via
    _streamlit_home_url) so returning lands on the entry form scrolled to the
    Previous reports section rather than at the top; &open=<date> is appended
    so the specific date folder this report lives in re-opens on arrival, with
    every other folder left collapsed.

    Relative links inside the report (an index page's per-use-case card links,
    a detail report's "../index.html") are rewritten to absolute file:// URLs
    against the report's real directory, so they keep working even though the
    modified copy is opened from a temp folder.
    """
    # Remove the report's own home link(s) so there's never two competing,
    # differently-styled "back" affordances — only the uniform bar below.
    html = _IN_REPORT_HOME_LINK_RE.sub("", html)

    # Rewrite relative *.html links (e.g. an index page's per-use-case card
    # links, or a detail report's "../index.html") to absolute file:// URLs
    # against the report's real directory. A <base href> wouldn't be enough:
    # the index page's theme JS recomputes each link with
    # `new URL(href, window.location.href)`, which resolves against the temp
    # copy's location (ignoring <base>) and would point at a non-existent
    # /tmp/... path. Absolute URLs are immune to that.
    def _absolutize(match: "re.Match[str]") -> str:
        rel = match.group(1)
        try:
            return f'href="{(report_path.parent / rel).resolve().as_uri()}"'
        except Exception:  # noqa: BLE001 - leave anything odd untouched
            return match.group(0)

    html = re.sub(r'href="(?!https?:|file:|#|mailto:)([^"]+\.html)"', _absolutize, html)

    home_url = _streamlit_home_url()
    report_date = report_date or _report_date_from_path(report_path)
    if report_date:
        home_url = f"{home_url}&open={report_date}"

    back_bar = (
        '<div style="position:sticky;top:0;z-index:99999;background:#0369a1;'
        'padding:11px 20px;box-shadow:0 1px 6px rgba(0,0,0,.18);'
        'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;">'
        f'<a href="{home_url}" '
        'style="color:#fff;text-decoration:none;font-weight:600;font-size:0.95rem;'
        'display:inline-flex;align-items:center;gap:8px;">'
        '🏠 Back to use cases</a>'
        '</div>'
    )

    # Drop the bar in right after the opening <body> tag so it's the first
    # thing on the page and stays pinned to the top while scrolling.
    match = re.search(r"<body[^>]*>", html)
    if match:
        insert_at = match.end()
        html = html[:insert_at] + back_bar + html[insert_at:]
    else:
        html = back_bar + html
    return html


def _open_report_in_browser(report_path: str, report_date: Optional[str] = None) -> None:
    """
    Open a finished report as its own full-page browser tab.

    The report is a standalone HTML file on disk. Opening it through a file://
    URL gives the user the full-width report in a brand-new tab, instead of
    squeezing it into an embedded iframe inside this Streamlit page. Streamlit
    runs locally here (server == the user's own machine), so file:// resolves
    to exactly the report that visual_report_node just wrote.

    Every report is opened through the same path: a uniform "🏠 Back to use
    cases" bar is injected (see _inject_back_bar) and a temp copy is opened, so
    old and new reports alike present an identical way back to the entry form —
    which lands scrolled to the Previous reports history, with this report's own
    date folder re-opened (report_date, else inferred from the path).

    Best-effort only: if anything goes wrong it falls back to opening the
    original file, and if even that fails it silently no-ops so a browser
    launch can never crash the app.
    """
    try:
        path = Path(report_path).resolve()
        html = path.read_text(encoding="utf-8")
        html = _inject_back_bar(html, path, report_date)
        tmp_path = Path(tempfile.gettempdir()) / f"af_report_{uuid.uuid4().hex}.html"
        tmp_path.write_text(html, encoding="utf-8")
        webbrowser.open_new_tab(tmp_path.as_uri())
    except Exception:  # noqa: BLE001 - opening a browser must never crash the app
        try:
            webbrowser.open_new_tab(Path(report_path).resolve().as_uri())
        except Exception:  # noqa: BLE001
            pass


def _scroll_to_history() -> None:
    """
    Scroll the main page to the "Previous reports" history heading.

    Triggered when the user arrives via a report's "Back to use cases" link
    (which carries ?goto=history). Finds the heading by its text rather than an
    id, since Streamlit's markdown sanitizer can strip injected ids. Retries a
    few times because the heading may not be painted the instant this runs.
    """
    st.components.v1.html(
        """
        <script>
        (function () {
            const doc = window.parent.document;
            function findHeading() {
                const headings = doc.querySelectorAll("h1, h2, h3");
                for (const h of headings) {
                    if (h.textContent && h.textContent.indexOf("Previous reports") !== -1) {
                        return h;
                    }
                }
                return null;
            }
            // Keep trying until the heading is painted, then scroll to it a few
            // more times over ~1s so late layout shifts (expanders rendering,
            // etc.) can't leave the page parked back at the top.
            function run(attempt) {
                const h = findHeading();
                if (h) {
                    h.scrollIntoView({ behavior: "smooth", block: "start" });
                    if (attempt < 6) { setTimeout(() => run(attempt + 1), 180); }
                } else if (attempt < 40) {
                    setTimeout(() => run(attempt + 1), 120);
                }
            }
            setTimeout(() => run(0), 150);
        })();
        </script>
        """,
        height=0,
    )


# All generated reports live under data/reports/<YYYY-MM-DD>/<run_id>/, written
# by data/reports/build_report.py at the end of every run.
_REPORTS_DIR = _PROJECT_ROOT / "data" / "reports"


def _list_previous_reports(max_reports: int = 30) -> list[dict]:
    """
    Every previously generated run report on disk, newest first.

    Scans data/reports/<date>/<run_id>/ and, for each run, picks the page to
    open: the run's index.html (the multi-use-case overview) when present,
    otherwise its single report.html. The "exact time the run happened" is
    taken from that file's last-modified time — the report is written at the
    very end of the run, so its mtime is effectively the run's finish time.

    Capped at max_reports so a long history never floods the page; the most
    recent runs are the ones anyone actually wants to reopen.
    """
    if not _REPORTS_DIR.is_dir():
        return []

    reports: list[dict] = []
    for date_dir in _REPORTS_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for run_dir in date_dir.iterdir():
            if not run_dir.is_dir():
                continue
            # index.html (multi-use-case overview) is the preferred entry
            # point; fall back to a lone report.html for single-state runs.
            entry = run_dir / "index.html"
            if not entry.is_file():
                entry = run_dir / "report.html"
            if not entry.is_file():
                continue

            # How many use cases this run covered. A multi-use-case run has one
            # sub-folder per use case (each with its own report.html) alongside
            # the index.html; a single report.html at the run root is just one.
            if entry.name == "index.html":
                use_case_count = sum(
                    1
                    for child in run_dir.iterdir()
                    if child.is_dir() and (child / "report.html").is_file()
                )
                use_case_count = use_case_count or 1
            else:
                use_case_count = 1

            mtime = entry.stat().st_mtime
            reports.append(
                {
                    "run_id": run_dir.name,
                    "date": date_dir.name,
                    "entry_path": str(entry.resolve()),
                    "when": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    # Time only — the date is already the folder heading it sits
                    # under, so the row itself just needs the clock time.
                    "time": datetime.fromtimestamp(mtime).strftime("%H:%M:%S"),
                    "use_case_count": use_case_count,
                    "_sort": mtime,
                }
            )

    reports.sort(key=lambda r: r["_sort"], reverse=True)
    return reports[:max_reports]


def render_use_case_form(
    *,
    editing_entry: Optional[repo.CatalogEntry] = None,
    prefill: Optional[UseCaseContract] = None,
) -> None:
    """
    Render the use case form. Used for both creating a brand-new custom use
    case (editing_entry=None) and editing an existing custom one (editing_entry
    + prefill set) — one implementation so the two flows can never drift apart.
    """
    is_edit = editing_entry is not None
    form_prefix = f"edit_{editing_entry.id}" if is_edit else "create"

    if not is_edit:
        st.markdown('<div class="section-eyebrow">New use case</div>', unsafe_allow_html=True)
        st.subheader("Define the scenario")

    # Platform lives outside st.form() on purpose: widgets inside a form only
    # trigger a rerun when the form is submitted, so if platform were inside
    # the form, switching it wouldn't immediately reveal the iOS/Android
    # fields below — it would only update after the next submit.
    default_platform = prefill.platform if prefill else "ios"
    platform = st.selectbox(
        "Platform *",
        PLATFORMS,
        index=PLATFORMS.index(default_platform),
        key=f"{form_prefix}_platform",
    )

    with st.form(f"{form_prefix}_form", clear_on_submit=not is_edit):
        name = None
        if not is_edit:
            # Only asked for on creation — it seeds the generated id/filename.
            # An existing custom use case keeps the id it was created with.
            name = st.text_input("Use case name * (used to generate its id/filename)")

        # Filtered by the platform selected above, so it's impossible to pick an
        # app whose extension the schema would reject for that platform (e.g. an
        # .apk while "ios" is selected).
        available_apps = _apps_for_platform(platform)
        if available_apps:
            default_app_path = prefill.app_path if prefill else available_apps[0]
            default_index = (
                available_apps.index(default_app_path)
                if default_app_path in available_apps
                else 0
            )
            # A selectbox, not a text input: app_path must always be one of the
            # apps that actually exist under data/application, never free text.
            app_path = st.selectbox("App (from data/application) *", available_apps, index=default_index)
        else:
            app_path = None
            st.warning(
                f"No compatible apps found under data/application for platform '{platform}'. "
                "Add one there before creating a use case."
            )

        prompt_goal = st.text_area(
            "Prompt goal *",
            value=(prefill.prompt_goal if prefill else ""),
            height=150,
            placeholder="Anything else you want the agent to do...",
        )
        installation_agent_summary = st.text_area(
            "Installation agent summary *",
            value=(prefill.installation_agent_summary if prefill else ""),
            height=80,
            placeholder="e.g. SDK integrated via CocoaPods, ATT and deep linking configured, app builds and launches cleanly.",
            help=(
                "A short recap of what the installation agent already did to this app "
                "(SDK integration, config, deep link setup, etc.) before this test runs. "
                "The answer agent uses it as context so it knows what's already in place "
                "instead of guessing from scratch."
            ),
        )
        default_llm = prefill.llm_model if prefill else DEFAULT_LLM_MODEL
        llm_model = st.selectbox(
            "LLM model this workflow should run on *",
            LLM_MODEL_OPTIONS,
            index=LLM_MODEL_OPTIONS.index(default_llm),
        )

        # --- Platform-required policy block ---------------------------------
        # The schema requires ios_minimal for iOS and android for Android, and
        # forbids the other one — so the fields shown here must always match
        # whichever platform is currently selected above.
        st.divider()
        st.markdown("**Required platform policy**")
        existing_ios = prefill.answer_policy.ios_minimal if prefill else None
        existing_android = prefill.answer_policy.android if prefill else None
        if platform == "ios":
            col1, col2 = st.columns(2)
            with col1:
                use_att = st.checkbox("Use ATT", value=existing_ios.use_att if existing_ios else True)
                use_scene_delegate = st.checkbox(
                    "Use Scene Delegate",
                    value=existing_ios.use_scene_delegate if existing_ios else True,
                )
            with col2:
                use_cuid = st.checkbox("Use CUID", value=existing_ios.use_cuid if existing_ios else False)
                use_response_listener = st.checkbox(
                    "Use Response Listener",
                    value=existing_ios.use_response_listener if existing_ios else True,
                )
            android_device_id, android_has_sha256, android_sha256 = None, False, None
        else:
            android_device_id = st.text_input(
                "Device ID", value=(existing_android.device_id or "") if existing_android else ""
            )
            android_has_sha256 = st.checkbox(
                "Has SHA256 fingerprint",
                value=existing_android.has_sha256 if existing_android else False,
            )
            android_sha256 = (
                st.text_input(
                    "SHA256 fingerprint",
                    value=(existing_android.sha256_fingerprint or "") if existing_android else "",
                )
                if android_has_sha256
                else None
            )
            use_att = use_cuid = use_scene_delegate = use_response_listener = None

        # --- Optional policy blocks ------------------------------------------
        # These stay entirely absent (None) unless explicitly turned on, so a
        # use case that doesn't care about deep links/in-app events doesn't
        # carry irrelevant empty structures.
        st.divider()
        st.markdown("**Optional policies**")
        existing_deeplink = prefill.answer_policy.deeplink if prefill else None
        enable_deeplink = st.checkbox(
            "Enable deep link policy",
            value=bool(existing_deeplink and existing_deeplink.use_deep_linking),
        )
        if enable_deeplink:
            onelink_url = st.text_input(
                "Onelink URL", value=(existing_deeplink.onelink_url or "") if existing_deeplink else ""
            )
            url_identifier = st.text_input(
                "URL identifier",
                value=(existing_deeplink.url_identifier or "") if existing_deeplink else "",
            )
            uri_scheme = st.text_input(
                "URI scheme (optional)",
                value=(existing_deeplink.uri_scheme or "") if existing_deeplink else "",
            )
            use_custom_uri_scheme = st.checkbox(
                "Use custom URI scheme",
                value=bool(existing_deeplink and existing_deeplink.use_custom_uri_scheme),
            )
        else:
            onelink_url = url_identifier = uri_scheme = None
            use_custom_uri_scheme = False

        existing_event = prefill.answer_policy.in_app_event if prefill else None
        enable_in_app_event = st.checkbox(
            "Enable in-app event policy",
            value=bool(existing_event and existing_event.inapp_event_method != "none"),
        )
        if enable_in_app_event:
            default_method = (
                existing_event.inapp_event_method
                if existing_event and existing_event.inapp_event_method in IN_APP_EVENT_METHODS
                else IN_APP_EVENT_METHODS[0]
            )
            inapp_event_method = st.selectbox(
                "In-app event method",
                IN_APP_EVENT_METHODS,
                index=IN_APP_EVENT_METHODS.index(default_method),
            )
            event_name = st.text_input(
                "Event name", value=(existing_event.event_name or "") if existing_event else ""
            )
        else:
            inapp_event_method = "none"
            event_name = None

        existing_integration_policy = prefill.answer_policy.integration_policy if prefill else None
        integration_policy = st.text_area(
            "Integration Policy",
            value=existing_integration_policy or "",
            height=100,
            placeholder=(
                "e.g., Specify required SDK versions, initialization constraints, "
                "or authentication rules..."
            ),
            help=(
                "Any important notes or requirements regarding the SDK integration."
            ),
        )
        existing_app_event_policy = prefill.answer_policy.app_event_policy if prefill else None
        app_event_policy = st.text_area(
            "AppEvent Policy",
            value=existing_app_event_policy or "",
            height=100,
            placeholder=(
                "e.g., Define custom parameters to track, triggers, or specific "
                "naming conventions..."
            ),
            help=(
                "Any specific requests or configurations concerning the AppEvents."
            ),
        )

        existing_verify = prefill.answer_policy.verify_sdk if prefill else None
        verify_logs_ready = st.checkbox(
            "Verify logs ready", value=existing_verify.verify_logs_ready if existing_verify else True
        )
        app_launched = st.checkbox(
            "App launched", value=existing_verify.app_launched if existing_verify else True
        )

        # Credentials are only collected here on creation. Editing only
        # touches the test-scenario fields above; whatever credentials the
        # use case already carries are left untouched (see the patch below).
        app_id, dev_key = None, None
        if not is_edit:
            st.divider()
            st.markdown(
                "**Your credentials** — kept for this session only, never assumed from the stored file"
            )
            app_id, dev_key = credentials_section(form_prefix)

        st.write("")
        submitted = st.form_submit_button(
            "Save changes" if is_edit else "Create use case", use_container_width=True, type="primary"
        )

    if not submitted:
        return

    if not is_edit and not (name or "").strip():
        st.error("Use case name is required.")
        return
    if not app_path:
        st.error("No app selected — add a file under data/application first.")
        return
    if not is_edit and (not app_id or not dev_key):
        st.error("App ID and Dev Key are required.")
        return

    # Every with_*() call below constructs a Pydantic policy sub-model
    # immediately (e.g. DeepLinkPolicy validates onelink_url/url_identifier
    # the moment with_deeplink() runs) — validation is not deferred until
    # build(). So the whole assembly has to be inside this try block, not
    # just the final build() call, or a bad optional field would crash the
    # app instead of showing a clean error message.
    try:
        builder = (
            UseCaseBuilder()
            .with_core(
                app_path=app_path,
                platform=platform,
                prompt_goal=prompt_goal,
                installation_agent_summary=installation_agent_summary,
                app_id=(app_id if not is_edit else None),
                dev_key=(dev_key if not is_edit else None),
            )
            .with_llm_model(llm_model)
            .with_verify_sdk(verify_logs_ready=verify_logs_ready, app_launched=app_launched)
            .with_integration_policy(integration_policy.strip() or None)
            .with_app_event_policy(app_event_policy.strip() or None)
        )
        if platform == "ios":
            builder = builder.with_ios_minimal(
                use_att=use_att,
                use_cuid=use_cuid,
                use_scene_delegate=use_scene_delegate,
                use_response_listener=use_response_listener,
            )
        else:
            builder = builder.with_android(
                device_id=android_device_id or None,
                has_sha256=android_has_sha256,
                sha256_fingerprint=android_sha256 or None,
            )
        if enable_deeplink:
            builder = builder.with_deeplink(
                use_deep_linking=True,
                onelink_url=onelink_url or None,
                url_identifier=url_identifier or None,
                uri_scheme=uri_scheme or None,
                use_custom_uri_scheme=use_custom_uri_scheme,
            )
        if enable_in_app_event:
            builder = builder.with_in_app_event(method=inapp_event_method, event_name=event_name or None)

        contract = builder.build()
    except ValidationError as exc:
        _display_validation_errors(exc)
        return

    if is_edit:
        # Carry the original (already-encoded) credentials forward unchanged —
        # editing the test scenario should never silently wipe or fabricate them.
        data = json.loads(contract.to_pretty_json())
        data["app_id"] = prefill.app_id
        data["dev_key"] = prefill.dev_key
        contract = UseCaseContract.model_validate(data)
        entry = repo.update_custom_use_case(editing_entry.id, contract)
        st.session_state["editing_use_case_id"] = None
        # The edited platform/content may no longer match whatever was
        # previously resolved into the run selection under this id — drop it
        # rather than leave a stale entry; the user can re-choose it fresh.
        _selected_map().pop(entry.id, None)
        _flash("success", f"'{entry.id}' updated.")
    else:
        entry = repo.save_custom_use_case(contract, name=name)
        conflict = _platform_conflict(contract.platform, entry.id)
        if conflict:
            _flash(
                "warning",
                f"Use case '{entry.id}' created and saved, but not added to this "
                f"run's selection: {conflict}",
            )
        else:
            _selected_map()[entry.id] = {"contract": contract, "catalog_platform": contract.platform}
            _flash("success", f"Use case '{entry.id}' created and added to this run's selection.")
    st.rerun()


def _render_use_case_entry(entry: repo.CatalogEntry, *, run_platform: str) -> None:
    """
    Render one catalog entry: its content, and the actions available for it.

    run_platform is the concrete platform ('ios'/'android') currently chosen
    in render_existing_tab's selector — every entry shown there, common ones
    included, is stamped with it once selected (see _stamp_run_platform).
    """
    with st.container(border=True):
        try:
            contract = repo.load_use_case(entry)
        except ValidationError as exc:
            st.markdown(f"**{entry.id}**", unsafe_allow_html=True)
            st.error("This use case file is invalid and cannot be loaded.")
            _display_validation_errors(exc)
            return
        except FileNotFoundError:
            st.markdown(f"**{entry.id}**", unsafe_allow_html=True)
            st.error(
                f"This use case is listed in the catalog but its file ({entry.path}) "
                "is missing on disk."
            )
            if entry.is_editable and st.button(
                "🗑️ Remove broken entry", key=f"remove_missing_{entry.id}"
            ):
                repo.delete_custom_use_case(entry.id)
                _flash("success", f"Removed broken catalog entry '{entry.id}'.")
                st.rerun()
            return

        is_editing = st.session_state.get("editing_use_case_id") == entry.id

        # Per-entry toggle state for the panels below — each button just
        # flips a flag; nothing renders until its panel section is reached
        # further down, so the row of buttons stays the only thing visible
        # by default instead of everything (JSON + credentials form) at once.
        preview_key = f"preview_open_{entry.id}"
        choose_key = f"choose_open_{entry.id}"
        confirm_delete_key = f"confirm_delete_open_{entry.id}"

        # Keeping the expander open whenever one of its inner panels is
        # active (or it's mid-edit) is what stops the "everything collapses
        # and the page jumps back" feeling after every click.
        should_expand = (
            is_editing
            or st.session_state.get(preview_key, False)
            or st.session_state.get(choose_key, False)
            or st.session_state.get(confirm_delete_key, False)
        )

        header = f"{entry.id}  ·  {entry.type}"
        with st.expander(header, expanded=should_expand):
            st.markdown(_badge_html(entry.is_editable), unsafe_allow_html=True)
            st.write("")

            if is_editing:
                render_use_case_form(editing_entry=entry, prefill=contract)
                if st.button("Cancel edit", key=f"cancel_edit_{entry.id}"):
                    st.session_state["editing_use_case_id"] = None
                    st.rerun()
                return

            # Seed use cases are the shared, known-good baseline — never editable
            # or deletable, so they only ever get Preview/Choose. Custom ones get
            # all four actions.
            if entry.is_editable:
                col_preview, col_edit, col_delete, col_choose = st.columns(4)
            else:
                col_preview, col_choose = st.columns(2)
                col_edit = col_delete = None

            with col_preview:
                if st.button("👁️ Preview", key=f"preview_btn_{entry.id}", use_container_width=True):
                    st.session_state[preview_key] = not st.session_state.get(preview_key, False)

            if col_edit is not None:
                with col_edit:
                    if st.button("✏️ Edit", key=f"edit_{entry.id}", use_container_width=True):
                        st.session_state["editing_use_case_id"] = entry.id
                        st.rerun()

            if col_delete is not None:
                with col_delete:
                    if st.button("🗑️ Delete", key=f"delete_btn_{entry.id}", use_container_width=True):
                        st.session_state[confirm_delete_key] = not st.session_state.get(
                            confirm_delete_key, False
                        )

            # 'common' use cases are always compatible; concrete ios/android ones
            # can only join a selection that doesn't already contain the other
            # concrete platform. Already-selected entries are exempt from their
            # own check (re-resolving/re-choosing something already in the set
            # never conflicts with itself).
            is_selected = entry.id in _selected_map()
            blocked_reason = None if is_selected else _platform_conflict(entry.platform, entry.id)

            with col_choose:
                choose_label = "✓ Selected" if is_selected else "Choose"
                if st.button(
                    choose_label,
                    key=f"choose_btn_{entry.id}",
                    use_container_width=True,
                    disabled=bool(blocked_reason),
                    type="primary" if is_selected else "secondary",
                ):
                    st.session_state[choose_key] = not st.session_state.get(choose_key, False)

            if blocked_reason:
                st.caption(f"🚫 {blocked_reason}")

            # --- Delete confirmation panel (custom only) -------------------------
            if entry.is_editable and st.session_state.get(confirm_delete_key):
                st.write("")
                st.warning(f"Delete '{entry.id}'? This cannot be undone.")
                col_confirm, col_cancel = st.columns(2)
                with col_confirm:
                    if st.button("Yes, delete it", key=f"confirm_delete_yes_{entry.id}", use_container_width=True):
                        repo.delete_custom_use_case(entry.id)
                        _selected_map().pop(entry.id, None)
                        st.session_state[confirm_delete_key] = False
                        _flash("success", f"Deleted '{entry.id}'.")
                        st.rerun()
                with col_cancel:
                    if st.button("Cancel", key=f"confirm_delete_no_{entry.id}", use_container_width=True):
                        st.session_state[confirm_delete_key] = False
                        st.rerun()

            # --- Preview panel -----------------------------------------------------
            if st.session_state.get(preview_key):
                st.write("")
                st.json(json.loads(contract.to_pretty_json()))

            # --- Choose panel (credentials required for both seed and custom) ------
            if st.session_state.get(choose_key):
                # Defensive re-check: the selection could have changed (via another
                # entry rendered earlier in this same script pass) since the button
                # above was drawn.
                conflict = _platform_conflict(entry.platform, entry.id)
                if conflict:
                    st.write("")
                    st.error(conflict)
                elif is_selected:
                    # Show a confirmation in place instead of collapsing the panel
                    # right after a successful selection — snapping should_expand
                    # back to False here (by clearing choose_key) collapsed this
                    # whole card the instant it succeeded, which reads as the page
                    # abruptly jumping/flashing and looks like nothing happened.
                    # Leaving it open and swapping in this confirmation keeps the
                    # result visible until the user deliberately closes it.
                    st.write("")
                    st.success(f"✓ '{entry.id}' is selected for this run.")
                    if st.button("Close", key=f"close_choose_{entry.id}", use_container_width=True):
                        st.session_state[choose_key] = False
                        st.rerun()
                else:
                    st.write("")
                    st.markdown("**Use this use case** — requires your credentials for this run")
                    with st.form(f"use_form_{entry.id}"):
                        use_app_id, use_dev_key = credentials_section(f"use_{entry.id}")
                        use_submitted = st.form_submit_button(
                            "Resolve & select for this run", use_container_width=True, type="primary"
                        )
                    if use_submitted:
                        if not use_app_id or not use_dev_key:
                            st.error("App ID and Dev Key are required.")
                        else:
                            # Overlays this run's credentials without ever writing
                            # them back into the shared seed/custom file on disk.
                            resolved = repo.resolve_for_run(contract, app_id=use_app_id, dev_key=use_dev_key)
                            resolved = _stamp_run_platform(resolved, run_platform)
                            _selected_map()[entry.id] = {
                                "contract": resolved,
                                "catalog_platform": entry.platform,
                            }
                            # choose_key deliberately stays True so should_expand
                            # keeps this card open on the rerun below — it'll now
                            # take the is_selected branch above and show the
                            # confirmation instead of collapsing away.
                            _flash("success", f"'{entry.id}' added to this run's selection.")
                            st.rerun()


def render_existing_tab() -> None:
    """
    Browse catalog entries for one platform at a time.

    A platform must be chosen first — there is no way to browse or pick a
    use case before that. Once chosen, the list shown is that platform's own
    use cases plus every 'common' one, combined — 'common' use cases never
    have a tab of their own, precisely so one can't be selected in isolation
    without a platform attached to it (see _stamp_run_platform).
    """
    all_entries = repo.list_use_cases(enabled_only=False)
    if not all_entries:
        st.info("No use cases found yet.")
        return

    platform_labels = {"ios": "iOS", "android": "Android"}
    concrete_platforms = [p for p in PLATFORMS if any(e.platform == p for e in all_entries)]
    if not concrete_platforms:
        st.info("No use cases found yet.")
        return

    # A session-state-backed selector (rather than st.tabs) for the same
    # reason the top-level nav above uses one: st.tabs always snaps back to
    # its first tab on any rerun that wasn't itself a tab click — and every
    # Preview/Edit/Delete/Choose button inside a use case card triggers
    # exactly that kind of rerun. With native tabs, choosing e.g. an Android
    # use case would immediately bounce the view back to iOS.
    state_key = "existing_tab_platform"
    if st.session_state.get(state_key) not in concrete_platforms:
        st.session_state[state_key] = concrete_platforms[0]

    st.markdown("**1. Choose a platform**")
    selected_platform = st.radio(
        "__platform_tabs__",
        concrete_platforms,
        format_func=lambda p: platform_labels[p],
        key=state_key,
        horizontal=True,
        label_visibility="collapsed",
    )

    entries = [e for e in all_entries if e.platform in (selected_platform, "common")]
    st.caption(
        f"**2. Pick a use case** — showing {platform_labels[selected_platform]} use cases "
        "plus every common one, all tagged for this run as "
        f"**{platform_labels[selected_platform]}**."
    )

    st.write("")
    for entry in entries:
        _render_use_case_entry(entry, run_platform=selected_platform)


def _render_run_status_banner(running: bool) -> None:
    """Status banner shown while the workflow is running — no controls, just a heads-up."""
    if running:
        st.warning("⏳ Workflow running — the rest of the page is locked until it finishes.")


st.set_page_config(page_title="Use Case Builder", page_icon="🧪", layout="centered")
_inject_base_styles()

# A report's "🏠 Back to use cases" link returns here with ?goto=history —
# meaning "land on the form, but scrolled to the Previous reports section".
# Capture that intent into session_state and clear the query param so it fires
# exactly once (later reruns from button clicks won't keep re-scrolling), then
# skip the normal scroll-position restore for this run so the history scroll
# below wins instead of being fought by the restored offset.
if st.query_params.get("goto") == "history":
    st.session_state["_scroll_to_history"] = True
    # &open=<date> tells us which history folder to re-open on arrival — the
    # date folder of the report just returned from. Absent it, all folders
    # stay collapsed.
    open_date = st.query_params.get("open")
    if open_date:
        st.session_state["_history_open_date"] = open_date
    st.query_params.clear()

if not st.session_state.get("_scroll_to_history"):
    _restore_scroll_position()

st.title("🧪 Use Case Builder")
st.caption("Create a new test use case, or reuse an existing one, for the AppsFlyer SDK automation pipeline.")
_show_flash()

# Drives the whole page's lock state while a run is actually executing.
# Read once here so both the status banner and every section below agree
# on it for this rerun.
locked = st.session_state.setdefault("workflow_running", False)
_render_run_status_banner(locked)

# A session-state-backed selector (rather than st.tabs) so the active
# section survives reruns triggered by buttons deeper in the page — with
# native st.tabs, every rerun snaps back to the first tab.
NAV_OPTIONS = ["➕ Create new use case", "📂 Existing use cases"]
active_section = st.radio(
    "__nav__",
    NAV_OPTIONS,
    key="active_nav_section",
    horizontal=True,
    label_visibility="collapsed",
    disabled=locked,
)

if locked:
    st.info("🔒 The builder is locked while the workflow is running.")
elif active_section == NAV_OPTIONS[0]:
    render_use_case_form()
else:
    render_existing_tab()

selected_map = _selected_map()
if selected_map:
    st.divider()
    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">This run</div>', unsafe_allow_html=True)
        st.subheader(f"Selected use cases ({len(selected_map)})")

        for use_case_id, info in list(selected_map.items()):
            contract: UseCaseContract = info["contract"]
            catalog_platform = info["catalog_platform"]

            # A short stamp instead of the full contract — platform + goal is
            # enough to confirm "yes, this is one I picked" without repeating
            # everything already shown while browsing/creating it. A 'common'
            # use case also shows the concrete platform it was tagged with
            # (run_platform), since 'common' alone doesn't say which one.
            run_platform = getattr(contract, "run_platform", None)
            platform_label = (
                f"{catalog_platform.upper()}→{run_platform.upper()}"
                if catalog_platform == "common" and run_platform
                else catalog_platform.upper()
            )
            stamp = f"[{platform_label}] {use_case_id} — {contract.prompt_goal}"
            if len(stamp) > 90:
                stamp = stamp[:87] + "..."

            col_stamp, col_preview, col_remove = st.columns([6, 2, 2])
            with col_stamp:
                st.caption(stamp)
            with col_preview:
                preview_key = f"preview_selected_{use_case_id}"
                if st.button(
                    "👁️ Preview",
                    key=f"preview_selected_btn_{use_case_id}",
                    use_container_width=True,
                    disabled=locked,
                ):
                    st.session_state[preview_key] = not st.session_state.get(preview_key, False)
            with col_remove:
                if st.button(
                    "✖ Remove",
                    key=f"remove_selected_{use_case_id}",
                    use_container_width=True,
                    disabled=locked,
                ):
                    selected_map.pop(use_case_id, None)
                    st.rerun()

            if st.session_state.get(f"preview_selected_{use_case_id}"):
                st.json(json.loads(contract.to_pretty_json()))

        st.write("")

        if not locked:
            if st.button("🚀 Save and run tests", use_container_width=True, type="primary"):
                session_id = _session_id()
                try:
                    run_repo.save_selected_use_cases(session_id, selected_map)
                except run_repo.RunRepositoryError as exc:
                    _flash("error", str(exc))
                else:
                    st.session_state["workflow_running"] = True
                    st.session_state["_pending_session_id"] = session_id
                st.rerun()
        else:
            from infra.workflow import run_launcher

            session_id = st.session_state.get("_pending_session_id")
            # try/finally (not just try/except) here on purpose: if session_id
            # is somehow missing, or anything below the actual start_workflow()
            # call raises (e.g. while reading the result), the page must still
            # unlock and rerun — otherwise workflow_running stays True forever
            # and the app looks permanently stuck with no report ever shown.
            try:
                if not session_id:
                    _flash("error", "No pending run found to start — please save the selection again.")
                else:
                    with st.spinner("Running the workflow..."):
                        try:
                            final_state = run_launcher.start_workflow(session_id)
                        except run_repo.RunRepositoryError as exc:
                            _flash("error", str(exc))
                        except Exception as exc:  # noqa: BLE001 - surface any node failure to the user
                            st.session_state["_last_workflow_error"] = str(exc)
                            _flash("error", f"Workflow failed: {exc}")
                        else:
                            st.session_state["_last_workflow_result"] = final_state
                            # report_path is captured regardless of pass/fail —
                            # visual_report_node always writes it — so the report
                            # can still be opened even on a failed run.
                            report_path = final_state.get("report_path") or ""
                            st.session_state["_last_report_path"] = report_path
                            # Open the finished report right away in its own full
                            # browser tab (not embedded in this page) the moment
                            # the run ends. The selection form on this page stays
                            # put, so the user can go back and run other use cases.
                            if report_path and Path(report_path).is_file():
                                _open_report_in_browser(report_path)
                            if final_state.get("test_status") == "FAIL":
                                reason = (
                                    final_state.get("fail_reason")
                                    or final_state.get("error_reason")
                                    or "See nodes_log for details."
                                )
                                _flash(
                                    "error",
                                    f"Workflow finished with failures. {reason}",
                                )
                            elif report_path:
                                _flash("success", f"Workflow finished. Report saved to {report_path}.")
                            else:
                                _flash("success", "Workflow finished running.")
            finally:
                # Back to unlocked regardless of outcome — a finished run
                # (pass, fail, or exception) always unlocks the page again.
                st.session_state["workflow_running"] = False
                st.session_state.pop("_pending_session_id", None)
            st.rerun()

        # Hidden entirely (not just disabled) while locked: st.expander has
        # no disabled= option, and the embedded report is a plain HTML
        # iframe Streamlit can't gate at all — not rendering either one is
        # the only way to guarantee nothing here can be pressed or opened
        # while the workflow is running.
        if not locked:
            last_workflow_error = st.session_state.pop("_last_workflow_error", None)
            if last_workflow_error:
                st.divider()
                st.error(
                    "The last run crashed before it could produce a report. Raw error:\n\n"
                    f"```\n{last_workflow_error}\n```"
                )

            last_report_path = st.session_state.get("_last_report_path")
            last_result = st.session_state.get("_last_workflow_result") or {}
            if last_report_path and Path(last_report_path).is_file():
                last_run_id = last_result.get("run_id", "")
                st.divider()
                st.markdown('<div class="section-eyebrow">Run output</div>', unsafe_allow_html=True)
                st.subheader("Pipeline Run Report")
                st.caption(f"Run ID: `{last_run_id}`")
                report_html = Path(last_report_path).read_text(encoding="utf-8")

                # The report is opened automatically in its own full-page browser
                # tab the moment the run finishes (see the run handler above) — it
                # is never embedded in a small box here, and there is deliberately
                # no "open report" button. The report tab itself carries a
                # "🏠 Back to use cases" link back to this page. Only a download
                # option is offered here for keeping a copy.
                st.caption("The report opened in a new browser tab when the run finished.")
                st.download_button(
                    "Download report.html",
                    data=report_html,
                    file_name=f"{last_run_id or 'report'}.html",
                    mime="text/html",
                    use_container_width=True,
                )

            if last_result:
                with st.expander("Last workflow run — nodes_log", expanded=False):
                    st.json(last_result.get("nodes_log", []))

# --- Previous reports --------------------------------------------------------
# Always available, independent of the current selection: lets the user reopen
# the full HTML report of any earlier run straight from this entry page (this
# is where the report's "🏠 Back to use cases" link brings them back to). Each
# run is shown as a green, file-like link that opens the report in a new tab,
# next to the exact time that run finished.
previous_reports = _list_previous_reports()
if previous_reports:
    # Group by the data/reports/<date>/ folder so the history mirrors the
    # on-disk layout: one collapsible folder per date, runs listed inside it —
    # instead of one long undifferentiated row of every run ever.
    reports_by_date: dict[str, list[dict]] = {}
    for rep in previous_reports:
        reports_by_date.setdefault(rep["date"], []).append(rep)
    # Newest date first; the most recent folder starts expanded, the rest
    # collapsed to keep the section compact.
    ordered_dates = sorted(reports_by_date, reverse=True)

    # By default every folder is collapsed. Only the folder recorded in
    # _history_open_date opens — that's set either when the user clicks a run
    # here, or on returning from a report via its "Back to use cases" link
    # (which carries &open=<date>). So the last folder the user engaged with
    # is the one shown open, and a plain fresh visit shows everything closed.
    open_date = st.session_state.get("_history_open_date")

    st.divider()
    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">History</div>', unsafe_allow_html=True)
        st.subheader("Previous reports")
        st.caption("Browse earlier runs by date. Click a run to open its report in a new browser tab.")
        st.write("")

        for date in ordered_dates:
            runs = reports_by_date[date]
            run_word = "run" if len(runs) == 1 else "runs"
            with st.expander(
                f"📁  {date}   ·   {len(runs)} {run_word}",
                expanded=(date == open_date),
            ):
                for row_index, rep in enumerate(runs):
                    # A thin rule between rows (not before the first) gives the
                    # list clean, evenly separated rows instead of floating text.
                    if row_index > 0:
                        st.markdown("<hr class='history-sep'>", unsafe_allow_html=True)

                    # vertical_alignment keeps the green run link and its meta
                    # (use-case count + time) on the same baseline per row.
                    col_run, col_meta = st.columns([6, 4], vertical_alignment="center")
                    with col_run:
                        # A tertiary (link-style) button styled green + monospace
                        # by the CSS above, so it reads like a clickable file
                        # name. Clicking opens that run's report in its own tab.
                        if st.button(
                            f"📄  {rep['run_id']}",
                            key=f"open_prev_report_{rep['date']}_{rep['run_id']}",
                            type="tertiary",
                            use_container_width=True,
                        ):
                            # Remember this folder so it stays open on the next
                            # rerun / when returning from the report.
                            st.session_state["_history_open_date"] = rep["date"]
                            _open_report_in_browser(rep["entry_path"], rep["date"])
                    with col_meta:
                        use_case_count = rep["use_case_count"]
                        uc_word = "use case" if use_case_count == 1 else "use cases"
                        st.markdown(
                            "<div class='history-meta'>"
                            f"<span class='uc-chip'>🧩 {use_case_count} {uc_word}</span>"
                            f"<span class='history-time'>🕒 {rep['time']}</span>"
                            "</div>",
                            unsafe_allow_html=True,
                        )

# Arrived here via a report's "Back to use cases" link (?goto=history handled
# at the top): now that the history is on the page, scroll to it. pop() so it
# only fires this once, not on every following rerun.
if st.session_state.pop("_scroll_to_history", False):
    _scroll_to_history()
