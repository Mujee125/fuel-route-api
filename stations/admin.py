from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "price", "price_flagged")
    list_filter = ("state", "price_flagged")
    search_fields = ("name", "city")