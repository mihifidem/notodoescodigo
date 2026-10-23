from django import forms
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django_prose_editor.widgets import ProseEditorWidget

from apps.courses.models import CourseCategory, Curso, Tema

from .models import (
    Announcement,
    AnnouncementComment,
    Badge,
    DiscussionPost,
    DiscussionTopic,
    FileAsset,
    FormativeUnit,
    LearningActivity,
    ModuleContent,
    ModuleSchedule,
    Page,
    Quiz,
)

class ModuleBadgeForm(forms.ModelForm):
    class Meta:
        model = Badge
        fields = ['name', 'description', 'module', 'image', 'is_active']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.course = course
        self.fields['module'].queryset = course.temas.order_by('orden', 'pk')
        self.fields['module'].required = True
        self.fields['image'].required = True
        self.fields['image'].help_text = _('Imagen PNG, JPEG, GIF o WebP, máximo 5 MB.')

    def clean_module(self):
        module = self.cleaned_data['module']
        if self.instance.pk and self.instance.awards.exists():
            original = Badge.objects.get(pk=self.instance.pk)
            if original.module_id != module.pk:
                raise forms.ValidationError(_('Una insignia ya concedida no puede cambiar de tema. Crea otra insignia.'))
        return module

    def clean_image(self):
        image = self.cleaned_data['image']
        if image.size > 5 * 1024 * 1024:
            raise forms.ValidationError(_('La imagen no puede superar 5 MB.'))
        if getattr(image, 'content_type', None) and image.content_type not in {
            'image/png', 'image/jpeg', 'image/gif', 'image/webp',
        }:
            raise forms.ValidationError(_('Selecciona una imagen PNG, JPEG, GIF o WebP.'))
        return image


