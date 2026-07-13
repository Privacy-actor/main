import asyncio
import time

from .anonymizer import redact_text
from .llm_adapter import verify_with_llm
from .ner_adapter import ner_adapter
from .recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from .schemas import DetectRequest, EntityType, Strategy, TraceStep


async def run_pipeline(request: DetectRequest, policies: dict[EntityType, Strategy] | None = None):
    rule_job = asyncio.to_thread(detect_rule_spans, request.text, request.strategy)
    lite_job = asyncio.to_thread(detect_lite_ner_spans, request.text, request.strategy)
    model_job = ner_adapter.detect(request.text, request.strategy)
    (rule_spans, rule_trace), (lite_spans, lite_trace), (model_spans, model_trace) = await asyncio.gather(rule_job, lite_job, model_job)
    merged = merge_spans(request.text, rule_spans + lite_spans + model_spans)
    if request.use_llm:
        merged, llm_trace = await verify_with_llm(request.text, merged, request.strategy)
        merged = merge_spans(request.text, merged)
    else:
        llm_trace = TraceStep(key="llm", label="14B 核验与补漏", duration_ms=0, count=0, status="skipped", detail="本次请求已关闭 LLM")
    if policies:
        for span in merged:
            span.strategy = policies.get(span.entity_type, request.strategy)
    started = time.perf_counter()
    redacted = redact_text(request.text, merged, None if policies else request.strategy)
    merge_trace = TraceStep(key="merge", label="合并与脱敏", duration_ms=max(3, round((time.perf_counter() - started) * 1000)), count=len(merged), detail="偏移校验、重叠消歧、从后向前确定性替换")
    traces = [rule_trace]
    if model_trace.status != "skipped":
        traces.append(model_trace)
    else:
        traces.append(lite_trace)
    traces.extend([llm_trace, merge_trace])
    return merged, redacted, traces
