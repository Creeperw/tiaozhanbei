import json
import re
from typing import Any, Dict, List, Tuple

def strip_end_tokens(text: str) -> str:
    return (text or "").replace("<|im_end|>", "").strip()

def split_think(text: str) -> Tuple[str, str]:
    """Return (think, visible). Handles missing <think> with present </think>."""
    text = strip_end_tokens(text)
    start = text.find("<think>")
    end = text.rfind("</think>")
    if start >= 0 and end >= 0 and end > start:
        return text[start + len("<think>"):end].strip(), text[end + len("</think>"):].strip()
    if start >= 0 and end < 0:
        return text[start + len("<think>"):].strip(), ""
    if start < 0 and end >= 0:
        return text[:end].strip(), text[end + len("</think>"):].strip()
    return "", text.strip()

def extract_json_object(text: str) -> Dict[str, Any]:
    text = text or ""
    candidates: List[str] = []
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            current = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif current == "\\":
                    escape = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:idx + 1])
                    break
    valid: List[Dict[str, Any]] = []
    for raw in candidates:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                valid.append(parsed)
        except Exception:
            continue
    if valid:
        return max(valid, key=lambda item: len(json.dumps(item, ensure_ascii=False)))
    return {}

def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    calls = []
    pattern = re.compile(r"<tool_call>\s*<function=([^>]+)>\s*([\s\S]*?)\s*</function>\s*</tool_call>", re.I)
    param_pattern = re.compile(r"<parameter=([^>]+)>\s*([\s\S]*?)\s*</parameter>", re.I)
    for fn, body in pattern.findall(text or ""):
        params = {key.strip(): value.strip() for key, value in param_pattern.findall(body or "")}
        query = params.get("query") or next(iter(params.values()), "")
        if query.startswith("{"):
            try:
                parsed = json.loads(query)
                if isinstance(parsed, dict):
                    query = str(parsed.get("query") or next(iter(parsed.values()), ""))
                    params = parsed
            except Exception:
                pass
        if "query" not in params and len(params) > 1:
            params["query"] = query.strip()
        calls.append({"name": fn.strip(), "query": query.strip(), "args": params})
    return calls

def rough_token_count(text: str) -> int:
    return max(len(text or "") // 2, 1)

def safe_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)