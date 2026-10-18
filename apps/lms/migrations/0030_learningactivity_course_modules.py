from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


def use_course_topics(apps, schema_editor):
    Activity = apps.get_model('lms', 'LearningActivity')
    Topic = apps.get_model('courses', 'Tema')
    alias = schema_editor.connection.alias
    topics = {}
    for activity in Activity.objects.using(alias).exclude(module=None).select_related('module').iterator():
        module = activity.module
        key = (activity.course_id, module.pk)
        if key not in topics:
            topic = Topic.objects.using(alias).filter(
                curso_id=activity.course_id, titulo=module.title,
            ).order_by('pk').first()
            if topic is None:
                topic = Topic.objects.using(alias).create(
                    curso_id=activity.course_id, titulo=module.title,
                    contenido=module.description or module.summary, orden=module.position,
                )
            topics[key] = topic.pk
        Activity.objects.using(alias).filter(pk=activity.pk).update(course_topic_id=topics[key])


def restore_lms_modules(apps, schema_editor):
    Activity = apps.get_model('lms', 'LearningActivity')
    Module = apps.get_model('lms', 'CourseModule')
    alias = schema_editor.connection.alias
    modules = {}
    for activity in Activity.objects.using(alias).exclude(course_topic=None).select_related('course_topic').iterator():
        topic = activity.course_topic
        key = (activity.course_id, topic.pk)
        if key not in modules:
            module = Module.objects.using(alias).filter(
                course_id=activity.course_id, title=topic.titulo,
            ).order_by('pk').first()
            if module is None:
                module = Module.objects.using(alias).create(
                    course_id=activity.course_id, title=topic.titulo,
                    slug=f'{slugify(topic.titulo)[:190]}-topic-{topic.pk}',
                    description=topic.contenido, position=topic.orden,
                )
            modules[key] = module.pk
        Activity.objects.using(alias).filter(pk=activity.pk).update(module_id=modules[key])


class Migration(migrations.Migration):
    dependencies = [
        ('lms', '0029_quiz_reference_code'),
        ('courses', '0011_ensure_curso_horas_duracion'),
    ]

    operations = [
        migrations.AddField(
            model_name='learningactivity',
            name='course_topic',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='learning_activities', to='courses.tema', verbose_name='módulo',
            ),
        ),
        migrations.RunPython(use_course_topics, restore_lms_modules),
        migrations.RemoveField(model_name='learningactivity', name='module'),
        migrations.RenameField(model_name='learningactivity', old_name='course_topic', new_name='module'),
    ]
