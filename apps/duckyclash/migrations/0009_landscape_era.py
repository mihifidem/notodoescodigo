from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('duckyclash', '0008_subcategory_question_sources'),
    ]

    operations = [
        migrations.AddField(
            model_name='landscape',
            name='era',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PRE', 'Prehistoria'),
                    ('GRE', 'Grecia'),
                    ('ROM', 'Roma'),
                    ('MED', 'Medieval'),
                    ('REN', 'Renacimiento'),
                    ('REV', 'Revolución industrial'),
                    ('SXX', 'Siglo XX'),
                    ('ACT', 'Actualidad'),
                ],
                default='',
                max_length=3,
                verbose_name='época',
            ),
        ),
    ]
