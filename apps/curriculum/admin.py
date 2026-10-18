from django.contrib import admin
from .models import (
    CurriculumVitae, PersonalInfo, ProfessionalProfile, WorkExperience,
    Education, ComplementaryTraining, Skill, Language, Project, Achievement,
    Volunteering, SocialNetwork, Interest, OtherInfo, CVProfile,
)


class WorkExperienceInline(admin.TabularInline):
    model = WorkExperience
    extra = 0


class EducationInline(admin.TabularInline):
    model = Education
    extra = 0


class SkillInline(admin.TabularInline):
    model = Skill
    extra = 0


@admin.register(CurriculumVitae)
class CurriculumVitaeAdmin(admin.ModelAdmin):
    list_display = ['user', 'created_at', 'updated_at']
    inlines = [WorkExperienceInline, EducationInline, SkillInline]


@admin.register(CVProfile)
class CVProfileAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'user', 'slug', 'template', 'is_public', 'created_at']
    list_filter = ['template', 'is_public']
    search_fields = ['nombre', 'slug', 'user__username']
    prepopulated_fields = {'slug': ('nombre',)}


admin.site.register(PersonalInfo)
admin.site.register(ProfessionalProfile)
admin.site.register(WorkExperience)
admin.site.register(Education)
admin.site.register(ComplementaryTraining)
admin.site.register(Skill)
admin.site.register(Language)
admin.site.register(Project)
admin.site.register(Achievement)
admin.site.register(Volunteering)
admin.site.register(SocialNetwork)
admin.site.register(Interest)
admin.site.register(OtherInfo)
