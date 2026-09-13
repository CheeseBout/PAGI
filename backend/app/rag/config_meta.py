"""Form metadata sidecar for ``RagConfig`` (SPEC §17.2).

Lives next to the Pydantic class, not in the frontend: adding a field to
``RagConfig`` without a row here makes ``test_every_config_field_has_metadata``
red. That test is the only thing keeping the generated form in sync with the
model over time (SPEC §17.5).

Each entry:
  group             — one of RAG_GROUPS ids
  label             — short human label
  help              — one line, includes tuning hints from KNOWLEDGE.md §5
  requires_reingest — 🔁 in the UI; only takes effect on next ingest
  cost_hint         — extra LLM cost this field turns on (or None)
  widget            — override the type-inferred widget (or None)
  depends_on        — {other_field: value} — greyed out unless it matches
"""

from __future__ import annotations

from typing import Any

from .config import RagConfig

RAG_GROUPS: list[dict[str, Any]] = [
    {"id": "mode", "label": "Chế độ & sinh câu trả lời", "order": 1},
    {
        "id": "indexing",
        "label": "Lập chỉ mục",
        "order": 2,
        "note": "Chỉ có hiệu lực từ lần ingest / re-ingest sau.",
    },
    {"id": "retrieval", "label": "Truy hồi", "order": 3},
    {"id": "rerank", "label": "Xếp hạng lại", "order": 4},
    {"id": "query_transform", "label": "Biến đổi truy vấn & Corrective", "order": 5},
    {"id": "evaluation", "label": "Đánh giá", "order": 6},
]

