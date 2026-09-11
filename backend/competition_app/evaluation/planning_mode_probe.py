"""Temporary, opt-in, account-scoped model experiment without learner writes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
import httpx

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.route_binding import document_issues
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel, _flatten_schema_for_strict_mode
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.response_diagnostics import digest, safe_response_diagnostics, update_response_metadata


def fixed_input(model):
    catalog = json.loads((Path(__file__).parents[1] / "data/textbook_routes/tcm_textbook_routes.v1.json").read_text())
    route = next(r for r in catalog["routes"] if r["route_id"] == "textbook_tcm_physician")
    skill = prompt_skill_registry.load("diagnosis_agent", "learning_plan")
    schema = DiagnosisAgent._planning_draft_schema("long_term")
    context = {
        "trace_id": "SYNTHETIC_MODE_PROBE", "request_id": "SYNTHETIC_MODE_PROBE",
        "learner_id": "SYNTHETIC_NO_ACCOUNT", "task_type": "learning_plan",
        "now": datetime(2026, 9, 7, tzinfo=timezone.utc),
        "user_request": "为合成学习者编写中医执业医师长期学习计划。每日50分钟，晚间学习，偏好知识卡片、对比表和刷题。学过基础理论与诊断学，正在复习方剂，重点提高四君子汤相关方证辨析；学过不代表整本完成或已通过前置。沿规范路线，未知前置先诊断，考试日期未知。只编写长期计划，不执行学习任务。",
    }
    payload = build_model_context(
        context, target_agent="diagnosis_agent", prompt_skill=skill,
        permission_note="只生成当前长期层完整规划文档，不生成其他层，不推断教材已经完成。",
        payload={"plan_scope": "long_term", "output_schema": schema,
                 "planning_status": "approved_route", "textbook_route": route,
                 "fixed_route_policy": {"binding_mode": "fixed_route_v1", "route_id": route["route_id"], "route_version": route["route_version"]},
                 "available_minutes": 50, "learning_evidence": {"evidence_status": "insufficient"},
                 "unmet_prerequisite_courses": [{"course": "中医诊断学", "status": "unknown", "before_stage_id": "stage-2"}],
                 "path_candidates": []},
    )
    messages = model._build_messages("diagnosis_agent", payload, strict_json=False, business_json=True)
    body = {"model": model.model, "messages": messages, "stream": True}
    if model.model.lower().startswith("deepseek-v4"):
        body["thinking"] = {"type": "enabled"}
    return body, _flatten_schema_for_strict_mode(schema), skill


class PlanningModeProbe:
    def __init__(self, model, root: Path, mode: str):
        self.model = model
        self.root = root
        self.mode = mode
        self.receipt = root / "receipt.json"
        self.task = None
        self.state = {"status": "not_started", "mode": mode, "results": []}
        if self.receipt.exists():
            self.state = json.loads(self.receipt.read_text())

    def save(self):
        temp = self.root / "receipt.tmp"
        with temp.open("w") as f:
            os.chmod(temp, 0o600)
            json.dump(self.state, f, ensure_ascii=False)
        temp.replace(self.receipt)

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with (self.root / "started.lock").open("x") as f:
                f.write("one batch only\n")
        except FileExistsError:
            raise HTTPException(409, "本次对照已启动过，禁止重复执行") from None
        self.state = {"status": "running", "mode": self.mode, "results": [],
                      "experiment": "synthetic_not_historical_replay", "learner_writes": False}
        self.save()
        self.task = asyncio.create_task(self.run())
        return self.state

    async def run(self):
        try:
            base, schema, skill = fixed_input(self.model)
            self.state.update({"common_body_digest": digest(base), "messages_digest": digest(base["messages"]),
                               "skill_digest": digest(skill.instructions), "skill_version": skill.version,
                               "model": self.model.model, "stream": True, "automatic_retry": False,
                               "automatic_fallback": False, "order": ["json_schema", "json_object"] * 3})
            # Use the same header/session for all six calls; do not mutate model state.
            _, api_key = await self.model._current_api_key()
            headers = self.model._provider_headers(api_key)
            headers["x-opencode-session"] = "tcm-mode-probe-" + uuid4().hex
            for number, mode in enumerate(self.state["order"], 1):
                body = deepcopy(base)
                body["response_format"] = (
                    {"type": "json_schema", "json_schema": {"name": "structured_output", "strict": True, "schema": schema}}
                    if mode == "json_schema" else {"type": "json_object"}
                )
                assert digest({k: v for k, v in body.items() if k != "response_format"}) == self.state["common_body_digest"]
                result = await self.sample(body, headers)
                result.update({"number": number, "mode": mode, "common_body_digest": self.state["common_body_digest"]})
                self.state["results"].append(result)
                self.save()
            self.state["status"] = "completed"
        except asyncio.CancelledError:
            self.state["status"] = "cancelled"
            raise
        except Exception:
            # Do not persist exception text: it can echo provider/user data.
            self.state["status"] = "failed"
        finally:
            self.save()

    async def sample(self, body, headers):
        metadata = {"stream": True, "done_received": False, "body_read_completed": False, "content_chars": 0}
        result = {"json_valid": False, "six_sections_complete": False}
        parts = []
        try:
            # Independent client prevents retry/fallback in the business adapter.
            # Use the same proxy environment, endpoint, headers and model as production.
            async with self.model._request_semaphore:
                async with httpx.AsyncClient(transport=self.model.transport, timeout=httpx.Timeout(180, connect=30)) as client:
                    async def receive():
                        async with client.stream("POST", self.model.base_url + "/chat/completions", headers=headers, json=body) as response:
                            metadata["http_status"] = response.status_code
                            if response.status_code != 200:
                                return
                            async for line in response.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                data = line[6:]
                                if data == "[DONE]":
                                    metadata["done_received"] = True
                                    break
                                event = json.loads(data)
                                update_response_metadata(metadata, event)
                                choices = event.get("choices") or []
                                content = (choices[0].get("delta") or {}).get("content") if choices else None
                                if isinstance(content, str):
                                    metadata["content_chars"] += len(content)
                                    if metadata["content_chars"] > 100_000:
                                        raise ValueError("bounded response")
                                    parts.append(content)
                            metadata["body_read_completed"] = True
                    await asyncio.wait_for(receive(), timeout=240)
            raw = "".join(parts)
            result["response_digest"] = digest(raw)
            parsed = json.loads(raw)
            result["json_valid"] = isinstance(parsed, dict)
            document = parsed.get("plan_document") if isinstance(parsed, dict) else None
            if isinstance(document, str):
                issues = document_issues(document)
                result.update({"document_chars": len(document), "document_lines": len(document.splitlines()),
                               "document_digest": digest(document), "missing_sections": [i["field_path"] for i in issues],
                               "six_sections_complete": not issues})
        except (ValueError, TypeError, KeyError, IndexError):
            result["error_kind"] = "invalid_or_oversize_response"
        except (httpx.HTTPError, asyncio.TimeoutError):
            result["error_kind"] = "transport_or_timeout"
        result["metadata"] = safe_response_diagnostics(metadata)
        return result


def register_planning_mode_probe(app, *, model, runtime_root, mode, current_user):
    if runtime_root is None:
        return
    root = Path(runtime_root) / "evaluation/planning-mode-probe"
    config = root / "config.json"
    if not config.is_file() or not isinstance(model, OpenAICompatibleChatModel):
        return
    try:
        options = json.loads(config.read_text())
        expires = datetime.fromisoformat(options["expires_at"])
        owner = options["owner_id"]
        if expires.tzinfo is None or not isinstance(owner, str) or not owner:
            return
    except (ValueError, KeyError, TypeError):
        return
    panel_html = PANEL
    if options.get("experiment") == "strict_error":
        from competition_app.evaluation.planning_error_probe import ERROR_PANEL, PlanningErrorProbe
        probe = PlanningErrorProbe(model, root / "strict-error", mode)
        panel_html = ERROR_PANEL
    else:
        probe = PlanningModeProbe(model, root, mode)

    def authorize(request):
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "需要登录")
        if user.user_id != owner:
            raise HTTPException(403, "仅限授权诊断账号")
        if datetime.now(timezone.utc) >= expires:
            raise HTTPException(410, "诊断入口已过期")
        if mode != "live":
            raise HTTPException(409, "仅允许显式 Live 模式")

    @app.get("/internal-eval/planning-mode-probe", response_class=HTMLResponse)
    async def panel(request: Request):
        authorize(request)
        return HTMLResponse(panel_html, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/internal-eval/planning-mode-probe")
    async def status(request: Request):
        authorize(request)
        return probe.state

    @app.post("/api/v1/internal-eval/planning-mode-probe")
    async def start(request: Request):
        authorize(request)
        origin = urlsplit(request.headers.get("origin", ""))
        if origin.netloc != request.url.netloc or origin.scheme != request.url.scheme or request.headers.get("X-Planning-Mode-Probe") != "execute":
            raise HTTPException(403, "必须从本站诊断面板执行")
        return probe.start()


PANEL = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>规划输出模式对照</title>
<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;background:#f4f7fb;color:#17304a}main{background:white;padding:28px;border-radius:16px}button{padding:12px 26px;background:#1769cc;color:white;border:0;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<main><h1>规划输出模式对照</h1><p>固定合成输入，非历史重放。严格 JSON / 普通 JSON 各三次；不写账号数据，不保存正文或思考。</p><p>仅 response_format 不同，流式及 thinking 设置保持相同。HTTP 错误不重试、不回退。</p>
<button id="execute">Execute</button><p id="state">正在核对 Live 模式</p><pre id="result"></pre></main>
<script>
const endpoint='/api/v1/internal-eval/planning-mode-probe';
const button=document.getElementById('execute'), state=document.getElementById('state'), result=document.getElementById('result');
async function refresh(){const r=await fetch(endpoint);if(!r.ok)throw Error('诊断访问失败 '+r.status);const data=await r.json();state.textContent=data.mode+' / '+data.status;result.textContent=JSON.stringify(data,null,2);button.disabled=data.status!=='not_started';if(data.status==='running')setTimeout(()=>refresh().catch(fail),2000);}
function fail(e){state.textContent=e.message;button.disabled=true;}
button.onclick=async()=>{button.disabled=true;try{const h=await(await fetch('/health')).json();if(h.mode!=='live')throw Error('非 Live，禁止执行');const r=await fetch(endpoint,{method:'POST',headers:{'X-Planning-Mode-Probe':'execute'}});if(!r.ok)throw Error('启动失败 '+r.status);await refresh();}catch(e){fail(e);}};
refresh().catch(fail);
</script></html>"""