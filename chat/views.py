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

from elasticdash import get_client

elasticdash = get_client()

# All spans are automatically closed when exiting their context blocks

client = OpenAI(api_key=settings.OPENAI_API_KEY)
genaiClient = genai.Client(api_key=settings.GEMINI_API_KEY)

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
        messages: list[ChatCompletionMessageParam] = [
            {
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
        ]

        if session:
            previous_messages = session.messages.all()[:10]
            for msg in previous_messages:
                messages.append(cast(ChatCompletionMessageParam, {
                    "role": "user" if msg.message_type == "user" else "assistant",
                    "content": str(msg.content)
                }))

        messages.append(cast(ChatCompletionMessageParam, {
            "role": "user",
            "content": user_message
        }))

        # Create a span using a context manager
        with elasticdash.start_as_current_observation(as_type="span", name="process-request") as span:
            span.update(input=messages)
        
            # Create a nested generation for an LLM call
            with elasticdash.start_as_current_observation(as_type="generation", name="llm-response", model="gpt-3.5-turbo") as generation:
                generation.update(input=messages)
                # Your LLM call logic here
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    max_tokens=200,
                    temperature=0.7
                )
                generation.update(output=response.choices[0].message.content)
                span.update(output=response.choices[0].message.content)
                return response.choices[0].message.content

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

def generate_gemini_response(user_message, session=None):
    """Generate AI response using Gemini API with conversation context"""
    try:
        # Build conversation history for Gemini
        history = []
        
        if session:
            previous_messages = session.messages.all()[:10]
            for msg in previous_messages:
                history.append({
                    "role": "user" if msg.message_type == "user" else "model",
                    "parts": [{"text": msg.content}]
                })

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

        with elasticdash.start_as_current_observation(as_type="span", name="process-gemini-request") as span:
            span.update(input={"message": user_message, "history": history})

            with elasticdash.start_as_current_observation(as_type="generation", name="gemini-response", model="gemini-1.5-flash") as generation:
                generation.update(input={"message": user_message, "history": history})

                history.insert(0, {
                    "role": "user",
                    "parts": [{"text": system_instruction}]
                })
                
                # Start chat with history
                chat = genaiClient.chats.create(
                    model="gemini-2.5-flash",
                    history=history,
                )
                
                # Send message and get response
                response = chat.send_message(user_message)
                bot_response = response.text

                generation.update(output=bot_response)
                span.update(output=bot_response)
                return bot_response

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

        bot_response_text = generate_gemini_response(message, session)

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
