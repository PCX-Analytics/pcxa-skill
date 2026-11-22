"""AI chat conversation commands."""

import json
import sys
import time

from pcxa._http import requests
from pcxa._output import out_json, out_table

CHAT_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "waiting_for_input"}


def _resolve_chat_conversation(client, args):
    """Pick the conversation to use for `chat send` based on args."""
    if getattr(args, "new", False):
        return client.post("conversations/new/", {"title": getattr(args, "title", "") or ""})
    if getattr(args, "conversation", None):
        return client.get(f"conversations/{args.conversation}/")
    return client.post("conversations/current/")


def _wait_for_agent_task(client, task_id, timeout, interval=1.0):
    """Poll the task's light ``status/`` read until terminal or timeout."""
    start = time.time()
    last_status = None
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"status": last_status or "timeout", "_timed_out": True, "_elapsed": elapsed}
        try:
            task = client.get(f"agent-tasks/{task_id}/status/")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                time.sleep(interval)
                continue
            raise
        last_status = task.get("status")
        if last_status in CHAT_TERMINAL_STATUSES:
            task["_elapsed"] = elapsed
            return task
        time.sleep(interval)


def cmd_chat_send(client, args):
    conv = _resolve_chat_conversation(client, args)
    conv_id = conv["id"]

    payload = {"content": args.message}
    if args.research:
        payload["research_mode"] = True
    if args.model:
        payload["model"] = args.model
    if args.page_url:
        payload["page_url"] = args.page_url
    if getattr(args, "trace", False):
        payload["trace"] = True

    try:
        sent = client.post(f"conversations/{conv_id}/send/", payload)
    except requests.HTTPError as e:
        body = _error_body(e)
        if e.response is not None and e.response.status_code == 409 and body.get("agent_task_id"):
            print(
                f"A turn is already running on conversation {conv_id} (task {body['agent_task_id']}). "
                "Wait for it, or send with --new.",
                file=sys.stderr,
            )
            sys.exit(3)
        raise
    user_msg = sent.get("message") or {}
    task_id = sent.get("agent_task_id")

    if args.no_wait:
        out_json({
            "conversation_id": conv_id,
            "agent_task_id": task_id,
            "user_message": user_msg,
            "note": "Response is still processing. Re-run with `pcxa chat get %d` later." % conv_id,
        })
        return

    task = _wait_for_agent_task(client, task_id, args.timeout)
    final_status = task.get("status")
    elapsed = task.get("_elapsed", 0.0)

    detail = client.get(f"conversations/{conv_id}/")
    msgs = detail.get("messages", [])
    user_msg_id = user_msg.get("id")
    assistant_msg = None
    if user_msg_id is not None:
        for m in msgs:
            if m.get("role") == "assistant" and (m.get("id") or 0) > user_msg_id:
                assistant_msg = m
                break

    result = {
        "conversation_id": conv_id,
        "agent_task_id": task_id,
        "agent_task_status": final_status,
        "elapsed_seconds": round(elapsed, 2),
        "timed_out": bool(task.get("_timed_out")),
        "user_message": {"id": user_msg.get("id"), "content": user_msg.get("content")},
        "assistant_message": assistant_msg,
    }
    if getattr(args, "trace", False):
        # The answer is already in hand: a server without the trace endpoint (404)
        # or a non-staff account (403) is reported, not raised.
        try:
            result["trace"] = client.get(f"agent-tasks/{task_id}/trace/")
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            result["trace_error"] = f"trace unavailable (HTTP {status})"

    if args.format == "table":
        print(f"Conversation: {conv_id}  Task: {task_id}  Status: {final_status}  Elapsed: {result['elapsed_seconds']}s")
        if result["timed_out"]:
            print(f"(timed out after {args.timeout}s — task may still be running)")
        print()
        print(f"USER:\n{(user_msg.get('content') or '').strip()}\n")
        if assistant_msg:
            print(f"ASSISTANT:\n{(assistant_msg.get('content') or '').strip()}")
            tools = assistant_msg.get("tool_steps") or []
            thinking = assistant_msg.get("thinking_steps") or []
            cards = assistant_msg.get("action_cards") or []
            meta = []
            if tools:
                meta.append(f"{len(tools)} tool calls")
            if thinking:
                meta.append(f"{len(thinking)} thinking steps")
            if cards:
                meta.append(f"{len(cards)} action cards")
            if meta:
                print("\n[" + ", ".join(meta) + "]")
        else:
            print("(no assistant response yet)")
        if result.get("trace"):
            print()
            _print_trace(result["trace"])
        elif result.get("trace_error"):
            print(f"\n({result['trace_error']})")
    else:
        out_json(result)

    if final_status == "failed":
        sys.exit(2)


def _error_body(err):
    try:
        body = err.response.json() if err.response is not None else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _secs(ms):
    return "-" if ms is None else f"{ms / 1000:.1f}s"