class DiscussionPostForm(forms.ModelForm):
    class Meta:
        model = DiscussionPost
        fields = ['title', 'content']
        labels = {'title': _('Título de la consulta'), 'content': _('Mensaje')}
        widgets = {'content': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, is_reply=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_reply:
            self.fields.pop('title')
        else:
            self.fields['title'].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_content(self):
        from django.utils.html import strip_tags
        from .models import sanitize_html_content

        content = sanitize_html_content(self.cleaned_data['content'])
        if not strip_tags(content).strip():
            raise forms.ValidationError(_('El mensaje no puede estar vacío.'))
        return content


class DiscussionTopicForm(forms.ModelForm):
    class Meta:
        model = DiscussionTopic
        fields = ['title', 'content']
        labels = {'title': _('Nombre del foro'), 'content': _('Descripción del foro')}
        widgets = {'content': forms.Textarea(attrs={'rows': 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')


class QuizFeedbackForm(forms.Form):
    quiz_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    student_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    message = forms.CharField(
        label=_('Comentario / feedback'), max_length=5000,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'lms-input'}),
    )


class PeopleNotificationForm(forms.Form):
    title = forms.CharField(label=_('Título'), max_length=200)
    message = forms.CharField(label=_('Mensaje'), max_length=5000, widget=forms.Textarea(attrs={'rows': 4}))
    send_all = forms.BooleanField(label=_('Enviar a todos los alumnos del curso'), required=False)
    recipients = forms.MultipleChoiceField(
        label=_('Alumnos destinatarios'), required=False, widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, participants, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recipients'].choices = [
            (person['user'].pk, person['user'].get_full_name() or person['user'].username)
            for person in participants if person['role_key'] == 'student'
        ]

    def clean(self):
        data = super().clean()
        if not data.get('send_all') and not data.get('recipients'):
            raise forms.ValidationError(_('Selecciona al menos un alumno o la opción de enviar a todos.'))
        if data.get('send_all') and not self.fields['recipients'].choices:
            raise forms.ValidationError(_('No hay alumnos inscritos a los que enviar la notificación.'))
        return data


class CourseCategoryForm(forms.ModelForm):
    class Meta:
        model = CourseCategory
        fields = ['nombre', 'descripcion', 'slug']
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'slug': forms.HiddenInput(),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        if CourseCategory.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError(_('Ya existe una categoría con este nombre.'))
        return nombre

    def clean_slug(self):
        slug = slugify(self.cleaned_data.get('nombre', ''))
        if not slug:
            raise forms.ValidationError(_('El nombre debe contener letras o números para generar el slug.'))
        return slug


class CourseForm(forms.ModelForm):
    class Meta:
        model = Curso
        fields = [
            'nombre',
            'slug',
            'codigo',
            'categoria',
            'resumen',
            'descripcion',
            'nivel',
            'idioma',
            'visibilidad',
            'status',
            'fecha_inicio',
            'fecha_fin',
            'fecha_publicacion',
            'zona_horaria',
            'capacidad_maxima',
            'matriculacion',
            'regla_finalizacion',
            'seo_title',
            'seo_description',
            'seo_keywords',
        ]
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 6}),
            'resumen': forms.Textarea(attrs={'rows': 2}),
            'fecha_inicio': forms.DateInput(attrs={'type': 'date'}),
            'fecha_fin': forms.DateInput(attrs={'type': 'date'}),
            'fecha_publicacion': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['descripcion'].widget = ProseEditorWidget(
            preset='configurable', config=PROSE_EDITOR_CONFIG,
        )
        self.fields['descripcion'].label = _('Descripción larga')
        self.fields['descripcion'].help_text = _(
            'Da formato con títulos, negrita, cursiva, listas, enlaces y tablas.'
        )
        self.fields['categoria'].queryset = CourseCategory.objects.filter(activa=True)
        self.fields['slug'].help_text = _(
            'Se genera automáticamente a partir del nombre del curso, separando las palabras con guiones. '
            'Puedes modificarlo manualmente.'
        )
        self.fields['status'].help_text = _('Programado activa la publicación automática en la fecha indicada; Borrador no se publica solo.')
        self.fields['regla_finalizacion'].help_text = _(
            'Texto informativo. La finalización automática requiere completar todos los módulos publicados '
            'y temas del curso, y aprobar todas las pruebas publicadas que correspondan al alumno.'
        )
        for name in ['slug', 'codigo', 'nivel', 'idioma', 'visibilidad', 'status', 'zona_horaria', 'capacidad_maxima', 'matriculacion']:
            if name in self.fields:
                self.fields[name].required = False
        for name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'lms-input')
            field.widget.attrs['aria-describedby'] = (
                f'id_{name}_helptext id_{name}_errors'
            )

    def clean_descripcion(self):
        from django.utils.html import strip_tags
        from .models import sanitize_html_content

        content = sanitize_html_content(self.cleaned_data['descripcion'])
        if not strip_tags(content).replace('\xa0', ' ').strip():
            raise forms.ValidationError(_('Escribe la descripción del curso.'))
        return content

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if not slug:
            slug = self.instance.slug or slugify(self.cleaned_data.get('nombre', ''))
        return slug

    def clean_codigo(self):
        codigo = (self.cleaned_data.get('codigo') or '').strip()
        return codigo

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault('nivel', 'beginner')
        cleaned.setdefault('idioma', 'es')
        cleaned.setdefault('visibilidad', 'public')
        cleaned.setdefault('status', 'draft')
        cleaned.setdefault('zona_horaria', 'Europe/Madrid')
        cleaned.setdefault('capacidad_maxima', 0)
        cleaned.setdefault('matriculacion', 'open')
        if cleaned.get('nivel') in (None, ''):
            cleaned['nivel'] = 'beginner'
        if cleaned.get('idioma') in (None, ''):
            cleaned['idioma'] = 'es'
        if cleaned.get('visibilidad') in (None, ''):
            cleaned['visibilidad'] = 'public'
        if cleaned.get('status') in (None, ''):
            cleaned['status'] = 'draft'
        if cleaned.get('zona_horaria') in (None, ''):
            cleaned['zona_horaria'] = 'Europe/Madrid'
        if cleaned.get('capacidad_maxima') in (None, ''):
            cleaned['capacidad_maxima'] = 0
        if cleaned.get('matriculacion') in (None, ''):
            cleaned['matriculacion'] = 'open'
        starts = cleaned.get('fecha_inicio')
        ends = cleaned.get('fecha_fin')
        if starts and ends and ends < starts:
            self.add_error('fecha_fin', _('La fecha de fin debe ser igual o posterior al inicio.'))
        if cleaned.get('status') == 'scheduled' and not cleaned.get('fecha_publicacion'):
            self.add_error('fecha_publicacion', _('Un curso programado necesita fecha de publicación.'))
        if cleaned.get('status') == 'published' and not cleaned.get('fecha_publicacion'):
            cleaned['fecha_publicacion'] = timezone.now()
        return cleaned


