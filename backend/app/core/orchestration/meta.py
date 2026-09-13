"""Form metadata sidecar for ``OrchestrationConfig`` (SPEC §17.2, §17.5).

Same contract as ``rag/config_meta.py``: a field with no row here fails
``test_every_config_field_has_metadata``.
"""

from __future__ import annotations

from typing import Any

from .config import OrchestrationConfig

ORCH_GROUPS: list[dict[str, Any]] = [
    {"id": "shared", "label": "Chung", "order": 1},
    {"id": "reflexion", "label": "Reflexion", "order": 2},
    {"id": "plan_execute", "label": "Plan & Execute", "order": 3},
    {"id": "router", "label": "Router", "order": 4},
    {"id": "supervisor", "label": "Supervisor", "order": 5},
    {"id": "debate", "label": "Debate", "order": 6},
    {"id": "evaluator_optimizer", "label": "Evaluator / Optimizer", "order": 7},
]

# rough LLM-calls-per-turn + cost tier for the pattern picker (SPEC §16.5)
PATTERN_INFO: dict[str, dict[str, Any]] = {
    "react": {
        "label": "ReAct",
        "summary": "Vòng Thought → Action → Observation. Mặc định, đơn agent.",
        "cost_tier": "$",
        "requires_delegation": False,
        "calls_hint": "1 + số vòng tool",
    },
    "reflexion": {
        "label": "Reflexion",
        "summary": "Generate → Evaluate → Reflect → Retry. Tự sửa lỗi qua vài lần thử.",
        "cost_tier": "$$",
        "requires_delegation": False,
        "calls_hint": "3 × max_attempts",
    },
    "plan_execute": {
        "label": "Plan & Execute",
        "summary": "Lập kế hoạch trước → chạy từng bước có verifier → lập lại nếu hỏng.",
        "cost_tier": "$$",
        "requires_delegation": False,
        "calls_hint": "1 + 2 × max_steps",
    },
    "router": {
        "label": "Router",
        "summary": "Model rẻ phân loại → chọn nhánh / agent / model tier. Thường rẻ hơn ReAct.",
        "cost_tier": "$",
        "requires_delegation": False,
        "calls_hint": "1 + chi phí nhánh",
    },
    "supervisor": {
        "label": "Supervisor",
        "summary": "Chia việc → giao cho worker (song song) → tổng hợp. Cần agent worker.",
        "cost_tier": "$$$",
        "requires_delegation": True,
        "calls_hint": "2 + Σ(chi phí mỗi worker)",
    },
    "debate": {
        "label": "Debate",
        "summary": "N agent trả lời độc lập → critique chéo → judge. Nên dùng model khác nhau.",
        "cost_tier": "$$$$",
        "requires_delegation": True,
        "calls_hint": "n × (1 + rounds) + 1",
    },
    "evaluator_optimizer": {
        "label": "Evaluator / Optimizer",
        "summary": "generator → evaluator → optimizer, lặp tới stop_score hoặc max_rounds.",
        "cost_tier": "$$$",
        "requires_delegation": False,
        "calls_hint": "3 × max_rounds",
    },
}

