"""Audited local Agent Chat backed by the configured analysis LLM."""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .db import connect, initialize
from .models import chat_model_options, require_chat_model

CHAT_MODEL = "deepseek-v4-flash"
SYSTEM_PROMPT = """你是 RoofTop 投资研究 Agent。只做分析，不下单。
所有关键陈述必须显式标注（事实）、（观点）或（待验证假设）。
引用本地数据库外的事实时说明需要信源；不得杜撰引用。
涉及交易纪律时，只能给出人工复核提示，并说明数据时点、模型限制和主要风险。
回答应清晰、直接，并主动区分已知信息与尚待验证的信息。"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def list_sessions() -> list[dict]:
    initialize()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM chat_sessions ORDER BY updated_at DESC").fetchall()]


def messages_for(session_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id,role,content,model_name,created_at FROM chat_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()]


def available_models() -> dict:
    return {"default_model": CHAT_MODEL, "models": [item for item in chat_model_options() if item["configured"]]}


def _completion(messages: list[dict], model_name: str = CHAT_MODEL) -> str:
    credential = require_chat_model(model_name)
    base_url = (credential.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    key = credential.get("api_key") or credential.get("key")
    if not key:
        raise RuntimeError(f"{model_name} API key is not configured")
    payload = json.dumps({
        "model": credential.get("api_model") or credential.get("model") or model_name,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.2,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/chat/completions", data=payload, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"LLM API is unavailable: {exc}") from exc
    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected chat response: {str(result)[:300]}") from exc


def send_message(content: str, session_id: int | None = None, model_name: str = CHAT_MODEL) -> dict:
    initialize()
    require_chat_model(model_name)
    content = content.strip()
    if not content or len(content) > 20_000:
        raise ValueError("message must contain 1 to 20000 characters")
    now = _now()
    with connect() as conn:
        if session_id is None:
            title = content.replace("\n", " ")[:36]
            session_id = conn.execute(
                "INSERT INTO chat_sessions(title,created_at,updated_at) VALUES(?,?,?)", (title, now, now),
            ).lastrowid
        elif not conn.execute("SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)).fetchone():
            raise ValueError("chat session not found")
        conn.execute("INSERT INTO chat_messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                     (session_id, "user", content, now))
        conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
        conn.commit()
        history = [dict(row) for row in conn.execute(
            "SELECT role,content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 20", (session_id,),
        ).fetchall()][::-1]
    try:
        answer = _completion(history, model_name)
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "INSERT INTO analysis_runs(agent_name,input_snapshot,output_snapshot,model_name,created_at) VALUES(?,?,?,?,?)",
                ("Agent Chat", json.dumps(history, ensure_ascii=False),
                 json.dumps({"status": "FAILED", "error": repr(exc)}, ensure_ascii=False), model_name, _now()),
            )
            conn.commit()
        raise
    finished = _now()
    with connect() as conn:
        message_id = conn.execute(
            "INSERT INTO chat_messages(session_id,role,content,model_name,created_at) VALUES(?,?,?,?,?)",
            (session_id, "assistant", answer, model_name, finished),
        ).lastrowid
        conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (finished, session_id))
        conn.execute(
            "INSERT INTO analysis_runs(agent_name,input_snapshot,output_snapshot,model_name,created_at) VALUES(?,?,?,?,?)",
            ("Agent Chat", json.dumps(history, ensure_ascii=False),
             json.dumps({"status": "SUCCESS", "content": answer}, ensure_ascii=False), model_name, finished),
        )
        conn.commit()
    return {"session_id": session_id, "message": {"id": message_id, "role": "assistant",
            "content": answer, "model_name": model_name, "created_at": finished}}
