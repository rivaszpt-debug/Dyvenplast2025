from google import genai

client = genai.Client()

print("\nConsultando los servidores de Google...")
print("Modelos disponibles para tu llave:\n")

# Le pedimos a la API que nos liste sus modelos
for modelo in client.models.list():
    # Filtramos para ver solo los que dicen "gemini"
    if "gemini" in modelo.name:
        print(modelo.name)