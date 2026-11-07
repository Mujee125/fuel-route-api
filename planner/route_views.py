import time

from django.http import HttpResponse
from django.shortcuts import render
from django.utils.html import escape
from django.views.decorators.http import require_GET
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_errors import ErrorInfo, describe_error, elapsed_ms, error_body
from .serializers import RoutePlanRequestSerializer
from .services import plan_route


class RoutePlanView(APIView):
    """POST {start, finish} or GET ?start=&finish= -> route, fuel stops, total cost."""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return self._run(lambda: request.query_params)

    def post(self, request):
        return self._run(lambda: request.data)

    def _run(self, get_params):
        started = time.perf_counter()
        try:
            params = get_params()
        except ParseError:
            info = ErrorInfo(400, "malformed_json", "Request body is not valid JSON.", 0)
            return Response(error_body(info, started), status=400)

        ser = RoutePlanRequestSerializer(data=params)
        if not ser.is_valid():
            info = ErrorInfo(400, "invalid_input", "'start' and 'finish' are required.", 0,
                            {"fields": ser.errors})
            return Response(error_body(info, started), status=400)

        d = ser.validated_data
        try:
            payload = plan_route(d["start"], d["finish"], d["include_initial_tank_cost"])
        except Exception as exc:
            info = describe_error(exc)
            return Response(error_body(info, started), status=info.status)

        payload["meta"]["processing_ms"] = elapsed_ms(started)
        return Response(payload)


@require_GET
def route_plan_map(request):
    """Leaflet page for the same plan (US-11)."""
    ser = RoutePlanRequestSerializer(data=request.GET)
    if not ser.is_valid():
        return HttpResponse("<h1>400</h1><p>Provide ?start=...&amp;finish=...</p>", status=400)
    d = ser.validated_data
    try:
        payload = plan_route(d["start"], d["finish"], d["include_initial_tank_cost"])
    except Exception as exc:
        info = describe_error(exc)
        return HttpResponse(f"<h1>{info.status}</h1><p>{escape(info.message)}</p>", status=info.status)
    return render(request, "planner/map.html", {
        "geojson": payload["map"]["geojson"],
        "start_label": payload["start"]["label"],
        "finish_label": payload["finish"]["label"],
        "distance": payload["route"]["distance_miles"],
        "stops": payload["summary"]["stops_count"],
        "total": payload["summary"]["total_fuel_cost"],
        "calls": payload["meta"]["external_api_calls"],
        "profile": payload["meta"]["routing_profile"],
    })