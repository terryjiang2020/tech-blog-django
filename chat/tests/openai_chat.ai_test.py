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
        steps = [
            {
                "prompt": getattr(step, "prompt", ""),
                "completion": getattr(step, "completion", ""),
                "model": getattr(step, "model", ""),
                "provider": getattr(step, "provider", ""),
            }
            for step in trace.get_llm_steps()
        ]
        note = f"Expected prompt containing '{filter_contains}'" + (f" at position {nth}" if nth is not None else "")
        if label:
            note += f" ({label})"
        raise AssertionError(f"{note}; captured steps={steps}; original={exc}")


async def _call_openai_live(message: str, ctx):
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
        # Emit trace steps for debugging
        steps = [
            {
                "prompt": getattr(step, "prompt", ""),
                "completion": getattr(step, "completion", ""),
                "model": getattr(step, "model", ""),
                "provider": getattr(step, "provider", ""),
            }
            for step in ctx.trace.get_llm_steps()
        ]
        raise AssertionError(f"Expected 200, got {response.status_code}; body={body}; steps={steps}")

    payload = json.loads(response.content)
    answer = payload.get("bot_response", {}).get("content", "")
    assert isinstance(answer, str) and answer.strip(), "Expected non-empty OpenAI answer"
    return answer


@ai_test("openai live prompts emitted")
async def test_openai_live_prompts(ctx):
    await _call_openai_live("How do I create a post?", ctx)
    expect(ctx.trace).to_have_llm_step(provider="openai", min_times=3)
    _expect_prompt(ctx.trace, filter_contains="Analyze the user's intent")
    _expect_prompt(ctx.trace, filter_contains="Answer to rate:")
    _expect_prompt(ctx.trace, filter_contains="User message:")


@ai_test("openai live end-to-end workflow")
async def test_openai_live_workflow(ctx):
    answer = await _call_openai_live("I want to write and publish my first post, how?", ctx)
    # Assert with message to surface the actual response when it does not mention the expected keyword.
    assert "post" in answer.lower(), f"Expected answer to mention 'post'; got: {answer!r}"
    expect(ctx.trace).to_have_llm_step(provider="openai", min_times=3)
    _expect_prompt(ctx.trace, filter_contains="Analyze the user's intent")
    _expect_prompt(ctx.trace, filter_contains="Answer to rate:")
    _expect_prompt(ctx.trace, filter_contains="User message:")
