#!/usr/bin/env python3
import os
import httpx
from datetime import datetime,timezone, timedelta
import re 
from html import unescape
from typing import Any
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

# the response from announcements endpoint has weird html characters, this helper converts to text and cleans it
def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def abs_url(url: str | None) -> str | None:
    if not url:
        return url
    if url.startswith("/"):
        return base_url + url
    return url

def fetch_dashboard_cards(term_prefix: str | None = None):
    url = base_url + "/api/v1/dashboard/dashboard_cards?per_page=100"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers)
    cards = r.json()

    data = []
    for card in cards:
        name = card["shortName"]
        id = card["id"]
        if term_prefix and not name.startswith(term_prefix):
            continue
        data.append({"id": id, "name": name})
    return data

def fetch_assignments(course_id: int, days_ahead: int, include_overdue: bool):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    params = {"per_page": 100, "include[]": "submission"}
    r = canvas_get(f"/api/v1/courses/{course_id}/assignments", params)

    if not r["ok"]:
        return r

    assignments = r["data"]
    results = []

    for assignment in assignments:
        due_at_raw = assignment.get("due_at")
        if not due_at_raw:
            continue

        due = datetime.fromisoformat(due_at_raw.replace("Z", "+00:00"))

        submission = assignment.get("submission") or {}
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

    results.sort(key=lambda assignment: (not assignment["is_overdue"], assignment["due_at"]))
    return results

@mcp.tool(description="get a list of all the canvas courses")
def get_courses(_=None):
    url = base_url+"/api/v1/courses?per_page=100"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers)
    return r.json();

@mcp.tool(description="get a list of the dashboard cards of the courses")
def get_dashboard_cards(term_prefix: str | None = None):
    return fetch_dashboard_cards(term_prefix)

@mcp.tool(description="get a list of upcoming and overdue assignments for a course")
def get_assignments(course_id: int, days_ahead: int, include_overdue: bool):
    return fetch_assignments(course_id, days_ahead, include_overdue)

@mcp.tool(description="get a list of all upcoming assignments in the upcoming week in one call")
def get_upcoming_assignments(days_ahead: int = 7, include_overdue: bool = False, term_prefix: str | None = None, max_courses: int = 8):
    courses = fetch_dashboard_cards(term_prefix)

    if not term_prefix and max_courses and max_courses > 0:
        courses = courses[:max_courses]

    all_assignments = []

    for course in courses:
        course_id = course["id"]
        course_name = course["name"]
        assignments = fetch_assignments(course_id, days_ahead, include_overdue)
        if isinstance(assignments, list):
            for assignment in assignments:
                assignment["course_name"] = course_name
                all_assignments.append(assignment)

    all_assignments.sort(key=lambda assignment: (not assignment["is_overdue"], assignment["due_at"]))

    return all_assignments;

@mcp.tool(description="get a list of recent announcements")
def get_recent_announcements(days_back: int =7, term_prefix: str | None = None, max_courses: int = 8, per_course: int = 5, include_body: bool = False):
    now = datetime.now(timezone.utc)
    start =  now - timedelta(days=days_back)

    courses = fetch_dashboard_cards(term_prefix)

    if not term_prefix and max_courses and max_courses > 0:
        courses = courses[:max_courses]
    
    all_items: list[dict[str, Any]] = []

    for course in courses:
        course_id = course["id"]
        course_name = course["name"]
        params = {
            "only_announcements" : "true",
            "per_page" : 50
        }

        r = canvas_get(f"/api/v1/courses/{course_id}/discussion_topics", params)

        if not r["ok"]:
            continue

        topics = r["data"] or []
        results_for_course: list[dict[str, Any]] = []

        for topic in topics:
            posted_raw = topic.get("posted_at") or topic.get("created_at")
            if not posted_raw:
                continue

            posted = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
            if posted < start:
                continue

            item: dict[str, Any] = {
                "type": "announcement",
                "course_id": course_id,
                "course_name": course_name,
                "id": topic.get("id"),
                "title": topic.get("title"),
                "posted_at": posted.isoformat(),
                "author": (topic.get("author") or {}).get("display_name") or topic.get("user_name"),
                "read_state": topic.get("read_state"),
                "unread_count": topic.get("unread_count"),
                "html_url": abs_url(topic.get("html_url") or topic.get("url")),
            }

            if include_body:
                body_html = topic.get("message") or ""
                item["message_html"] = body_html
                item["message_text"] = strip_html(body_html) if body_html else ""

            results_for_course.append(item)

        results_for_course.sort(key=lambda x: x["posted_at"], reverse=True)
        all_items.extend(results_for_course[:per_course])

    all_items.sort(key=lambda x: x["posted_at"], reverse=True)
    return all_items

