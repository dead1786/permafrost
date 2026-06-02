"""Tests for core/multi_agent.py — PFMultiAgent cross-agent communication."""
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.multi_agent import PFMultiAgent


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_multi_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def alice(data_dir):
    return PFMultiAgent("alice", data_dir=data_dir)


@pytest.fixture
def bob(data_dir):
    return PFMultiAgent("bob", data_dir=data_dir)


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------

class TestInit:
    def test_comms_dir_created(self, alice):
        assert alice.comms_dir.exists()

    def test_inbox_outbox_paths(self, alice):
        assert alice.inbox_file.name == "alice-inbox.json"
        assert alice.outbox_file.name == "alice-outbox.json"

    def test_default_data_dir(self, monkeypatch):
        """Without data_dir arg, falls back to ~/.permafrost."""
        monkeypatch.setenv("HOME", tempfile.mkdtemp())
        agent = PFMultiAgent("test_agent")
        assert agent.data_dir == Path(os.path.expanduser("~/.permafrost"))


# ---------------------------------------------------------------------------
# TestSend
# ---------------------------------------------------------------------------

class TestSend:
    def test_send_creates_target_inbox(self, alice, bob, data_dir):
        alice.send("bob", "hello bob")
        assert bob.inbox_file.exists()

    def test_sent_message_fields(self, alice, bob, data_dir):
        alice.send("bob", "ping")
        msgs = json.loads(bob.inbox_file.read_text(encoding="utf-8"))
        assert len(msgs) == 1
        m = msgs[0]
        assert m["from"] == "alice"
        assert m["to"] == "bob"
        assert m["message"] == "ping"
        assert m["read"] is False
        assert "timestamp" in m

    def test_send_appends_to_existing_inbox(self, alice, bob, data_dir):
        alice.send("bob", "first")
        alice.send("bob", "second")
        msgs = json.loads(bob.inbox_file.read_text(encoding="utf-8"))
        assert len(msgs) == 2

    def test_send_logs_to_outbox(self, alice, bob, data_dir):
        alice.send("bob", "check outbox")
        assert alice.outbox_file.exists()
        outbox = json.loads(alice.outbox_file.read_text(encoding="utf-8"))
        assert len(outbox) == 1
        assert outbox[0]["to"] == "bob"

    def test_outbox_capped_at_200(self, alice, data_dir):
        for i in range(210):
            alice.send("bob", f"msg {i}")
        outbox = json.loads(alice.outbox_file.read_text(encoding="utf-8"))
        assert len(outbox) == 200

    def test_send_handles_corrupt_target_inbox(self, alice, data_dir):
        """If target inbox has corrupt JSON, send still works."""
        target = alice.comms_dir / "bob-inbox.json"
        target.write_text("not-json", encoding="utf-8")
        alice.send("bob", "recover")
        msgs = json.loads(target.read_text(encoding="utf-8"))
        assert len(msgs) == 1

    def test_send_handles_corrupt_own_outbox(self, alice, data_dir):
        """If own outbox has corrupt JSON, send still works."""
        alice.outbox_file.write_text("bad", encoding="utf-8")
        alice.send("bob", "recover outbox")
        outbox = json.loads(alice.outbox_file.read_text(encoding="utf-8"))
        assert len(outbox) == 1


# ---------------------------------------------------------------------------
# TestBroadcast
# ---------------------------------------------------------------------------

class TestBroadcast:
    def test_broadcasts_to_all_except_self(self, data_dir):
        alice = PFMultiAgent("alice", data_dir=data_dir)
        agents = ["alice", "bob", "carol"]
        alice.broadcast(agents, "hello everyone")

        # bob and carol get message; alice does not send to herself
        bob_inbox = alice.comms_dir / "bob-inbox.json"
        carol_inbox = alice.comms_dir / "carol-inbox.json"
        alice_inbox = alice.comms_dir / "alice-inbox.json"

        assert bob_inbox.exists()
        assert carol_inbox.exists()
        assert not alice_inbox.exists()

    def test_broadcast_content(self, data_dir):
        alice = PFMultiAgent("alice", data_dir=data_dir)
        alice.broadcast(["bob"], "broadcast msg")
        bob_inbox = alice.comms_dir / "bob-inbox.json"
        msgs = json.loads(bob_inbox.read_text(encoding="utf-8"))
        assert msgs[0]["message"] == "broadcast msg"

    def test_broadcast_empty_list(self, alice):
        """Should not raise when agent list is empty."""
        alice.broadcast([], "no one home")


