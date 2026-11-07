from rest_framework import serializers


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200)
    finish = serializers.CharField(max_length=200)
    include_initial_tank_cost = serializers.BooleanField(required=False, default=False)