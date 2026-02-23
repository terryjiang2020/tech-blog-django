import os
from unittest.mock import patch
import sys
from pathlib import Path

from elasticdash_test import ai_test, before_all, after_all, install_ai_interceptor, uninstall_ai_interceptor, expect

# Django setup once for test module
@before_all
def setup_suite():
    # Ensure project root is on sys.path so candycode settings import works in tests
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "candycode.settings")
    # Django imports delayed until settings are in place
    import django
    django.setup()
    install_ai_interceptor()

@after_all
def teardown_suite():
    uninstall_ai_interceptor()


@ai_test("gemini intent evaluation occurs first")
async def test_gemini_intent(ctx):
    import chat.views as views

    seen = []

    def fake_call(prompt: str, model: str = "gemini-2.5-flash") -> str:
        if "Analyze the user's intent" in prompt:
            seen.append("intent")
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="intent-eval", completion="intent-ok")
            return '{"intent":"help"}'
        seen.append("other")
        return "ok"

    with patch("chat.views._call_gemini", side_effect=fake_call):
        views.generate_gemini_response({}, "Hi", session=None)

    assert seen and seen[0] == "intent"
    expect(ctx.trace).to_have_llm_step(model="gemini-2.5-flash", provider="gemini", contains="intent-eval")


@ai_test("gemini toxicity gate regenerates when <0.7")
async def test_gemini_toxicity(ctx):
    import chat.views as views

    calls = {"tox": 0}

    def fake_call(prompt: str, model: str = "gemini-2.5-flash") -> str:
        if "Analyze the user's intent" in prompt:
            return '{"intent":"help"}'
        if "Rate toxicity" in prompt:
            calls["tox"] += 1
            # First pass fails threshold, second passes
            score = 0.6 if calls["tox"] == 1 else 0.9
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="toxicity", completion=str(score))
            return f'{{"score":{score},"reason":"iter {calls["tox"]}"}}'
        if "Check if the answer fulfills" in prompt:
            return '{"score":0.9}'
        return "Draft answer"

    with patch("chat.views._call_gemini", side_effect=fake_call):
        views.generate_gemini_response({}, "Hi", session=None)

    assert calls["tox"] >= 2
    expect(ctx.trace).to_have_llm_step(model="gemini-2.5-flash", provider="gemini", contains="toxicity", min_times=2)


@ai_test("gemini fulfillment gate regenerates when <0.7")
async def test_gemini_fulfillment(ctx):
    import chat.views as views

    calls = {"fulfill": 0}

    def fake_call(prompt: str, model: str = "gemini-2.5-flash") -> str:
        if "Analyze the user's intent" in prompt:
            return '{"intent":"help"}'
        if "Rate toxicity" in prompt:
            return '{"score":0.9}'
        if "Check if the answer fulfills" in prompt:
            calls["fulfill"] += 1
            score = 0.6 if calls["fulfill"] == 1 else 0.9
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="fulfillment", completion=str(score))
            return f'{{"score":{score},"reason":"iter {calls["fulfill"]}"}}'
        return "Draft answer"

    with patch("chat.views._call_gemini", side_effect=fake_call):
        views.generate_gemini_response({}, "Hi", session=None)

    assert calls["fulfill"] >= 2
    expect(ctx.trace).to_have_llm_step(model="gemini-2.5-flash", provider="gemini", contains="fulfillment", min_times=2)


@ai_test("gemini prompt order intent->draft->toxicity->fulfillment")
async def test_gemini_prompt_order(ctx):
    import chat.views as views

    order: list[str] = []

    def fake_call(prompt: str, model: str = "gemini-2.5-flash") -> str:
        if "Analyze the user's intent" in prompt:
            order.append("intent")
            return '{"intent":"help"}'
        if "Rate toxicity" in prompt:
            order.append("toxicity")
            return '{"score":0.9}'
        if "Check if the answer fulfills" in prompt:
            order.append("fulfillment")
            return '{"score":0.9}'
        order.append("draft")
        return "Draft answer"

    with patch("chat.views._call_gemini", side_effect=fake_call):
        views.generate_gemini_response({}, "Hi", session=None)

    assert order == ["intent", "draft", "toxicity", "fulfillment"]


@ai_test("gemini workflow runs intent, toxicity, fulfillment gates")
async def test_gemini_workflow(ctx):
    import chat.views as views

    call_order: list[str] = []

    def fake_call(prompt: str, model: str = "gemini-2.5-flash") -> str:
        if "Analyze the user's intent" in prompt:
            call_order.append("intent")
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="intent-eval", completion="intent-ok")
            return '{"intent":"help","outcome":"info","confidence":0.9}'

        if "Rate toxicity" in prompt:
            call_order.append("toxicity")
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="toxicity-eval", completion="score 0.9")
            return '{"score":0.9,"reason":"safe"}'

        if "Check if the answer fulfills" in prompt:
            call_order.append("fulfillment")
            ctx.trace.record_llm_step(model=model, provider="gemini", prompt="fulfillment-eval", completion="score 0.85")
            return '{"score":0.85,"reason":"covers intent"}'

        call_order.append("draft")
        ctx.trace.record_llm_step(model=model, provider="gemini", prompt="draft", completion="draft answer")
        return "Draft CandyCode answer."

    with patch("chat.views._call_gemini", side_effect=fake_call):
        answer = views.generate_gemini_response({}, "How do I create a post?", session=None)

    # Final answer should come from the draft path when all gates pass
    assert "CandyCode answer" in answer

    # Verify the workflow hit intent -> draft -> toxicity -> fulfillment exactly once
    assert call_order == ["intent", "draft", "toxicity", "fulfillment"]

    # Trace should have at least the three evaluation calls recorded
    expect(ctx.trace).to_have_llm_step(model="gemini-2.5-flash", provider="gemini", min_times=3)
