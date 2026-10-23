from django.contrib import admin
from apps.users.access import is_platform_admin
from .models import (
    CurriculumVitae, PersonalInfo, ProfessionalProfile, WorkExperience,
    Education, ComplementaryTraining, Skill, Language, Project, Achievement,
    Volunteering, SocialNetwork, Interest, OtherInfo, CVProfile, ProfileTemplate,
    ProfileVisit, CVProfileRevision,
    LetterConfiguration, LetterGeneration,
    ATSConfiguration, ATSOperation,
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
    list_display = ['nombre', 'user', 'slug', 'profile_template', 'status', 'is_public', 'created_at']
    list_filter = ['status', 'profile_template', 'is_public']
    search_fields = ['nombre', 'slug', 'user__username']
    prepopulated_fields = {'slug': ('nombre',)}

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


@admin.register(ProfileTemplate)
class ProfileTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'version', 'category', 'allowed_plans', 'status', 'updated_at']
    list_filter = ['category', 'status']
    search_fields = ['name', 'code', 'description']
    readonly_fields = ['created_at', 'updated_at', 'published_at']
    fieldsets = (
        ('Identidad versionada', {'fields': ('code', 'name', 'description', 'category', 'version', 'schema_version', 'renderer')}),
        ('Acceso y recomendaciones', {'fields': ('allowed_plans', 'recommended_branch_codes', 'compatible_branch_codes')}),
        ('Diseño', {'fields': ('default_color', 'customization_options')}),
        ('Publicación', {'fields': ('status', 'published_at', 'created_at', 'updated_at')}),
    )

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(['code', 'version'])
        return fields

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProfileVisit)
class ProfileVisitAdmin(admin.ModelAdmin):
    list_display = ['profile', 'visited_at']
    list_filter = ['visited_at']
    readonly_fields = ['profile', 'visited_at']
    ordering = ['-visited_at']

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CVProfileRevision)
class CVProfileRevisionAdmin(admin.ModelAdmin):
    list_display = ['profile', 'revision', 'template', 'template_version', 'created_at']
    list_filter = ['template', 'created_at']
    search_fields = ['profile__nombre', 'profile__user__username']
    readonly_fields = ['profile', 'revision', 'snapshot', 'template', 'template_version', 'brand_color', 'created_at']

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(LetterConfiguration)
class LetterConfigurationAdmin(admin.ModelAdmin):
    list_display = ['version', 'available', 'generation_enabled', 'created_at']
    readonly_fields = ['created_at']
    fields = [
        'version', 'available', 'generation_enabled', 'instructions',
        'max_offer_length', 'max_content_length', 'requests_per_minute', 'created_at',
    ]

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        current = LetterConfiguration.objects.first()
        if current:
            initial.update({
                field: getattr(current, field)
                for field in (
                    'available', 'generation_enabled', 'instructions',
                    'max_offer_length', 'max_content_length', 'requests_per_minute',
                )
            })
            initial['version'] = current.version + 1
        return initial


@admin.register(LetterGeneration)
class LetterGenerationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_id', 'status', 'error_code', 'configuration_version', 'created_at']
    list_filter = ['status', 'error_code']
    fields = [
        'id', 'user_id', 'letter_id', 'idempotency_key', 'status', 'error_code',
        'configuration_version', 'created_at',
    ]
    readonly_fields = fields

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ATSConfiguration)
class ATSConfigurationAdmin(LetterConfigurationAdmin):
    list_display = ['version', 'enabled', 'criteria_version', 'max_pages', 'max_pdf_bytes', 'created_at']
    fields = [
        'version', 'enabled', 'criteria_version', 'enabled_rules', 'max_pdf_bytes', 'max_pages',
        'max_text_chars', 'processing_seconds', 'requests_per_minute',
        'detail_entitlement', 'comparison_entitlement', 'created_at',
    ]
    readonly_fields = ['criteria_version', 'created_at']

    def get_changeform_initial_data(self, request):
        current = ATSConfiguration.objects.first()
        if current is None:
            return {'version': 1}
        initial = {
            field: getattr(current, field)
            for field in self.fields if field not in ('version', 'criteria_version', 'created_at')
        }
        initial['version'] = current.version + 1
        return initial


@admin.register(ATSOperation)
class ATSOperationAdmin(LetterGenerationAdmin):
    list_display = ['id', 'user_id', 'status', 'error_code', 'task', 'created_at']
    fields = ['id', 'user_id', 'report_id', 'idempotency_key', 'task', 'status', 'error_code', 'created_at']
    readonly_fields = fields
