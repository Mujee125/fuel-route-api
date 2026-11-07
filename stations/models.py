from django.db import models

# Create your models here.
from django.db import models
from django.db.models import Q


class FuelStation(models.Model):
    opis_id = models.PositiveIntegerField(unique=True)       # D5: one row per OPIS ID
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)   # untrusted text
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=6, decimal_places=3)  # lowest price per ID

    # filled by the geocoder (US-04)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    geocode_method = models.CharField(max_length=20, blank=True)  # exact / normalized / fuzzy

    price_flagged = models.BooleanField(default=False)       # price > $5 outlier, kept in table

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lng"], name="station_lat_lng_idx"),      # bbox query
            models.Index(fields=["state", "city"], name="station_state_city_idx"), # geocode/resolver
            models.Index(fields=["price"], name="station_price_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="station_price_positive"),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"