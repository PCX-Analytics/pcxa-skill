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
    """Poll assistant task status until terminal or timeout."""
    start = time.time()
    last_status = None
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"status": last_status or "timeout", "_timed_out": True, "_elapsed": elapsed}
        try:
            task = client.get(f"agent-tasks/{task_id}/")
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

    sent = client.post(f"conversations/{conv_id}/send/", payload)
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
    else:
        out_json(result)

    if final_status == "failed":
        sys.exit(2)


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
