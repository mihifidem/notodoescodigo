from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('lms', '0031_activity_student_workflow')]

    operations = [
        migrations.AddField(
            model_name='learningactivity', name='reference_code',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='referencia'),
        ),
    ]