# ---------------------------------------------------------------------------
# TestCheckInbox
# ---------------------------------------------------------------------------

class TestCheckInbox:
    def test_returns_empty_when_no_inbox(self, alice):
        assert alice.check_inbox() == []

    def test_returns_unread_messages(self, alice, bob, data_dir):
        alice.send("bob", "read me")
        unread = bob.check_inbox()
        assert len(unread) == 1
        assert unread[0]["message"] == "read me"

    def test_filters_out_read_messages(self, alice, bob, data_dir):
        alice.send("bob", "first")
        bob.mark_read()
        alice.send("bob", "second")
        unread = bob.check_inbox()
        assert len(unread) == 1
        assert unread[0]["message"] == "second"

    def test_returns_empty_on_corrupt_inbox(self, bob):
        bob.inbox_file.parent.mkdir(parents=True, exist_ok=True)
        bob.inbox_file.write_text("not-json", encoding="utf-8")
        assert bob.check_inbox() == []


# ---------------------------------------------------------------------------
# TestMarkRead
# ---------------------------------------------------------------------------

class TestMarkRead:
    def test_marks_all_messages_read(self, alice, bob, data_dir):
        alice.send("bob", "msg1")
        alice.send("bob", "msg2")
        bob.mark_read()
        msgs = json.loads(bob.inbox_file.read_text(encoding="utf-8"))
        assert all(m["read"] for m in msgs)

    def test_noop_when_no_inbox(self, bob):
        bob.mark_read()  # should not raise

    def test_noop_when_inbox_corrupt(self, bob):
        bob.inbox_file.parent.mkdir(parents=True, exist_ok=True)
        bob.inbox_file.write_text("bad", encoding="utf-8")
        bob.mark_read()  # should not raise


# ---------------------------------------------------------------------------
# TestGetConversation
# ---------------------------------------------------------------------------

class TestGetConversation:
    def test_empty_conversation(self, alice, bob):
        assert alice.get_conversation("bob") == []

    def test_includes_sent_messages(self, alice, bob, data_dir):
        alice.send("bob", "hello")
        conv = alice.get_conversation("bob")
        assert any(m["direction"] == "sent" for m in conv)

    def test_includes_received_messages(self, alice, bob, data_dir):
        bob.send("alice", "hey alice")
        conv = alice.get_conversation("bob")
        assert any(m["direction"] == "received" for m in conv)

    def test_conversation_sorted_by_timestamp(self, alice, bob, data_dir):
        alice.send("bob", "first")
        bob.send("alice", "reply")
        alice.send("bob", "followup")
        conv = alice.get_conversation("bob")
        timestamps = [m["timestamp"] for m in conv]
        assert timestamps == sorted(timestamps)

    def test_limit_respected(self, alice, bob, data_dir):
        for i in range(30):
            alice.send("bob", f"msg {i}")
        conv = alice.get_conversation("bob", limit=5)
        assert len(conv) <= 5

    def test_does_not_include_other_agents(self, data_dir):
        alice = PFMultiAgent("alice", data_dir=data_dir)
        carol = PFMultiAgent("carol", data_dir=data_dir)
        alice.send("carol", "not for bob")
        conv = alice.get_conversation("bob")
        assert conv == []

    def test_handles_corrupt_outbox(self, alice, bob, data_dir):
        alice.outbox_file.parent.mkdir(parents=True, exist_ok=True)
        alice.outbox_file.write_text("bad", encoding="utf-8")
        # Should not raise; just return what's available
        conv = alice.get_conversation("bob")
        assert isinstance(conv, list)

    def test_handles_corrupt_inbox(self, alice, bob, data_dir):
        alice.inbox_file.parent.mkdir(parents=True, exist_ok=True)
        alice.inbox_file.write_text("bad", encoding="utf-8")
        conv = alice.get_conversation("bob")
        assert isinstance(conv, list)


# ---------------------------------------------------------------------------
# TestSpawnAgent
# ---------------------------------------------------------------------------

