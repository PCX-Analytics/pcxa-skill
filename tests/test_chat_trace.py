"""`pcxa chat send --trace` and `pcxa chat trace`.

The send asks the server to record the turn's search events and prints the
trace after the answer; the wait polls the light ``status/`` read, not the task
detail that carries every step's tool output; a second send while a turn is
running exits 3 with the running task's id instead of a raw 409.
"""

from types import SimpleNamespace

import pytest

from pcxa._http import HTTPError
from pcxa._main import SUB_HANDLERS
from pcxa._parser import build_parser
from pcxa.commands.chat import cmd_chat_send, cmd_chat_trace
from tests.conftest import FakeResponse

TRACE = {
    "task": {"id": 9, "status": "completed", "model": "m"},
    "timings_ms": {"queue_wait": 1200, "total": 42000},
    "tools": {"search_project_data": {"calls": 2, "total_ms": 3100, "errors": 1}},
    "steps": [{"n": 1, "type": "tool_result", "tool": "search_project_data", "offset_ms": 900,
               "duration_ms": 2100, "output": {"chars": 5000, "results": 10}}],
    "search": {"events": [{"kind": "fts_fallback", "reason": "retry_unavailable", "t_ms": 3000,
                           "detail": "FtsUnavailable cause=ReadTimeoutError status=None"}], "dropped": 0},
    "children": [],
}


class FakeClient:
    def __init__(self, send_error=None):
        self.send_error = send_error
        self.gets = []
        self.posts = []

    def post(self, path, json_data=None, **kw):
        self.posts.append((path, json_data))
        if path == "conversations/current/":
            return {"id": 5}
        if self.send_error:
            raise self.send_error
        return {"message": {"id": 1, "content": "q"}, "agent_task_id": 9}

    def get(self, path, params=None, **kw):
        self.gets.append((path, params))
        if path.endswith("/status/"):
            return {"id": 9, "status": "completed"}
        if path.endswith("/trace/"):
            return TRACE
        if path == "conversations/5/":
            return {"messages": [{"id": 2, "role": "assistant", "content": "answer"}]}
        raise AssertionError(f"unexpected GET {path}")


def send_args(**over):
    base = dict(message="q", new=False, conversation=None, title=None, research=False, model=None,
                page_url=None, no_wait=False, timeout=5, trace=False, format="table")
    base.update(over)
    return SimpleNamespace(**base)


def test_parser_wires_trace():
    args = build_parser().parse_args(["chat", "trace", "9", "--full"])
    assert args.agent_task_id == 9 and args.full
    assert SUB_HANDLERS["chat"]["trace"] is cmd_chat_trace
    assert build_parser().parse_args(["chat", "send", "q", "--trace"]).trace


def test_send_with_trace_asks_for_it_and_prints_the_timeline(capsys):
    client = FakeClient()

    cmd_chat_send(client, send_args(trace=True))

    assert client.posts[-1] == ("conversations/5/send/", {"content": "q", "trace": True})
    assert ("agent-tasks/9/trace/", None) in client.gets
    out = capsys.readouterr().out
    assert "answer" in out
    assert "fts_fallback retry_unavailable" in out
    assert "search_project_data: 2 calls, 3.1s, 1 errors" in out


def test_a_server_without_the_trace_endpoint_still_returns_the_answer(capsys):
    client = FakeClient()
    real_get = client.get

    def get(path, params=None, **kw):
        if path.endswith("/trace/"):
            raise HTTPError(FakeResponse(404, {}, reason="Not Found"))
        return real_get(path, params, **kw)

    client.get = get
    cmd_chat_send(client, send_args(trace=True))

    out = capsys.readouterr().out
    assert "answer" in out
    assert "trace unavailable (HTTP 404)" in out


def test_send_without_trace_reads_no_trace():
    client = FakeClient()

    cmd_chat_send(client, send_args())

    assert "trace" not in client.posts[-1][1]
    assert not any(p.endswith("/trace/") for p, _ in client.gets)


def test_the_wait_polls_the_light_status_read():
    client = FakeClient()

    cmd_chat_send(client, send_args())

    assert client.gets[0][0] == "agent-tasks/9/status/"
    assert ("agent-tasks/9/", None) not in client.gets


def test_a_turn_already_running_exits_3_with_its_task(capsys):
    err = HTTPError(FakeResponse(409, {"code": "turn_in_progress", "agent_task_id": 77}, reason="Conflict"))
    client = FakeClient(send_error=err)

    with pytest.raises(SystemExit) as exc:
        cmd_chat_send(client, send_args())

    assert exc.value.code == 3
    assert "task 77" in capsys.readouterr().err


def test_trace_full_passes_the_flag():
    client = FakeClient()

    cmd_chat_trace(client, SimpleNamespace(agent_task_id=9, full=True, format="json"))

    assert client.gets == [("agent-tasks/9/trace/", {"full": "1"})]
