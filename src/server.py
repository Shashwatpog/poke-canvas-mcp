#!/usr/bin/env python3
import os
import secrets
import httpx
from datetime import datetime,timezone, timedelta
import re 
from html import unescape
from typing import Any
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware.middleware import Middleware, MiddlewareContext, CallNext
from dotenv import load_dotenv

load_dotenv()
base_url = os.getenv("CANVAS_BASE_URL")
access_token = os.getenv("CANVAS_ACCESS_TOKEN")
poke_api_key = os.getenv("POKE_API_KEY")

if not poke_api_key:
    raise RuntimeError("POKE_API_KEY is required. Set it in your environment to secure this MCP server.")

class ApiKeyMiddleware(Middleware):
    def __init__(self, api_key: str, header_name: str = "x-api-key"):
        self.api_key = api_key
        self.header_name = header_name

    async def on_message(self, context: MiddlewareContext, call_next: CallNext):
        request = get_http_request()
        provided = request.headers.get(self.header_name)

        if not provided:
            auth = request.headers.get("authorization")
            if auth and auth.lower().startswith("bearer "):
                provided = auth[7:].strip()

        if not provided or not secrets.compare_digest(provided, self.api_key):
            raise PermissionError("Missing or invalid API key.")

        return await call_next(context)

mcp = FastMCP("poke-canvas-mcp", middleware=[ApiKeyMiddleware(poke_api_key)])

def canvas_get(path : str, params : dict | None = None):
    url = base_url + path
    headers = {"Authorization" : f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers, params=params, timeout=90.0)

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
    r = httpx.get(url, headers=headers, timeout=90.0)
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

@mcp.tool(description="""
Use when the user asks: 'What classes am I enrolled in?' or 'Show all my courses'.
Returns Canvas course objects for all enrolled/active courses (raw Canvas response). 
Best for troubleshooting or listing everything.""")
def list_courses_raw(_=None):
    url = base_url+"/api/v1/courses?per_page=100"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers, timeout=90.0)
    return r.json();

@mcp.tool(description="""
Use when the user asks: 'What are my current classes this term?' or 'Show my dashboard classes'.
Returns a lightweight list of active dashboard courses in the user's dashboard order (id + name).
Supports filtering by term prefix like '26SS' or '26FS'.""")
def get_dashboard_cards(term_prefix: str | None = None):
    return fetch_dashboard_cards(term_prefix)

@mcp.tool(description="""
Use when the user asks about assignments for ONE specific course (e.g. 'What is due in my Algorithms class?'). 
Returns upcoming assignments (and optionally overdue) for that course with due date and submission status. 
If the user wants everything across classes, prefer get_upcoming_assignments.""")
def get_course_assignments(course_id: int, days_ahead: int, include_overdue: bool):
    return fetch_assignments(course_id, days_ahead, include_overdue)

@mcp.tool(description="""
Use when the user asks: 'What assignments are due soon?' 'What do I have due this week?' or 'Any overdue work?' 
Returns a single sorted list of upcoming (and optionally overdue) assignments across dashboard courses. 
Best for deadline-only views (no announcements/grades).""")
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

@mcp.tool(description="""
Use when the user asks: 'Any new announcements?' 'Did my professor post anything?' or 'Any class updates?' 
Returns recent Canvas announcements across dashboard courses (title, author, posted time, link). 
Optionally include the full body text for summarization.""")
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

@mcp.tool(description="""
Use when the user asks: 'What does my week look like?' 'What’s coming up?' or 'Help me plan my week'. 
Returns Canvas planner items in a date range (assignments, quizzes, calendar events) with links and course names. 
Best for planning; not as curated as get_today_summary.""")
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

@mcp.tool(description="""
Use when the user asks: 'Did anything get graded?' 'Any grades posted recently?' or 'Any feedback?' 
Returns planner items that were graded in the last N days (optionally only those with feedback). 
Best for grade-checking / notifications.""")
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

