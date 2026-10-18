from django.conf import settings
from django.db import models


class DevToolVote(models.Model):
    LIKE = 1
    DISLIKE = -1
    VOTE_CHOICES = (
        (LIKE, 'Me gusta'),
        (DISLIKE, 'No me gusta'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='devtool_votes',
    )
    tool = models.CharField(max_length=80, db_index=True)
    vote = models.SmallIntegerField(choices=VOTE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'tool'],
                name='unique_devtool_vote_per_user_tool',
            )
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} -> {self.tool}: {self.get_vote_display()}'


class DevToolConfig(models.Model):
    slug = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=120, default='')
    is_active = models.BooleanField(default=True)
    is_private = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'slug']

    def __str__(self):
        return self.name or self.slug
