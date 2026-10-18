"""Management command: python manage.py generar_posts"""
import logging

from django.core.management.base import BaseCommand

from apps.blog.services.scraper import obtener_articulos_medium
from apps.blog.services.ai_generator import generar_contenido, generar_imagen_dall_e
from apps.blog.services.post_builder import construir_datos_post
from apps.blog.services.save_post import guardar_post

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Genera posts de blog automáticamente desde Medium usando IA."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keyword",
            type=str,
            default="python",
            help="Keyword/tag de Medium a buscar (default: python)",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=3,
            help="Número máximo de artículos a procesar (default: 3)",
        )
        parser.add_argument(
            "--categoria",
            type=str,
            default="Tecnología",
            help="Categoría del blog a asignar (default: Tecnología)",
        )
        parser.add_argument(
            "--publicar",
            action="store_true",
            default=False,
            help="Publicar los posts directamente (default: False, quedan como borrador)",
        )
        parser.add_argument(
            "--con-imagen",
            action="store_true",
            default=False,
            help="Generar imagen con DALL·E 3 para cada post (más lento y caro)",
        )

    def handle(self, *args, **options):
        keyword = options["keyword"]
        max_items = options["max"]
        categoria = options["categoria"]
        publicar = options["publicar"]
        con_imagen = options["con_imagen"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n🤖 Generando posts | keyword='{keyword}' | max={max_items} | publicar={publicar}\n"
        ))

        # 1. Scraping
        self.stdout.write("📡 Obteniendo artículos desde Medium...")
        articulos = obtener_articulos_medium(keyword=keyword, max_items=max_items)

        if not articulos:
            self.stdout.write(self.style.WARNING("⚠️  No se encontraron artículos. Revisa la keyword."))
            return

        self.stdout.write(self.style.SUCCESS(f"✅ {len(articulos)} artículos obtenidos."))

        creados = 0
        omitidos = 0
        errores = 0

        for i, articulo in enumerate(articulos, start=1):
            self.stdout.write(f"\n[{i}/{len(articulos)}] 📝 {articulo.titulo[:70]}")

            # 2. Procesar con IA
            self.stdout.write("    🧠 Generando contenido con IA...")
            ai_data = generar_contenido(articulo.titulo, articulo.contenido)

            if not ai_data:
                self.stdout.write(self.style.ERROR("    ❌ Error en generación IA. Saltando."))
                errores += 1
                continue

            # 3. Construir datos del post
            datos = construir_datos_post(ai_data, fuente_url=articulo.link)

            # 4. Generar imagen (opcional)
            imagen_bytes = None
            if con_imagen:
                self.stdout.write("    🖼️  Generando imagen con DALL·E...")
                imagen_bytes = generar_imagen_dall_e(datos["titulo"])
                if imagen_bytes:
                    self.stdout.write("    ✅ Imagen generada.")
                else:
                    self.stdout.write(self.style.WARNING("    ⚠️  No se pudo generar imagen."))

            # 5. Guardar en DB
            post = guardar_post(
                datos,
                categoria_nombre=categoria,
                imagen_bytes=imagen_bytes,
                publicado=publicar,
            )

            if post:
                estado = "publicado" if publicar else "borrador"
                self.stdout.write(self.style.SUCCESS(
                    f"    ✅ Guardado (id={post.pk}) como {estado}: {post.titulo[:60]}"
                ))
                creados += 1
            else:
                self.stdout.write(self.style.WARNING("    ⏭️  Duplicado, ignorado."))
                omitidos += 1

        # Resumen final
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n📊 Resumen: {creados} creados | {omitidos} omitidos | {errores} errores\n"
        ))
        if creados and not publicar:
            self.stdout.write(self.style.WARNING(
                "ℹ️  Los posts quedaron como borradores. Usa --publicar para publicarlos directamente."
            ))
