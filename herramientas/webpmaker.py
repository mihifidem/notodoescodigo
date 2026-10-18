from PIL import Image

# 1. Cargar la imagen
img = Image.open("herramientas/999.png")

width, height = img.size

# Son 4 fotogramas distribuidos horizontalmente
num_frames = 8
frame_width = width // num_frames

frames = []
for i in range(num_frames):
    left = i * frame_width
    right = left + frame_width
    # Recortar cada escena
    frame = img.crop((left, 0, right, height))
    frames.append(frame)

# 2. Guardar como WebP animado
# duration = duración por fotograma en milisegundos (ej. 150 ms)
frames[0].save(
    "herramientas/PRE1_999.webp",
    format="WEBP",
    save_all=True,
    append_images=frames[1:],
    duration=380,
    loop=0  # 0 significa bucle infinito
)

print("¡Archivo animacion.webp creado con éxito!")