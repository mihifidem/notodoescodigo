from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
import django.db.models.deletion
from django.utils import timezone


def migrate_lms_data(apps, schema_editor):
    database = schema_editor.connection.alias
    LegacyCourse = apps.get_model('lms', 'Course')
    LegacyMembership = apps.get_model('lms', 'Membership')
    Course = apps.get_model('courses', 'Curso')
    InscripcionCurso = apps.get_model('courses', 'InscripcionCurso')
    Section = apps.get_model('lms', 'Section')
    CourseMembership = apps.get_model('lms', 'CourseMembership')
    CourseSettings = apps.get_model('courses', 'CourseSettings')
    AuditEvent = apps.get_model('lms', 'AuditEvent')
    UserProfileLMS = apps.get_model('lms', 'UserProfileLMS')

    course_map = {}
    for old_course in LegacyCourse.objects.using(database).iterator():
        starts_at = old_course.starts_at
        start_date = starts_at.date() if starts_at else timezone.localdate()
        status = old_course.status
        legacy_slug = old_course.slug
        slug = legacy_slug
        suffix = 1
        while Course.objects.using(database).filter(slug=slug).exists():
            slug = f'{legacy_slug[:200]}-lms-{old_course.pk}-{suffix}'
            suffix += 1
        course = Course.objects.using(database).create(
            nombre=old_course.title,
            slug=slug,
            codigo=f'LEGACY-LMS-{old_course.pk}',
            resumen=old_course.summary,
            descripcion=old_course.description,
            fecha_inicio=start_date,
            fecha_fin=old_course.ends_at.date() if old_course.ends_at else None,
            fecha_publicacion=old_course.published_at,
            zona_horaria='Europe/Madrid',
            matriculacion='open' if old_course.enrollment_open else 'closed',
            visibilidad='public',
            status=status,
            activo=status == 'published',
            creado_por_id=old_course.created_by_id,
        )
        Course.objects.using(database).filter(pk=course.pk).update(
            creado_en=old_course.created_at,
            actualizado_en=old_course.updated_at,
        )
        CourseSettings.objects.using(database).create(
            course_id=course.pk,
            visibilidad='public',
            status=status,
            allow_self_enrollment=old_course.enrollment_open,
            timezone='Europe/Madrid',
        )
        course_map[old_course.pk] = course.pk

    for section in Section.objects.using(database).iterator():
        section.course_new_id = course_map[section.course_id]
        section.save(using=database, update_fields=['course_new'])

    legacy_membership_keys = set()
    for membership in LegacyMembership.objects.using(database).iterator():
        course_id = course_map[membership.course_id]
        role = {
            'instructor': 'teacher',
            'assistant': 'assistant',
            'student': 'student',
        }.get(membership.role, 'student')
        status = 'suspended' if membership.status == 'suspended' else 'active'
        CourseMembership.objects.using(database).get_or_create(
            course_id=course_id,
            user_id=membership.user_id,
            defaults={
                'role': role,
                'status': status,
                'section_id': membership.section_id,
                'joined_at': membership.joined_at,
            },
        )
        legacy_membership_keys.add((course_id, membership.user_id))

    Enrollment = InscripcionCurso
    for enrollment in Enrollment.objects.using(database).iterator():
        key = (enrollment.curso_id, enrollment.usuario_id)
        if key in legacy_membership_keys:
            continue
        CourseMembership.objects.using(database).get_or_create(
            course_id=enrollment.curso_id,
            user_id=enrollment.usuario_id,
            defaults={
                'role': 'student',
                'status': 'completed' if enrollment.completado else 'active',
                'joined_at': enrollment.fecha_inscripcion,
                'completed_at': enrollment.fecha_completado,
            },
        )
        legacy_membership_keys.add(key)

    for old_course in LegacyCourse.objects.using(database).iterator():
        key = (course_map[old_course.pk], old_course.created_by_id)
        if key not in legacy_membership_keys:
            CourseMembership.objects.using(database).create(
                course_id=key[0],
                user_id=key[1],
                role='owner',
                status='active',
                joined_at=timezone.now(),
            )
            legacy_membership_keys.add(key)

    for event in AuditEvent.objects.using(database).iterator():
        if event.course_id:
            event.course_new_id = course_map[event.course_id]
            event.save(using=database, update_fields=['course_new'])

    user_model_label, user_model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(user_model_label, user_model_name)
    profile_model = UserProfileLMS
    existing_user_ids = set(
        profile_model.objects.using(database).values_list('user_id', flat=True)
    )
    profiles = (
        profile_model(user_id=user_id)
        for user_id in User.objects.using(database)
        .exclude(pk__in=existing_user_ids)
        .values_list('pk', flat=True)
        .iterator()
    )
    while True:
        batch = []
        try:
            for _ in range(500):
                batch.append(next(profiles))
        except StopIteration:
            pass
        if not batch:
            break
        profile_model.objects.using(database).bulk_create(batch, ignore_conflicts=True)


