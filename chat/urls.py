from django.urls import path
from . import views

urlpatterns = [
    path('send/', views.send_message, name='chat_send_message'),
    path('history/', views.get_chat_history, name='chat_history'),
    path('gemini/send/', views.send_gemini_message, name='gemini_send_message'),
]
