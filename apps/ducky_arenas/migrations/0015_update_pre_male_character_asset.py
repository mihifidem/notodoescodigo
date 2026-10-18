from django.db import migrations


def update_pre_male_image(apps, schema_editor):
    CharacterAsset = apps.get_model('ducky_arenas', 'CharacterAsset')
    CharacterAsset.objects.using(schema_editor.connection.alias).filter(
        era__code='PRE',
        variant='BOY',
        static_image_path='duckyarenas/DAR_personajes/DAR_PRE1.jpg',
    ).update(static_image_path='duckyarenas/DAR_personajes/DAR_PRE1.png')


def restore_pre_male_image(apps, schema_editor):
    CharacterAsset = apps.get_model('ducky_arenas', 'CharacterAsset')
    CharacterAsset.objects.using(schema_editor.connection.alias).filter(
        era__code='PRE',
        variant='BOY',
        static_image_path='duckyarenas/DAR_personajes/DAR_PRE1.png',
    ).update(static_image_path='duckyarenas/DAR_personajes/DAR_PRE1.jpg')


class Migration(migrations.Migration):
    dependencies = [
        ('ducky_arenas', '0014_professionselectionpolicy_alter_profession_options_and_more'),
    ]

    operations = [
        migrations.RunPython(update_pre_male_image, restore_pre_male_image),
    ]