@mcp.tool(description="""
Use for daily check-ins like: 'What do I need to do today?' 'Anything important on Canvas?' 
Returns a curated summary window: upcoming deadlines + events, recent announcements, newly graded items, and overdue not-submitted assignments from the last week. Best default tool for general student questions.""")
def get_today_summary(
    future_hours: int = 48,
    past_hours: int = 48,
    term_prefix: str | None = None,
    max_courses: int = 8,
    per_course_announcements: int = 5,
    include_announcement_body: bool = False,
    include_only_with_feedback: bool = False,
    planner_per_page: int = 100,
):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=past_hours)
    window_end = now + timedelta(hours=future_hours)

    days_ahead = max(1, int((future_hours + 23) // 24))
    days_back = max(1, int((past_hours + 23) // 24))

    courses = fetch_dashboard_cards(term_prefix)
    if not term_prefix and max_courses and max_courses > 0:
        courses = courses[:max_courses]
    allowed_course_ids = {c["id"] for c in courses}

    # planner deadlines + events
    planner_params = {
        "per_page": planner_per_page,
        "start_date": now.isoformat().replace("+00:00", "Z"),
        "end_date": (now + timedelta(days=days_ahead)).isoformat().replace("+00:00", "Z"),
    }

    r = canvas_get("/api/v1/planner/items", planner_params)
    planner_items = r["data"] if isinstance(r, dict) and r.get("ok") else []

    deadlines: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for item in planner_items or []:
        course_id = item.get("course_id")
        if course_id not in allowed_course_ids:
            continue

        dt_raw = item.get("plannable_date")
        if not dt_raw:
            continue

        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except Exception:
            continue

        if not (now <= dt <= window_end):
            continue

        plannable = item.get("plannable") or {}
        pl_type = item.get("plannable_type")

        normalized: dict[str, Any] = {
            "type": pl_type,
            "course_id": course_id,
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
            events.append(normalized)
            continue

        if pl_type in ("assignment", "quiz"):
            sub = normalized.get("submission")
            if isinstance(sub, dict) and sub.get("submitted") is True:
                continue
            deadlines.append(normalized)

    deadlines.sort(key=lambda x: x.get("date", ""))
    events.sort(key=lambda x: x.get("date", ""))

    # past hour announcements
    announcements: list[dict[str, Any]] = []

    for course in courses:
        course_id = course["id"]
        course_name = course["name"]

        params = {"only_announcements": "true", "per_page": 50}
        rr = canvas_get(f"/api/v1/courses/{course_id}/discussion_topics", params)
        if not (isinstance(rr, dict) and rr.get("ok")):
            continue

        topics = rr["data"] or []
        per_course_bucket: list[dict[str, Any]] = []

        for topic in topics:
            posted_raw = topic.get("posted_at") or topic.get("created_at")
            if not posted_raw:
                continue

            try:
                posted = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            if posted < (now - timedelta(days=days_back)):
                continue

            if not (window_start <= posted <= now):
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

            if include_announcement_body:
                body_html = topic.get("message") or ""
                item["message_html"] = body_html
                item["message_text"] = strip_html(body_html) if body_html else ""

            per_course_bucket.append(item)

        per_course_bucket.sort(key=lambda x: x.get("posted_at", ""), reverse=True)
        announcements.extend(per_course_bucket[:per_course_announcements])

    announcements.sort(key=lambda x: x.get("posted_at", ""), reverse=True)

    # graded items 
    graded: list[dict[str, Any]] = []

    graded_params = {
        "per_page": planner_per_page,
        "start_date": (now - timedelta(days=days_back)).isoformat().replace("+00:00", "Z"),
        "end_date": now.isoformat().replace("+00:00", "Z"),
    }
    rr = canvas_get("/api/v1/planner/items", graded_params)
    graded_items = rr["data"] if isinstance(rr, dict) and rr.get("ok") else []

    for item in graded_items or []:
        course_id = item.get("course_id")
        if course_id not in allowed_course_ids:
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

        if not (window_start <= grade_posted_at <= now):
            continue

        plannable = item.get("plannable") or {}
        pl_type = item.get("plannable_type")

        graded.append({
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

    graded.sort(key=lambda x: x.get("grade_posted_at", ""), reverse=True)

    # overdue and not submitted assignments in the last 7 days cuz i be forgetting
    overdue: list[dict[str, Any]] = []
    overdue_cutoff = now - timedelta(days=7)

    for course in courses:
        course_id = course["id"]
        course_name = course["name"]

        items = fetch_assignments(course_id, days_ahead = 0, include_overdue = True)
        if not isinstance(items, list):
            continue

        for a in items:
            if a.get("is_overdue") is not True:
                continue
            if a.get("submitted") is True:
                continue

            due_raw = a.get("due_at")
            if not due_raw:
                continue

            try:
                due = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            if not (overdue_cutoff <= due <= now):
                continue

            a["course_name"] = course_name
            overdue.append(a)
    overdue.sort(key=lambda x: x.get("due_at", ""), reverse=True)


    return {
        "generated_at": now.isoformat(),
        "window": {
            "past_hours": past_hours,
            "future_hours": future_hours,
        },
        "counts": {
            "deadlines": len(deadlines),
            "events": len(events),
            "announcements": len(announcements),
            "graded": len(graded),
            "overdue": len(overdue),
        },
        "deadlines": deadlines,
        "events": events,
        "announcements": announcements,
        "graded": graded,
        "overdue": overdue,
    }

@mcp.resource(
    "canvas://terms/prefix",
    description="How Canvas term_prefix works.",
)
def resource_term_prefix():
    return {
        "where": "dashboard_cards[].shortName starts with '(YYTT)'",
        "format": "YY = year (25,26,27), TT = FS|SS|US",
        "examples": ["26SS=Spring 2026", "26FS=Fall 2026", "27US=Summer 2027"],
        "use": "Pass term_prefix to tools to hide old/community courses",
    }

@mcp.resource(
    "canvas://courses/dashboard/{term_prefix}",
    description="Dashboard courses filtered by term prefix like '26SS' or '26FS'.",
)
def resource_dashboard_courses_by_term(term_prefix: str):
    return fetch_dashboard_cards(term_prefix=term_prefix)

@mcp.resource(
    "canvas://help",
    description="Tool routing cheatsheet for this MCP (helps assistants choose the right tool).",
)
def resource_help():
    return {
        "recommended_default_tool": "get_today_summary",
        "routing": [
            {"ask_like": ["what do i need to do today", "anything important", "today on canvas"], "use_tool": "get_today_summary"},
            {"ask_like": ["what's due", "deadlines", "overdue", "due this week"], "use_tool": "get_upcoming_assignments"},
            {"ask_like": ["announcements", "updates", "did my professor post"], "use_tool": "get_recent_announcements"},
            {"ask_like": ["graded", "grades posted", "feedback"], "use_tool": "get_recently_graded"},
            {"ask_like": ["week ahead", "plan my week"], "use_tool": "get_week_ahead"},
        ],
        "term_prefix_hint": "If old courses show up, use term_prefix like '26SS' to filter.",
    }

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