ORCH_FIELD_META: dict[str, dict[str, Any]] = {
    # shared
    "pattern": {
        "group": "shared",
        "label": "Design pattern",
        "help": "react giữ nguyên hành vi hiện tại. Mọi pattern khác là opt-in.",
        "order": 1,
    },
    "max_llm_calls": {
        "group": "shared",
        "label": "Trần lượt gọi LLM / turn",
        "help": "Để trống = dùng ORCH_MAX_TOTAL_LLM_CALLS của hệ thống.",
        "order": 2,
    },
    "node_timeout_seconds": {
        "group": "shared",
        "label": "Timeout mỗi node",
        "help": "Để trống = dùng ORCH_NODE_TIMEOUT_SECONDS.",
        "order": 3,
    },
    "emit_intermediate": {
        "group": "shared",
        "label": "Phát event trung gian",
        "help": "plan_created / reflection / … ra WebSocket. Tắt = chỉ hiện kết quả cuối.",
        "order": 4,
    },
    "ab_pattern": {
        "group": "shared",
        "label": "A/B — pattern thách thức",
        "help": "Với xác suất ab_split, lượt chạy dùng pattern này thay vì pattern chính. Arm đã chọn hiện ở agent_runs.pattern.",
        "order": 5,
    },
    "ab_split": {
        "group": "shared",
        "label": "A/B — tỉ lệ dùng challenger",
        "help": "0 = tắt A/B. 0.5 = một nửa lưu lượng.",
        "order": 6,
    },
    # reflexion
    "max_attempts": {
        "group": "reflexion",
        "label": "Số lần thử tối đa",
        "help": "Trần vòng Generate → Evaluate → Reflect → Retry.",
        "order": 1,
    },
    "evaluator_model": {
        "group": "reflexion",
        "label": "Model evaluator",
        "help": "Để trống = model của agent. Nên khác model generator để giảm self-preference bias.",
        "widget": "model-picker",
        "order": 2,
    },
    "success_score": {
        "group": "reflexion",
        "label": "Điểm đạt (dừng sớm)",
        "help": "Evaluator đạt ngưỡng này thì dừng.",
        "order": 3,
    },
    "reflection_max_chars": {
        "group": "reflexion",
        "label": "Độ dài bài học tối đa",
        "help": "Chặn context bloat — bài học phải ngắn, không phải bản chép lại của trace.",
        "order": 4,
    },
    # plan_execute
    "planner_model": {
        "group": "plan_execute",
        "label": "Model lập kế hoạch",
        "help": "Nên dùng model mạnh — kế hoạch sai thì mọi bước sau đều sai.",
        "widget": "model-picker",
        "order": 1,
    },
    "max_steps": {
        "group": "plan_execute",
        "label": "Số bước tối đa",
        "help": "",
        "order": 2,
    },
    "verify_each_step": {
        "group": "plan_execute",
        "label": "Verify sau mỗi bước",
        "help": "Chấm done_when sau mỗi bước thay vì chỉ ở cuối.",
        "order": 3,
    },
    "replan_on_failure": {
        "group": "plan_execute",
        "label": "Lập lại kế hoạch khi hỏng",
        "help": "",
        "order": 4,
    },
    "max_replans": {
        "group": "plan_execute",
        "label": "Số lần lập lại tối đa",
        "help": "",
        "depends_on": {"replan_on_failure": True},
        "order": 5,
    },
    # router
    "router_model": {
        "group": "router",
        "label": "Model định tuyến",
        "help": "Nên là model rẻ — định tuyến là phân loại, không cần suy luận sâu.",
        "widget": "model-picker",
        "order": 1,
    },
    "routes": {
        "group": "router",
        "label": "Các nhánh",
        "help": "[{name, description, target_agent_id?, target_model?}]",
        "widget": "json",
        "order": 2,
    },
    "fallback_route": {
        "group": "router",
        "label": "Nhánh dự phòng",
        "help": "Dùng khi phân loại không chắc; để trống = tự xử lý bằng react.",
        "order": 3,
    },
    "route_confidence_min": {
        "group": "router",
        "label": "Ngưỡng độ tin cậy",
        "help": "Dưới ngưỡng → fallback_route.",
        "order": 4,
    },
    # supervisor
    "supervisor_model": {
        "group": "supervisor",
        "label": "Model supervisor",
        "help": "Không nhất thiết là model lớn nhất — điều phối khác suy luận sâu.",
        "widget": "model-picker",
        "order": 1,
    },
    "worker_agent_ids": {
        "group": "supervisor",
        "label": "Agent worker",
        "help": "Chỉ agent có is_delegatable = true. Rỗng → 422.",
        "widget": "agent-picker",
        "order": 2,
    },
    "max_workers": {
        "group": "supervisor",
        "label": "Số worker tối đa",
        "help": "",
        "order": 3,
    },
    "parallel": {
        "group": "supervisor",
        "label": "Chạy song song",
        "help": "asyncio.gather, giới hạn bởi SUBAGENT_PARALLEL_LIMIT.",
        "order": 4,
    },
    "synthesis_model": {
        "group": "supervisor",
        "label": "Model tổng hợp",
        "help": "",
        "widget": "model-picker",
        "order": 5,
    },
    "worker_timeout_seconds": {
        "group": "supervisor",
        "label": "Timeout mỗi worker",
        "help": "Để trống = SUBAGENT_TIMEOUT_SECONDS.",
        "order": 6,
    },
    # debate
    "debater_agent_ids": {
        "group": "debate",
        "label": "Agent tranh luận",
        "help": "2–4 agent trả lời độc lập.",
        "widget": "agent-picker",
        "order": 1,
    },
    "rounds": {
        "group": "debate",
        "label": "Số vòng critique",
        "help": "",
        "order": 2,
    },
    "judge_model": {
        "group": "debate",
        "label": "Model judge",
        "help": "",
        "widget": "model-picker",
        "order": 3,
    },
    "require_distinct_models": {
        "group": "debate",
        "label": "Bắt buộc model khác nhau",
        "help": "Society of Mind: model khác nhau giảm correlated errors.",
        "order": 4,
    },
    # evaluator_optimizer
    "max_rounds": {
        "group": "evaluator_optimizer",
        "label": "Số vòng tối đa",
        "help": "Trần cứng — điều kiện dừng không được phép chỉ phụ thuộc điểm do model tự chấm.",
        "order": 1,
    },
    "stop_score": {
        "group": "evaluator_optimizer",
        "label": "Điểm dừng",
        "help": "",
        "order": 2,
    },
    "optimizer_model": {
        "group": "evaluator_optimizer",
        "label": "Model optimizer",
        "help": "",
        "widget": "model-picker",
        "order": 3,
    },
}

_GROUP_IDS = {g["id"] for g in ORCH_GROUPS}


def check_field_metadata() -> list[str]:
    problems: list[str] = []
    for name in OrchestrationConfig.model_fields:
        meta = ORCH_FIELD_META.get(name)
        if meta is None:
            problems.append(f"OrchestrationConfig.{name}: missing sidecar metadata")
            continue
        if meta.get("group") not in _GROUP_IDS:
            problems.append(
                f"OrchestrationConfig.{name}: group {meta.get('group')!r} not in ORCH_GROUPS"
            )
        if "help" not in meta:
            problems.append(f"OrchestrationConfig.{name}: missing 'help'")
    for name in ORCH_FIELD_META:
        if name not in OrchestrationConfig.model_fields:
            problems.append(f"ORCH_FIELD_META has stale field {name!r}")
    return problems
