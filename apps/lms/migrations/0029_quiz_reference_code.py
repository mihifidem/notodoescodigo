from django.db import migrations, models
import apps.lms.models


def assign_existing_references(apps, schema_editor):
    quiz_model = apps.get_model('lms', 'Quiz')
    for quiz in quiz_model.objects.using(schema_editor.connection.alias).all().iterator():
        quiz.reference_code = 'QZ-' + str(quiz.pk).zfill(6)
        quiz.save(using=schema_editor.connection.alias, update_fields=['reference_code'])


class Migration(migrations.Migration):
    dependencies = [
        ('lms', '0028_quizattempt_blank_count_quizattempt_correct_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='quiz', name='reference_code',
            field=models.CharField(max_length=64, null=True, verbose_name='código de referencia'),
        ),
        migrations.RunPython(assign_existing_references, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='quiz', name='reference_code',
            field=models.CharField(default=apps.lms.models.generate_quiz_reference, max_length=64,
                                   unique=True, verbose_name='código de referencia'),
        ),
    ]
