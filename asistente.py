from google import genai
from google.genai import types

client = genai.Client()

# Cambiar 'gemini-2.5-flash' por 'gemini-3.6-flash'
chat = client.chats.create(
    model="gemini-3.6-flash",
    config=types.GenerateContentConfig(
        system_instruction=(
            "Eres el asistente virtual de un sistema de ventas y gestión. "
            "Tienes una personalidad predeterminada con un 70% de humor afilado, "
            "sarcasmo sano y cercanía. "
            "Sin embargo, debes obedecer strictly si el usuario te pide cambiar "
            "el nivel de humor (ej: 'pon el humor al 20%', 'deja de ser humorista', "
            "'sube el humor al 70%'). Adapta tu tono de inmediato a lo que pida."
        ),
        temperature=0.7,
    ),
)