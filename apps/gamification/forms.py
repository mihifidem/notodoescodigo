from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Insignia, InsigniaVersion


class InsigniaForm(forms.ModelForm):
    class Meta:
        model = Insignia
        fields = [
            'nombre',
            'descripcion',
            'imagen',
            'tipo',
            'requisito_valor',
            'curso_objetivo',
            'tema_objetivo',
            'visible',
        ]
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'imagen': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'requisito_valor': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'curso_objetivo': forms.Select(attrs={'class': 'form-select'}),
            'tema_objetivo': forms.Select(attrs={'class': 'form-select'}),
            'visible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class InsigniaDefinitionForm(forms.ModelForm):
    rule_type = forms.ChoiceField(
        choices=InsigniaVersion.RuleType.choices,
        label='Condición verificable',
    )
    course = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Curso requerido',
    )
    topic = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Tema requerido',
    )
    quiz = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Evaluación requerida',
    )
    threshold = forms.IntegerField(min_value=1, initial=1, label='Meta')
    public_requirements = forms.CharField(
        max_length=300,
        label='Requisitos visibles',
        widget=forms.TextInput(attrs={'placeholder': 'Describe de forma clara cómo obtenerla'}),
    )
    source_description = forms.CharField(
        max_length=160,
        initial='Resultado verificado del módulo',
        label='Fuente de verificación',
    )
    verification_description = forms.CharField(
        max_length=160,
        initial='Validación de datos almacenados por el servidor',
        label='Método de comprobación',
    )
    organization = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Organización propietaria',
    )

    class Meta:
        model = Insignia
        fields = (
            'nombre',
            'descripcion',
            'imagen',
            'texto_alternativo',
            'category',
            'issuer_name',
            'organization',
            'issue_method',
            'visible',
        )
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'imagen': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'texto_alternativo': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'issuer_name': forms.TextInput(attrs={'class': 'form-control'}),
            'organization': forms.Select(attrs={'class': 'form-select'}),
            'issue_method': forms.Select(attrs={'class': 'form-select'}),
            'visible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        actor = kwargs.pop('actor', None)
        super().__init__(*args, **kwargs)
        from apps.courses.models import Curso, Quiz, Tema
        from apps.users.access import is_platform_admin
        from apps.users.models import AccessRoleAssignment, Organization, OrganizationMembership

        self.fields['course'].queryset = Curso.objects.all().order_by('nombre')
        self.fields['topic'].queryset = Tema.objects.select_related('curso').order_by('curso__nombre', 'titulo')
        self.fields['quiz'].queryset = Quiz.objects.select_related('tema__curso').order_by('titulo')
        organizations = Organization.objects.none()
        if actor and is_platform_admin(actor):
            organizations = Organization.objects.filter(is_active=True).order_by('name')
        elif actor and actor.is_authenticated:
            organizations = Organization.objects.filter(
                is_active=True,
                kind=Organization.Kind.ACADEMY,
                memberships__user=actor,
                memberships__status=OrganizationMembership.Status.ACTIVE,
                role_assignments__user=actor,
                role_assignments__role=AccessRoleAssignment.Role.ACADEMIA,
                role_assignments__is_active=True,
            ).distinct().order_by('name')
        self.fields['organization'].queryset = organizations
        for name, field in self.fields.items():
            if name not in self.Meta.fields:
                field.widget.attrs.setdefault(
                    'class',
                    'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
                )
        if self.instance.pk:
            version = self.instance.versions.order_by('-version').first()
            if version:
                self.initial.update({
                    'rule_type': version.rule_type,
                    'course': version.course_id,
                    'topic': version.topic_id,
                    'quiz': version.quiz_id,
                    'threshold': version.threshold,
                    'public_requirements': version.public_requirements,
                    'source_description': version.source_description,
                    'verification_description': version.verification_description,
                })

    def clean(self):
        cleaned = super().clean()
        rule_type = cleaned.get('rule_type')
        required_related = {
            InsigniaVersion.RuleType.COURSE_COMPLETED: 'course',
            InsigniaVersion.RuleType.TOPIC_COMPLETED: 'topic',
            InsigniaVersion.RuleType.QUIZ_PASSED: 'quiz',
        }
        required = required_related.get(rule_type)
        if required and not cleaned.get(required):
            self.add_error(required, 'Selecciona el recurso para esta condición.')
        for rule, field_name in required_related.items():
            if rule_type and rule_type != rule and cleaned.get(field_name):
                self.add_error(field_name, 'Este recurso no corresponde a la condición elegida.')
        issuer = cleaned.get('issuer_name', '').strip()
        if cleaned.get('organization') and issuer.casefold() in {
            'notodoescodigo.com', 'notodoescodigo', 'ntec',
        }:
            self.add_error(
                'issuer_name',
                'El emisor de la plataforma requiere autorización administrativa.',
            )
        if cleaned.get('issue_method') == Insignia.IssueMethod.MANUAL and not cleaned.get('public_requirements'):
            raise ValidationError('La concesión manual también debe explicar sus requisitos públicos.')
        return cleaned


class InsigniaGrantForm(forms.Form):
    recipient = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        label='Usuario destinatario',
    )
    evidence = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Actividad o evidencia',
    )
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Motivo',
    )

    def __init__(self, *args, **kwargs):
        queryset = kwargs.pop('recipient_queryset', get_user_model().objects.none())
        super().__init__(*args, **kwargs)
        self.fields['recipient'].queryset = queryset
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')


class InsigniaReasonForm(forms.Form):
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        label='Motivo',
    )
