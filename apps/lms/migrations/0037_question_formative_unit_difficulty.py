import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('lms', '0036_questionbank_import_errors')]
    operations = [
        migrations.AddField(
            model_name='question',
            name='formative_unit',
            field=models.ForeignKey(
                to='lms.formativeunit', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='knowledge_questions', verbose_name='unidad formativa',
            ),
        ),
        migrations.AlterField(
            model_name='question', name='difficulty',
            field=models.PositiveSmallIntegerField(
                default=1, verbose_name='dificultad',
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)],
            ),
        ),
        migrations.AddConstraint(
            model_name='question',
            constraint=models.CheckConstraint(
                check=models.Q(difficulty__gte=1, difficulty__lte=5),
                name='lms_question_difficulty_1_5',
            ),
        ),
    ]
