from datetime import timedelta

from django.db import migrations, models
import django.db.models.deletion


def link_notifications(apps, schema_editor):
    Notification = apps.get_model('lms', 'Notification')
    Notificacion = apps.get_model('users', 'Notificacion')
    database = schema_editor.connection.alias
    for notice in Notification.objects.using(database).order_by('created_at', 'pk').iterator():
        candidates = list(Notificacion.objects.using(database).filter(
            usuario_id=notice.recipient_id, tipo='sistema', titulo=notice.title,
            mensaje=notice.message, lms_notification__isnull=True,
            creada_en__gte=notice.created_at,
            creada_en__lte=notice.created_at + timedelta(seconds=5),
        )[:2])
        if len(candidates) == 1:
            platform = candidates[0]
            read = platform.leida or notice.status == 'read'
            Notificacion.objects.using(database).filter(pk=platform.pk).update(
                lms_notification_id=notice.pk, leida=read,
            )
            Notification.objects.using(database).filter(pk=notice.pk).update(
                status='read' if read else 'unread',
                read_at=notice.read_at or (platform.creada_en if read else None),
            )
        else:
            platform = Notificacion.objects.using(database).create(
                usuario_id=notice.recipient_id, tipo='sistema', titulo=notice.title,
                mensaje=notice.message, leida=notice.status == 'read', lms_notification_id=notice.pk,
            )
            Notificacion.objects.using(database).filter(pk=platform.pk).update(creada_en=notice.created_at)


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0009_user_role'),
        ('lms', '0032_learningactivity_reference_code'),
    ]
    operations = [
        migrations.AddField(
            model_name='notificacion', name='lms_notification',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='platform_notification', to='lms.notification',
            ),
        ),
        migrations.RunPython(link_notifications, migrations.RunPython.noop),
    ]