RAG_FIELD_META: dict[str, dict[str, Any]] = {
    # ── group: mode ────────────────────────────────────────────────────
    "mode": {
        "group": "mode",
        "label": "Chế độ RAG",
        "help": "off = tắt · always = luôn truy hồi trước mỗi lượt · tool = để agent tự gọi rag_search · auto = cả hai.",
        "order": 1,
    },
    "context_budget_pct": {
        "group": "mode",
        "label": "Ngân sách context (%)",
        "help": "Phần cửa sổ context dành cho tài liệu truy hồi. 20% system / 60% context / 20% headroom là điểm khởi đầu tốt.",
        "order": 2,
    },
    "context_max_tokens": {
        "group": "mode",
        "label": "Trần token context",
        "help": "Trần tuyệt đối, thắng context_budget_pct nếu nhỏ hơn. Để trống = không giới hạn cứng.",
        "order": 3,
    },
    "context_format": {
        "group": "mode",
        "label": "Định dạng khối context",
        "help": "xml tách ranh giới dữ liệu/lệnh rõ hơn markdown — mặc định vì an toàn.",
        "order": 4,
    },
    "order_strategy": {
        "group": "mode",
        "label": "Thứ tự tài liệu",
        "help": "lost_in_middle đặt tài liệu tốt nhất ở đầu và cuối; model đọc lướt phần giữa.",
        "order": 5,
    },
    "grounding_strictness": {
        "group": "mode",
        "label": "Độ chặt grounding",
        "help": "strict = cấm dùng kiến thức ngoài context, bắt buộc nói 'không đủ dữ liệu' khi thiếu.",
        "order": 6,
    },
    "include_citations": {
        "group": "mode",
        "label": "Yêu cầu trích dẫn",
        "help": "Ép model trích [doc N]; backend map ngược ra chunk_id.",
        "order": 7,
    },
    "include_doc_dates": {
        "group": "mode",
        "label": "Đưa ngày tài liệu vào context",
        "help": "Để model tự phát hiện tài liệu cũ.",
        "order": 8,
    },
    # ── group: indexing 🔁 ─────────────────────────────────────────────
    "chunk_strategy": {
        "group": "indexing",
        "label": "Chiến lược chunk",
        "help": "recursive an toàn cho hầu hết văn bản · parent_child cho tài liệu dài · semantic khi ranh giới chủ đề rõ.",
        "requires_reingest": True,
        "order": 1,
    },
    "chunk_size": {
        "group": "indexing",
        "label": "Kích thước chunk (token)",
        "help": "Quá lớn (>1000) gây nhiễu, quá nhỏ (<50) mất ngữ cảnh.",
        "requires_reingest": True,
        "order": 2,
    },
    "chunk_overlap_pct": {
        "group": "indexing",
        "label": "Chồng lấn (%)",
        "help": "Chồng lấn giữa hai chunk liền kề.",
        "requires_reingest": True,
        "order": 3,
    },
    "parent_chunk_size": {
        "group": "indexing",
        "label": "Kích thước chunk cha",
        "help": "Chỉ dùng với parent_child.",
        "requires_reingest": True,
        "depends_on": {"chunk_strategy": "parent_child"},
        "order": 4,
    },
    "semantic_breakpoint_percentile": {
        "group": "indexing",
        "label": "Ngưỡng ngắt semantic (percentile)",
        "help": "Chỉ dùng với semantic.",
        "requires_reingest": True,
        "depends_on": {"chunk_strategy": "semantic"},
        "order": 5,
    },
    "enrich_contextual": {
        "group": "indexing",
        "label": "Ngữ cảnh hoá chunk trước khi embed",
        "help": "LLM viết 1–2 câu ngữ cảnh gắn trước chunk. Giảm retrieval failure đáng kể.",
        "requires_reingest": True,
        "cost_hint": "1 lượt LLM / chunk khi ingest",
        "order": 6,
    },
    "enrich_contextual_model": {
        "group": "indexing",
        "label": "Model ngữ cảnh hoá",
        "help": "Để trống = dùng summary_model, rồi tới model của agent.",
        "requires_reingest": True,
        "widget": "model-picker",
        "depends_on": {"enrich_contextual": True},
        "order": 7,
    },
    "enrich_summary": {
        "group": "indexing",
        "label": "Tóm tắt document vào metadata",
        "help": "1 lượt LLM / document.",
        "requires_reingest": True,
        "cost_hint": "1 lượt LLM / document",
        "order": 8,
    },
    "enrich_hyqa": {
        "group": "indexing",
        "label": "Sinh câu hỏi giả định (HyQA)",
        "help": "Embed thêm câu hỏi giả định để bắt truy vấn dạng câu hỏi.",
        "requires_reingest": True,
        "cost_hint": "1 lượt LLM / chunk",
        "order": 9,
    },
    "enrich_hyqa_n": {
        "group": "indexing",
        "label": "Số câu hỏi HyQA",
        "help": "",
        "requires_reingest": True,
        "depends_on": {"enrich_hyqa": True},
        "order": 10,
    },
    "enrich_metadata_extract": {
        "group": "indexing",
        "label": "Trích metadata ({author, date, doc_type, entities})",
        "help": "Dùng cho metadata_filter.",
        "requires_reingest": True,
        "cost_hint": "1 lượt LLM / document",
        "order": 11,
    },
    "embedding_batch_size": {
        "group": "indexing",
        "label": "Batch size embedding",
        "help": "",
        "requires_reingest": True,
        "order": 12,
    },
    # ── group: retrieval ──────────────────────────────────────────────
    "retrieval_mode": {
        "group": "retrieval",
        "label": "Chế độ truy hồi",
        "help": "dense mạnh với diễn đạt khác từ · sparse mạnh với mã số/ID/tên riêng · hybrid là mặc định đúng cho gần như mọi corpus.",
        "order": 1,
    },
    "top_k_dense": {
        "group": "retrieval",
        "label": "top_k dense",
        "help": "Truy hồi rộng (50–100) rồi mới rerank hẹp lại.",
        "order": 2,
    },
    "top_k_sparse": {
        "group": "retrieval",
        "label": "top_k sparse",
        "help": "",
        "order": 3,
    },
    "fusion": {
        "group": "retrieval",
        "label": "Cách hợp nhất",
        "help": "rrf gộp theo thứ hạng, miễn nhiễm với thang điểm khác nhau · weighted dùng hybrid_alpha.",
        "order": 4,
    },
    "rrf_k": {
        "group": "retrieval",
        "label": "rrf_k",
        "help": "Chỉ dùng khi fusion = rrf.",
        "depends_on": {"fusion": "rrf"},
        "order": 5,
    },
    "hybrid_alpha": {
        "group": "retrieval",
        "label": "Trọng số nhánh dense (alpha)",
        "help": "Chỉ khi fusion = weighted. FAQ/hỏi đáp tự nhiên 0.7–0.9; code/log/văn bản pháp lý nhiều mã số 0.2–0.4.",
        "depends_on": {"fusion": "weighted"},
        "order": 6,
    },
    "score_threshold": {
        "group": "retrieval",
        "label": "Ngưỡng điểm",
        "help": "Bỏ chunk dưới ngưỡng sau khi fuse. Đặt >0 để agent dám nói 'không có trong tài liệu'.",
        "order": 7,
    },
    "bm25_k1": {
        "group": "retrieval",
        "label": "BM25 k1",
        "help": "Tham số Okapi BM25.",
        "order": 8,
    },
    "bm25_b": {
        "group": "retrieval",
        "label": "BM25 b",
        "help": "Tham số Okapi BM25.",
        "order": 9,
    },
    "vi_segmentation": {
        "group": "retrieval",
        "label": "Tách từ tiếng Việt (BM25)",
        "help": "Thiếu underthesea → tự tắt, không lỗi.",
        "order": 10,
    },
    "metadata_filter": {
        "group": "retrieval",
        "label": "Pre-filter metadata",
        "help": "Áp trước khi tính similarity, theo kb_documents.meta / kb_chunks.meta.",
        "widget": "json",
        "order": 11,
    },
    "dedupe_similarity": {
        "group": "retrieval",
        "label": "Ngưỡng gộp trùng",
        "help": "Gộp hai chunk gần trùng nhau.",
        "order": 12,
    },
    # ── group: rerank ────────────────────────────────────────────────
    "rerank_mode": {
        "group": "rerank",
        "label": "Chế độ rerank",
        "help": "MMR khi cần đa góc nhìn · cross_encoder khi cần precision cao (legal/compliance) · llm dùng chính provider gateway.",
        "order": 1,
    },
    "rerank_model": {
        "group": "rerank",
        "label": "Model rerank",
        "help": "Với llm: model chấm. Với cross_encoder: tên model/endpoint.",
        "widget": "model-picker",
        "order": 2,
    },
    "rerank_top_n": {
        "group": "rerank",
        "label": "Số chunk cuối cùng",
        "help": "3–5 là hợp lý; retrieve broad 50–100 rồi rerank.",
        "order": 3,
    },
    "mmr_lambda": {
        "group": "rerank",
        "label": "MMR lambda",
        "help": "Gần 1 = ưu tiên liên quan, gần 0 = ưu tiên đa dạng góc nhìn.",
        "depends_on": {"rerank_mode": "mmr"},
        "order": 4,
    },
    # ── group: query_transform ──────────────────────────────────────
    "query_transform": {
        "group": "query_transform",
        "label": "Biến đổi truy vấn",
        "help": "auto để router chọn theo độ phức tạp. Mỗi phép biến đổi tốn thêm 1 lượt LLM — bật khi eval cho thấy có lợi.",
        "cost_hint": "1+ lượt LLM / truy vấn",
        "order": 1,
    },
    "multi_query_n": {
        "group": "query_transform",
        "label": "Số truy vấn (multi_query)",
        "help": "",
        "depends_on": {"query_transform": "multi_query"},
        "order": 2,
    },
    "transform_model": {
        "group": "query_transform",
        "label": "Model biến đổi",
        "help": "Nên dùng model rẻ.",
        "widget": "model-picker",
        "order": 3,
    },
    "crag_enabled": {
        "group": "query_transform",
        "label": "Corrective RAG",
        "help": "Chấm mức liên quan của kết quả trước khi generate.",
        "cost_hint": "1 lượt LLM / truy vấn",
        "order": 4,
    },
    "crag_threshold": {
        "group": "query_transform",
        "label": "Ngưỡng CRAG",
        "help": "Dưới ngưỡng → theo crag_fallback.",
        "depends_on": {"crag_enabled": True},
        "order": 5,
    },
    "crag_fallback": {
        "group": "query_transform",
        "label": "Fallback CRAG",
        "help": "web_search chỉ dùng được khi agent có tool đó và có TAVILY_API_KEY.",
        "depends_on": {"crag_enabled": True},
        "order": 6,
    },
    "max_retrieval_rounds": {
        "group": "query_transform",
        "label": "Số vòng truy hồi tối đa",
        "help": "Chặn vòng lặp truy hồi ở chế độ agentic.",
        "order": 7,
    },
    # ── group: evaluation ──────────────────────────────────────────
    "eval_judge_model": {
        "group": "evaluation",
        "label": "Model judge",
        "help": "",
        "widget": "model-picker",
        "order": 1,
    },
    "faithfulness_min": {
        "group": "evaluation",
        "label": "Ngưỡng faithfulness",
        "help": "Lĩnh vực y tế/pháp lý nên nâng lên 0.95.",
        "order": 2,
    },
    "answer_relevancy_min": {
        "group": "evaluation",
        "label": "Ngưỡng answer_relevancy",
        "help": "",
        "order": 3,
    },
    "context_precision_min": {
        "group": "evaluation",
        "label": "Ngưỡng context_precision",
        "help": "",
        "order": 4,
    },
    "context_recall_min": {
        "group": "evaluation",
        "label": "Ngưỡng context_recall",
        "help": "",
        "order": 5,
    },
    "eval_sample_rate": {
        "group": "evaluation",
        "label": "Tỉ lệ chấm ngầm lượt chat thật",
        "help": "0 = tắt. Chưa nối trong Phase 12 (SPEC §14.10).",
        "order": 6,
    },
}

_GROUP_IDS = {g["id"] for g in RAG_GROUPS}


def check_field_metadata() -> list[str]:
    """Return a list of problems (empty = OK) — used by SPEC §17.5's test."""
    problems: list[str] = []
    for name in RagConfig.model_fields:
        meta = RAG_FIELD_META.get(name)
        if meta is None:
            problems.append(f"RagConfig.{name}: missing sidecar metadata")
            continue
        if meta.get("group") not in _GROUP_IDS:
            problems.append(f"RagConfig.{name}: group {meta.get('group')!r} not in RAG_GROUPS")
        if "help" not in meta:
            problems.append(f"RagConfig.{name}: missing 'help'")
    for name in RAG_FIELD_META:
        if name not in RagConfig.model_fields:
            problems.append(f"RAG_FIELD_META has stale field {name!r}")
    return problems
