from django.urls import path

from . import views

app_name = 'bug_reports'

urlpatterns = [
    path('', views.BugReportPublicListView.as_view(), name='list'),
    path('nuevo/', views.BugReportCreateView.as_view(), name='create'),
    path('consultar/', views.BugReportPublicListView.as_view(), name='public_list'),
    path('admin/', views.AdminBugReportListView.as_view(), name='admin_list'),
    path('admin/<int:pk>/', views.AdminBugReportReviewView.as_view(), name='admin_review'),
]
