from django.urls import path
from .views import index, blueprints_list, jobs_list, token_management, authorize_blueprints, authorize_jobs

app_name = 'indy_hub'
urlpatterns = [
    path('', index, name='index'),
    path('blueprints/', blueprints_list, name='blueprints_list'),
    path('jobs/', jobs_list, name='jobs_list'),
    path('tokens/', token_management, name='token_management'),
    path('authorize/blueprints/', authorize_blueprints, name='authorize_blueprints'),
    path('authorize/jobs/', authorize_jobs, name='authorize_jobs'),
]
