from xml.sax.saxutils import escape

from django.http import HttpResponse
from apps.curriculum.models import PersonalInfo
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, HRFlowable, Paragraph, SimpleDocTemplate, Spacer


def build_letter_pdf(letter, version):
    return build_text_pdf(
        version.content, f'carta-{letter.pk}-v{version.number}.pdf',
    )


def build_text_pdf(content, filename):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    document = SimpleDocTemplate(
        response, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )
    style = getSampleStyleSheet()['BodyText']
    style.fontSize = 11
    style.leading = 16
    story = []
    for paragraph in content.splitlines():
        if paragraph.strip():
            story.append(Paragraph(escape(paragraph), style))
        else:
            story.append(Spacer(1, 8))
    document.build(story or [Spacer(1, 1)])
    return response


def build_cv_pdf(profile, snapshot):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{profile.slug}.pdf"'
    document = SimpleDocTemplate(
        response, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(escape(snapshot.get('name') or profile.nombre), styles['Title']),
        Paragraph(escape(snapshot.get('profession', '')), styles['Heading2']),
    ]
    if snapshot.get('location'):
        story.append(Paragraph(escape(snapshot['location']), styles['Normal']))
    contacts = [snapshot.get('email'), snapshot.get('phone')]
    if any(contacts):
        story.append(Paragraph(
            ' · '.join(escape(item) for item in contacts if item), styles['Normal'],
        ))
    story.append(Spacer(1, 8))
    if snapshot.get('summary'):
        story.extend([
            Paragraph('Perfil profesional', styles['Heading2']),
            Paragraph(escape(snapshot['summary']).replace('\n', '<br/>'), styles['BodyText']),
        ])
    for title, key, fields in (
        ('Experiencia', 'experiences', ('puesto', 'empresa', 'fecha_inicio', 'fecha_fin', 'descripcion')),
        ('Formación', 'education', ('titulo', 'centro', 'fecha_inicio', 'fecha_fin')),
        ('Formación complementaria', 'training', ('nombre', 'entidad', 'fecha', 'descripcion')),
        ('Habilidades', 'skills', ('nombre',)),
        ('Idiomas', 'languages', ('idioma', 'nivel')),
        ('Proyectos', 'projects', ('nombre', 'descripcion', 'tecnologias')),
        ('Logros', 'achievements', ('titulo', 'descripcion')),
        ('Voluntariado', 'volunteering', ('funcion', 'organizacion', 'impacto')),
    ):
        items = snapshot.get(key, [])
        if not items:
            continue
        story.extend([Spacer(1, 8), Paragraph(title, styles['Heading2']), HRFlowable(width='100%')])
        for item in items:
            content = ' · '.join(
                escape(item.get(field, '')) for field in fields if item.get(field)
            )
            if content:
                story.append(Paragraph(content, styles['BodyText']))
            if key == 'projects' and item.get('link'):
                safe_link = escape(item['link'], {'"': '&quot;'})
                story.append(Paragraph(
                    f'<link href="{safe_link}" color="blue">Ver proyecto</link>',
                    styles['BodyText'],
                ))
    if snapshot.get('links'):
        story.extend([Spacer(1, 8), Paragraph('Enlaces', styles['Heading2'])])
        for item in snapshot['links']:
            safe_link = escape(item['url'], {'"': '&quot;'})
            story.append(Paragraph(
                f'<link href="{safe_link}" color="blue">{escape(item["label"])}</link>',
                styles['BodyText'],
            ))
    if snapshot.get('photo'):
        photo_storage = PersonalInfo._meta.get_field('foto').storage
        with photo_storage.open(snapshot['photo'], 'rb') as photo_file:
            photo = Image(photo_file, width=28 * mm, height=28 * mm, kind='proportional')
            photo.hAlign = 'RIGHT'
            story.insert(1, photo)
            document.build(story)
    else:
        document.build(story)
    return response
