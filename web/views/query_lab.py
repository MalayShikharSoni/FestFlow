"""Query Lab: run any named pipeline, or a custom read-only one, and see pipeline + results + time."""
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from core.auth import role_required
from core.exceptions import FestFlowError
from services import analytics_service
from web.forms import QueryLabForm

STARTER = '[\n  {"$match": {"status": "confirmed"}},\n  {"$group": {"_id": "$eventType", "bookings": {"$sum": 1}}},\n' \
          '  {"$sort": {"bookings": -1}}\n]'


@role_required("organizer")
def query_lab(request: HttpRequest) -> HttpResponse:
    result = error = None
    name = request.GET.get("name")
    if request.method == "POST":
        form = QueryLabForm(request.POST)
        if form.is_valid():
            try:
                result = analytics_service.run_custom(form.cleaned_data["collection"], form.cleaned_data["pipeline"])
            except FestFlowError as exc:
                error = str(exc)  # rule violations and MongoDB's own error text, verbatim
    elif name:
        try:
            result = analytics_service.run_named(name)
        except FestFlowError as exc:
            error = str(exc)
        # Pre-fill the editor with the named pipeline so it can be modified and re-run.
        form = QueryLabForm(initial={"collection": result.collection, "pipeline": result.pipeline_json}
                            if result else {"collection": "bookings", "pipeline": STARTER})
    else:
        form = QueryLabForm(initial={"collection": "bookings", "pipeline": STARTER})
    return render(request, "web/organizer/query_lab.html", {
        "form": form, "result": result, "error": error, "selected": name,
        "pipelines": analytics_service.named_pipelines(), "max_rows": analytics_service.MAX_ROWS,
    })
