from django.contrib import admin
from django_prose_editor.widgets import AdminProseEditorWidget

from .forms import PROSE_EDITOR_CONFIG

from .models import (
    Announcement,
    AnnouncementComment,
    AnnouncementReaction,
    AnnouncementTarget,
    AuditEvent,
    CourseMembership,
    CourseModule,
    CourseSettingsLMS,
    CourseSyllabus,
    DiscussionModerationLog,
    DiscussionPost,
    DiscussionPostLike,
    DiscussionReport,
    DiscussionSubscription,
    DiscussionTag,
    DiscussionTopic,
    FileAsset,
    FileAssetLink,
    FormativeUnit,
    LearningActivity,
    LearningActivitySection,
    LearningEvent,
    LearningProgress,
    ModuleItem,
    ModuleProgress,
    Page,
    PageRevision,
    Section,
    UserProfileLMS,
)


@admin.register(UserProfileLMS)
class UserProfileLMSAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'preferred_language',
        'time_zone',
        'email_notifications',
        'onboarding_completed',
    )
    list_filter = ('preferred_language', 'email_notifications', 'onboarding_completed')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'created_at', 'updated_at')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'position')
    list_filter = ('course',)
    search_fields = ('title', 'course__nombre')


@admin.register(CourseSettingsLMS)
class CourseSettingsLMSAdmin(admin.ModelAdmin):
    list_display = ('course', 'enrollment_open', 'archived', 'legacy_slug')
    list_filter = ('enrollment_open', 'archived')
    search_fields = ('course__nombre', 'legacy_slug')
    readonly_fields = ('legacy_slug',)


@admin.register(CourseMembership)
class CourseMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'role', 'status', 'section', 'joined_at')
    list_filter = ('role', 'status', 'course')
    search_fields = ('user__username', 'user__email', 'course__nombre')
    autocomplete_fields = ('user', 'course', 'section')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(CourseModule)
class CourseModuleAdmin(admin.ModelAdmin):
    list_display = ('course', 'title', 'status', 'position', 'starts_at', 'ends_at')
    list_filter = ('status', 'course')
    search_fields = ('title', 'course__nombre', 'slug')


@admin.register(FormativeUnit)
class FormativeUnitAdmin(admin.ModelAdmin):
    list_display = ('title', 'module', 'position')
    list_filter = ('module__curso',)
    search_fields = ('title', 'module__titulo', 'module__curso__nombre')


@admin.register(ModuleItem)
class ModuleItemAdmin(admin.ModelAdmin):
    list_display = ('module', 'title', 'item_type', 'position', 'required')
    list_filter = ('item_type', 'required', 'module__course')
    search_fields = ('title', 'module__title')


@admin.register(LearningActivity)
class LearningActivityAdmin(admin.ModelAdmin):
    list_display = ('course', 'title', 'status', 'starts_at', 'due_at', 'solution_release_at')
    list_filter = ('status', 'course', 'is_evaluable')
    search_fields = ('title', 'course__nombre')


@admin.register(LearningActivitySection)
class LearningActivitySectionAdmin(admin.ModelAdmin):
    list_display = ('activity', 'title', 'section_type', 'position', 'is_solution_section')
    list_filter = ('section_type', 'is_solution_section', 'activity__course')
    search_fields = ('title', 'activity__title', 'activity__course__nombre')


@admin.register(LearningEvent)
class LearningEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'object_type', 'event_type', 'created_at')
    list_filter = ('course', 'event_type', 'object_type')
    search_fields = ('user__username', 'course__nombre')
    readonly_fields = ('created_at',)


@admin.register(ModuleProgress)
class ModuleProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'module', 'percentage', 'status', 'updated_at')
    list_filter = ('course', 'status')
    search_fields = ('user__username', 'course__nombre', 'module__title')


@admin.register(LearningProgress)
class LearningProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'completion_percentage', 'modules_completed', 'total_modules', 'computed_at')
    list_filter = ('course',)
    search_fields = ('user__username', 'course__nombre')


@admin.register(FileAsset)
class FileAssetAdmin(admin.ModelAdmin):
    list_display = ('course', 'title', 'folder', 'visible_to_all', 'is_active', 'size', 'created_at')
    list_filter = ('course', 'visibility', 'access_policy', 'is_active')
    search_fields = ('title', 'file_name', 'course__nombre', 'slug')

    def visible_to_all(self, obj):
        return obj.visibility in {obj.Visibility.PUBLIC, obj.Visibility.STUDENTS}

    visible_to_all.boolean = True


