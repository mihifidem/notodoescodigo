from .selectors import teacher_memberships


def lms_teacher_nav(request):
    if not request.user.is_authenticated:
        return {
            'is_lms_teacher': False,
            'teacher_course_count': 0,
        }

    memberships = teacher_memberships(request.user)
    return {
        'is_lms_teacher': memberships.exists(),
        'teacher_course_count': memberships.count(),
    }
