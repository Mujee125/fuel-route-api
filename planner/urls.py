from django.urls import path

from planner import route_views
from . import views

urlpatterns = [path("health/", views.health),path("route-plan/", route_views.RoutePlanView.as_view(), name="route-plan"),
    path("route-plan/map/", route_views.route_plan_map, name="route-plan-map"),]
