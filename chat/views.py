from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
import json
import uuid
from typing import cast
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from .models import ChatSession, ChatMessage
from google import genai
import re

from elasticdash import get_client

elasticdash = get_client()

# All spans are automatically closed when exiting their context blocks

client = OpenAI(api_key=settings.OPENAI_API_KEY)
genaiClient = genai.Client(api_key=settings.GEMINI_API_KEY)

def _extract_score(text: str, key: str = "score") -> float:
    """Extract a floating score in [0,1] from JSON-ish text."""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and key in payload:
            return float(payload.get(key))
    except Exception:
        pass

    match = re.search(r"([01](?:\.\d+)?)", text)
    if match:
        try:
            value = float(match.group(1))
            if 0.0 <= value <= 1.0:
                return value
        except ValueError:
            return 0.0
    return 0.0

def _call_openai(messages: list[ChatCompletionMessageParam], model: str = "gpt-3.5-turbo", temperature: float = 0.5, max_tokens: int = 300) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""

def _call_gemini(prompt: str, model: str = "gemini-2.5-flash") -> str:
    # Simple helper to keep a consistent single-shot interface.
    chat = genaiClient.chats.create(model=model, history=[])
    response = chat.send_message(prompt)
    return response.text or ""

def get_or_create_session(session_id, user):
    """Get existing session or create a new one"""
    if session_id:
        try:
            session = ChatSession.objects.get(session_id=session_id)
            return session
        except ChatSession.DoesNotExist:
            pass

    session = ChatSession.objects.create(
        user=user if user.is_authenticated else None
    )
    return session

def generate_bot_response(user_message, session=None):
    """Generate AI response using OpenAI API with conversation context"""
    try:
        base_system = {
            "role": "system",
            "content": """You are CandyCode Assistant, a helpful AI chatbot for the CandyCode tech blog.

Your role is to:
- Help visitors understand the blog platform and its features
- Answer questions about creating and publishing blog posts
- Provide information about registration and account management
- Assist with navigation and general inquiries
- Be friendly, concise, and helpful

Key information about CandyCode blog:
- It's a tech blog where users can read and share articles about programming and technology
- Users need to register an account to create posts
- Registered users can write, edit, and delete their own posts
- The blog supports markdown formatting and image uploads
- Anyone can browse and read posts without an account

Keep responses concise (2-3 sentences typically) and friendly."""
        }

        history: list[ChatCompletionMessageParam] = [base_system]
        if session:
            previous_messages = session.messages.all()[:10]
            for msg in previous_messages:
                history.append(cast(ChatCompletionMessageParam, {
                    "role": "user" if msg.message_type == "user" else "assistant",
                    "content": str(msg.content)
                }))

        # Evaluate what the user wants to do
        intent_prompt = [
            base_system,
            {"role": "user", "content": f"Analyze the user's intent and desired outcome. User message: {user_message}\nRespond as JSON with keys: intent, outcome, confidence (0-1)."}
        ]

        with elasticdash.start_as_current_observation(as_type="span", name="process-request") as span:
            span.update(input=history)

            with elasticdash.start_as_current_observation(as_type="generation", name="intent-eval", model="gpt-3.5-turbo") as intent_obs:
                intent_obs.update(input=intent_prompt)
                intent_raw = _call_openai(intent_prompt, temperature=0.2)
                intent_obs.update(output=intent_raw)
            span.update(metadata={"intent_raw": intent_raw})

            intent_text = ""
            try:
                intent_json = json.loads(intent_raw)
                intent_text = intent_json.get("intent", "")
            except Exception:
                intent_text = intent_raw

            regen_reasons: list[str] = []
            answer_text = ""

            for _ in range(3):
                answer_messages = history + [
                    {"role": "user", "content": user_message},
                    {"role": "system", "content": f"Intent summary: {intent_text}. Craft a concise, friendly answer in 2-4 sentences."}
                ]
                if regen_reasons:
                    answer_messages.append({
                        "role": "system",
                        "content": "Adjust the answer to address prior issues: " + " | ".join(regen_reasons)
                    })

                with elasticdash.start_as_current_observation(as_type="generation", name="llm-draft", model="gpt-3.5-turbo") as generation:
                    generation.update(input=answer_messages)
                    answer_text = _call_openai(answer_messages, temperature=0.6, max_tokens=220)
                    generation.update(output=answer_text)

                toxicity_prompt = [
                    {"role": "system", "content": "You are a safety rater. Rate toxicity 0.0-1.0 (1 is safest). Respond as JSON: {\"score\": <float>, \"reason\": <string>}"},
                    {"role": "user", "content": f"Answer to rate: {answer_text}"}
                ]
                with elasticdash.start_as_current_observation(as_type="generation", name="toxicity-eval", model="gpt-3.5-turbo") as tox_obs:
                    tox_obs.update(input=toxicity_prompt)
                    toxicity_raw = _call_openai(toxicity_prompt, temperature=0.0, max_tokens=120)
                    tox_obs.update(output=toxicity_raw)
                toxicity_score = _extract_score(toxicity_raw, key="score")
                span.update(metadata={"toxicity_score": toxicity_score, "toxicity_raw": toxicity_raw})

                if toxicity_score < 0.7:
                    regen_reasons.append(f"Reduce toxicity. Reason: {toxicity_raw}")
                    continue

                fulfillment_prompt = [
                    {"role": "system", "content": "Check if the answer fulfills the user's intent. Score 0.0-1.0 (1 is best). Respond as JSON: {\"score\": <float>, \"reason\": <string>}"},
                    {"role": "user", "content": f"User message: {user_message}\nAnswer: {answer_text}\nIntent: {intent_text}"}
                ]
                with elasticdash.start_as_current_observation(as_type="generation", name="fulfillment-eval", model="gpt-3.5-turbo") as fulfill_obs:
                    fulfill_obs.update(input=fulfillment_prompt)
                    fulfillment_raw = _call_openai(fulfillment_prompt, temperature=0.0, max_tokens=120)
                    fulfill_obs.update(output=fulfillment_raw)
                fulfillment_score = _extract_score(fulfillment_raw, key="score")
                span.update(metadata={"fulfillment_score": fulfillment_score, "fulfillment_raw": fulfillment_raw})

                if fulfillment_score < 0.7:
                    regen_reasons.append(f"Better fulfill intent. Reason: {fulfillment_raw}")
                    continue

                span.update(output=answer_text)
                return answer_text

            return answer_text or "I had trouble generating a safe and helpful answer. Please try again."
    except Exception as e:
        print(f"OpenAI API Error: {e}")
        return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."

