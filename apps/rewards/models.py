"""Models for the rewards/marketplace app."""
from django.db import models
from django.conf import settings


class Recompensa(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to='recompensas/', null=True, blank=True)
    puntos_necesarios = models.PositiveIntegerField()
    stock = models.IntegerField(default=-1, help_text='-1 = stock ilimitado')
    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Recompensa'
        verbose_name_plural = 'Recompensas'
        ordering = ['puntos_necesarios']

    def __str__(self):
        return self.nombre

    @property
    def disponible(self):
        return self.activa and (self.stock == -1 or self.stock > 0)


class CanjeRecompensa(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('procesado', 'Procesado'),
        ('rechazado', 'Rechazado'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='canjes'
    )
    recompensa = models.ForeignKey(Recompensa, on_delete=models.CASCADE, related_name='canjes')
    fecha_canje = models.DateTimeField(auto_now_add=True)
    puntos_gastados = models.PositiveIntegerField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    notas_admin = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Canje'
        verbose_name_plural = 'Canjes'
        ordering = ['-fecha_canje']

    def __str__(self):
        return f'{self.usuario.username} → {self.recompensa.nombre} ({self.estado})'


class Seccion(models.Model):
    CATEGORIA_CHOICES = [
        ('cv', 'CV'),
        ('curso', 'Curso'),
        ('logro', 'Logro'),
        ('mision', 'Misión'),
        ('comunidad', 'Comunidad'),
    ]

    CV_SECCIONES = {  # noqa: RUF012
        'datos_personales': {
            'nombre': 'Datos personales',
            'categoria': 'cv',
            'puntos_base': 25,
        },
        'creacion_perfil_cv_web': {
            'nombre': 'Creación de perfil CV web',
            'categoria': 'cv',
            'puntos_base': 25,
        },
        'perfil_profesional': {
            'nombre': 'Perfil profesional',
            'categoria': 'cv',
            'puntos_base': 25,
        },
        'redes_sociales': {
            'nombre': 'Redes profesionales',
            'categoria': 'cv',
            'puntos_base': 15,
        },
        'otros_datos': {
            'nombre': 'Otros datos',
            'categoria': 'cv',
            'puntos_base': 10,
        },
        'experiencia_laboral': {
            'nombre': 'Experiencia laboral',
            'categoria': 'cv',
            'puntos_base': 30,
        },
        'educacion': {
            'nombre': 'Formación académica',
            'categoria': 'cv',
            'puntos_base': 25,
        },
        'formacion_complementaria': {
            'nombre': 'Formación complementaria',
            'categoria': 'cv',
            'puntos_base': 20,
        },
        'habilidades': {
            'nombre': 'Habilidades',
            'categoria': 'cv',
            'puntos_base': 20,
        },
        'idiomas': {
            'nombre': 'Idiomas',
            'categoria': 'cv',
            'puntos_base': 20,
        },
        'proyectos': {
            'nombre': 'Proyectos',
            'categoria': 'cv',
            'puntos_base': 25,
        },
        'logros': {
            'nombre': 'Logros y reconocimientos',
            'categoria': 'cv',
            'puntos_base': 20,
        },
        'voluntariado': {
            'nombre': 'Voluntariado',
            'categoria': 'cv',
            'puntos_base': 20,
        },
        'intereses': {
            'nombre': 'Intereses',
            'categoria': 'cv',
            'puntos_base': 10,
        },
    }

    codigo = models.CharField(max_length=80, unique=True, blank=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    categoria = models.CharField(max_length=40, choices=CATEGORIA_CHOICES, default='cv', db_index=True)
    puntos_base = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sección'
        verbose_name_plural = 'Secciones'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    @classmethod
    def sync_cv_sections(cls):
        for codigo, config in cls.CV_SECCIONES.items():
            seccion, created = cls.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'nombre': config['nombre'],
                    'descripcion': f'Sección del CV: {config["nombre"]}.',
                    'categoria': config.get('categoria', 'cv'),
                    'puntos_base': config['puntos_base'],
                    'activa': True,
                },
            )

            if not created:
                updated = False
                if seccion.nombre != config['nombre']:
                    seccion.nombre = config['nombre']
                    updated = True
                if seccion.categoria != config.get('categoria', 'cv'):
                    seccion.categoria = config.get('categoria', 'cv')
                    updated = True
                if seccion.puntos_base != config['puntos_base']:
                    seccion.puntos_base = config['puntos_base']
                    updated = True
                if not seccion.descripcion:
                    seccion.descripcion = f'Sección del CV: {config["nombre"]}.'
                    updated = True
                if updated:
                    seccion.save(update_fields=['nombre', 'descripcion', 'categoria', 'puntos_base'])

        return list(cls.objects.filter(codigo__in=cls.CV_SECCIONES).order_by('nombre'))

    @classmethod
    def registrar_puntos_usuario(cls, usuario, tema=None, puntos=None, notas=None, nombre_seccion=None, codigo_seccion=None):
        if usuario is None:
            return None

        if tema is not None:
            puntos = puntos if puntos is not None else getattr(tema, 'puntos_otorgados', 0)
            curso = getattr(tema, 'curso', None)
            nombre = getattr(tema, 'titulo', None)
            if nombre is None:
                nombre = str(tema)
            if curso is not None:
                nombre = f'{curso.nombre} - {nombre}'
            codigo = codigo_seccion or f'tema:{nombre.lower().replace(" ", "-")}'
        else:
            nombre = nombre_seccion or 'Sección sin nombre'
            codigo = codigo_seccion or nombre.lower().replace(' ', '_')
            puntos = puntos if puntos is not None else 0

        if puntos <= 0:
            return None

        descripcion = notas or (
            f'Puntos otorgados por completar la sección "{nombre}".'
        )

        seccion, _ = cls.objects.get_or_create(
            codigo=codigo,
            defaults={
                'nombre': nombre,
                'descripcion': descripcion,
                'puntos_base': puntos,
                'activa': True,
            },
        )

        if seccion.nombre != nombre:
            seccion.nombre = nombre
            seccion.save(update_fields=['nombre'])

        registro, creado = PuntosSeccionUsuario.objects.get_or_create(
            usuario=usuario,
            seccion=seccion,
            defaults={'puntos_otorgados': puntos, 'notas': descripcion},
        )

        if not creado and registro.puntos_otorgados != puntos:
            registro.puntos_otorgados = puntos
            registro.notas = descripcion
            registro.save(update_fields=['puntos_otorgados', 'notas'])

        return registro


class PuntosSeccionUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='puntos_por_seccion',
    )
    seccion = models.ForeignKey(
        Seccion,
        on_delete=models.CASCADE,
        related_name='puntos_usuarios',
    )
    puntos_otorgados = models.PositiveIntegerField(default=0)
    otorgado_en = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Puntos por sección'
        verbose_name_plural = 'Puntos por secciones'
        unique_together = ('usuario', 'seccion')
        ordering = ['-otorgado_en']

    def __str__(self):
        return f'{self.usuario.username} → {self.seccion.nombre}: {self.puntos_otorgados} pts'
