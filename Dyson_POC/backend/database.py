import sqlite3
import json
from pathlib import Path
from datetime import datetime


DATABASE_PATH = Path(__file__).parent / "dfm_analysis.db"


def get_connection():
    return sqlite3.connect(DATABASE_PATH)


def initialize_database():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            process_family TEXT NOT NULL,
            created_at TEXT NOT NULL,
            findings TEXT NOT NULL,
            validation_issues TEXT NOT NULL
        )
    """)

    # Columns added after the first release are backfilled rather than requiring
    # the database to be thrown away. Material matters because comparing two
    # analyses of the same part run under different materials would otherwise
    # look like a design change; the rest are what a report needs to be
    # regenerated from an id alone, long after the request that produced it.
    existing_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(analysis_versions)")
    }
    for column in ("material", "summary", "coverage", "part_metadata"):
        if column not in existing_columns:
            cursor.execute(
                f"ALTER TABLE analysis_versions ADD COLUMN {column} TEXT"
            )

    # The tessellated part, stored as a blob so the 3D view survives the STEP
    # file being deleted after the request. It is kept out of `get_analysis`
    # deliberately: it is megabytes where the rest of the row is kilobytes, and
    # every caller of that function wants the findings, not the geometry.
    # How the part was read: which families were analysed, on what evidence.
    # Stored so a report regenerated months later still explains why it has the
    # sections it has.
    if "classification" not in existing_columns:
        cursor.execute("ALTER TABLE analysis_versions ADD COLUMN classification TEXT")

    if "mesh" not in existing_columns:
        cursor.execute("ALTER TABLE analysis_versions ADD COLUMN mesh BLOB")

    connection.commit()
    connection.close()


def save_analysis(
    file_name: str,
    process_family: str,
    findings: list,
    validation_issues: list,
    material: str | None = None,
    summary: dict | None = None,
    coverage: dict | None = None,
    part_metadata: dict | None = None,
    classification: dict | None = None,
    mesh: bytes | None = None,
):
    """Persists one analysis run.

    Everything a report needs is stored, not just the findings, so a report can
    be produced from a version id at any later point without re-running the
    geometry or paying for the model again.
    """
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO analysis_versions
        (
            file_name,
            process_family,
            created_at,
            findings,
            validation_issues,
            material,
            summary,
            coverage,
            part_metadata,
            classification,
            mesh
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_name,
            process_family,
            datetime.now().isoformat(),
            json.dumps(
                [finding.model_dump() for finding in findings]
            ),
            json.dumps(validation_issues),
            material,
            json.dumps(summary) if summary else None,
            json.dumps(coverage) if coverage else None,
            json.dumps(part_metadata) if part_metadata else None,
            json.dumps(classification) if classification else None,
            mesh,
        )
    )

    version_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return version_id

def get_analysis(version_id: int):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            file_name,
            process_family,
            created_at,
            findings,
            validation_issues,
            material,
            summary,
            coverage,
            part_metadata,
            classification
        FROM analysis_versions
        WHERE id = ?
        """,
        (version_id,)
    )

    row = cursor.fetchone()

    connection.close()

    if row is None:
        return None

    def _json(value):
        # Rows written before these columns existed carry NULL.
        return json.loads(value) if value else None

    return {
        "id": row[0],
        "file_name": row[1],
        "process_family": row[2],
        "material": row[6],
        "summary": _json(row[7]),
        "coverage": _json(row[8]),
        "part_metadata": _json(row[9]),
        "classification": _json(row[10]),
        "created_at": row[3],
        "findings": json.loads(row[4]),
        "validation_issues": json.loads(row[5]),
    }
def get_mesh(version_id: int) -> bytes | None:
    """The tessellated part for one run, or None if it was never built.

    Separate from `get_analysis` so that reading findings never drags a
    multi-megabyte blob out of the database with them.
    """
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT mesh FROM analysis_versions WHERE id = ?", (version_id,)
    )
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


def get_all_analyses():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            file_name,
            process_family,
            created_at
        FROM analysis_versions
        ORDER BY id DESC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        {
            "id": row[0],
            "file_name": row[1],
            "process_family": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]
def compare_analyses(old_version_id: int, new_version_id: int):
    old_analysis = get_analysis(old_version_id)
    new_analysis = get_analysis(new_version_id)

    if old_analysis is None:
        raise ValueError(f"Version {old_version_id} not found")

    if new_analysis is None:
        raise ValueError(f"Version {new_version_id} not found")

    old_findings = old_analysis["findings"]
    new_findings = new_analysis["findings"]

    # Get the overall status of each rule.
    # A rule is considered non-compliant if at least one
    # finding for that rule is NON-COMPLIANT.
    def build_rule_status(findings):
        rule_status = {}

        for finding in findings:
            rule_id = finding["rule_id"]
            status = finding["status"]

            if rule_id not in rule_status:
                rule_status[rule_id] = status

            if status == "NON-COMPLIANT":
                rule_status[rule_id] = "NON-COMPLIANT"

        return rule_status

    old_status = build_rule_status(old_findings)
    new_status = build_rule_status(new_findings)

    fixed = []
    still_open = []
    new_issues = []

    # Rules that existed in the old version
    for rule_id, old_rule_status in old_status.items():

        new_rule_status = new_status.get(rule_id)

        # Previously failed, now no longer fails
        if (
            old_rule_status == "NON-COMPLIANT"
            and new_rule_status != "NON-COMPLIANT"
        ):
            fixed.append(rule_id)

        # Still failing
        elif (
            old_rule_status == "NON-COMPLIANT"
            and new_rule_status == "NON-COMPLIANT"
        ):
            still_open.append(rule_id)

    # Rules that are newly failing
    for rule_id, new_rule_status in new_status.items():

        if (
            rule_id not in old_status
            and new_rule_status == "NON-COMPLIANT"
        ):
            new_issues.append(rule_id)

    return {
        "old_version": old_version_id,
        "new_version": new_version_id,
        "fixed": fixed,
        "still_open": still_open,
        "new_issues": new_issues,
        "summary": {
            "fixed": len(fixed),
            "still_open": len(still_open),
            "new_issues": len(new_issues),
        },
    }