from __future__ import annotations

import json
import sys
import uuid
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

from infra.user_interface_use_case.builders.use_case_builder import UseCaseBuilder
from infra.user_interface_use_case.repositories import run_repository as run_repo
from infra.user_interface_use_case.repositories import use_case_repository as repo
from infra.user_interface_use_case.schemas import DEFAULT_LLM_MODEL, LlmModel, UseCaseContract

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
    /* ---- Global spacing & typography ------------------------------------ */
    .block-container {
        padding-top: 2.25rem;
        padding-bottom: 3rem;
        max-width: 900px;
    }
    h1 {
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 0.15rem !important;
    }
    h2, h3 {
        font-weight: 600;
        letter-spacing: -0.01em;
    }
    p, .stCaption, [data-testid="stCaptionContainer"] {
        color: #475569;
    }

    /* ---- Segmented top-level navigation ---------------------------------- */
    div[role="radiogroup"][aria-label="__nav__"] {
        display: flex;
        gap: 0.35rem;
        background: #F1F5F9;
        padding: 0.3rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        border: 1px solid #E2E8F0;
    }
    div[role="radiogroup"][aria-label="__nav__"] label {
        flex: 1;
        justify-content: center;
        border-radius: 9px;
        padding: 0.45rem 0.75rem !important;
        margin: 0 !important;
        transition: background 0.15s ease, color 0.15s ease;
        font-weight: 500;
    }
    div[role="radiogroup"][aria-label="__nav__"] label:has(input:checked) {
        background: #FFFFFF;
        box-shadow: 0 1px 3px rgba(13, 148, 136, 0.18);
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
        border-bottom: 1px solid #E2E8F0;
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
        color: #0F172A;
        border-bottom-color: #0D9488;
    }
    div[role="radiogroup"][aria-label="__platform_tabs__"] input {
        display: none;
    }

    /* ---- Cards / containers ------------------------------------------------ */
    div[data-testid="stExpander"] {
        border: 1px solid #E2E8F0 !important;
        border-radius: 12px !important;
        background: #FFFFFF;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        margin-bottom: 0.6rem;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 500;
        padding: 0.65rem 0.9rem !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
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
        background: #CCFBF1;
        color: #0F766E;
    }
    .badge-seed {
        background: #F1F5F9;
        color: #475569;
    }

    /* ---- Section headers --------------------------------------------------- */
    .section-eyebrow {
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #0D9488;
        margin-bottom: 0.15rem;
    }

    /* ---- Buttons ------------------------------------------------------------ */
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
    }

    /* ---- Divider breathing room --------------------------------------------- */
    hr {
        margin: 1.75rem 0 !important;
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


def _render_use_case_entry(entry: repo.CatalogEntry) -> None:
    """Render one catalog entry: its content, and the actions available for it."""
    with st.container(border=True):
        try:
            contract = repo.load_use_case(entry)
        except ValidationError as exc:
            st.markdown(f"**{entry.id}**", unsafe_allow_html=True)
            st.error("This use case file is invalid and cannot be loaded.")
            _display_validation_errors(exc)
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
    """Browse every catalog entry, grouped by platform."""
    all_entries = repo.list_use_cases(enabled_only=False)
    if not all_entries:
        st.info("No use cases found yet.")
        return

    platform_labels = {"ios": "iOS", "android": "Android", "common": "Common"}
    groups = [
        (platform_group, [e for e in all_entries if e.platform == platform_group])
        for platform_group in ["ios", "android", "common"]
    ]
    groups = [(p, g) for p, g in groups if g]

    if not groups:
        st.info("No use cases found yet.")
        return

    # A session-state-backed selector (rather than st.tabs) for the same
    # reason the top-level nav above uses one: st.tabs always snaps back to
    # its first tab on any rerun that wasn't itself a tab click — and every
    # Preview/Edit/Delete/Choose button inside a use case card triggers
    # exactly that kind of rerun. With native tabs, choosing e.g. an Android
    # use case would immediately bounce the view back to iOS.
    group_by_platform = dict(groups)
    platform_codes = list(group_by_platform.keys())
    group_labels = {p: f"{platform_labels[p]} ({len(g)})" for p, g in groups}

    state_key = "existing_tab_platform"
    if st.session_state.get(state_key) not in platform_codes:
        st.session_state[state_key] = platform_codes[0]

    selected_platform = st.radio(
        "__platform_tabs__",
        platform_codes,
        format_func=lambda p: group_labels[p],
        key=state_key,
        horizontal=True,
        label_visibility="collapsed",
    )

    st.write("")
    for entry in group_by_platform[selected_platform]:
        _render_use_case_entry(entry)


st.set_page_config(page_title="Use Case Builder", page_icon="🧪", layout="centered")
_inject_base_styles()
_restore_scroll_position()

st.title("🧪 Use Case Builder")
st.caption("Create a new test use case, or reuse an existing one, for the AppsFlyer SDK automation pipeline.")
_show_flash()

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
)

