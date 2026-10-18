from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import TemplateCV
from .forms import TemplateCVForm

@staff_member_required
def templatecv_list(request):
    templates = TemplateCV.objects.all()
    return render(request, 'cv_templates/templatecv_list.html', {'templates': templates})

@staff_member_required
def templatecv_create(request):
    if request.method == 'POST':
        form = TemplateCVForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('cv_templates:list')
    else:
        form = TemplateCVForm()
    return render(request, 'cv_templates/templatecv_form.html', {'form': form})

@staff_member_required
def templatecv_update(request, pk):
    template = get_object_or_404(TemplateCV, pk=pk)
    if request.method == 'POST':
        form = TemplateCVForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            return redirect('cv_templates:list')
    else:
        form = TemplateCVForm(instance=template)
    return render(request, 'cv_templates/templatecv_form.html', {'form': form})

@staff_member_required
def templatecv_delete(request, pk):
    template = get_object_or_404(TemplateCV, pk=pk)
    if request.method == 'POST':
        template.delete()
        return redirect('cv_templates:list')
    return render(request, 'cv_templates/templatecv_confirm_delete.html', {'template': template})
