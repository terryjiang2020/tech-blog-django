# Chat Widget Quick Start Guide

## What's Been Implemented

A fully functional AI-powered chat widget using OpenAI GPT-3.5-turbo has been added to your CandyCode blog.

## Installation & Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Verify Environment Variables
Make sure `.env` file contains:
```
OPENAI_API_KEY=sk-proj-...
```

### 3. Run Migrations (Already Done)
```bash
python manage.py migrate
```

### 4. Start the Server
```bash
python manage.py runserver
```

## How to Use

1. Visit any page on your blog at `http://localhost:8000`
2. Look for the purple circular chat button at the bottom right
3. Click it to open the chat panel
4. Type a message and press Enter or click Send
5. The AI assistant will respond instantly

## Example Conversations

Try asking:
- "Hello, what is this blog about?"
- "How do I create a new post?"
- "Do I need to register?"
- "What features does this blog have?"
- "How do I edit my profile?"

## Features

✅ **Real AI Responses** - Powered by OpenAI GPT-3.5-turbo
✅ **Conversation Context** - Remembers last 10 messages
✅ **Persistent Sessions** - Chat history saved across page visits
✅ **No Login Required** - Anyone can chat
✅ **Beautiful UI** - Modern gradient design with smooth animations
✅ **Mobile Responsive** - Works on all devices
✅ **Database Storage** - All conversations saved for analysis

## Admin Panel

Access chat data at `http://localhost:8000/admin/chat/`

You can view:
- All chat sessions
- Individual messages
- Search and filter conversations

## Configuration

### Change AI Model
Edit `chat/views.py` line 66:
```python
model="gpt-4"  # Change from gpt-3.5-turbo to gpt-4
```

### Adjust Response Length
Edit `chat/views.py` line 69:
```python
max_tokens=300  # Increase from 200 for longer responses
```

### Modify AI Personality
Edit the system prompt in `chat/views.py` lines 30-49 to customize the chatbot's behavior.

## Troubleshooting

### Chat button not appearing?
- Clear browser cache
- Check browser console for JavaScript errors
- Verify `chat_widget.html` is in `templates/` directory

### Bot not responding?
- Check OpenAI API key is valid in `.env`
- Verify you have OpenAI API credits
- Check Django logs for error messages
- Test API key: `python -c "from openai import OpenAI; print(OpenAI(api_key='your-key').models.list())"`

### Database errors?
- Run: `python manage.py migrate`
- Check if `chat` app is in `INSTALLED_APPS`

## Cost Considerations

OpenAI GPT-3.5-turbo pricing (as of 2024):
- Input: ~$0.0005 per 1K tokens
- Output: ~$0.0015 per 1K tokens

Estimated cost per conversation:
- Average chat: ~200-300 tokens
- Cost: ~$0.0005 per exchange

Monitor usage at: https://platform.openai.com/usage

## Security Notes

- ✅ CSRF protection enabled
- ✅ API key stored securely in `.env`
- ✅ Input validation on messages
- ⚠️ Consider adding rate limiting for production
- ⚠️ Monitor API usage to prevent abuse

## Files Modified

```
chat/                          # New Django app
├── models.py                  # ChatSession and ChatMessage models
├── views.py                   # OpenAI integration
├── urls.py                    # API endpoints
├── admin.py                   # Admin panel config
└── templates/
    └── chat_widget.html       # Complete UI

candycode/
├── settings.py                # Added chat app and OpenAI config
└── urls.py                    # Added chat URLs

templates/
├── base.html                  # Included chat widget
└── chat_widget.html           # Chat UI (copy)

requirements.txt               # Added openai==1.57.4
.env                          # Contains OPENAI_API_KEY
```

## Support

For detailed documentation, see `CHAT_IMPLEMENTATION.md`

For issues or questions, check:
- Django logs: Terminal where runserver is running
- Browser console: F12 → Console tab
- OpenAI status: https://status.openai.com

---

**Implementation Complete** ✨

The chat widget is now live and ready to assist your blog visitors with intelligent, context-aware responses!
