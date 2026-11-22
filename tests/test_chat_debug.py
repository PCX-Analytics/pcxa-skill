"""`pcxa chat debug` (a session's turns) and the sub-agent, leg and layer lines of `chat trace`."""

from types import SimpleNamespace

from pcxa._main import SUB_HANDLERS
from pcxa._parser import build_parser
from pcxa.commands.chat import cmd_chat_debug, cmd_chat_trace

SESSION = {
    "conversation": {"id": 501, "title": "Overdue submittals"},
    "turns": [
        {
            "task_id": 7001,
            "status": "completed",
            "model": "gpt-5.6-luna",
            "prompt": "Which submittals are overdue?",
            "traced": True,
            "timings_ms": {"queue_wait": 800, "first_text": 180000, "total": 519000},
            "tools": {"read_document": {"calls": 22, "total_ms": 90000, "max_ms": 50100, "errors": 1}},
            "subagents": {"count": 6, "failed": 1, "max_ms": 224000},
            "instruction_layers": {"user_global_present": True, "user_global_chars": 42,
                                   "project_default_title": "Site handbook"},
            "search_summary": {"keyword_engines": {"postgres": 3}, "fallbacks": {"retry_unavailable": 1},
                               "slowest_leg_ms": 24900, "leg_errors": 0},
        },
        {"task_id": 7002, "status": "failed", "error_category": "timeout", "traced": False,
         "timings_ms": {}, "tools": {}, "subagents": {"count": 0}},
    ],
}

TRACE = {
    "task": {"id": 7001, "status": "completed"},
    "timings_ms": {},
    "instruction_layers": {"active_personal_titles": ["Answer briefly"]},
    "tools": {},
    "steps": [],
    "search": {"events": [{"kind": "leg", "leg": "bm25", "ms": 24900, "rows": 0, "error": "QueryTimedOut",
                           "t_ms": 1200, "task": 7003}], "dropped": 0},
    "children": [{"id": 7003, "agent_type": "researcher", "status": "failed", "offset_ms": 4000,
                  "duration_ms": 224000, "error_category": "timeout", "error_message": "deadline",
                  "tools": {"search_files": {"calls": 12, "total_ms": 40000, "max_ms": 24900, "errors": 2}}}],
}


class FakeClient:
    def __init__(self, body):
        self.body = body
        self.gets = []

    def get(self, path, params=None, **kw):
        self.gets.append((path, params))
        return self.body


def test_parser_wires_debug():
    args = build_parser().parse_args(["chat", "debug", "501"])
    assert args.conversation_id == 501
    assert SUB_HANDLERS["chat"]["debug"] is cmd_chat_debug


def test_debug_prints_one_block_per_turn(capsys):
    client = FakeClient(SESSION)

    cmd_chat_debug(client, SimpleNamespace(conversation_id=501, format="table"))

    out = capsys.readouterr().out
    assert client.gets == [("conversations/501/trace/", None)]
    assert "TURN task 7001  completed" in out
    assert "read_document x22 (1 errors)" in out
    assert "sub-agents: 6, 1 failed, longest 224.0s" in out
    assert 'project default "Site handbook"' in out
    assert "fallbacks {'retry_unavailable': 1}" in out
    assert "error: timeout" in out
    assert "searches: not recorded (send with --trace)" in out
    assert "details: pcxa chat trace 7002" in out


def test_trace_shows_sub_agent_tools_leg_errors_and_layers(capsys):
    cmd_chat_trace(FakeClient(TRACE), SimpleNamespace(agent_task_id=7001, full=False, format="table"))

    out = capsys.readouterr().out
    assert 'instructions: personal "Answer briefly"' in out
    assert "leg bm25 24900ms rows=0 ERROR QueryTimedOut [sub-agent 7003]" in out
    assert "SUB-AGENT task 7003 researcher failed +4.0s took 224.0s" in out
    assert "search_files: 12 calls, 40.0s, max 24.9s, 2 errors" in out