def _print_trace(trace):
    """A turn trace as a readable timeline (``--format table``)."""
    task = trace.get("task") or {}
    t = trace.get("timings_ms") or {}
    print(f"TRACE task {task.get('id')}  {task.get('status')}  model {task.get('model')}")
    if task.get("error_category"):
        print(f"  error: {task['error_category']}: {task.get('error_message') or ''}")
    print(
        f"  queue {_secs(t.get('queue_wait'))} · setup {_secs(t.get('setup'))} · "
        f"first text {_secs(t.get('first_text'))} · total {_secs(t.get('total'))}"
    )
    tools = trace.get("tools") or {}
    if tools:
        print("TOOLS")
        for name, agg in sorted(tools.items(), key=lambda kv: -kv[1].get("total_ms", 0)):
            errors = f", {agg['errors']} errors" if agg.get("errors") else ""
            print(f"  {name}: {agg['calls']} calls, {_secs(agg.get('total_ms'))}{errors}")
    print("STEPS")
    for step in trace.get("steps") or []:
        label = step.get("tool") or step.get("type")
        line = f"  #{step.get('n')} +{_secs(step.get('offset_ms'))} {step.get('type')} {label}"
        if step.get("duration_ms") is not None:
            line += f" {step['duration_ms']}ms"
        out = step.get("output")
        if isinstance(out, dict) and "chars" in out:
            line += f" out={out['chars']} chars"
            if out.get("results") is not None:
                line += f", {out['results']} results"
            if out.get("error"):
                line += f" ERROR {out['error']}"
        print(line)
    search = trace.get("search")
    if search is None:
        print("SEARCHES (not recorded: send with --trace)")
    else:
        print("SEARCHES")
        for ev in search.get("events") or []:
            what = ev.get("engine") or ev.get("reason") or ""
            ms = ev.get("engine_ms")
            took = f" {ms}ms" if ms is not None else ""
            rows = f" rows={ev['rows']}" if ev.get("rows") is not None else ""
            query = f' "{ev["query"]}"' if ev.get("query") else ""
            extra = f" detail={ev['detail']}" if ev.get("detail") else ""
            print(f"  +{_secs(ev.get('t_ms'))} {ev.get('kind')} {what}{took}{rows}{query}{extra}")
        if search.get("dropped"):
            print(f"  ({search['dropped']} events not recorded)")
    for child in trace.get("children") or []:
        print(
            f"SUB-AGENT task {child.get('id')} {child.get('agent_type')} {child.get('status')} "
            f"{_secs(child.get('duration_ms'))}  (pcxa chat trace {child.get('id')})"
        )


def cmd_chat_trace(client, args):
    params = {"full": "1"} if args.full else None
    trace = client.get(f"agent-tasks/{args.agent_task_id}/trace/", params)
    if args.format == "table":
        _print_trace(trace)
    else:
        out_json(trace)


def cmd_chat_ls(client, args):
    params = {"page_size": args.limit}
    if args.search:
        params["search"] = args.search
    data = client.get("conversations/", params)
    results = data.get("results", data) if isinstance(data, dict) else data
    if args.format == "json":
        out_json(results)
        return
    rows = []
    for c in results:
        last = c.get("last_message") or {}
        rows.append({
            "id": str(c.get("id", "")),
            "title": (c.get("title") or "(untitled)")[:40],
            "msgs": str(c.get("message_count", 0)),
            "last": (last.get("role") or "") + ": " + (last.get("content") or "")[:40],
            "updated": str(c.get("updated_at", ""))[:19],
        })
    out_table(rows, ["id", "title", "msgs", "last", "updated"])


def cmd_chat_get(client, args):
    if args.conversation_id:
        data = client.get(f"conversations/{args.conversation_id}/")
    else:
        data = client.post("conversations/current/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Conversation {data['id']}: {data.get('title') or '(untitled)'}")
    usage = data.get("context_usage") or {}
    print(
        f"Tokens: {usage.get('total_tokens', 0)}/{usage.get('threshold', 0)} "
        f"({usage.get('percent', 0)}%) | Active messages: {usage.get('message_count', 0)}"
        f" | Compacted: {usage.get('compacted_count', 0)}"
    )
    print()
    for m in data.get("messages", []):
        role = (m.get("role") or "?").upper()
        content = (m.get("content") or "").strip()
        print(f"[{m.get('id')}] {role}:")
        print(content if content else "(empty)")
        tools = m.get("tool_steps") or []
        if tools and args.show_tools:
            print(f"  -- {len(tools)} tool calls --")
            for t in tools:
                print(f"  - {t.get('tool_name', '?')}: {json.dumps(t.get('tool_input', {}))[:120]}")
        print()


def cmd_chat_new(client, args):
    payload = {"title": args.title or ""}
    data = client.post("conversations/new/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created conversation {data['id']}: {data.get('title') or '(untitled)'}")


def cmd_chat_delete(client, args):
    client.delete(f"conversations/{args.conversation_id}/archive/")
    print(f"Archived conversation {args.conversation_id}")


def cmd_chat_models(client, args):
    data = client.get("ai/models/")
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for m in data.get("models", []):
        rows.append({
            "id": str(m.get("id", "")),
            "label": str(m.get("label", "")),
            "tier": str(m.get("tier", "")),
            "default": "*" if m.get("default") else "",
        })
    print(f"Default: {data.get('default_model', '?')}\n")
    out_table(rows, ["id", "label", "tier", "default"])
