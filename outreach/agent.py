"""The agent loop: the model plans, calls tools, observes results, and decides the next step.

Cost controls: the posting and current state are pre-loaded into the first message (saves turns),
the system prompt, tools and conversation are prompt-cached, web searches are capped per company,
and every run's token/search usage is recorded so the daily budget can be enforced.
"""
import json
from datetime import date
import anthropic
from . import config, db, prompts, postings
from .tools import CLIENT_TOOLS, server_tools, ToolRunner, as_result

EPHEMERAL = {"type": "ephemeral"}


def _cached_tools():
    tools = [dict(t) for t in CLIENT_TOOLS] + server_tools()
    tools[-1] = {**tools[-1], "cache_control": EPHEMERAL}
    return tools


def _mark_last_user_block(messages):
    """Keep exactly one rolling cache breakpoint on the newest user turn (API allows max 4)."""
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if last["role"] == "user":
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = EPHEMERAL


def estimate_cost(u):
    inp = (u["input"] + u["cache_write"] * 1.25 + u["cache_read"] * 0.1) * config.PRICE_INPUT_PER_MTOK / 1e6
    out = u["output"] * config.PRICE_OUTPUT_PER_MTOK / 1e6
    return round(inp + out + u["searches"] * config.PRICE_PER_SEARCH, 4)


def run_company(c, company_id, verbose=True, reason=""):
    co = c.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not config.ANTHROPIC_API_KEY.strip():
        raise SystemExit(f"ANTHROPIC_API_KEY is empty - set it in {config.ROOT / '.env'} and save the file")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    runner = ToolRunner(c, company_id)
    c.execute("UPDATE companies SET status='in_progress', updated_at=? WHERE id=? AND status='new'",
              (db.now(), company_id))
    c.commit()

    posting = postings.read_posting(co["url"]) if co["url"] else {"ok": False, "note": "no URL - find the posting with web_search"}
    if posting.get("text"):
        posting["text"] = posting["text"][:3500]
    state = db.company_state(c, company_id)

    messages = [{"role": "user", "content": (
        f"Today is {date.today().isoformat()}. Company: {co['name']}. Role she applied to: {co['title']}. "
        f"Applied on: {co['applied_on']}.\n"
        f"{('Why you are running now: ' + reason) if reason else ''}\n\n"
        f"POSTING (already fetched):\n{json.dumps(posting, default=str)}\n\n"
        f"CURRENT STATE (already loaded - no need to call get_state):\n{json.dumps(state, default=str)}\n\n"
        "Work toward the goal for this company, batching independent tool calls in one turn, then call set_status.")}]

    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "searches": 0}
    system = [{"type": "text", "text": prompts.system_prompt(), "cache_control": EPHEMERAL}]
    tools = _cached_tools()

    for _ in range(config.MAX_AGENT_TURNS):
        _mark_last_user_block(messages)
        resp = client.messages.create(model=config.MODEL, max_tokens=2500, system=system,
                                      tools=tools, messages=messages)
        u = resp.usage
        usage["input"] += u.input_tokens or 0
        usage["output"] += u.output_tokens or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        usage["searches"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        messages.append({"role": "assistant", "content": resp.content})

        if verbose:
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    print(f"  [{co['name']}] {b.text.strip()[:200]}")
                elif b.type in ("tool_use", "server_tool_use"):
                    print(f"  [{co['name']}] -> {b.name}({_short(b.input)})")

        if resp.stop_reason == "pause_turn":
            continue
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            break
        messages.append({"role": "user", "content": [as_result(x.id, runner.run(x.name, x.input or {})) for x in uses]})
        if runner.finished:
            break

    if not runner.finished:
        runner.t_set_status("waiting", "agent stopped without a decision - check events")
    cost = estimate_cost(usage)
    c.execute("INSERT INTO runs(company_id,day,input,output,cache_read,cache_write,searches,cost_usd,at) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (company_id, date.today().isoformat(), usage["input"], usage["output"],
              usage["cache_read"], usage["cache_write"], usage["searches"], cost, db.now()))
    c.commit()
    if verbose:
        print(f"  [{co['name']}] est. cost ${cost:.3f}  ({usage['searches']} searches, "
              f"{usage['input'] + usage['cache_read'] + usage['cache_write']:,} in / {usage['output']:,} out tokens, "
              f"{usage['cache_read']:,} cached)")
    return db.company_state(c, company_id)


def spent_today(c):
    r = c.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs WHERE day=?", (date.today().isoformat(),)).fetchone()
    return r[0]


def _short(d):
    s = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in (d or {}).items())
    return s[:160]