@mcp.tool(description="get all events for the upcoming week")
def get_week_ahead(days_ahead: int = 7, days_back: int = 0, per_page: int = 100):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)
    params = {
        "per_page": per_page,
        "start_date": start.isoformat().replace("+00:00", "Z"),
        "end_date": end.isoformat().replace("+00:00", "Z"), 
    }

    r = canvas_get("/api/v1/planner/items", params)
    if not r["ok"]:
        return r

    items = r["data"] or []
    #print("planner/items returned:", len(items))
    out: list[dict[str, Any]] = []

    for item in items:
        dt_raw = item.get("plannable_date")
        if not dt_raw:
            continue

        dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        if not (start <= dt <= end):
            continue

        plannable = item.get("plannable") or {}
        pl_type = item.get("plannable_type")

        normalized: dict[str, Any] = {
            "type": pl_type,
            "course_id": item.get("course_id"),
            "course_name": item.get("context_name"),
            "id": item.get("plannable_id"),
            "title": plannable.get("title"),
            "date": dt.isoformat(),
            "new_activity": item.get("new_activity", False),
            "html_url": abs_url(item.get("html_url") or ""),
        }

        subs = item.get("submissions")
        if isinstance(subs, dict):
            normalized["submission"] = {
                "submitted": subs.get("submitted"),
                "graded": subs.get("graded"),
                "late": subs.get("late"),
                "missing": subs.get("missing"),
                "posted_at": subs.get("posted_at"),
                "has_feedback": subs.get("has_feedback"),
            }

        if pl_type in ("assignment", "quiz"):
            normalized["due_at"] = plannable.get("due_at")
            normalized["points_possible"] = plannable.get("points_possible")
            normalized["assignment_id"] = plannable.get("assignment_id")

        if pl_type == "calendar_event":
            normalized["start_at"] = plannable.get("start_at")
            normalized["end_at"] = plannable.get("end_at")
            normalized["location_name"] = plannable.get("location_name")
            normalized["online_meeting_url"] = plannable.get("online_meeting_url")

        out.append(normalized)
    out.sort(key=lambda x: x["date"])
    return out

@mcp.tool(description="get assignment graded with the grade notification")
def get_recently_graded(days_back: int = 7, term_prefix: str | None = None, max_courses: int = 8, per_page : int = 100, include_only_with_feedback: bool = False):

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    allowed_course_ids: set[int] | None = None
    if term_prefix is not None or (max_courses and max_courses > 0):
        courses = fetch_dashboard_cards(term_prefix)
        if not term_prefix and max_courses and max_courses > 0:
            courses = courses[:max_courses]
        allowed_course_ids = {c["id"] for c in courses}

    params = {
        "per_page": per_page,
        "start_date": start.isoformat().replace("+00:00", "Z"),
        "end_date": now.isoformat().replace("+00:00", "Z"),
    }

    r = canvas_get("/api/v1/planner/items", params)
    if not r["ok"]:
        return r

    items = r["data"] or []
    out: list[dict[str, Any]] = []

    for item in items:
        course_id = item.get("course_id")
        if allowed_course_ids is not None and course_id not in allowed_course_ids:
            continue

        subs = item.get("submissions")
        if not isinstance(subs, dict):
            continue

        if subs.get("graded") is not True:
            continue

        if include_only_with_feedback and subs.get("has_feedback") is not True:
            continue

        grade_posted_raw = subs.get("posted_at") or item.get("plannable_date")
        if not grade_posted_raw:
            continue

        try:
            grade_posted_at = datetime.fromisoformat(grade_posted_raw.replace("Z", "+00:00"))
        except Exception:
            continue

        if not (start <= grade_posted_at <= now):
            continue

        plannable = item.get("plannable") or {}
        pl_type = item.get("plannable_type")

        out.append({
            "type": "graded",
            "plannable_type": pl_type, 
            "course_id": course_id,
            "course_name": item.get("context_name"),
            "id": item.get("plannable_id"),
            "title": plannable.get("title"),
            "grade_posted_at": grade_posted_at.isoformat(),
            "html_url": abs_url(item.get("html_url") or ""),
            "submission": {
                "submitted": subs.get("submitted"),
                "graded": subs.get("graded"),
                "late": subs.get("late"),
                "missing": subs.get("missing"),
                "posted_at": subs.get("posted_at"),
                "has_feedback": subs.get("has_feedback"),
            },
        })

    out.sort(key=lambda x: x["grade_posted_at"], reverse=True)
    return out;

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
