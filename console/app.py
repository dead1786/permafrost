"""
Permafrost Console — Web UI for setup, chat, scheduling, and monitoring.
Run: streamlit run app.py --server.port 8503 --server.headless true
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent.parent / "docs" / "logo.png"

# Import registries for dynamic config generation
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.providers import list_providers, _PROVIDERS  # noqa: E402
from core.token_tracker import get_usage_summary, get_today_usage  # noqa: E402
from channels.base import list_channels, _CHANNELS  # noqa: E402
from console.i18n import t, SUPPORTED_LANGUAGES  # noqa: E402
# Trigger registration by importing channel modules
import channels.telegram  # noqa: F401, E402
import channels.discord  # noqa: F401, E402
import channels.web  # noqa: F401, E402
import channels.line  # noqa: F401, E402

st.set_page_config(
    page_title="Permafrost",
    page_icon="\u2744\ufe0f",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_DIR = Path(os.environ.get("PF_DATA_DIR", os.path.expanduser("~/.permafrost")))
CONFIG_FILE = DATA_DIR / "config.json"


def safe_read_json(path: Path, default=None):
    """Read JSON file with BOM/encoding tolerance. Returns default on any error."""
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return default
        return json.loads(text)
    except (json.JSONDecodeError, OSError, TypeError):
        return default

# ── Styles ──
st.markdown("""
<style>
.block-container { max-width: 100% !important; padding: 1rem 2rem !important; }
[data-testid="stToolbar"] { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stSidebar"] { display: none !important; }
.setup-card {
    border: 1px solid #333; border-radius: 12px;
    padding: 24px; margin: 8px 0; background: #1a1a2e;
}
.brand { font-size: 2.5em; font-weight: bold; }
.tagline { color: #888; font-size: 1.1em; margin-bottom: 2rem; }
/* No flash/transition on rerun */
.stApp, .main, section[data-testid="stMain"],
[data-testid="stVerticalBlock"], [data-testid="stChatMessage"] {
    transition: none !important;
    animation: none !important;
}
/* Fixed chat input at bottom */
div[data-testid="stChatInput"] {
    position: fixed !important;
    bottom: 0 !important;
    left: 0 !important;
    right: 0 !important;
    z-index: 9998 !important;
    background: #0e1117 !important;
    padding: 0.5rem 2rem !important;
    border-top: 1px solid #333 !important;
}
/* Add padding at bottom so last message isn't hidden behind fixed input */
section[data-testid="stMain"] > div {
    padding-bottom: 5rem !important;
}
</style>
""", unsafe_allow_html=True)

# Fixed tab bar + auto-scroll chat to bottom
st.markdown("""<style>
/* Fixed tab navigation — immune to scroll/overflow issues */
div[data-baseweb="tab-list"] {
    position: fixed !important;
    top: 0 !important;
    left: 0 !important;
    right: 0 !important;
    z-index: 9999 !important;
    background: #0e1117 !important;
    padding: 0.5rem 2rem !important;
    border-bottom: 1px solid #333 !important;
}
/* Push content below the fixed tab bar */
div[data-testid="stTabs"] > div:last-child {
    padding-top: 3.5rem !important;
}
</style>""", unsafe_allow_html=True)


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def get_lang() -> str:
    """Get current UI language from config."""
    return load_config().get("language", "en")


def is_configured():
    config = load_config()
    return bool(config.get("ai_provider") and config.get("api_key"))


# ── Global language ──
_lang = get_lang()

# ── Navigation ──
if "page" not in st.session_state:
    st.session_state.page = "chat" if is_configured() else "setup"

if is_configured():
    tab_chat, tab_schedule, tab_status, tab_settings = st.tabs([
        f"\U0001f4ac {t('chat', _lang)}",
        f"\U0001f4c5 {t('schedule', _lang)}",
        f"\U0001f4ca {t('status', _lang)}",
        f"\u2699\ufe0f {t('settings', _lang)}",
    ])
else:
    tab_chat = None
    tab_schedule = None
    tab_status = None
    tab_settings = None

page = st.session_state.page
if not is_configured():
    page = "setup"


# ══════════════════════════════════════════
# Setup Wizard (shown when not configured, or via settings tab)
# ══════════════════════════════════════════
def render_setup():
    st.markdown('<div class="brand">\u2744\ufe0f Permafrost</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="tagline">{t("tagline", _lang)}</div>', unsafe_allow_html=True)

    config = load_config()

    # ── Step 1: AI Model (dynamic from provider registry) ──
    st.markdown(f"### {t('step1_model', _lang)}")

    providers = list_providers()
    provider_labels = [p["label"] for p in providers]
    provider_names = [p["name"] for p in providers]

    current = config.get("ai_provider", "claude")
    current_idx = provider_names.index(current) if current in provider_names else 0

    selected_label = st.selectbox(t("ai_provider_label", _lang), provider_labels, index=current_idx)
    selected_idx = provider_labels.index(selected_label)
    provider_info = providers[selected_idx]
    provider = provider_info["name"]

    if provider_info["needs_api_key"]:
        if provider == "ollama":
            api_key = st.text_input(t("ollama_endpoint", _lang),
                                     value=config.get("api_key", "http://localhost:11434"))
        else:
            api_key = st.text_input(f"{provider_info['label']} {t('api_key', _lang)}",
                                     value=config.get("api_key", ""), type="password")
    else:
        api_key = config.get("api_key", "")

    ai_model = st.text_input(t("model_id", _lang),
                              value=config.get("ai_model", provider_info["default_model"]),
                              help=provider_info["model_help"])

    system_prompt = st.text_area(t("system_prompt", _lang),
                                  value=config.get("system_prompt", ""),
                                  help=t("system_prompt_help", _lang))

    # ── Step 2: Channels (dynamic from channel registry) ──
    st.markdown(f"### {t('step2_channels', _lang)}")

    channels_info = list_channels()
    channel_configs = {}

    for ch in channels_info:
        key_enabled = f"{ch['name']}_enabled"
        is_web = ch["name"] == "web"

        with st.expander(f"{'🟢' if config.get(key_enabled, is_web) else '⚪'} {ch['label']}", expanded=config.get(key_enabled, is_web)):
            enabled = st.checkbox(
                f"Enable {ch['label']}", value=config.get(key_enabled, is_web),
                disabled=is_web, key=key_enabled)
            channel_configs[key_enabled] = enabled

            if ch["config_fields"]:
                for field in ch["config_fields"]:
                    field_key = field["name"]
                    if field["type"] == "password":
                        val = st.text_input(field["label"], value=config.get(field_key, ""),
                                            type="password", help=field.get("help", ""),
                                            key=field_key)
                    elif field["type"] == "select":
                        options = field.get("options", [""])
                        curr_val = config.get(field_key, "")
                        idx = options.index(curr_val) if curr_val in options else 0
                        val = st.selectbox(field["label"], options, index=idx,
                                           help=field.get("help", ""), key=field_key)
                    else:
                        val = st.text_input(field["label"], value=str(config.get(field_key, "")),
                                            help=field.get("help", ""), key=field_key)
                    channel_configs[field_key] = val

            # Show LINE webhook URL if available
            if ch["name"] == "line" and config.get("line_webhook_url"):
                st.info(f"**Webhook URL** (paste in LINE Developers Console):\n\n`{config['line_webhook_url']}`")

    # ── Step 3: AI Persona ──
    st.markdown(f"### {t('step3_persona', _lang)}")
    st.caption(t("step3_caption", _lang))

    from smart.persona_wizard import DEFAULT_QUESTIONS, build_system_prompt

    persona_answers = {}
    for q in DEFAULT_QUESTIONS:
        label = q.get("question_zh", q["question"])
        req = " *" if q.get("required") else ""
        val = config.get(f"persona_{q['id']}", "")
        answer = st.text_input(f"{label}{req}", value=val,
                               help=q.get("example", ""), key=f"persona_{q['id']}")
        if answer:
            persona_answers[q["id"]] = answer

    # Show preview if enough answers
    if persona_answers.get("name") and persona_answers.get("role"):
        with st.expander(t("preview_prompt", _lang)):
            preview = build_system_prompt(persona_answers)
            st.code(preview, language=None)

    # Allow manual override
    system_prompt_override = st.text_area(
        t("manual_prompt", _lang),
        value=config.get("system_prompt", ""),
        help=t("manual_prompt_help", _lang))

    # ── Step 4: Security Level ──
    st.markdown(f"### {t('step4_security', _lang)}")

    from core.security import SecurityLevel
    security_options = {
        t("sec_strict", _lang): "strict",
        t("sec_standard", _lang): "standard",
        t("sec_relaxed", _lang): "relaxed",
    }
    current_sec = config.get("security_level", "strict")
    sec_labels = list(security_options.keys())
    sec_values = list(security_options.values())
    sec_idx = sec_values.index(current_sec) if current_sec in sec_values else 0
    selected_sec = st.selectbox(t("security_level", _lang), sec_labels, index=sec_idx,
                                help=t("security_help", _lang))
    security_level = security_options[selected_sec]

    enable_tools = st.checkbox("Enable AI Tools (bash, file ops, web search)",
                               value=config.get("enable_tools", True))

    # ── Step 5: Preferences ──
    st.markdown(f"### {t('step5_preferences', _lang)}")

    # Language selector
    lang_labels = list(SUPPORTED_LANGUAGES.values())
    lang_codes = list(SUPPORTED_LANGUAGES.keys())
    current_lang = config.get("language", "en")
    lang_idx = lang_codes.index(current_lang) if current_lang in lang_codes else 0
    selected_lang_label = st.selectbox(t("language", _lang), lang_labels, index=lang_idx,
                                        help=t("lang_help", _lang))
    selected_lang = lang_codes[lang_labels.index(selected_lang_label)]

    night_start = st.text_input(t("night_start", _lang), value=config.get("night_start", "00:00"))
    night_end = st.text_input(t("night_end", _lang), value=config.get("night_end", "08:00"))

    # ── Save ──
    if st.button(f"\U0001f680 {t('launch', _lang)}", type="primary", use_container_width=True):
        # Validate required fields
        errors = []
        if provider_info["needs_api_key"] and not api_key:
            errors.append(f"{provider_info['label']} {t('api_key', _lang)} {t('is_required', _lang)}")
        if not ai_model:
            errors.append(t("model_required", _lang))

        # Validate channel configs
        for ch in channels_info:
            key_enabled = f"{ch['name']}_enabled"
            if channel_configs.get(key_enabled, False):
                for field in ch.get("config_fields", []):
                    if field.get("required") and not channel_configs.get(field["name"]):
                        errors.append(f"{ch['label']}: {field['label']} {t('is_required', _lang)}")

        if errors:
            for err in errors:
                st.error(err)
        else:
            # Build system prompt: manual override > wizard > empty
            final_prompt = system_prompt_override.strip()
            if not final_prompt and persona_answers.get("name"):
                final_prompt = build_system_prompt(persona_answers)

            # Merge with existing config (preserve fields not shown in form)
            new_config = load_config()
            new_config.update({
                "ai_provider": provider,
                "api_key": api_key,
                "ai_model": ai_model,
                "system_prompt": final_prompt,
                "security_level": security_level,
                "enable_tools": enable_tools,
                "language": selected_lang,
                "night_start": night_start,
                "night_end": night_end,
                "configured_at": datetime.now().isoformat(),
            })
            # Save persona answers for re-editing
            for q_id, ans in persona_answers.items():
                new_config[f"persona_{q_id}"] = ans
            # Update channel configs — preserve existing passwords if form field is empty
            for k, v in channel_configs.items():
                if v:  # has value — always update
                    new_config[k] = v
                elif k.endswith("_enabled"):  # enabled flags — always update
                    new_config[k] = v
                elif not v and k not in new_config:  # new key with no value — set it
                    new_config[k] = v
                # else: empty value + key already exists = keep old value (password not re-displayed)
            save_config(new_config)
            # Write reload trigger so brain picks up new config without restart
            try:
                reload_file = DATA_DIR / "brain-reload.trigger"
                reload_file.write_text(datetime.now().isoformat())
            except OSError:
                pass
            st.success(f"\u2705 {t('config_saved', _lang)}")
            st.balloons()
            st.session_state.page = "chat"
            st.rerun()

    # ── Danger Zone ──
    st.markdown("---")
    st.markdown("#### Danger Zone")
    if st.button("Reset All Data", type="secondary"):
        if st.session_state.get("confirm_reset"):
            import shutil
            shutil.rmtree(DATA_DIR, ignore_errors=True)
            st.success("All data cleared. Restart Permafrost.")
            st.session_state.clear()
            st.rerun()
        else:
            st.session_state.confirm_reset = True
            st.warning("Click again to confirm — this will delete all data, memories, and settings.")
            st.rerun()


# ══════════════════════════════════════════
# Chat
# ══════════════════════════════════════════
def render_chat():
    st.markdown(f"### \U0001f4ac {t('chat_title', _lang)}")

    # Load chat history
    chat_file = DATA_DIR / "chat-history.json"
    if "messages" not in st.session_state:
        loaded = safe_read_json(chat_file)[-50:]
        st.session_state.messages = [
            m for m in loaded
            if not (m.get("role") == "assistant" and m.get("content", "").startswith("[error]"))
        ]

    # Clear chat button
    col1, col2 = st.columns([8, 1])
    with col2:
        if st.button(t("clear", _lang), key="clear_chat"):
            st.session_state.messages = []
            try:
                chat_file.write_text("[]", encoding="utf-8")
            except OSError:
                pass
            st.rerun()

    # Display messages
    for msg in st.session_state.messages:
        avatar = str(LOGO_PATH) if msg["role"] == "assistant" else None
        with st.chat_message(msg["role"], avatar=avatar):
            st.write(msg["content"])

    # Auto-scroll: inject JS via components.html with retry
    import streamlit.components.v1 as _components
    if st.session_state.messages:
        _components.html("""
        <script>
        function scrollToBottom() {
            try {
                var d = window.parent.document;
                var els = d.querySelectorAll('[data-testid="stChatMessage"]');
                if (els.length > 0) {
                    els[els.length - 1].scrollIntoView({block: 'end'});
                } else {
                    d.querySelector('section.main').scrollTop = 999999;
                }
            } catch(e) {}
        }
        setTimeout(scrollToBottom, 100);
        setTimeout(scrollToBottom, 500);
        </script>
        """, height=0)

    # Input
    if prompt := st.chat_input(t("type_message", _lang)):
        # Show user message immediately
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        # Save to chat history
        try:
            chat_file.parent.mkdir(parents=True, exist_ok=True)
            history = st.session_state.messages[-200:]
            chat_file.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

        # Write to inbox for brain to pick up
        inbox_file = DATA_DIR / "web-inbox.json"
        inbox = []
        if inbox_file.exists():
            try:
                inbox = safe_read_json(inbox_file)
            except (json.JSONDecodeError, OSError):
                inbox = []
        inbox.append({
            "text": prompt,
            "source": "web",
            "timestamp": datetime.now().isoformat(),
            "read": False,
        })
        inbox_file.write_text(json.dumps(inbox, ensure_ascii=False, indent=2), encoding="utf-8")

        # Write wake trigger
        wake_file = DATA_DIR / "brain-wake.trigger"
        wake_file.write_text(datetime.now().isoformat())

        # Poll for response from web-outbox.json
        outbox_file = DATA_DIR / "web-outbox.json"
        sent_at = datetime.now().isoformat()
        with st.chat_message("assistant", avatar=str(LOGO_PATH)):
            placeholder = st.empty()
            placeholder.write(f"\u23f3 {t('thinking', _lang)}")
            response = None
            for _ in range(60):  # wait up to 60 seconds
                outbox = safe_read_json(outbox_file)
                # Only read responses newer than when we sent our message
                new_msgs = [m for m in outbox
                            if m.get("timestamp", "") > sent_at and not m.get("read", False)]
                if new_msgs:
                    response = new_msgs[-1]["text"]
                    # Clear outbox
                    try:
                        outbox_file.write_text("[]", encoding="utf-8")
                    except OSError:
                        pass
                    break
                time.sleep(1)

            if response:
                if response.startswith("[error]"):
                    placeholder.write(f"\u26a0\ufe0f {response}")
                else:
                    placeholder.write(response)
                    st.session_state.messages.append({"role": "assistant", "content": response})
                    # Update chat history
                    try:
                        history = st.session_state.messages[-200:]
                        chat_file.write_text(
                            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except OSError:
                        pass
            else:
                placeholder.write(f"\u26a0\ufe0f {t('no_response', _lang)}")
            st.rerun()


# ══════════════════════════════════════════
# Schedule
# ══════════════════════════════════════════
def render_schedule():
    st.markdown(f"### \U0001f4c5 {t('scheduled_tasks', _lang)}")

    schedule_file = DATA_DIR / "schedule.json"
    if schedule_file.exists():
        try:
            schedule = safe_read_json(schedule_file)
            tasks = schedule.get("tasks", schedule) if isinstance(schedule, dict) else schedule
            if isinstance(tasks, list) and tasks:
                for task in tasks:
                    if isinstance(task, dict):
                        enabled = "\U0001f7e2" if task.get("enabled", True) else "\U0001f534"
                        sched = task.get("schedule", {})
                        sched_str = sched.get("cron", sched.get("time", sched.get("type", "")))
                        st.markdown(f"{enabled} **{task.get('id', '?')}** — "
                                    f"{task.get('description', '')[:60]} `{sched_str}`")
            else:
                st.info(t("no_tasks", _lang))
        except (json.JSONDecodeError, OSError):
            st.info(t("no_schedule_file", _lang))
    else:
        st.info(t("no_schedule_file", _lang))

    st.markdown("---")
    st.markdown(f"*{t('task_mgmt_coming', _lang)}*")


# ══════════════════════════════════════════
# Status
# ══════════════════════════════════════════
def render_status():
    # Auto-refresh every 10 seconds
    import time as _time
    if "last_status_refresh" not in st.session_state:
        st.session_state.last_status_refresh = _time.time()
    if _time.time() - st.session_state.last_status_refresh > 10:
        st.session_state.last_status_refresh = _time.time()
        st.rerun()

    st.markdown(f"### \U0001f4ca {t('system_status', _lang)}")

    col1, col2, col3 = st.columns(3)

    # Brain status
    hb_file = DATA_DIR / "brain-heartbeat.json"
    if hb_file.exists():
        try:
            hb = safe_read_json(hb_file, {})
            age = (datetime.now() - datetime.fromisoformat(hb["timestamp"])).total_seconds()
            status = t("online", _lang) if age < 180 else t("offline", _lang)
            provider = hb.get("provider", "?")
            col1.metric(t("brain", _lang), status, f"{age:.0f}{t('seconds_ago', _lang)}")
            col1.caption(f"{t('provider_label', _lang)}: {provider}")
        except (json.JSONDecodeError, KeyError, OSError):
            col1.metric(t("brain", _lang), t("error", _lang), "")
    else:
        col1.metric(t("brain", _lang), t("not_started", _lang), "")

    # Scheduler status
    sh_file = DATA_DIR / "scheduler-heartbeat.json"
    if sh_file.exists():
        try:
            sh = safe_read_json(sh_file, {})
            age = (datetime.now() - datetime.fromisoformat(sh["timestamp"])).total_seconds()
            col2.metric(t("scheduler", _lang),
                        t("online", _lang) if age < 180 else t("offline", _lang),
                        f"{age:.0f}{t('seconds_ago', _lang)}")
        except (json.JSONDecodeError, KeyError, OSError):
            col2.metric(t("scheduler", _lang), t("error", _lang), "")
    else:
        col2.metric(t("scheduler", _lang), t("not_started", _lang), "")

    # Config
    config = load_config()
    col3.metric(t("ai_provider", _lang), config.get("ai_provider", t("not_set", _lang)).title())
    col3.caption(f"{t('model_label', _lang)}: {config.get('ai_model', 'default')}")

    # Channels
    st.markdown(f"#### {t('channels', _lang)}")
    channel_status = []
    for ch_info in list_channels():
        key = f"{ch_info['name']}_enabled"
        if config.get(key, ch_info["name"] == "web"):
            channel_status.append(f"\U0001f7e2 {ch_info['label']}")
        else:
            channel_status.append(f"\u26aa {ch_info['label']}")
    st.write(" | ".join(channel_status))

    # Service controls
    st.markdown(f"#### {t('controls', _lang)}")
    ctrl1, ctrl2 = st.columns(2)
    launcher_path = Path(__file__).resolve().parent.parent / "launcher.py"
    if ctrl1.button(f"\U0001f680 {t('start_brain', _lang)}", use_container_width=True):
        # Remove stop trigger so brain can start cleanly
        stop_trigger = DATA_DIR / "brain-stop.trigger"
        stop_trigger.unlink(missing_ok=True)

        import subprocess
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    [sys.executable, str(launcher_path)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                subprocess.Popen(
                    [sys.executable, str(launcher_path)],
                    start_new_session=True,
                )
            st.success(t("brain_launched", _lang))
        except Exception as e:
            st.error(f"{t('launch_failed', _lang)}: {e}")

    if ctrl2.button(f"\U0001f6d1 {t('stop_brain', _lang)}", use_container_width=True):
        # Write stop trigger for graceful shutdown (brain main loop checks this)
        stop_trigger = DATA_DIR / "brain-stop.trigger"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            stop_trigger.write_text(datetime.now().isoformat())
        except OSError:
            pass

        # Also try PID-based kill as fallback
        pid_file = DATA_DIR / "brain.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if sys.platform == "win32":
                    os.system(f"taskkill /PID {pid} /F >nul 2>&1")
                else:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                pid_file.unlink(missing_ok=True)
                st.success(f"{t('stopped_pid', _lang)} {pid}")
            except Exception as e:
                st.error(f"{t('stop_brain', _lang)}: {e}")
        else:
            st.success(t("stop_signal_sent", _lang))

    # Token Usage
    st.markdown(f"#### {t('token_usage', _lang)}")
    try:
        usage = get_usage_summary()
        today = get_today_usage()

        tu1, tu2, tu3 = st.columns(3)
        tu1.metric(t("today_calls", _lang), today.get("calls", 0))
        tu2.metric(
            t("today_tokens", _lang),
            f"{today.get('prompt', 0) + today.get('completion', 0):,}",
            f"{today.get('prompt', 0):,} {t('prompt', _lang)} / {today.get('completion', 0):,} {t('completion', _lang)}",
        )
        tu3.metric(t("today_cost", _lang), f"${today.get('cost_usd', 0):.4f}")

        tu4, tu5 = st.columns(2)
        total_tok = usage.get("total_prompt_tokens", 0) + usage.get("total_completion_tokens", 0)
        tu4.metric(
            t("total_tokens", _lang),
            f"{total_tok:,}",
            f"{usage.get('total_prompt_tokens', 0):,} {t('prompt', _lang)} / {usage.get('total_completion_tokens', 0):,} {t('completion', _lang)}",
        )
        tu5.metric(t("total_cost", _lang), f"${usage.get('total_cost_usd', 0):.4f}")

        # Daily breakdown (last 7 days)
        daily = usage.get("daily", {})
        if daily:
            with st.expander(t("daily_breakdown", _lang)):
                sorted_days = sorted(daily.keys(), reverse=True)[:7]
                header = f"| {t('date', _lang)} | {t('calls', _lang)} | {t('tokens_p_c', _lang)} | {t('cost', _lang)} |"
                st.markdown(header)
                st.markdown("|---|---|---|---|")
                for day in sorted_days:
                    d = daily[day]
                    st.markdown(
                        f"| {day} | {d.get('calls', 0)} | "
                        f"{d.get('prompt', 0):,} / {d.get('completion', 0):,} | "
                        f"${d.get('cost_usd', 0):.4f} |"
                    )
    except Exception:
        st.caption(t("token_usage_na", _lang))

    # Memory Stats (L1-L6)
    st.markdown(f"#### Memory Layers")
    try:
        from smart.memory import PFMemory
        mem = PFMemory(str(DATA_DIR))
        stats = mem.get_stats()
        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        mc1.metric("L1 Rules", stats.get("L1", 0))
        mc2.metric("L2 Verified", stats.get("L2", 0))
        mc3.metric("L3 Dynamic", stats.get("L3", 0))
        mc4.metric("L4 Monthly", stats.get("L4", 0))
        mc5.metric("L5 Quarterly", stats.get("L5", 0))
        mc6.metric("L6 Annual", stats.get("L6", 0))
    except Exception:
        st.caption("Memory stats unavailable")

    # Recent activity
    st.markdown(f"#### {t('recent_messages', _lang)}")
    msg_log = DATA_DIR / "message-log.json"
    if msg_log.exists():
        try:
            msgs = safe_read_json(msg_log)[-15:]
            for m in reversed(msgs):
                icon = "\U0001f4e5" if m.get("direction") == "in" else "\U0001f4e4"
                ts = m.get("timestamp", "")[:16]
                st.caption(f"{icon} [{m.get('channel', '?')}] {ts} — {m.get('text', '')[:100]}")
        except (json.JSONDecodeError, OSError):
            st.caption(t("msg_log_error", _lang))
    else:
        st.caption(t("no_messages", _lang))


# ══════════════════════════════════════════
# Routing: tabs mode (configured) or direct setup
# ══════════════════════════════════════════
if is_configured():
    with tab_chat:
        render_chat()
    with tab_schedule:
        render_schedule()
    with tab_status:
        render_status()
    with tab_settings:
        render_setup()
else:
    render_setup()
