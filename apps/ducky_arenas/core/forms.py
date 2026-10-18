from django import forms

from apps.ducky_arenas.models import Profession


class DuckyProfessionForm(forms.Form):
    profession = forms.ModelChoiceField(
        label='Elige tu profesión',
        queryset=Profession.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profession'].queryset = Profession.objects.filter(
            is_available=True,
        ).order_by('display_order', 'name')
