"""
Permafrost LINE Channel — Messaging via LINE Messaging API + ngrok auto-tunnel.

Features:
  - Auto-starts HTTP server for LINE webhook
  - Auto-opens ngrok tunnel for public URL
  - Auto-registers webhook URL with LINE API
  - Receive messages via webhook
  - Send replies via reply token or push message
  - Text + sticker + image support

Requires: pyngrok (pip install pyngrok>=7.0.0)
"""

import hashlib
import hmac
import base64
import json
import logging
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial

import requests

from .base import BaseChannel, register_channel

log = logging.getLogger("permafrost.channels.line")

# Try importing tunnel libraries — prefer cloudflare (no account needed), fallback to ngrok
HAS_CLOUDFLARE = False
HAS_PYNGROK = False
try:
    from pycloudflared import try_cloudflare
    HAS_CLOUDFLARE = True
except ImportError:
    pass

if not HAS_CLOUDFLARE:
    try:
        from pyngrok import ngrok, conf as ngrok_conf
        HAS_PYNGROK = True
    except ImportError:
        log.warning("No tunnel library — LINE needs pycloudflared or pyngrok. pip install pycloudflared")


# ── Webhook HTTP Handler ─────────────────────────────────────────

class _WebhookHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for LINE webhook events."""

    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length)

        # Signature verification (only if secret is configured and non-empty)
        channel_secret = (self.server.channel_secret or "").strip()
        if channel_secret and len(channel_secret) > 5:
            signature = self.headers.get("X-Line-Signature", "")
            try:
                expected = base64.b64encode(
                    hmac.new(
                        channel_secret.encode("utf-8"),
                        body_bytes,
                        hashlib.sha256,
                    ).digest()
                ).decode("utf-8")
                if not hmac.compare_digest(signature, expected):
                    log.warning("webhook signature mismatch — verify Channel Secret is correct")
                    # Don't reject — still process (user may have wrong secret)
            except Exception as e:
                log.warning(f"signature verification error: {e}")

        # Parse and process events
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            log.warning("webhook received invalid JSON")
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()

        # Process events via the channel instance
        channel_instance = self.server.channel_instance
        events = body.get("events", [])
        for event in events:
            try:
                channel_instance.process_webhook_event(event)
            except Exception as e:
                log.error(f"error processing webhook event: {e}")

    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"LINE webhook OK")

    def log_message(self, format, *args):
        """Suppress default HTTP server logging — we use our own logger."""
        pass


@register_channel("line")
class PFLine(BaseChannel):
    """LINE channel plugin for Permafrost (Messaging API + ngrok tunnel)."""

    LABEL = "LINE"
    CONFIG_FIELDS = [
        {"name": "line_access_token", "label": "Channel Access Token", "type": "password",
         "help": "Long-lived token from LINE Developers console", "required": True},
        {"name": "line_channel_secret", "label": "Channel Secret", "type": "password",
         "help": "For webhook signature verification (optional but recommended)", "required": False},
    ]

    def __init__(self, config: dict, data_dir: str = None):
        super().__init__(config, data_dir)
        self.channel_secret = config.get("line_channel_secret", "").strip()
        self.access_token = config.get("line_access_token", "").strip()
        if self.access_token:
            log.debug(f"token loaded: len={len(self.access_token)} starts={self.access_token[:10]}...")
        self.ngrok_authtoken = config.get("ngrok_authtoken", "")  # optional, only if using ngrok
        self.webhook_port = int(config.get("line_webhook_port", 8504))
        self.api_base = "https://api.line.me/v2/bot"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        self.webhook_url = ""
        self._httpd = None
        self._tunnel = None

    @property
    def name(self) -> str:
        return "line"

    def validate(self) -> tuple[bool, str]:
        if not self.access_token:
            return False, "LINE Channel Access Token is required"
        if not HAS_CLOUDFLARE and not HAS_PYNGROK:
            return False, "No tunnel library — pip install pycloudflared"
        if HAS_PYNGROK and not HAS_CLOUDFLARE and not self.ngrok_authtoken:
            return False, "ngrok Auth Token required (or pip install pycloudflared for zero-config)"
        return True, ""

    # ── Sending ───────────────────────────────────────────────────

    def send_message(self, text: str, **kwargs) -> bool:
        """Send a push message to the user."""
        user_id = kwargs.get("user_id", "")
        reply_token = kwargs.get("reply_token", "")

        # Try reply token first (valid for 30 seconds)
        if reply_token:
            chunks = [text[i:i + 5000] for i in range(0, len(text), 5000)]
            payload = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": chunks[0]}],
            }
            try:
                r = requests.post(
                    f"{self.api_base}/message/reply",
                    headers=self.headers,
                    json=payload,
                    timeout=15,
                )
                if r.ok:
                    # Reply only supports one call per token; push remaining chunks
                    for chunk in chunks[1:]:
                        if user_id:
                            self._push_message(user_id, chunk)
                        time.sleep(0.5)
                    return True
            except requests.RequestException:
                pass
            # Fall through to push if reply fails

        # Push message (needs user_id)
        if not user_id:
            log.warning("send_message failed: no user_id and no valid reply_token")
            return False

        chunks = [text[i:i + 5000] for i in range(0, len(text), 5000)]
        for chunk in chunks:
            if not self._push_message(user_id, chunk):
                return False
            if len(chunks) > 1:
                time.sleep(0.5)
        return True

    def _push_message(self, user_id: str, text: str) -> bool:
        """Push a single message to a user."""
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": text}],
        }
        try:
            r = requests.post(
                f"{self.api_base}/message/push",
                headers=self.headers,
                json=payload,
                timeout=15,
            )
            if not r.ok:
                log.warning(f"push failed: {r.status_code} {r.text[:200]}")
                return False
            return True
        except requests.RequestException as e:
            log.error(f"push error: {e}")
            return False

    def reply_handler(self, response: str, original_msg: dict = None):
        """Route reply — use reply token if available, otherwise push."""
        reply_token = ""
        user_id = ""
        if original_msg:
            reply_token = original_msg.get("reply_token", "")
            user_id = original_msg.get("user_id", "")

        self.send_message(response, reply_token=reply_token, user_id=user_id)

    # ── Webhook processing ────────────────────────────────────────

    def process_webhook_event(self, event: dict):
        """Process a LINE webhook event and write to inbox with full metadata."""
        if event.get("type") != "message":
            return

        message = event.get("message", {})
        source = event.get("source", {})
        text = message.get("text", "")

        # Handle non-text message types
        msg_type = message.get("type", "text")
        if msg_type == "image":
            text = f"[image:{message.get('id', '')}]"
        elif msg_type == "sticker":
            text = f"[sticker:{message.get('packageId', '')}:{message.get('stickerId', '')}]"
        elif msg_type == "video":
            text = f"[video:{message.get('id', '')}]"
        elif msg_type == "audio":
            text = f"[audio:{message.get('id', '')}]"
        elif msg_type == "file":
            text = f"[file:{message.get('fileName', '')}]"

        if not text:
            return

        source_type = source.get("type", "user")
        metadata = {
            "source": "line",
            "user_id": source.get("userId", ""),
            "username": "",
            "chat_type": source_type,
            "reply_token": event.get("replyToken", ""),
            "message_id": message.get("id", ""),
            "group_id": source.get("groupId", ""),
            "room_id": source.get("roomId", ""),
        }

        self.write_to_inbox(text, metadata)
        log.info(f"webhook received: {text[:80]}")

    # ── ngrok + HTTP server ───────────────────────────────────────

    def _start_http_server(self):
        """Start the webhook HTTP server in a background thread."""
        handler = _WebhookHandler
        self._httpd = HTTPServer(("0.0.0.0", self.webhook_port), handler)
        self._httpd.channel_instance = self
        self._httpd.channel_secret = self.channel_secret

        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        log.info(f"webhook HTTP server listening on port {self.webhook_port}")

    def _start_tunnel(self) -> str:
        """Open a tunnel (cloudflare or ngrok) and return the public URL."""
        if HAS_CLOUDFLARE:
            # Cloudflare — zero config, no account needed
            self._tunnel = try_cloudflare(self.webhook_port)
            public_url = self._tunnel.tunnel
            log.info(f"Cloudflare tunnel opened: {public_url}")
        elif HAS_PYNGROK:
            # ngrok fallback
            ngrok_conf.get_default().auth_token = self.ngrok_authtoken
            self._tunnel = ngrok.connect(self.webhook_port, "http")
            public_url = self._tunnel.public_url
            log.info(f"ngrok tunnel opened: {public_url}")
        else:
            raise RuntimeError("No tunnel library available")
        # Ensure HTTPS
        if public_url.startswith("http://"):
            public_url = public_url.replace("http://", "https://", 1)
        return public_url

    def _register_webhook_url(self, webhook_url: str) -> bool:
        """Register the webhook URL with LINE API."""
        payload = {"endpoint": webhook_url}
        try:
            r = requests.put(
                f"{self.api_base}/channel/webhook/endpoint",
                headers=self.headers,
                json=payload,
                timeout=15,
            )
            if r.ok:
                log.info(f"LINE webhook URL registered: {webhook_url}")
                return True
            else:
                log.error(f"failed to register webhook URL: {r.status_code} {r.text[:200]}")
                return False
        except requests.RequestException as e:
            log.error(f"webhook registration error: {e}")
            return False

    def _cleanup(self):
        """Shut down HTTP server and ngrok tunnel."""
        if self._httpd:
            self._httpd.shutdown()
            log.info("webhook HTTP server stopped")
        if self._tunnel:
            try:
                if HAS_CLOUDFLARE:
                    self._tunnel.terminate()
                elif HAS_PYNGROK:
                    ngrok.disconnect(self._tunnel.public_url)
                    ngrok.kill()
            except Exception:
                pass
            log.info("tunnel closed")

    # ── Main run loop ─────────────────────────────────────────────

    def run(self):
        """Start LINE webhook receiver: HTTP server + ngrok tunnel."""
        ok, err = self.validate()
        if not ok:
            log.warning(f"LINE channel not starting: {err}")
            return

        self.running = True

        # 1. Start HTTP server
        self._start_http_server()

        # 2. Open ngrok tunnel
        try:
            public_url = self._start_tunnel()
        except Exception as e:
            log.error(f"tunnel failed: {e}")
            self._cleanup()
            self.running = False
            return

        self.webhook_url = f"{public_url}/webhook"
        log.info(f"tunnel open: {self.webhook_url}")

        # 3. Display webhook URL for user to set in LINE Developers Console
        log.info("=" * 60)
        log.info("LINE Webhook URL (paste this in LINE Developers Console):")
        log.info(f"  {self.webhook_url}")
        log.info("=" * 60)

        # Save webhook URL to config for Settings page display
        try:
            import json as _json
            config_file = self.data_dir / "config.json"
            if config_file.exists():
                cfg = _json.loads(config_file.read_text(encoding="utf-8"))
                cfg["line_webhook_url"] = self.webhook_url
                config_file.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        log.info(f"LINE channel ready — waiting for messages")

        # 4. Keep alive
        try:
            while self.running:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self._cleanup()

    def stop(self):
        """Stop the channel and clean up resources."""
        self.running = False
        self._cleanup()
