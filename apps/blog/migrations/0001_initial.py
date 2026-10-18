# Generated manually for blog app
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CategoriaBlog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=120, unique=True)),
                ('slug', models.SlugField(max_length=140, unique=True)),
                ('descripcion', models.TextField(blank=True)),
                ('activa', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Categoria del blog',
                'verbose_name_plural': 'Categorias del blog',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='HashtagBlog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=60, unique=True)),
                ('slug', models.SlugField(max_length=80, unique=True)),
            ],
            options={
                'verbose_name': 'Hashtag',
                'verbose_name_plural': 'Hashtags',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='SubcategoriaBlog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=120)),
                ('slug', models.SlugField(max_length=140)),
                ('descripcion', models.TextField(blank=True)),
                ('activa', models.BooleanField(default=True)),
                ('categoria', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subcategorias', to='blog.categoriablog')),
            ],
            options={
                'verbose_name': 'Subcategoria del blog',
                'verbose_name_plural': 'Subcategorias del blog',
                'ordering': ['categoria__nombre', 'nombre'],
            },
        ),
        migrations.CreateModel(
            name='PostBlog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titulo', models.CharField(max_length=220)),
                ('slug', models.SlugField(max_length=240, unique=True)),
                ('resumen', models.TextField(blank=True)),
                ('contenido_publico', models.TextField(help_text='Texto visible para todos los roles.')),
                ('contenido_privado', models.TextField(blank=True, help_text='Texto solo visible para membresia premium.')),
                ('imagen', models.ImageField(blank=True, null=True, upload_to='blog/')),
                ('visibilidad', models.CharField(choices=[('publico', 'Publico total'), ('semipublico', 'Semipublico'), ('privado', 'Privado membresia premium')], default='publico', max_length=20)),
                ('publicado', models.BooleanField(default=True)),
                ('destacado', models.BooleanField(default=False)),
                ('publicado_en', models.DateTimeField(default=django.utils.timezone.now)),
                ('puntos_lectura', models.PositiveIntegerField(default=10)),
                ('segundos_lectura_requeridos', models.PositiveIntegerField(blank=True, help_text='Si esta vacio, se calcula automaticamente por numero de palabras.', null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('categoria', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='posts', to='blog.categoriablog')),
                ('hashtags', models.ManyToManyField(blank=True, related_name='posts', to='blog.hashtagblog')),
                ('subcategoria', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='posts', to='blog.subcategoriablog')),
            ],
            options={
                'verbose_name': 'Post del blog',
                'verbose_name_plural': 'Posts del blog',
                'ordering': ['-destacado', '-publicado_en'],
            },
        ),
        migrations.CreateModel(
            name='ValoracionPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='valoraciones', to='blog.postblog')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='valoraciones_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Valoracion de post',
                'verbose_name_plural': 'Valoraciones de posts',
                'ordering': ['-creada_en'],
            },
        ),
        migrations.CreateModel(
            name='LecturaPostUsuario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('iniciada_en', models.DateTimeField(default=django.utils.timezone.now)),
                ('completada_en', models.DateTimeField(blank=True, null=True)),
                ('puntos_otorgados', models.BooleanField(default=False)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lecturas', to='blog.postblog')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lecturas_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Lectura de post',
                'verbose_name_plural': 'Lecturas de posts',
                'ordering': ['-iniciada_en'],
            },
        ),
        migrations.AddConstraint(
            model_name='subcategoriablog',
            constraint=models.UniqueConstraint(fields=('categoria', 'slug'), name='uniq_subcategoria_blog_categoria_slug'),
        ),
        migrations.AddConstraint(
            model_name='valoracionpost',
            constraint=models.UniqueConstraint(fields=('usuario', 'post'), name='uniq_valoracion_post_usuario_post'),
        ),
        migrations.AddConstraint(
            model_name='lecturapostusuario',
            constraint=models.UniqueConstraint(fields=('usuario', 'post'), name='uniq_lectura_post_usuario_post'),
        ),
    ]
