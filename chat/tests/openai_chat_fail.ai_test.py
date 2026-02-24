import os
import sys
import json
from pathlib import Path

from elasticdash_test import ai_test, before_all, after_all, install_ai_interceptor, uninstall_ai_interceptor, expect
from asgiref.sync import sync_to_async


@before_all
def setup_suite():
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "candycode.settings")
    import django
    django.setup()
    install_ai_interceptor()


@after_all
def teardown_suite():
    uninstall_ai_interceptor()


def _expect_prompt(trace, *, filter_contains: str, nth: int | None = None, label: str = ""):
    try:
        expect(trace).to_have_prompt_where(filter_contains=filter_contains, nth=nth)
    except AssertionError as exc:
        from elasticdash_test.trace import get_current_trace
        trace = trace or get_current_trace()
        steps = [
            {
                "prompt": getattr(step, "prompt", ""),
                "completion": getattr(step, "completion", ""),
                "model": getattr(step, "model", ""),
                "provider": getattr(step, "provider", ""),
            }
            for step in (trace.get_llm_steps() if trace else [])
        ]
        note = f"Expected prompt containing '{filter_contains}'" + (f" at position {nth}" if nth is not None else "")
        if label:
            note += f" ({label})"
        raise AssertionError(f"{note}; captured steps={steps}; original={exc}")


async def _call_live(message: str):
    import chat.views as views
    from django.test import RequestFactory
    from django.contrib.auth.models import AnonymousUser

    body = json.dumps({"message": message, "session_id": None})
    rf = RequestFactory()
    request = rf.post("/chat/send/", data=body, content_type="application/json")
    request.user = AnonymousUser()

    response = await sync_to_async(views.send_message, thread_sensitive=True)(request)

    if response.status_code != 200:
        try:
            body = response.content.decode()
        except Exception:
            body = str(response.content)
        from elasticdash_test.trace import get_current_trace
        trace = get_current_trace()
        steps = [
            {
                "prompt": getattr(step, "prompt", ""),
                "completion": getattr(step, "completion", ""),
                "model": getattr(step, "model", ""),
                "provider": getattr(step, "provider", ""),
            }
            for step in (trace.get_llm_steps() if trace else [])
        ]
        raise AssertionError(f"Expected 200, got {response.status_code}; body={body}; steps={steps}")

    payload = json.loads(response.content)
    answer = payload.get("bot_response", {}).get("content", "")
    assert isinstance(answer, str) and answer.strip(), "Expected non-empty OpenAI answer"
    return answer


@ai_test("[EXPECTED FAILURE] impossible prompt order")
async def test_openai_prompt_order_failure(ctx):
    await _call_live("Explain posting flow")
    # The toxicity is supposed to be the 3rd step after intent and draft, but we are testing the failure case where it is mistakenly placed as the first step, so we check for it explicitly at position 0 to trigger the failure.
    _expect_prompt(ctx.trace, filter_contains="Rate toxicity", nth=2, label="expected toxicity prompt first; correct order is intent -> draft -> toxicity -> fulfillment")


@ai_test("[EXPECTED FAILURE] missing fulfillment prompt")
async def test_openai_missing_fulfillment_failure(ctx):
    await _call_live("Help me register")
    _expect_prompt(ctx.trace, filter_contains="Nonexistent fulfillment marker", label="fulfillment prompt should be present after toxicity")


@ai_test("openai prompt order success")
async def test_openai_prompt_order_success(ctx):
    await _call_live("Walk me through creating a post")
    _expect_prompt(ctx.trace, filter_contains="Analyze the user's intent", nth=0, label="intent eval should run first")
    _expect_prompt(ctx.trace, filter_contains="Walk me through creating a post", nth=1, label="draft should follow intent eval")
    _expect_prompt(ctx.trace, filter_contains="Answer to rate", nth=2, label="toxicity check should follow draft")
    _expect_prompt(ctx.trace, filter_contains="Intent:", nth=3, label="fulfillment check should come after toxicity and include intent context")