class CourseSettingsForm(CourseForm):
    sequential_modules = forms.BooleanField(
        label=_('Desbloquear cada módulo al completar los anteriores'), required=False,
        help_text=_('Activa fechas y prerrequisitos. Los contenidos sin módulo conservan sus permisos habituales.'),
    )
    class Meta(CourseForm.Meta):
        fields = ['imagen'] + CourseForm.Meta.fields

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('fecha_inicio', 'fecha_fin'):
            self.fields[name].widget.format = '%Y-%m-%d'
        self.fields['fecha_publicacion'].widget.format = '%Y-%m-%dT%H:%M'
        if self.instance.pk and not self.is_bound:
            from .models import CourseSettingsLMS
            self.initial['sequential_modules'] = CourseSettingsLMS.objects.filter(
                course=self.instance, sequential_modules=True,
            ).exists()


class ModuleScheduleForm(forms.ModelForm):
    class Meta:
        model = ModuleSchedule
        fields = ['starts_at', 'ends_at', 'estimated_minutes']
        widgets = {
            'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'ends_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['estimated_minutes'].required = False
        self.fields['estimated_minutes'].help_text = _('Duración orientativa; no exige permanecer ese tiempo conectado.')

    def clean_estimated_minutes(self):
        return self.cleaned_data.get('estimated_minutes') or 0


class FormativeUnitForm(forms.ModelForm):
    class Meta:
        model = FormativeUnit
        fields = ['title', 'description', 'position']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, module, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.module = module
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_title(self):
        title = self.cleaned_data['title']
        if FormativeUnit.objects.filter(module=self.instance.module, title=title).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('Ya existe una unidad formativa con ese nombre en este módulo.'))
        return title


