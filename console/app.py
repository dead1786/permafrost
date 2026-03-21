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

# Import registries for dynamic config generation
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.providers import list_providers, _PROVIDERS  # noqa: E402
from channels.base import list_channels, _CHANNELS  # noqa: E402
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
</style>
""", unsafe_allow_html=True)


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def is_configured():
    config = load_config()
    return bool(config.get("ai_provider") and config.get("api_key"))


# ── Navigation ──
if "page" not in st.session_state:
    st.session_state.page = "chat" if is_configured() else "setup"

if is_configured():
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("\U0001f4ac Chat", use_container_width=True):
        st.session_state.page = "chat"
        st.rerun()
    if c2.button("\U0001f4c5 Schedule", use_container_width=True):
        st.session_state.page = "schedule"
        st.rerun()
    if c3.button("\U0001f4ca Status", use_container_width=True):
        st.session_state.page = "status"
        st.rerun()
    if c4.button("\u2699\ufe0f Settings", use_container_width=True):
        st.session_state.page = "setup"
        st.rerun()

page = st.session_state.page
if not is_configured():
    page = "setup"


# ══════════════════════════════════════════
# Setup Wizard
# ══════════════════════════════════════════
if page == "setup":
    st.markdown('<div class="brand">\u2744\ufe0f Permafrost</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">Turn any AI into a 24/7 autonomous brain.</div>', unsafe_allow_html=True)

    config = load_config()

    # ── Step 1: AI Model (dynamic from provider registry) ──
    st.markdown("### Step 1: Choose your AI model")

    providers = list_providers()
    provider_labels = [p["label"] for p in providers]
    provider_names = [p["name"] for p in providers]

    current = config.get("ai_provider", "claude")
    current_idx = provider_names.index(current) if current in provider_names else 0

    selected_label = st.selectbox("AI Provider", provider_labels, index=current_idx)
    selected_idx = provider_labels.index(selected_label)
    provider_info = providers[selected_idx]
    provider = provider_info["name"]

    if provider_info["needs_api_key"]:
        if provider == "ollama":
            api_key = st.text_input("Ollama endpoint",
                                     value=config.get("api_key", "http://localhost:11434"))
        else:
            api_key = st.text_input(f"{provider_info['label']} API Key",
                                     value=config.get("api_key", ""), type="password")
    else:
        api_key = config.get("api_key", "")

    ai_model = st.text_input("Model ID",
                              value=config.get("ai_model", provider_info["default_model"]),
                              help=provider_info["model_help"])

    system_prompt = st.text_area("System Prompt (optional)",
                                  value=config.get("system_prompt", ""),
                                  help="Instructions for the AI brain. Leave empty for default behavior.")

    # ── Step 2: Channels (dynamic from channel registry) ──
    st.markdown("### Step 2: Connect channels")

    channels_info = list_channels()
    channel_configs = {}

    cols = st.columns(min(len(channels_info), 3))
    for i, ch in enumerate(channels_info):
        col = cols[i % len(cols)]
        with col:
            key_enabled = f"{ch['name']}_enabled"
            is_web = ch["name"] == "web"
            enabled = st.checkbox(ch["label"], value=config.get(key_enabled, is_web),
                                  disabled=is_web)
            channel_configs[key_enabled] = enabled

            if enabled and ch["config_fields"]:
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

    # ── Step 3: AI Persona ──
    st.markdown("### Step 3: Create your AI persona")
    st.caption("Answer a few questions and we'll build a personality for your AI.")

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
        with st.expander("Preview generated system prompt"):
            preview = build_system_prompt(persona_answers)
            st.code(preview, language=None)

    # Allow manual override
    system_prompt_override = st.text_area(
        "Or write your own system prompt (overrides wizard)",
        value=config.get("system_prompt", ""),
        help="Leave empty to use the wizard-generated prompt above.")

    # ── Step 4: Security Level ──
    st.markdown("### Step 4: Security level")

    from core.security import SecurityLevel
    security_options = {
        "Strict (recommended)": "strict",
        "Standard": "standard",
        "Relaxed": "relaxed",
    }
    current_sec = config.get("security_level", "strict")
    sec_labels = list(security_options.keys())
    sec_values = list(security_options.values())
    sec_idx = sec_values.index(current_sec) if current_sec in sec_values else 0
    selected_sec = st.selectbox("Security Level", sec_labels, index=sec_idx,
                                help="Strict: deny by default. Standard: common tools allowed. Relaxed: most allowed.")
    security_level = security_options[selected_sec]

    # ── Step 5: Preferences ──
    st.markdown("### Step 5: Preferences")

    night_start = st.text_input("Night silence start", value=config.get("night_start", "00:00"))
    night_end = st.text_input("Night silence end", value=config.get("night_end", "08:00"))

    # ── Save ──
    if st.button("\U0001f680 Launch Permafrost", type="primary", use_container_width=True):
        # Validate required fields
        errors = []
        if provider_info["needs_api_key"] and not api_key:
            errors.append(f"{provider_info['label']} API Key is required")
        if not ai_model:
            errors.append("Model ID is required")

        # Validate channel configs
        for ch in channels_info:
            key_enabled = f"{ch['name']}_enabled"
            if channel_configs.get(key_enabled, False):
                for field in ch.get("config_fields", []):
                    if field.get("required") and not channel_configs.get(field["name"]):
                        errors.append(f"{ch['label']}: {field['label']} is required")

        if errors:
            for err in errors:
                st.error(err)
        else:
            # Build system prompt: manual override > wizard > empty
            final_prompt = system_prompt_override.strip()
            if not final_prompt and persona_answers.get("name"):
                final_prompt = build_system_prompt(persona_answers)

            new_config = {
                "ai_provider": provider,
                "api_key": api_key,
                "ai_model": ai_model,
                "system_prompt": final_prompt,
                "security_level": security_level,
                "night_start": night_start,
                "night_end": night_end,
                "configured_at": datetime.now().isoformat(),
            }
            # Save persona answers for re-editing
            for q_id, ans in persona_answers.items():
                new_config[f"persona_{q_id}"] = ans
            new_config.update(channel_configs)
            save_config(new_config)
            st.success("\u2705 Configuration saved! Permafrost is starting...")
            st.balloons()
            st.session_state.page = "chat"
            st.rerun()


# ══════════════════════════════════════════
# Chat
# ══════════════════════════════════════════
elif page == "chat":
    st.markdown("### \U0001f4ac Chat")

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
        if st.button("Clear", key="clear_chat"):
            st.session_state.messages = []
            try:
                chat_file.write_text("[]", encoding="utf-8")
            except OSError:
                pass
            st.rerun()

    # Display messages
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # Input
    if prompt := st.chat_input("Type a message..."):
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
        # Clear outbox BEFORE polling to avoid reading stale responses
        try:
            outbox_file.write_text("[]", encoding="utf-8")
        except OSError:
            pass
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.write("\u23f3 Thinking...")
            response = None
            for _ in range(60):  # wait up to 60 seconds
                if outbox_file.exists():
                    try:
                        outbox = safe_read_json(outbox_file)
                        unread = [m for m in outbox if not m.get("read", False)]
                        if unread:
                            response = unread[-1]["text"]
                            # Clear outbox completely to prevent stale messages
                            outbox_file.write_text("[]", encoding="utf-8")
                            break
                    except (json.JSONDecodeError, OSError):
                        pass
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
                placeholder.write("\u26a0\ufe0f No response from brain. Is it running?")
            st.rerun()


# ══════════════════════════════════════════
# Schedule
# ══════════════════════════════════════════
elif page == "schedule":
    st.markdown("### \U0001f4c5 Scheduled Tasks")

    schedule_file = DATA_DIR / "schedule.json"
    if schedule_file.exists():
        try:
            schedule = safe_read_json(schedule_file)
            tasks = schedule.get("tasks", schedule) if isinstance(schedule, dict) else schedule
            if isinstance(tasks, list) and tasks:
                for t in tasks:
                    if isinstance(t, dict):
                        enabled = "\U0001f7e2" if t.get("enabled", True) else "\U0001f534"
                        sched = t.get("schedule", {})
                        sched_str = sched.get("cron", sched.get("time", sched.get("type", "")))
                        st.markdown(f"{enabled} **{t.get('id', '?')}** — "
                                    f"{t.get('description', '')[:60]} `{sched_str}`")
            else:
                st.info("No tasks configured yet.")
        except (json.JSONDecodeError, OSError):
            st.info("No schedule file found.")
    else:
        st.info("No schedule file found. Tasks will appear here once configured.")

    st.markdown("---")
    st.markdown("*Task management UI coming in Phase 2.1*")


# ══════════════════════════════════════════
# Status
# ══════════════════════════════════════════
elif page == "status":
    st.markdown("### \U0001f4ca System Status")

    col1, col2, col3 = st.columns(3)

    # Brain status
    hb_file = DATA_DIR / "brain-heartbeat.json"
    if hb_file.exists():
        try:
            hb = safe_read_json(hb_file, {})
            age = (datetime.now() - datetime.fromisoformat(hb["timestamp"])).total_seconds()
            status = "Online" if age < 180 else "Offline"
            provider = hb.get("provider", "?")
            col1.metric("Brain", status, f"{age:.0f}s ago")
            col1.caption(f"Provider: {provider}")
        except (json.JSONDecodeError, KeyError, OSError):
            col1.metric("Brain", "Error", "")
    else:
        col1.metric("Brain", "Not started", "")

    # Scheduler status
    sh_file = DATA_DIR / "scheduler-heartbeat.json"
    if sh_file.exists():
        try:
            sh = safe_read_json(sh_file, {})
            age = (datetime.now() - datetime.fromisoformat(sh["timestamp"])).total_seconds()
            col2.metric("Scheduler", "Online" if age < 180 else "Offline", f"{age:.0f}s ago")
        except (json.JSONDecodeError, KeyError, OSError):
            col2.metric("Scheduler", "Error", "")
    else:
        col2.metric("Scheduler", "Not started", "")

    # Config
    config = load_config()
    col3.metric("AI Provider", config.get("ai_provider", "Not set").title())
    col3.caption(f"Model: {config.get('ai_model', 'default')}")

    # Channels
    st.markdown("#### Channels")
    channel_status = []
    for ch_info in list_channels():
        key = f"{ch_info['name']}_enabled"
        if config.get(key, ch_info["name"] == "web"):
            channel_status.append(f"\U0001f7e2 {ch_info['label']}")
        else:
            channel_status.append(f"\u26aa {ch_info['label']}")
    st.write(" | ".join(channel_status))

    # Service controls
    st.markdown("#### Controls")
    ctrl1, ctrl2 = st.columns(2)
    launcher_path = Path(__file__).resolve().parent.parent / "launcher.py"
    if ctrl1.button("\U0001f680 Start Brain", use_container_width=True):
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
            st.success("Brain launched! Refresh in a few seconds to see status.")
        except Exception as e:
            st.error(f"Launch failed: {e}")

    if ctrl2.button("\U0001f6d1 Stop Brain", use_container_width=True):
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
                st.success(f"Stopped PID {pid}")
            except Exception as e:
                st.error(f"Stop failed: {e}")
        else:
            st.info("No brain PID file found")

    # Recent activity
    st.markdown("#### Recent Messages")
    msg_log = DATA_DIR / "message-log.json"
    if msg_log.exists():
        try:
            msgs = safe_read_json(msg_log)[-15:]
            for m in reversed(msgs):
                icon = "\U0001f4e5" if m.get("direction") == "in" else "\U0001f4e4"
                ts = m.get("timestamp", "")[:16]
                st.caption(f"{icon} [{m.get('channel', '?')}] {ts} — {m.get('text', '')[:100]}")
        except (json.JSONDecodeError, OSError):
            st.caption("Error reading message log.")
    else:
        st.caption("No messages yet.")
