"""Local product studies; separate from social-agent simulations."""

from copy import deepcopy
from io import BytesIO
import json
import threading

from flask import Blueprint, current_app, jsonify, request, send_file

from ..chatgpt_auth import get_account_cache_scope, get_study_owner_scope, require_local_request
from ..models.project import ProjectManager
from ..services.product_studies import DEFAULT_PERSONAS, LIMITATION, ProductStudyRunner, StudyError, human_protocol, upper_bound, validate_spec
from ..services.study_store import StudyStore

product_testing_bp = Blueprint("product_testing", __name__, url_prefix="/api/product-testing")
_init_lock = threading.Lock()


def store_and_runner():
    with _init_lock:
        if "product_studies" not in current_app.extensions:
            store = StudyStore(current_app.config["PRODUCT_TESTING_DIR"])
            # Upgrade only the currently authenticated connection's early
            # study records; never claim another account's saved research.
            session, owner = get_account_cache_scope(), get_study_owner_scope()
            for legacy in store.list(session):
                if legacy.get("owner_version", 1) == 1:
                    store.update(legacy["id"], lambda d: d.update(owner=owner, owner_version=2))
            current_app.extensions["product_studies"] = (store, ProductStudyRunner(current_app._get_current_object(), store))
        return current_app.extensions["product_studies"]


@product_testing_bp.before_request
def local_account():
    require_local_request()
    get_account_cache_scope()


@product_testing_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@product_testing_bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(success=False, error=str(error)), 400


@product_testing_bp.errorhandler(KeyError)
def missing(error):
    return jsonify(success=False, error="Study or screenshot not found."), 404


def owned(study_id):
    store, _ = store_and_runner()
    data = store.get(study_id)
    if data["owner"] != get_study_owner_scope():
        raise KeyError("Study not found for this connection.")
    return data


def public_data(data):
    data = deepcopy(data)
    data.pop("owner", None)
    data.pop("worker_pid", None)
    data.pop("connection_scope", None)
    data["request_upper_bound"] = upper_bound(data["spec"])
    data["limitation"] = LIMITATION
    data["quality_warning"] = (
        "This early verification study captured a page before readiness checks were added. Its loading-screen responses are not a product-usability baseline. Create a new study using a ready screen."
        if any(v.get("asset", {}).get("source", {}).get("kind") == "public_page" and not v["asset"]["source"].get("capture_ready")
               for v in data["spec"]["variants"] if v.get("asset")) else None
    )
    return data


@product_testing_bp.get("/defaults")
def defaults():
    context = ""
    project_id = None
    for project in ProjectManager.list_projects():
        if project.name == "Map of XP - product background":
            context = (ProjectManager.get_extracted_text(project.project_id) or "")[:30000]
            project_id = project.project_id
            break
    return jsonify(success=True, data={"personas": DEFAULT_PERSONAS, "researcher_context": context,
        "project_id": project_id, "product_url": "https://mapofxp.com/", "limitation": LIMITATION,
        "asset_origins": ["https://tiles.openfreemap.org", "https://media.mapofxp.com"]})


@product_testing_bp.get("/studies")
def list_studies():
    store, _ = store_and_runner()
    data = [{"id": d["id"], "name": d["spec"]["name"], "kind": d["spec"]["kind"], "status": d["status"],
             "created_at": d["created_at"], "requests_used": d["requests_used"]} for d in store.list(get_study_owner_scope())]
    return jsonify(success=True, data=data)


@product_testing_bp.post("/studies")
def create_study():
    store, _ = store_and_runner()
    spec = validate_spec(request.get_json(silent=True))
    return jsonify(success=True, data=public_data(store.create(spec, get_study_owner_scope()))), 201


@product_testing_bp.get("/studies/<study_id>")
def get_study(study_id):
    return jsonify(success=True, data=public_data(owned(study_id)))


@product_testing_bp.patch("/studies/<study_id>/budget")
def update_budget(study_id):
    owned(study_id)
    cap = (request.get_json(silent=True) or {}).get("max_requests")
    if type(cap) is not int or not 3 <= cap <= 100:
        raise StudyError("Choose an explicit request cap between 3 and 100.")
    store, _ = store_and_runner()
    def change(data):
        if data["status"] in {"running", "queued", "completed"}:
            raise StudyError("The request cap can be changed only while a study is stopped.")
        if cap < data["requests_used"]:
            raise StudyError("The request cap cannot be lower than the attempts already used.")
        data["spec"]["max_requests"] = cap
    return jsonify(success=True, data=public_data(store.update(study_id, change)))


@product_testing_bp.post("/studies/<study_id>/variants/<variant_id>/image")
def upload_screen(study_id, variant_id):
    owned(study_id)
    uploaded = request.files.get("image")
    if not uploaded:
        raise StudyError("Choose a PNG, JPEG or WebP screenshot.")
    content = uploaded.read(5 * 1024 * 1024 + 1)
    store, _ = store_and_runner()
    asset = store.save_asset(content)
    def attach(data):
        if data["status"] != "draft":
            raise StudyError("Screens are frozen once a study starts. Create a new study to change them.")
        variant = next((v for v in data["spec"]["variants"] if v["id"] == variant_id), None)
        if not variant:
            raise KeyError(variant_id)
        if asset["width"] != data["spec"]["viewport"]["width"] or asset["height"] != data["spec"]["viewport"]["height"]:
            raise StudyError("Screenshot dimensions must match the study viewport. Select its exact width and height before saving the study.")
        variant["asset"] = asset
    return jsonify(success=True, data=public_data(store.update(study_id, attach)))


def referenced_assets(data):
    assets = [v.get("asset") for v in data["spec"]["variants"]]
    for run in data["runs"]:
        assets.append(run.get("screenshot"))
        assets.extend(s.get("screenshot") for s in run.get("steps", []))
        for attempt in run.get("previous_attempts", []):
            assets.extend(s.get("screenshot") for s in attempt)
    return {a["id"]: a for a in assets if a}


@product_testing_bp.get("/studies/<study_id>/assets/<asset_id>")
def get_screen(study_id, asset_id):
    asset = referenced_assets(owned(study_id)).get(asset_id)
    if not asset:
        raise KeyError(asset_id)
    store, _ = store_and_runner()
    response = send_file(BytesIO(store.asset(asset)), mimetype=asset["mime"], max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@product_testing_bp.post("/studies/<study_id>/start")
def start_study(study_id):
    owned(study_id)
    store, runner = store_and_runner()
    runner.start(study_id)
    return jsonify(success=True, data=public_data(store.get(study_id))), 202


@product_testing_bp.post("/studies/<study_id>/cancel")
def cancel_study(study_id):
    owned(study_id)
    store, _ = store_and_runner()
    def cancel(data):
        if data["status"] not in {"queued", "running"}:
            raise StudyError("Only an active study can be cancelled.")
        data.update(status="cancelled", error="Cancelled. An in-flight response may finish, but no further study actions will be dispatched.")
    return jsonify(success=True, data=public_data(store.update(study_id, cancel)))


@product_testing_bp.get("/studies/<study_id>/export")
def export_study(study_id):
    data = public_data(owned(study_id))
    if request.args.get("format") == "human-plan":
        content, mime, name = human_protocol(data).encode(), "text/markdown", study_id + "-human-validation.md"
    else:
        content, mime, name = json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json", study_id + ".json"
    response = send_file(BytesIO(content), mimetype=mime, as_attachment=True, download_name=name)
    response.headers["Cache-Control"] = "no-store"
    return response