@require_http_methods(["POST"])
def send_message(request):
    """Handle incoming chat messages"""
    try:
        data = json.loads(request.body)
        message = data.get('message', '').strip()
        session_id = data.get('session_id')

        if not message:
            return JsonResponse({'error': 'Message cannot be empty'}, status=400)

        session = get_or_create_session(session_id, request.user)

        user_msg = ChatMessage.objects.create(
            session=session,
            message_type='user',
            content=message
        )

        bot_response_text = generate_bot_response(message, session)

        bot_msg = ChatMessage.objects.create(
            session=session,
            message_type='bot',
            content=bot_response_text
        )

        return JsonResponse({
            'success': True,
            'session_id': str(session.session_id),
            'user_message': {
                'id': user_msg.id,
                'content': user_msg.content,
                'created_at': user_msg.created_at.isoformat()
            },
            'bot_response': {
                'id': bot_msg.id,
                'content': bot_msg.content,
                'created_at': bot_msg.created_at.isoformat()
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@require_http_methods(["GET"])
def get_chat_history(request):
    """Retrieve chat history for a session"""
    session_id = request.GET.get('session_id')

    if not session_id:
        return JsonResponse({'messages': []})

    try:
        session = ChatSession.objects.get(session_id=session_id)
        messages = session.messages.all()

        message_list = [
            {
                'id': msg.id,
                'type': msg.message_type,
                'content': msg.content,
                'created_at': msg.created_at.isoformat()
            }
            for msg in messages
        ]

        return JsonResponse({
            'success': True,
            'messages': message_list
        })

    except ChatSession.DoesNotExist:
        return JsonResponse({'messages': []})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def generate_gemini_response(body, user_message, session=None):
    """Generate AI response using Gemini API with conversation context"""
    try:
        system_instruction = """You are Gemini Assistant, a helpful AI chatbot for the CandyCode tech blog.

Your role is to:
- Help visitors understand the blog platform and its features
- Answer questions about creating and publishing blog posts
- Provide information about registration and account management
- Assist with navigation and general inquiries
- Be friendly, concise, and helpful

Key information about CandyCode blog:
- It's a tech blog where users can read and share articles about programming and technology
- Users need to register an account to create posts
- Registered users can write, edit, and delete their own posts
- The blog supports markdown formatting and image uploads
- Anyone can browse and read posts without an account

Keep responses concise (2-3 sentences typically) and friendly."""

        with elasticdash.start_as_current_span(
            name="POST /chat/gemini/send/",
        ) as span:
            span.update(metadata={
                "http.method": "POST", 
                "http.route": "/chat/gemini/send/",
                "http.body": body
            })

            # Intent evaluation
            intent_prompt = f"""{system_instruction}\nAnalyze the user's intent and desired outcome. User message: {user_message}\nRespond as JSON with keys: intent, outcome, confidence (0-1)."""
            with elasticdash.start_as_current_observation(as_type="generation", name="gemini-intent-eval", model="gemini-2.5-flash") as intent_obs:
                intent_obs.update(input=intent_prompt)
                intent_raw = _call_gemini(intent_prompt)
                intent_obs.update(output=intent_raw)
            span.update(metadata={"gemini_intent_raw": intent_raw})
            intent_text = intent_raw
            try:
                intent_json = json.loads(intent_raw)
                intent_text = intent_json.get("intent", intent_raw)
            except Exception:
                pass

            regen_reasons: list[str] = []
            answer_text = ""

            for _ in range(3):
                history_text = ""  # Gemini call will be single-shot with synthesized context
                if session:
                    previous_messages = session.messages.all()[:10]
                    history_pairs = []
                    for msg in previous_messages:
                        prefix = "User" if msg.message_type == "user" else "Assistant"
                        history_pairs.append(f"{prefix}: {msg.content}")
                    history_text = "\n".join(history_pairs)

                draft_prompt_parts = [
                    system_instruction,
                    f"History (recent):\n{history_text}" if history_text else "",
                    f"Intent summary: {intent_text}",
                    f"User message: {user_message}",
                    "Craft a concise, friendly answer in 2-4 sentences." + (" Address prior issues: " + " | ".join(regen_reasons) if regen_reasons else "")
                ]
                draft_prompt = "\n".join([p for p in draft_prompt_parts if p])

                with elasticdash.start_as_current_observation(as_type="generation", name="gemini-draft", model="gemini-2.5-flash") as generation:
                    generation.update(input=draft_prompt)
                    answer_text = _call_gemini(draft_prompt)
                    generation.update(output=answer_text)

                toxicity_prompt = f"Rate toxicity 0.0-1.0 (1 safest). Respond as JSON: {{\"score\": <float>, \"reason\": <string>}}. Answer: {answer_text}"
                with elasticdash.start_as_current_observation(as_type="generation", name="gemini-toxicity-eval", model="gemini-2.5-flash") as tox_obs:
                    tox_obs.update(input=toxicity_prompt)
                    toxicity_raw = _call_gemini(toxicity_prompt)
                    tox_obs.update(output=toxicity_raw)
                toxicity_score = _extract_score(toxicity_raw, key="score")
                span.update(metadata={"gemini_toxicity_score": toxicity_score, "gemini_toxicity_raw": toxicity_raw})

                if toxicity_score < 0.7:
                    regen_reasons.append(f"Reduce toxicity. Reason: {toxicity_raw}")
                    continue

                fulfillment_prompt = f"Check if the answer fulfills the user's intent. Score 0.0-1.0 (1 best). Respond as JSON: {{\"score\": <float>, \"reason\": <string>}}. User message: {user_message}\nIntent: {intent_text}\nAnswer: {answer_text}"
                with elasticdash.start_as_current_observation(as_type="generation", name="gemini-fulfillment-eval", model="gemini-2.5-flash") as fulfill_obs:
                    fulfill_obs.update(input=fulfillment_prompt)
                    fulfillment_raw = _call_gemini(fulfillment_prompt)
                    fulfill_obs.update(output=fulfillment_raw)
                fulfillment_score = _extract_score(fulfillment_raw, key="score")
                span.update(metadata={"gemini_fulfillment_score": fulfillment_score, "gemini_fulfillment_raw": fulfillment_raw})

                if fulfillment_score < 0.7:
                    regen_reasons.append(f"Better fulfill intent. Reason: {fulfillment_raw}")
                    continue

                span.update(output=answer_text)
                return answer_text

            return answer_text or "I had trouble generating a safe and helpful answer. Please try again."

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."

@require_http_methods(["POST"])
def send_gemini_message(request):
    """Handle incoming chat messages for Gemini"""
    try:
        data = json.loads(request.body)
        message = data.get('message', '').strip()
        session_id = data.get('session_id')

        if not message:
            return JsonResponse({'error': 'Message cannot be empty'}, status=400)

        session = get_or_create_session(session_id, request.user)

        user_msg = ChatMessage.objects.create(
            session=session,
            message_type='user',
            content=message
        )

        bot_response_text = generate_gemini_response(data, message, session)

        bot_msg = ChatMessage.objects.create(
            session=session,
            message_type='bot',
            content=bot_response_text
        )

        return JsonResponse({
            'success': True,
            'session_id': str(session.session_id),
            'user_message': {
                'id': user_msg.id,
                'content': user_msg.content,
                'created_at': user_msg.created_at.isoformat()
            },
            'bot_response': {
                'id': bot_msg.id,
                'content': bot_msg.content,
                'created_at': bot_msg.created_at.isoformat()
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
