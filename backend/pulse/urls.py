from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path

from triage.views import spa

admin.site.site_header = "Pulse administration"
admin.site.site_title = "Pulse admin"
admin.site.index_title = "Model, catalog and demo data"

urlpatterns = [
    # Hosting health check. Touches no database, so it never wakes a sleeping one.
    path("healthz/", lambda request: HttpResponse("ok", content_type="text/plain")),
    path("admin/", admin.site.urls),
    path("api/", include("triage.urls")),
    # Everything else is the React app (built into frontend/dist).
    re_path(r"^(?!api/|admin/|static/|healthz/).*$", spa, name="spa"),
]