class TestSpawnAgent:
    def test_creates_agent_workspace(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            alice.spawn_agent("worker", persona="You are a worker", task="do stuff")

        agent_dir = alice.data_dir / "agents" / "worker"
        assert agent_dir.exists()
        assert (agent_dir / "agent-status.json").exists()
        assert (agent_dir / "config.json").exists()

    def test_sanitizes_agent_name(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 99
            mock_popen.return_value = mock_proc
            alice.spawn_agent("My Worker Agent")

        agent_dir = alice.data_dir / "agents" / "my_worker_agent"
        assert agent_dir.exists()

    def test_creates_memory_structure(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("mem_worker")

        agent_dir = alice.data_dir / "agents" / "mem_worker"
        for level in ["L1", "L2", "L3", "L4", "L5", "L6"]:
            assert (agent_dir / "memory" / level).exists()

    def test_sends_initial_task_to_inbox(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("task_worker", task="do this")

        comms = alice.data_dir / "agents" / "task_worker" / "comms"
        inbox = comms / "task_worker-inbox.json"
        msgs = json.loads(inbox.read_text(encoding="utf-8"))
        assert msgs[0]["message"] == "do this"

    def test_skips_initial_task_when_none(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("notask_worker")

        comms = alice.data_dir / "agents" / "notask_worker" / "comms"
        inbox = comms / "notask_worker-inbox.json"
        msgs = json.loads(inbox.read_text(encoding="utf-8"))
        assert msgs == []

    def test_returns_pid_on_success(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 5678
            mock_popen.return_value = mock_proc
            result = alice.spawn_agent("pid_worker")
        assert "5678" in result

    def test_returns_error_on_popen_failure(self, alice, data_dir):
        with mock.patch("subprocess.Popen", side_effect=OSError("no launcher")):
            result = alice.spawn_agent("fail_worker")
        assert "failed" in result.lower()

    def test_inherits_main_config(self, alice, data_dir):
        main_config = alice.data_dir / "config.json"
        main_config.write_text(json.dumps({"api_key": "test-key"}), encoding="utf-8")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("config_worker", persona="be helpful")

        cfg = alice.data_dir / "agents" / "config_worker" / "config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["system_prompt"] == "be helpful"
        assert "api_key" in data

    def test_no_task_no_inbox_write(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("empty_task", task="")

        comms = alice.data_dir / "agents" / "empty_task" / "comms"
        inbox = comms / "empty_task-inbox.json"
        msgs = json.loads(inbox.read_text(encoding="utf-8"))
        assert msgs == []


# ---------------------------------------------------------------------------
# TestListAgents
# ---------------------------------------------------------------------------

class TestListAgents:
    def test_empty_when_no_agents(self, alice, data_dir):
        assert alice.list_agents() == []

    def test_lists_created_agents(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("worker_a")
            alice.spawn_agent("worker_b")

        agents = alice.list_agents()
        names = [a["name"] for a in agents]
        assert "worker_a" in names
        assert "worker_b" in names

    def test_handles_dir_without_status_file(self, alice, data_dir):
        agents_dir = alice.data_dir / "agents" / "ghost"
        agents_dir.mkdir(parents=True, exist_ok=True)
        # No status file — should not raise, just include with name only
        agents = alice.list_agents()
        assert isinstance(agents, list)

    def test_handles_corrupt_status_file(self, alice, data_dir):
        agents_dir = alice.data_dir / "agents" / "broken"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "agent-status.json").write_text("not-json", encoding="utf-8")
        agents = alice.list_agents()
        names = [a["name"] for a in agents]
        assert "broken" in names

    def test_includes_data_dir_in_result(self, alice, data_dir):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.pid = 1
            mock_popen.return_value = mock_proc
            alice.spawn_agent("dir_worker")

        agents = alice.list_agents()
        assert all("data_dir" in a for a in agents)


# ---------------------------------------------------------------------------
# TestCreateAgentWorkspace (static)
# ---------------------------------------------------------------------------

class TestCreateAgentWorkspace:
    def test_creates_workspace_dirs(self, data_dir):
        ws = PFMultiAgent.create_agent_workspace("tester", data_dir)
        ws_path = Path(ws)
        assert (ws_path / "memory").exists()
        assert (ws_path / "comms").exists()

    def test_creates_claude_md(self, data_dir):
        ws = PFMultiAgent.create_agent_workspace("tester", data_dir)
        assert (Path(ws) / "CLAUDE.md").exists()

    def test_custom_claude_md_content(self, data_dir):
        content = "# Custom Agent\nDo things."
        ws = PFMultiAgent.create_agent_workspace("custom", data_dir, claude_md_content=content)
        text = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
        assert text == content

    def test_does_not_overwrite_existing_claude_md(self, data_dir):
        # First call writes default
        ws = PFMultiAgent.create_agent_workspace("keeper", data_dir)
        original = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
        # Second call should not overwrite
        PFMultiAgent.create_agent_workspace("keeper", data_dir, claude_md_content="new content")
        after = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
        assert after == original