def prevent_unsafe_reverse(apps, schema_editor):
    raise IrreversibleError(
        'Antes de revertir lms.0002_integrate_courses_app, restaura el backup '
        'anterior a la migración; los cursos LMS se consolidaron en courses.Curso.'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0009_coursecategory_coursetag_remove_curso_horas_duracion_and_more'),
        ('lms', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('owner', 'Propietario'), ('teacher', 'Profesor'), ('assistant', 'Asistente'), ('student', 'Alumno'), ('observer', 'Observador')], default='student', max_length=12, verbose_name='capacidad en el curso')),
                ('status', models.CharField(choices=[('invited', 'Invitado'), ('active', 'Activo'), ('suspended', 'Suspendido'), ('completed', 'Completado'), ('dropped', 'Baja')], default='active', max_length=12, verbose_name='estado')),
                ('invited_at', models.DateTimeField(blank=True, null=True, verbose_name='invitado')),
                ('joined_at', models.DateTimeField(blank=True, null=True, verbose_name='incorporado')),
                ('completed_at', models.DateTimeField(blank=True, null=True, verbose_name='completado')),
                ('dropped_at', models.DateTimeField(blank=True, null=True, verbose_name='baja')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='creado')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='actualizado')),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lms_memberships', to='courses.curso')),
                ('section', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='memberships', to='lms.section', verbose_name='sección')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='course_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'pertenencia al curso',
                'verbose_name_plural': 'pertenencias a cursos',
                'permissions': [('publish_course', 'Puede publicar cursos LMS'), ('manage_platform', 'Puede administrar la plataforma LMS')],
            },
        ),
        migrations.AddField(
            model_name='section',
            name='course_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='courses.curso'),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='course_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='courses.curso'),
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='accessibility_preferences',
            field=models.JSONField(blank=True, default=dict, verbose_name='preferencias de accesibilidad'),
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='onboarding_completed',
            field=models.BooleanField(default=False, verbose_name='bienvenida completada'),
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='onboarding_dismissed',
            field=models.BooleanField(default=False, verbose_name='bienvenida descartada'),
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='onboarding_completed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='fecha de bienvenida'),
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, verbose_name='creado'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, default=timezone.now, verbose_name='actualizado'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userprofilelms',
            name='last_course',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lms_last_course_profiles', to='courses.curso', verbose_name='último curso'),
        ),
        migrations.AlterField(
            model_name='userprofilelms',
            name='time_zone',
            field=models.CharField(blank=True, default='', help_text='Opcional. Si se deja vacío, se utiliza la zona horaria del campus.', max_length=64, verbose_name='zona horaria'),
        ),
        migrations.AlterField(
            model_name='userprofilelms',
            name='preferred_language',
            field=models.CharField(default='es', max_length=10, verbose_name='idioma preferido'),
        ),
        migrations.RunPython(migrate_lms_data, prevent_unsafe_reverse),
        migrations.RemoveConstraint(
            model_name='section',
            name='lms_unique_section_title',
        ),
        migrations.RemoveIndex(
            model_name='auditevent',
            name='lms_audit_course_time',
        ),
        migrations.RemoveField(
            model_name='section',
            name='course',
        ),
        migrations.RenameField(
            model_name='section',
            old_name='course_new',
            new_name='course',
        ),
        migrations.AlterField(
            model_name='section',
            name='course',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lms_sections', to='courses.curso'),
        ),
        migrations.AddConstraint(
            model_name='section',
            constraint=models.UniqueConstraint(fields=('course', 'title'), name='lms_unique_section_title'),
        ),
        migrations.RemoveField(
            model_name='auditevent',
            name='course',
        ),
        migrations.RenameField(
            model_name='auditevent',
            old_name='course_new',
            new_name='course',
        ),
        migrations.AlterField(
            model_name='auditevent',
            name='course',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lms_audit_events', to='courses.curso'),
        ),
        migrations.AlterField(
            model_name='auditevent',
            name='action',
            field=models.CharField(choices=[('course_created', 'Curso creado'), ('course_updated', 'Curso actualizado'), ('course_published', 'Curso publicado'), ('member_enrolled', 'Estudiante inscrito'), ('member_status_changed', 'Estado de pertenencia cambiado')], max_length=32, verbose_name='acción'),
        ),
        migrations.DeleteModel(name='Membership'),
        migrations.DeleteModel(name='Course'),
        migrations.AddConstraint(
            model_name='coursemembership',
            constraint=models.UniqueConstraint(fields=('course', 'user'), name='lms_unique_course_member'),
        ),
        migrations.AddIndex(
            model_name='coursemembership',
            index=models.Index(fields=['user', 'status'], name='lms_member_user_status'),
        ),
        migrations.AddIndex(
            model_name='coursemembership',
            index=models.Index(fields=['course', 'role', 'status'], name='lms_member_course_role'),
        ),
        migrations.AddIndex(
            model_name='auditevent',
            index=models.Index(fields=['course', 'created_at'], name='lms_audit_course_time'),
        ),
    ]
