from django.urls import path
from desk import api, views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("requests/<int:pk>/", views.detail, name="detail"),
    path("imports/", views.import_view, name="import"),
    path("analytics/", views.analytics_view, name="analytics"),
    path("health", views.health),
    path("api/csrf/", api.csrf),
    path("api/login/", api.login_api),
    path("api/logout/", api.logout_api),
    path("api/me/", api.me),
    path("api/requests/", api.requests_api),
    path("api/requests/<int:pk>/", api.request_detail),
    path("api/requests/<int:pk>/transition/", api.transition_api),
    path("api/requests/<int:pk>/events/", api.events_api),
    path("api/requests/<int:pk>/assignments/", api.assignments_api),
    path("api/requests/<int:pk>/assignments/<str:episode_id>/", api.assignment_delete),
    path("api/episodes/", api.episodes_api),
    path("api/episodes/import/", api.import_api),
    path("api/analytics/", api.analytics_api),
    path("api/users/", api.users_api),
    path("api/users/<int:pk>/", api.user_update),
]