@admin.register(FileAssetLink)
class FileAssetLinkAdmin(admin.ModelAdmin):
    list_display = ('asset', 'token', 'created_by', 'expires_at', 'used_at')
    list_filter = ('asset__course', 'expires_at')
    search_fields = ('asset__title', 'token', 'created_by__username')


@admin.register(CourseSyllabus)
class CourseSyllabusAdmin(admin.ModelAdmin):
    list_display = ('course', 'teacher_name', 'estimated_hours', 'updated_at')
    search_fields = ('course__nombre', 'teacher_name', 'title')
    list_filter = ('course',)


class ProseContentAdmin(admin.ModelAdmin):
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'content':
            kwargs['widget'] = AdminProseEditorWidget(
                preset='configurable', config=PROSE_EDITOR_CONFIG,
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(Page)
class PageAdmin(ProseContentAdmin):
    list_display = ('course', 'title', 'status', 'visibility', 'position', 'is_homepage')
    list_filter = ('status', 'visibility', 'course')
    search_fields = ('title', 'course__nombre', 'slug')
    ordering = ('course__nombre', 'position', 'title')


@admin.register(PageRevision)
class PageRevisionAdmin(admin.ModelAdmin):
    list_display = ('page', 'revision_number', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'page__course')
    search_fields = ('page__title', 'summary')
    readonly_fields = ('revision_number', 'created_at')


@admin.register(Announcement)
class AnnouncementAdmin(ProseContentAdmin):
    list_display = ('course', 'title', 'status', 'is_pinned', 'publish_at', 'expire_at', 'author')
    list_filter = ('course', 'status', 'is_pinned', 'has_audio')
    search_fields = ('title', 'content', 'course__nombre', 'author__username')
    ordering = ('-publish_at', '-created_at')


@admin.register(AnnouncementTarget)
class AnnouncementTargetAdmin(admin.ModelAdmin):
    list_display = ('announcement', 'target_type', 'section')
    list_filter = ('target_type', 'announcement__course', 'section')


@admin.register(AnnouncementComment)
class AnnouncementCommentAdmin(admin.ModelAdmin):
    list_display = ('announcement', 'author', 'created_at')
    search_fields = ('announcement__title', 'content', 'author__username')


@admin.register(AnnouncementReaction)
class AnnouncementReactionAdmin(admin.ModelAdmin):
    list_display = ('announcement', 'user', 'kind', 'created_at')
    list_filter = ('kind', 'announcement__course')
    search_fields = ('announcement__title', 'user__username')


@admin.register(DiscussionTag)
class DiscussionTagAdmin(admin.ModelAdmin):
    list_display = ('course', 'name', 'slug', 'created_at')
    list_filter = ('course',)
    search_fields = ('name', 'course__nombre')


@admin.register(DiscussionTopic)
class DiscussionTopicAdmin(admin.ModelAdmin):
    list_display = ('course', 'section', 'title', 'status', 'is_pinned', 'is_locked', 'published_at')
    list_filter = ('course', 'status', 'is_pinned', 'is_locked', 'section')
    search_fields = ('title', 'course__nombre', 'author__username')
    filter_horizontal = ('tags',)


@admin.register(DiscussionPost)
class DiscussionPostAdmin(admin.ModelAdmin):
    list_display = ('topic', 'parent', 'author', 'is_edited', 'is_deleted', 'created_at')
    list_filter = ('topic__course', 'is_deleted', 'is_hidden', 'is_edited')
    search_fields = ('content', 'topic__title', 'author__username')


@admin.register(DiscussionPostLike)
class DiscussionPostLikeAdmin(admin.ModelAdmin):
    list_display = ('post', 'user', 'created_at')
    list_filter = ('post__topic__course',)
    search_fields = ('post__topic__title', 'user__username')


@admin.register(DiscussionSubscription)
class DiscussionSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('topic', 'user', 'is_active', 'created_at')
    list_filter = ('is_active', 'topic__course')
    search_fields = ('topic__title', 'user__username')


@admin.register(DiscussionReport)
class DiscussionReportAdmin(admin.ModelAdmin):
    list_display = ('topic', 'post', 'reporter', 'reason', 'status', 'created_at')
    list_filter = ('status', 'topic__course')
    search_fields = ('reason', 'details', 'topic__title', 'reporter__username')


@admin.register(DiscussionModerationLog)
class DiscussionModerationLogAdmin(admin.ModelAdmin):
    list_display = ('topic', 'actor', 'action', 'created_at')
    list_filter = ('topic__course',)
    search_fields = ('topic__title', 'action', 'actor__username')


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('action', 'course', 'actor', 'target_user', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('course__nombre', 'actor__username', 'target_user__username')
    readonly_fields = ('actor', 'course', 'target_user', 'action', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
