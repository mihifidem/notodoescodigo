"""Admin configuration for the users app."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Aula, User, Notificacion


@admin.register(Aula)
class AulaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'direccion', 'horario', 'total_cursos')
    search_fields = ('nombre', 'direccion', 'horario')
    filter_horizontal = ('cursos',)

    def total_cursos(self, obj):
        return obj.cursos.count()

    total_cursos.short_description = 'Cursos disponibles'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'username', 'email', 'first_name', 'last_name',
        'role', 'aula', 'nivel', 'puntos', 'streak', 'is_staff',
    )
    list_filter = ('role', 'nivel', 'is_staff', 'is_active', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Acceso y membresía', {'fields': ('role',)}),
        ('Gamificación', {'fields': ('puntos', 'nivel', 'streak', 'fecha_ultimo_acceso', 'avatar', 'bio')}),
        ('Datos academicos', {'fields': ('aula',)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Datos personales', {'fields': ('first_name', 'last_name', 'email', 'role')}),
    )


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'tipo', 'leida', 'creada_en')
    list_filter = ('tipo', 'leida')
    search_fields = ('titulo', 'usuario__username')
    list_editable = ('leida',)
    date_hierarchy = 'creada_en'
