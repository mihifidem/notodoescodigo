"""PDF generation utilities for certificates."""
import io
import os
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from django.core.files.base import ContentFile


def _build_pdf_buffer(certificado) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1.2 * inch,
        leftMargin=1.2 * inch,
        topMargin=1.0 * inch,
        bottomMargin=1.0 * inch,
    )

    styles = getSampleStyleSheet()
    BLUE = colors.HexColor('#2E3A8C')
    GOLD = colors.HexColor('#C9A84C')

    title_style = ParagraphStyle(
        'CertTitle',
        fontSize=30,
        textColor=colors.HexColor('#1D2A4A'),
        alignment=TA_CENTER,
        spaceAfter=10,
        spaceBefore=10,
        fontName='Helvetica-Bold',
        leading=36,
        backColor=colors.HexColor('#F6F9FF'),
        borderColor=colors.HexColor('#A38A46'),
        borderPadding=14,
        borderWidth=1.5,
        borderRadius=10,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', fontSize=11, textColor=colors.HexColor('#22365B'),
        alignment=TA_CENTER, spaceAfter=18, spaceBefore=12, fontName='Helvetica-Bold',
        letterSpace=2.2,
        leading=16,
    )
    label_style = ParagraphStyle(
        'Label', fontSize=13, textColor=colors.black,
        alignment=TA_CENTER, spaceAfter=4, fontName='Helvetica',
    )
    name_style = ParagraphStyle(
        'Name', fontSize=28, textColor=BLUE,
        alignment=TA_CENTER, spaceAfter=24, fontName='Helvetica-Bold',
    )
    course_style = ParagraphStyle(
        'Course', fontSize=25, textColor=colors.HexColor('#B28A3F'),
        alignment=TA_CENTER, spaceAfter=20, fontName='Helvetica-Bold',
    )
    small_style = ParagraphStyle(
        'Small', fontSize=9, textColor=colors.grey,
        alignment=TA_CENTER, fontName='Helvetica',
    )
    section_style = ParagraphStyle(
        'Section', fontSize=14, textColor=BLUE,
        alignment=TA_CENTER, spaceAfter=10, fontName='Helvetica-Bold',
    )
    topic_style = ParagraphStyle(
        'Topic', fontSize=10, textColor=colors.black,
        fontName='Helvetica', leading=13,
    )

    validation_url = f'http://www.noNoTodoEsCódigo.com/certificados/validar/{certificado.codigo_unico}/'
    qr_code = qr.QrCodeWidget(validation_url)
    qr_code.barHeight = 80
    qr_code.barWidth = 80
    qr_drawing = Drawing(90, 90)
    qr_drawing.add(qr_code)

    from apps.courses.models import ResultadoQuiz

    temas = list(certificado.curso.temas.select_related('quiz').prefetch_related('quiz__resultados'))
    resumen_temas = []
    for tema in temas:
        resultado = ResultadoQuiz.objects.filter(
            usuario=certificado.usuario,
            quiz__tema=tema,
        ).order_by('-fecha').first()
        nota = f'{resultado.puntuacion}/{resultado.total_preguntas}' if resultado else '—'
        porcentaje = f'{resultado.porcentaje}% ' if resultado else ''
        resumen_temas.append((tema.titulo, f'{porcentaje}{nota}'))

    topic_lines = []
    for titulo, nota in resumen_temas:
        topic_lines.append(f'• {titulo}: {nota}')

    total_paginas = 2 if topic_lines else 1

    story = [
        HRFlowable(width='100%', thickness=2, color=GOLD, spaceAfter=16),
        Paragraph('CERTIFICATE', title_style),
        Spacer(1, 0.08 * inch),
        Paragraph('DUCKY ACADEMY BY @MIHIFIDEM', subtitle_style),
        HRFlowable(width='100%', thickness=2, color=GOLD, spaceAfter=22),
        Paragraph('Este certificado se otorga a / This certificate is awarded to:', label_style),
        Paragraph(
            certificado.usuario.get_full_name() or certificado.usuario.username,
            name_style,
        ),
        Paragraph('por completar exitosamente el curso / for successfully completing the course:', label_style),
        Paragraph(certificado.curso.nombre, course_style),
        Spacer(1, 0.2 * inch),
        HRFlowable(width='60%', thickness=1, color=colors.lightgrey, spaceAfter=16),
        Paragraph(
            f'Fecha de emisión: {certificado.fecha_emision.strftime("%d de %B de %Y")}',
            label_style,
        ),
        Paragraph(
            f'Curso: {certificado.curso.fecha_inicio.strftime("%d de %B de %Y")} - {certificado.fecha_emision.strftime("%d de %B de %Y")}',
            label_style,
        ),
        Spacer(1, 0.12 * inch),
        Paragraph(
            f'Este documento consta de {total_paginas} páginas en total, incluidas las notas de cada módulo/tema adjuntas. / This document consists of {total_paginas} pages in total, including the notes for each module/topic attached.',
            label_style,
        ),
        Paragraph(
            'Se adjuntan las notas de cada módulo/tema para complementar la evaluación final del curso. / The notes for each module/topic are attached to complement the final assessment of the course.',
            small_style,
        ),
        Spacer(1, 0.15 * inch),
        Paragraph(
            f'Código de verificación: {certificado.codigo_unico}',
            small_style,
        ),
        Spacer(1, 0.25 * inch),
    ]

    if topic_lines:
        story.extend([
            PageBreak(),
            Paragraph('Temas y resultados del curso / Course topics and results', section_style),
            Spacer(1, 0.1 * inch),
        ])
        for topic_line in topic_lines:
            story.append(Paragraph(topic_line, topic_style))
            story.append(Spacer(1, 0.06 * inch))
    else:
        story.extend([
            Paragraph('Temas y resultados del curso / Course topics and results', section_style),
            Paragraph('• Sin resultados de quiz registrados. / No quiz results registered.', topic_style),
        ])

    final_footer = "La actividad formativa y comercial desarrollada bajo la denominación 'Ducky Academy by @mihifidem' es responsabilidad de D. Oscar Burgos, con NIF 46568497Y en calidad de titular."
    final_footer_en = "The educational and commercial activity developed under the name 'Ducky Academy by @mihifidem' is the responsibility of D. Oscar Burgos, with NIF 46568497Y as sole holder."

    def add_certificate_frame(canvas, doc):
        page_w = doc.pagesize[0]
        page_h = doc.pagesize[1]
        border_x = 0.5 * inch
        border_y = 0.5 * inch
        border_w = page_w - 1 * inch
        border_h = page_h - 1 * inch

        diploma_bg = os.path.join(settings.BASE_DIR, 'static', 'img', 'diploma.png')
        if os.path.exists(diploma_bg):
            canvas.saveState()
            canvas.setFillAlpha(0.22)
            canvas.drawImage(
                diploma_bg,
                0,
                0,
                width=page_w,
                height=page_h,
                preserveAspectRatio=True,
                mask='auto',
            )
            canvas.restoreState()

        canvas.saveState()
        canvas.setStrokeColor(BLUE)
        canvas.setLineWidth(2)
        canvas.roundRect(border_x, border_y, border_w, border_h, 18, stroke=1, fill=0)

        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1)
        canvas.roundRect(border_x + 0.18 * inch, border_y + 0.18 * inch, border_w - 0.36 * inch, border_h - 0.36 * inch, 14, stroke=1, fill=0)

        canvas.setStrokeColor(colors.HexColor('#D7EAF7'))
        canvas.setLineWidth(1)
        canvas.line(0.9 * inch, page_h - 0.7 * inch, page_w - 0.9 * inch, page_h - 0.7 * inch)
        canvas.line(0.9 * inch, 0.8 * inch, page_w - 0.9 * inch, 0.8 * inch)

        canvas.setFillColor(colors.HexColor('#F9F4E4'))
        canvas.circle(0.9 * inch, page_h - 0.9 * inch, 16, stroke=1, fill=1)
        canvas.circle(page_w - 0.9 * inch, page_h - 0.9 * inch, 16, stroke=1, fill=1)
        canvas.circle(0.9 * inch, 0.9 * inch, 16, stroke=1, fill=1)
        canvas.circle(page_w - 0.9 * inch, 0.9 * inch, 16, stroke=1, fill=1)

        canvas.setFillColor(colors.HexColor('#F7F3E5'))
        canvas.setStrokeColor(colors.HexColor('#EAE2C6'))
        canvas.setLineWidth(1)
        canvas.setFont('Helvetica-BoldOblique', 46)
        canvas.setFillAlpha(0.08)
        canvas.drawCentredString(page_w / 2, page_h / 2, 'DUCKY')
        canvas.drawCentredString(page_w / 2, page_h / 2 - 0.5 * inch, 'ACADEMY')
        canvas.setFillAlpha(1)

        canvas.setStrokeColor(BLUE)
        canvas.setFillColor(BLUE)
        canvas.setLineWidth(1)
        canvas.line(1.1 * inch, 1.5 * inch, 2.8 * inch, 1.5 * inch)
        canvas.line(page_w - 2.8 * inch, 1.5 * inch, page_w - 1.1 * inch, 1.5 * inch)
        canvas.setFont('Helvetica-Bold', 9)
        canvas.drawCentredString(2 * inch, 1.0 * inch, 'Director')
        canvas.drawCentredString(page_w - 2 * inch, 1.0 * inch, 'Firma autorizada')

        signature_path = os.path.join(settings.BASE_DIR, 'static', 'img', 'firma_certif.png')
        if os.path.exists(signature_path):
            canvas.drawImage(
                signature_path,
                1.18 * inch,
                1.38 * inch,
                width=2.2 * inch,
                height=0.9 * inch,
                preserveAspectRatio=True,
                mask='auto',
            )

        canvas.setStrokeColor(BLUE)
        canvas.setLineWidth(0.8)
        canvas.line(1.45 * inch, 1.35 * inch, 2.45 * inch, 1.35 * inch)
        canvas.line(page_w - 2.45 * inch, 1.35 * inch, page_w - 1.45 * inch, 1.35 * inch)

        canvas.setFillColor(colors.HexColor('#485669'))
        canvas.setFont('Helvetica', 6.5)
        canvas.drawCentredString(
            page_w / 2,
            0.93 * inch,
            final_footer,
        )
        canvas.drawCentredString(
            page_w / 2,
            0.76 * inch,
            final_footer_en,
        )

        canvas.restoreState()

        qr_x = page_w - 3.1 * inch
        qr_y = page_h - 3.45 * inch
        canvas.setFillColor(colors.HexColor('#4E596B'))
        canvas.setFont('Helvetica', 7)
        canvas.drawCentredString(qr_x + 0.45 * inch, qr_y + 1.2 * inch, 'validar / To validate')
        renderPDF.draw(qr_drawing, canvas, qr_x, qr_y)

    doc.build(story, onFirstPage=add_certificate_frame, onLaterPages=add_certificate_frame)
    buffer.seek(0)
    return buffer


def generar_y_guardar_certificado(certificado) -> None:
    """Generate certificate PDF and save it to the model's file field."""
    buffer = _build_pdf_buffer(certificado)
    filename = f'ducky_academy_certificado_{certificado.codigo_unico}.pdf'
    certificado.pdf.save(filename, ContentFile(buffer.read()), save=True)
