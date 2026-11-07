from django.shortcuts import render

# Create your views here.
from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["GET"])
def health(request):
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return Response({"status": "ok"})