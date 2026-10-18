"""Custom user model and notifications."""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Aula(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    direccion = models.CharField(max_length=255)
    horario = models.CharField(max_length=150)
    cursos = models.ManyToManyField(
        'courses.Curso',
        blank=True,
        related_name='aulas_disponibles',
        verbose_name='Cursos disponibles',
    )

    class Meta:
        verbose_name = 'Aula'
        verbose_name_plural = 'Aulas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class User(AbstractUser):
    GENERO_CHOICES = [
        ('hombre', 'Hombre'),
        ('mujer', 'Mujer'),
    ]

    DUCK_SPECIALTY_CHOICES = [
        ('developer', 'Developer'),
        ('systems', 'Sistemas'),
        ('cybersecurity', 'Ciberseguridad'),
        ('gamer', 'Gamer'),
        ('data_ia', 'Data IA'),
    ]

    ROLE_CHOICES = [
        ('alumno', 'Alumno'),
        ('basic', 'Basic'),
        ('premium', 'Premium'),
    ]

    NIVEL_CHOICES = [
        ('noob', 'Noob'),
        ('youngling', 'Youngling'),
        ('padawan', 'Padawan'),
        ('caballero_codigo', 'Caballero del codigo'),
        ('maestro_codigo', 'Maestro del codigo'),
        ('gran_maestro_jedi', 'Gran Maestro Jedi'),
    ]

    # Minimum total points required to reach each level.
    NIVEL_UMBRAL_PUNTOS = [
        ('noob', 0),
        ('youngling', 1000),
        ('padawan', 2500),
        ('caballero_codigo', 5000),
        ('maestro_codigo', 9000),
        ('gran_maestro_jedi', 15000),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='basic')
    puntos = models.IntegerField(default=0, help_text='Saldo canjeables actual')
    puntos_totales = models.IntegerField(default=0, help_text='Puntos acumulados históricos (nunca decrementan)')
    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='noob')
    streak = models.IntegerField(default=0, help_text='Racha de días consecutivos de acceso')
    fecha_ultimo_acceso = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    bio = models.TextField(blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name='Fecha de nacimiento')
    genero = models.CharField(max_length=10, choices=GENERO_CHOICES, blank=True, default='')
    duck_specialty = models.CharField(
        max_length=20, choices=DUCK_SPECIALTY_CHOICES, blank=True, default='',
        verbose_name='Especialidad Ducky',
    )
    aula = models.ForeignKey(
        Aula,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='usuarios',
        verbose_name='Aula',
    )

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    def __str__(self):
        return self.username

    @property
    def userProfileArenas(self):
        return getattr(self, 'user_profile_arenas', None)

    def get_cursos_disponibles(self):
        from apps.courses.models import Curso

        cursos_inscritos_ids = self.inscripciones.values_list('curso_id', flat=True)
        filtros = Q(pk__in=cursos_inscritos_ids)

        if self.aula_id:
            filtros |= Q(aulas_disponibles=self.aula)
        else:
            filtros = Q(activo=True)

        return Curso.objects.filter(filtros, activo=True).distinct()

    def puede_inscribirse_en_curso(self, curso) -> bool:
        if not self.aula_id or not curso.activo:
            return False
        return self.aula.cursos.filter(pk=curso.pk, activo=True).exists()

    # ------------------------------------------------------------------
    # Gamification helpers
    # ------------------------------------------------------------------
    def agregar_puntos(self, cantidad: int) -> None:
        """Add points to both spendable balance and cumulative total, then update level."""
        type(self).objects.filter(pk=self.pk).update(
            puntos=F('puntos') + cantidad, puntos_totales=F('puntos_totales') + cantidad,
        )
        self.refresh_from_db(fields=['puntos', 'puntos_totales'])
        self.actualizar_nivel()

    def actualizar_nivel(self) -> None:
        """Set level based on total accumulated points (never affected by redemptions)."""
        nuevo = 'noob'
        for nivel, umbral in self.NIVEL_UMBRAL_PUNTOS:
            if self.puntos_totales >= umbral:
                nuevo = nivel
        if nuevo != self.nivel:
            self.nivel = nuevo
            self.save(update_fields=['nivel'])

    def actualizar_streak(self) -> None:
        """Increment or reset the daily streak."""
        today = timezone.now().date()
        if self.fecha_ultimo_acceso:
            diff = (today - self.fecha_ultimo_acceso).days
            if diff == 1:
                self.streak += 1
            elif diff > 1:
                self.streak = 1
            # diff == 0 means same day – do nothing
        else:
            self.streak = 1
        self.fecha_ultimo_acceso = today
        self.save(update_fields=['streak', 'fecha_ultimo_acceso'])

    @property
    def progreso_nivel(self) -> int:
        """Return progress percentage towards the next level (based on total accumulated points)."""
        niveles = self.NIVEL_UMBRAL_PUNTOS
        for index, (nivel, umbral_actual) in enumerate(niveles):
            if nivel != self.nivel:
                continue
            if index == len(niveles) - 1:
                return 100
            _, siguiente_umbral = niveles[index + 1]
            avance = self.puntos_totales - umbral_actual
            tramo = max(siguiente_umbral - umbral_actual, 1)
            return min(max(int((avance / tramo) * 100), 0), 100)
        return 100


class Notificacion(models.Model):
    TIPO_CHOICES = [
        ('logro', 'Logro'),
        ('insignia', 'Insignia'),
        ('mision', 'Misión'),
        ('recompensa', 'Recompensa'),
        ('certificado', 'Certificado'),
        ('sistema', 'Sistema'),
        ('recordatorio', 'Recordatorio'),
    ]

    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notificaciones'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)
    lms_notification = models.OneToOneField(
        'lms.Notification', null=True, blank=True, on_delete=models.CASCADE,
        related_name='platform_notification',
    )

    class Meta:
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'
        ordering = ['-creada_en']

    def __str__(self):
        return f'{self.titulo} — {self.usuario.username}'

    @property
    def dias_antiguedad(self) -> int:
        """Full days elapsed since notification creation."""
        return max((timezone.now().date() - self.creada_en.date()).days, 0)
