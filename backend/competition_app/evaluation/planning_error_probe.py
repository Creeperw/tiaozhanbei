"""One-shot, synthetic HTTP error inspection; never invoke learner workflows."""

import asyncio
from difflib import SequenceMatcher
import json
import re
from uuid import uuid4

import httpx

from competition_app.evaluation.planning_mode_probe import PlanningModeProbe, fixed_input
from competition_app.llm.response_diagnostics import digest


def safe_error_fields(raw, *, body, headers):
    """Only error metadata, bounded and scrubbed of credentials/request echoes."""
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return {"error_body_format": "non_json"}
    error = value.get("error") if isinstance(value, dict) else None
    if not isinstance(error, dict):
        return {"error_body_format": "unrecognized"}
    forbidden = [str(v) for v in headers.values() if v]
    authorization = headers.get("Authorization") or headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        forbidden.append(authorization[7:])
    prompts = [m.get("content", "") for m in body.get("messages", [])]
    fields = {}
    for name in ("type", "code", "param", "message"):
        text = error.get(name)
        if not isinstance(text, (str, int)) or isinstance(text, bool):
            continue
        text = str(text)
        for secret in sorted(forbidden, key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        text = re.sub(r"(?i)\b(?:Bearer\s+\S+|sk[-_][a-z0-9_-]+)", "[redacted]", text)
        # Suppress rather than excerpt messages that echo request content.
        echoed = any(
            isinstance(p, str) and p and (
                p in text or SequenceMatcher(None, p, text, autojunk=False).find_longest_match().size >= 64
            ) for p in prompts
        )
        if echoed or re.search(r'(?i)["\'](?:messages|reasoning_content)["\']\s*:', text):
            text = "[request echo suppressed]"
        fields[name] = text[:1200 if name == "message" else 200]
    return fields


class PlanningErrorProbe(PlanningModeProbe):
    async def run(self):
        try:
            body, schema, skill = fixed_input(self.model)
            self.state.update({"experiment": "strict_error_single_request", "common_body_digest": digest(body),
                               "messages_digest": digest(body["messages"]), "schema": schema,
                               "skill_version": skill.version, "automatic_retry": False,
                               "automatic_fallback": False})
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "structured_output", "strict": True, "schema": schema}}
            _, key = await self.model._current_api_key()
            headers = self.model._provider_headers(key)
            headers["x-opencode-session"] = "tcm-error-probe-" + uuid4().hex
            result = {}
            async def receive():
                async with self.model._request_semaphore:
                    async with httpx.AsyncClient(transport=self.model.transport, timeout=httpx.Timeout(60, connect=30)) as client:
                        async with client.stream("POST", self.model.base_url + "/chat/completions", headers=headers, json=body) as response:
                            result["http_status"] = response.status_code
                            # On success, stop without retaining prose or reasoning.
                            if response.status_code < 400:
                                result["success_body_not_read"] = True
                                return
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(raw) + len(chunk) > 16384:
                                    result["error_body_oversize"] = True
                                    return
                                raw.extend(chunk)
                            result["provider_error"] = safe_error_fields(bytes(raw), body=body, headers=headers)
                            result["error_body_bytes"] = len(raw)
            try:
                await asyncio.wait_for(receive(), timeout=90)
            except asyncio.TimeoutError:
                result["error_kind"] = "timeout"
            except httpx.HTTPError as exc:
                result["error_kind"] = type(exc).__name__
            self.state["results"] = [result]
            self.state["status"] = "completed"
        except asyncio.CancelledError:
            self.state["status"] = "cancelled"
            raise
        except Exception:
            self.state["status"] = "failed"
        finally:
            self.save()


ERROR_PANEL = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>严格请求错误取证</title>
<style>body{font:16px system-ui;max-width:1000px;margin:40px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{padding:12px 24px}</style>
<h1>严格请求错误取证</h1><p>同一合成输入，仅一次严格请求，无重试或回退，不写学习数据。只显示脱敏错误字段，成功正文不读取。</p>
<button id="execute">Execute</button><p id="state"></p><pre id="result"></pre>
<script>
const endpoint='/api/v1/internal-eval/planning-mode-probe',button=document.getElementById('execute'),state=document.getElementById('state'),result=document.getElementById('result');
function fail(e){state.textContent=e.message;button.disabled=true;}
async function refresh(){const r=await fetch(endpoint);if(!r.ok)throw Error('访问失败 '+r.status);const d=await r.json();state.textContent=d.mode+' / '+d.status;result.textContent=JSON.stringify(d,null,2);button.disabled=d.status!=='not_started';if(d.status==='running')setTimeout(()=>refresh().catch(fail),2000);}
button.onclick=async()=>{button.disabled=true;try{const h=await(await fetch('/health')).json();if(h.mode!=='live')throw Error('非Live');const r=await fetch(endpoint,{method:'POST',headers:{'X-Planning-Mode-Probe':'execute'}});if(!r.ok)throw Error('执行失败 '+r.status);await refresh();}catch(e){fail(e);}};refresh().catch(fail);
</script></html>"""