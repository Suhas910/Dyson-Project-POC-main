import os
import tempfile
import database
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Form
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from OCC.Core.gp import gp_Dir

# Import the refactored pipeline logic
import pipeline
import rule_scoping

# Create the FastAPI app
app = FastAPI(title="DFM Analysis API")
database.initialize_database()
print("--- DFM Analysis API has been loaded ---")

# Allow all origins for CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define constants for the pipeline
RULES_CATALOG_PATH = Path(__file__).parent / "rules_catalog.json"
PULL_DIRECTION = gp_Dir(0, 0, 1)


@app.get("/")
async def read_root():
    """A simple root endpoint to confirm the API is running."""
    return {
        "message": "DFM Analysis API is running. Use the frontend to upload a file to the /analyze endpoint."
    }


@app.get("/api/process_families")
async def get_process_families():
    """
    Endpoint to retrieve a list of unique process families from the rules catalog.
    """
    with RULES_CATALOG_PATH.open("r", encoding="utf-8") as f:
        rules = json.load(f)
    process_families = sorted(
        list(
            set(
                rule.get("process_family")
                for rule in rules
                if rule.get("process_family")
            )
        )
    )
    return {
        "process_families": process_families,
        # Offered alongside the explicit families rather than replacing them:
        # an engineer who knows the process should still be able to say so, and
        # a disagreement between their choice and the geometry is itself worth
        # seeing.
        "auto": {
            "key": pipeline.AUTO_FAMILY,
            "label": "Detect from geometry",
            "description": (
                "Reads the likely process off the part and checks it against "
                "every family it could belong to."
            ),
        },
    }


@app.get("/api/report/{version_id}.html", response_class=HTMLResponse)
async def get_report_html(version_id: int):
    """The analysis as a self-contained HTML report.

    Rendered from stored results rather than re-run, so opening a report is
    instant and costs nothing, however long after the analysis it happens.
    """
    analysis = database.get_analysis(version_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"No analysis {version_id}")

    from report import renderer

    return HTMLResponse(content=renderer.render_html(analysis))


@app.get("/api/report/{version_id}.pdf")
async def get_report_pdf(version_id: int):
    """The same report as a PDF, for attaching to a design review."""
    analysis = database.get_analysis(version_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"No analysis {version_id}")

    from report import renderer

    available, reason = renderer.pdf_available()
    if not available:
        # The HTML report still works, so say which one is missing and why
        # rather than returning a bare 500.
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF rendering is unavailable in this environment "
                f"({reason}). The HTML report is unaffected."
            ),
        )

    # CPU-bound rendering; keep it off the event loop.
    pdf = await run_in_threadpool(renderer.render_pdf, analysis)

    stem = Path(analysis["file_name"]).stem or "report"
    filename = f"DFM_{stem}_v{version_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/api/mesh/{version_id}")
async def get_mesh(version_id: int):
    """The tessellated part, for the 3D view.

    A binary format rather than JSON: the payload lands straight in typed arrays
    with no per-number parsing, and carries a face id per vertex so the viewer
    can colour each face by its findings and resolve a click back to one.

    A 404 here means only that the part cannot be shown -- meshing is allowed to
    fail without taking the analysis with it, so the findings for this version
    are still served normally.
    """
    blob = database.get_mesh(version_id)
    if blob is None:
        raise HTTPException(
            status_code=404,
            detail=f"No 3D view stored for analysis {version_id}.",
        )
    return Response(
        content=blob,
        media_type="application/octet-stream",
        # Immutable: an analysis version is never rewritten, so the browser can
        # keep the mesh for as long as it likes.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/llm_status")
async def get_llm_status():
    """Reports whether the interpretive layer is configured.

    Lets the UI say plainly whether commentary is coming from a model or not,
    and makes a missing API key diagnosable without reading server logs.
    """
    import llm as llm_provider

    client = llm_provider.get_client()
    return {
        "enabled": client.is_available,
        "provider": "openrouter",
        "model": client.settings.model if client.is_available else None,
        "base_url": client.settings.base_url,
        "hint": (
            None
            if client.is_available
            else "Set OPENROUTER_API_KEY in backend/.env and restart the server."
        ),
    }


@app.get("/api/materials")
async def get_materials(process_family: str):
    """Materials whose rules differ within a process family.

    Wall-thickness limits for ABS, PC and PP are alternatives, so the analysis
    needs to know which one the part is made of. A family with no
    material-specific rules returns an empty list and the selector is hidden.
    """
    materials = rule_scoping.materials_for_family(process_family)
    return {
        "materials": [
            {"key": m.key, "label": m.label, "material_class": m.material_class}
            for m in materials
        ]
    }


@app.get("/api/versions")
async def get_versions():
    return {
        "versions": database.get_all_analyses()
    }
@app.get("/api/compare/{old_version_id}/{new_version_id}")
async def compare_versions(
    old_version_id: int,
    new_version_id: int
):
    try:
        comparison = database.compare_analyses(
            old_version_id,
            new_version_id
        )

        return comparison

    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e)
        )


@app.post("/api/analyze")
async def analyze_step_file(
    process_family: str = Form(...),
    file: UploadFile = File(...),
    material: Optional[str] = Form(None),
):
    """
    Endpoint to receive a STEP file, run the analysis pipeline,
    and return the findings as a JSON response.
    """
    if not file.filename.lower().endswith((".step", ".stp")):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a .step or .stp file.",
        )

    tmp_step_file = tempfile.NamedTemporaryFile(delete=False, suffix=".step")
    try:
        content = await file.read()
        tmp_step_file.write(content)
        tmp_step_file.close()

        # The pipeline is CPU-bound OCCT work. Running it directly in the async
        # handler blocks the event loop for the whole analysis, so the server
        # cannot answer anything else while a part is being measured.
        result = await run_in_threadpool(
            pipeline.run_analysis_pipeline,
            part_path=Path(tmp_step_file.name),
            rules_path=RULES_CATALOG_PATH,
            pull_direction=PULL_DIRECTION,
            process_family=process_family,
            material=material,
        )

        findings = result["findings"]
        validation_issues = result["validation_issues"]

        version_id = database.save_analysis(
            file_name=file.filename,
            process_family=process_family,
            findings=findings,
            validation_issues=validation_issues,
            material=material,
            summary=result["summary"],
            coverage=result["coverage"],
            part_metadata=result["part_metadata"],
            classification=result["classification"],
            mesh=result["mesh"],
        )

        if validation_issues:
            print(
                f"Validation issues for {file.filename}: {json.dumps(validation_issues, indent=2)}"
            )

        return {
            "findings": findings,
            "version_id": version_id,
            "rules_applied": result["rules_applied"],
            "validation_issues": validation_issues,
            "coverage": result["coverage"],
            "part_metadata": result["part_metadata"],
            "catalog_issues": result["catalog_issues"],
            "summary": result["summary"],
            "llm": result["llm"],
            "material": material,
            "process_families": result["process_families"],
            "classification": result["classification"],
            # The mesh itself is fetched separately; the flag lets the interface
            # decide whether to offer the 3D view at all rather than requesting
            # it and handling a 404.
            "has_mesh": result["mesh"] is not None,
        }

    except FileNotFoundError as e:
        print(f"File not found during analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        import traceback

        print(f"An unexpected error occurred during analysis for {file.filename}:")
        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"An unexpected server error occurred: {str(e)}"
        )
    finally:
        os.remove(tmp_step_file.name)

