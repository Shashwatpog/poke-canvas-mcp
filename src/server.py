#!/usr/bin/env python3
import os
import httpx
from datetime import datetime,timezone, timedelta
from fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()
base_url = os.getenv("CANVAS_BASE_URL")
access_token = os.getenv("CANVAS_ACCESS_TOKEN")

mcp = FastMCP("poke-canvas-mcp")

def canvas_get(path : str, params : dict | None = None):
    url = base_url + path
    headers = {"Authorization" : f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers, params=params, timeout=30.0)

    if r.status_code >= 400:
        return{
            "ok": False,
            "status": r.status_code,
            "error": r.text,
            "url": str(r.url)
        }
    return {"ok": True, "data":r.json()}

@mcp.tool(description="get a list of all the canvas courses")
def get_courses(_=None):
    url = base_url+"/api/v1/courses?per_page=100"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers)
    return r.json();

@mcp.tool(description="get a list of the dashboard cards of the courses")
def get_dashboard_cards(term_prefix: str | None = None):
    url = base_url+"/api/v1/dashboard/dashboard_cards?per_page=100"
    headers = {"Authorization" : f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers)
    cards =  r.json()
    data = []
    for card in cards:
        name = card["shortName"]
        id = card["id"]
        if term_prefix and not name.startswith(term_prefix):
            continue
        data.append({"id": id, "name": name})
    return data;

@mcp.tool(description="get a list of upcoming and overdue assignments for a course")
def get_assignments(course_id: int, days_ahead: int, include_overdue: bool):
    now = datetime.now(timezone.utc)
    end =  now + timedelta(days=days_ahead)

    params =  {"per_page" : 100, "include[]" : "submission"}
    r = canvas_get(f"/api/v1/courses/{course_id}/assignments", params)

    if not r["ok"]:
        return r
    
    assignments = r["data"]
    results = []

    for assignment in assignments:
        due_at_raw = assignment.get("due_at")
        if not due_at_raw:
            continue
        
        due = datetime.fromisoformat(due_at_raw.replace("Z","+00:00"))

        submission  =  assignment.get("submission") or {}
        submitted = submission.get("submitted_at") is not None

        is_overdue = due < now and not submitted
        is_upcoming = now <= due <= end

        if is_upcoming or (include_overdue and is_overdue):
            results.append({
                "type": "assignment",
                "course_id": course_id,
                "id": assignment.get("id"),
                "name": assignment.get("name"),
                "due_at": due.isoformat(),
                "is_overdue": is_overdue,
                "submitted": submitted,
                "points_possible": assignment.get("points_possible"),
                "html_url": assignment.get("html_url"),
            })

    results.sort(key=lambda a: (not a["is_overdue"], a["due_at"]))

    return results;



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    
    print(f"Starting FastMCP server on {host}:{port}")
    
    mcp.run(
        transport="http",
        host=host,
        port=port,
        stateless_http=True
    )