if active_section == NAV_OPTIONS[0]:
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
            # everything already shown while browsing/creating it.
            stamp = f"[{catalog_platform.upper()}] {use_case_id} — {contract.prompt_goal}"
            if len(stamp) > 90:
                stamp = stamp[:87] + "..."

            col_stamp, col_preview, col_remove = st.columns([6, 2, 2])
            with col_stamp:
                st.caption(stamp)
            with col_preview:
                preview_key = f"preview_selected_{use_case_id}"
                if st.button("👁️ Preview", key=f"preview_selected_btn_{use_case_id}", use_container_width=True):
                    st.session_state[preview_key] = not st.session_state.get(preview_key, False)
            with col_remove:
                if st.button("✖ Remove", key=f"remove_selected_{use_case_id}", use_container_width=True):
                    selected_map.pop(use_case_id, None)
                    st.rerun()

            if st.session_state.get(f"preview_selected_{use_case_id}"):
                st.json(json.loads(contract.to_pretty_json()))

        st.write("")
        if st.button("💾 Save selection for this run", use_container_width=True, type="primary"):
            try:
                saved = run_repo.save_selected_use_cases(_session_id(), selected_map)
            except run_repo.RunRepositoryError as exc:
                _flash("error", str(exc))
            else:
                relative_path = saved.file_path.relative_to(_PROJECT_ROOT)
                _flash(
                    "success",
                    f"Saved {saved.use_case_count} use case(s) for this session "
                    f"to {relative_path}. Saving again will overwrite this same file.",
                )
            st.rerun()

pending_runs = run_repo.list_pending_run_selections()

# This section is only relevant once the user has actually chosen a use case
# at some point — either it's currently selected, or a save from earlier is
# still sitting on disk waiting to be cleaned up. On a fresh session with
# neither, showing an empty "nothing pending" box is just clutter.
if selected_map or pending_runs:
    st.divider()
    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">Housekeeping</div>', unsafe_allow_html=True)
        st.subheader("Saved run selections pending cleanup")
        st.caption(
            "A saved selection is only supposed to exist for the lifetime of one run — "
            "from being saved above until that run's result has been reported back, at "
            "which point it should be deleted automatically. That automatic step doesn't "
            "exist yet, so use this to clear things out manually in the meantime."
        )
        if not pending_runs:
            st.caption("Nothing pending.")
        else:
            st.write("")
            for pending_run in pending_runs:
                col_info, col_delete = st.columns([4, 1])
                with col_info:
                    st.caption(f"`{pending_run.session_id}` — {pending_run.use_case_count} use case(s)")
                with col_delete:
                    if st.button(
                        "🗑️ Delete",
                        key=f"delete_pending_run_{pending_run.session_id}",
                        use_container_width=True,
                    ):
                        run_repo.delete_run_selection(pending_run.session_id)
                        _flash("success", f"Deleted saved selection for session '{pending_run.session_id}'.")
                        st.rerun()