class ModuleContentForm(forms.ModelForm):
    class Meta:
        model = ModuleContent
        fields = ['unit', 'page', 'file', 'quiz', 'activity', 'position', 'required', 'criterion', 'passing_percentage', 'prerequisite']
        labels = {
            'page': _('Página'), 'file': _('Fichero'), 'quiz': _('Prueba'),
            'activity': _('Actividad'), 'prerequisite': _('Prerrequisito'),
        }

    def __init__(self, *args, module, unit=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        self.instance.module = module
        self.fields['unit'].queryset = module.formative_units.all()
        self.fields['unit'].help_text = _('Si no seleccionas una unidad, se utilizará General. Los recursos conservan sus permisos y progreso.')
        if unit and not self.instance.pk:
            self.initial['unit'] = unit.pk
        for name, model in [('page', Page), ('file', FileAsset), ('quiz', Quiz), ('activity', LearningActivity)]:
            queryset = model.objects.filter(course_id=module.curso_id)
            if name in ('page', 'activity'):
                queryset = queryset.filter(Q(module=module) | Q(module__isnull=True))
            self.fields[name].queryset = queryset.filter(
                Q(learning_content__isnull=True) | Q(learning_content__pk=self.instance.pk),
            ).order_by('title', 'pk')
        self.fields['prerequisite'].queryset = module.learning_contents.exclude(pk=self.instance.pk)
        self.fields['passing_percentage'].help_text = _('Solo para aprobar actividades; las pruebas usan su nota mínima configurada.')
        self.fields['position'].required = False
        self.fields['passing_percentage'].required = False
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_position(self):
        return self.cleaned_data.get('position') or 0

    def clean_passing_percentage(self):
        value = self.cleaned_data.get('passing_percentage')
        return 50 if value is None else value

    def save(self, commit=True):
        content = super().save(commit=commit)
        if commit and content.kind in ('page', 'activity'):
            target = content.target
            if target.module_id != content.module_id:
                target.module = content.module
                target.save()
        return content


class ModuleContentBatchForm(forms.Form):
    resource_fields = ('page', 'file', 'quiz', 'activity')

    def __init__(self, *args, module, **kwargs):
        from copy import deepcopy
        super().__init__(*args, **kwargs)
        self.module = module
        template = ModuleContentForm(module=module)
        for name, field in template.fields.items():
            if name in self.resource_fields:
                self.fields[name] = forms.ModelMultipleChoiceField(
                    queryset=field.queryset, required=False, label=field.label,
                    widget=forms.CheckboxSelectMultiple,
                )
            else:
                self.fields[name] = deepcopy(field)
        self.initial = {**template.initial, **self.initial}
        self.fields['criterion'].help_text = _(
            'Para combinar distintos tipos, elige abrir el contenido o marcar como completado. '
            'Los criterios de entrega y aprobado deben ser compatibles con todos los recursos seleccionados.'
        )
        self.content_forms = []

    def clean(self):
        cleaned = super().clean()
        selected = [(name, target) for name in self.resource_fields for target in cleaned.get(name, [])]
        if not selected and not self.errors:
            raise forms.ValidationError(_('Selecciona al menos un contenido.'))
        if self.errors:
            return cleaned
        common = {
            name: cleaned.get(name) for name in self.fields if name not in self.resource_fields
        }
        common['prerequisite'] = common['prerequisite'].pk if common['prerequisite'] else ''
        common['unit'] = common['unit'].pk if common['unit'] else ''
        for offset, (name, target) in enumerate(selected):
            data = {**common, name: target.pk, 'position': (common['position'] or 0) + offset}
            form = ModuleContentForm(data, module=self.module)
            if form.is_valid():
                self.content_forms.append(form)
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        self.add_error(None if field == '__all__' else field, f'{target.title}: {error}')
        return cleaned

    def save(self):
        if not self.is_valid():
            raise ValueError('Cannot save invalid module content selection.')
        return [form.save() for form in self.content_forms]


class ModuleForm(forms.ModelForm):
    class Meta:
        model = Tema
        fields = ['titulo', 'contenido', 'orden']
        labels = {
            'titulo': _('Nombre del módulo'),
            'contenido': _('Resumen o descripción'),
            'orden': _('Orden'),
        }
        widgets = {'contenido': forms.Textarea(attrs={'rows': 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contenido'].required = False
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')


PROSE_EDITOR_CONFIG = {
    'extensions': {
        'Bold': True,
        'Italic': True,
        'Underline': True,
        'Strike': True,
        'Heading': {'levels': [1, 2, 3]},
        'BulletList': True,
        'OrderedList': True,
        'ListItem': True,
        'HardBreak': True,
        'Blockquote': True,
        'Link': True,
        'Image': True,
        'Table': True,
        'TableRow': True,
        'TableHeader': True,
        'TableCell': True,
        'Code': True,
        'CodeBlock': True,
        'HorizontalRule': True,
        'History': True,
        'HTML': True,
        'Fullscreen': True,
    },
}


class PageForm(forms.ModelForm):
    html_content = forms.CharField(
        label=_('Documento HTML con estilos propios'), required=False,
        widget=forms.Textarea(attrs={'rows': 16, 'spellcheck': 'false'}),
        help_text=_('Pega aquí el HTML completo, incluidos head, style y body. '
                    'Si se rellena, sustituye al contenido del editor visual. '
                    'Se conserva el CSS; no se ejecutan scripts.'),
    )
    class Meta:
        model = Page
        fields = [
            'title',
            'slug',
            'module',
            'status',
            'visibility',
            'content',
            'seo_title',
            'seo_description',
            'seo_keywords',
            'publish_at',
            'expires_at',
            'allow_pdf_export',
        ]
        widgets = {
            'content': ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG),
            'seo_description': forms.Textarea(attrs={'rows': 3}),
            'publish_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        self.course = kwargs.pop('course', None)
        super().__init__(*args, **kwargs)
        if self.course is not None:
            self.fields['module'].queryset = Tema.objects.filter(curso=self.course)
        if not self.is_bound and self.instance.has_own_styles:
            self.initial['html_content'] = self.instance.content
            self.initial['content'] = ''
        for field_name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if not slug:
            slug = slugify(self.cleaned_data.get('title', ''))
        if not slug:
            raise forms.ValidationError(_('El slug es obligatorio.'))
        existing = Page.objects.filter(course=self.course).exclude(pk=self.instance.pk)
        if existing.filter(slug=slug).exists():
            slug = f'{slug}-{existing.count() + 1}'
        return slug

    def clean_content(self):
        content = self.cleaned_data.get('content') or ''
        return content.strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('html_content'):
            cleaned['content'] = cleaned['html_content'].strip()
        return cleaned


class LearningActivityForm(forms.ModelForm):
    class Meta:
        model = LearningActivity
        fields = [
            'title',
            'reference_code',
            'module',
            'description',
            'instructions',
            'solution',
            'status',
            'starts_at',
            'due_at',
            'feedback_at',
            'solution_release_at',
            'points',
            'is_evaluable',
        ]
        widgets = {
            'description': ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG),
            'instructions': ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG),
            'solution': ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG),
            'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'due_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'feedback_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'solution_release_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['module'].queryset = Tema.objects.filter(curso=course).order_by('orden', 'pk')
        self.fields['module'].label = _('Módulo')
        self.fields['module'].help_text = _('Selecciona un módulo del curso o crea uno nuevo en el desplegable.')
        self.fields['reference_code'].help_text = _('Opcional. Código o referencia para identificar la actividad.')
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')


class FileAssetForm(forms.ModelForm):
    class Meta:
        model = FileAsset
        fields = [
            'title',
            'folder',
            'file',
            'visibility',
            'access_policy',
            'allow_download',
            'allow_preview',
        ]
        widgets = {
            'file': forms.ClearableFileInput(attrs={'accept': ','.join(FileAsset.ALLOWED_EXTENSIONS)}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['folder'].required = False
        self.fields['folder'].initial = self.fields['folder'].initial or 'general'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_folder(self):
        return (self.cleaned_data.get('folder') or '').strip() or 'general'


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = [
            'title',
            'slug',
            'status',
            'is_pinned',
            'content',
            'publish_at',
            'expire_at',
            'allow_comments',
            'allow_likes',
            'requires_acknowledgement',
            'has_audio',
            'audio_url',
        ]
        widgets = {
            'content': ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG),
            'publish_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'expire_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'audio_url': forms.URLInput(attrs={'placeholder': 'https://...'}),
        }

    def __init__(self, *args, **kwargs):
        self.course = kwargs.pop('course', None)
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'lms-input')

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if not slug:
            slug = slugify(self.cleaned_data.get('title', ''))
        if not slug:
            raise forms.ValidationError(_('El slug es obligatorio.'))
        existing = Announcement.objects.filter(course=self.course).exclude(pk=self.instance.pk)
        if existing.filter(slug=slug).exists():
            slug = f'{slug}-{existing.count() + 1}'
        return slug

    def clean_content(self):
        content = self.cleaned_data.get('content') or ''
        return content.strip()


class AnnouncementCommentForm(forms.ModelForm):
    class Meta:
        model = AnnouncementComment
        fields = ['content']
        widgets = {
            'content': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': _('Escribe un comentario...'),
                'class': 'lms-input',
            }),
        }

    def clean_content(self):
        content = (self.cleaned_data.get('content') or '').strip()
        if not content:
            raise forms.ValidationError(_('El comentario no puede estar vacío.'))
        return content
